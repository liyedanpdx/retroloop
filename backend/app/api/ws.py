"""The one WebSocket endpoint: `ws://<host>/ws/retro/{retro_id}?token=<jwt>`.

The token is a query parameter because the browser WebSocket API cannot set an
`Authorization` header (`_docs/decisions.md`, "JWT in query param"). It is the
same access token, decoded by the same `decode_access_token`, and membership is
the same `get_retro_for_member` the REST layer uses — there is no second
authorization policy here, only a second transport.

Connecting is a member's right, not the facilitator's: everyone in the room
needs to see the board move. Who may *cause* a move is still decided by the REST
endpoint that does the writing.

Nothing sent by the client is read as a command. The receive loop exists to
notice the socket closing; a message arriving on it is discarded.
"""

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from app.models.user import User
from app.services.access import get_retro_for_member
from app.services.auth import decode_access_token
from app.services.realtime import manager

router = APIRouter()

# Closing with the policy code, not 1000, so a rejected client can tell "you may
# not be here" from "the server went away" and stop retrying (#16).
POLICY_VIOLATION = 1008


async def _member_for(token: str | None, retro_id: str) -> str | None:
    """The room this token may join, or None if it may not join any.

    One return value for every rejection — bad token, expired token, deleted
    user, unknown retro, non-member — because the client is told the same thing
    in every case. Distinguishing them over the socket would let an outsider
    probe which retrospective ids exist.
    """
    if not token:
        return None

    user_id = decode_access_token(token)
    if user_id is None:
        return None

    user = await User.get(user_id)
    if user is None:
        return None

    try:
        retro = await get_retro_for_member(retro_id, user)
    except HTTPException:
        return None
    return str(retro.id)


@router.websocket("/ws/retro/{retro_id}")
async def retro_events(
    websocket: WebSocket, retro_id: str, token: str | None = Query(default=None)
):
    room = await _member_for(token, retro_id)
    if room is None:
        # Closed before `accept`, so an unauthorized client never joins a room
        # and never sees an event; the handshake itself is refused.
        await websocket.close(code=POLICY_VIOLATION)
        return

    await manager.connect(room, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(room, websocket)
