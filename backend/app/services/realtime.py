"""One room per retrospective, and the helper the REST layer pushes into it.

`_docs/decisions.md` ("Broadcast helper, not middleware") puts the call site in
the handler: a router calls `broadcast(retro_id, event, data)` after a write it
already knows succeeded. Middleware would have to reconstruct, from a response
object, what the handler had just done and whether it counted — and a no-op
`PATCH` looks exactly like a real one from there.

Nothing here is durable. The rooms are a dict in this process, so a second
worker has its own; that, and replaying what a client missed, are #29's. A
reconnecting client gets its state from `GET /api/retros/{id}` (#16), which is
why losing a room on restart is survivable rather than a data loss.
"""

from typing import Any


class ConnectionManager:
    """The live sockets, grouped by retrospective id.

    A send that fails takes its own socket out of the room. A client that has
    gone away must not be able to hold up delivery to the ones still there, and
    the REST call that triggered the broadcast has already committed — failing
    it now would report a database write as unsuccessful because an unrelated
    browser closed a tab.
    """

    def __init__(self) -> None:
        self._rooms: dict[str, set] = {}

    async def connect(self, retro_id: str, websocket) -> None:
        await websocket.accept()
        self._rooms.setdefault(retro_id, set()).add(websocket)

    def disconnect(self, retro_id: str, websocket) -> None:
        """Idempotent: a socket that closes and then errors is removed once.

        The empty room is dropped rather than left behind, so a server that has
        run a thousand retros is not carrying a thousand empty sets.
        """
        room = self._rooms.get(retro_id)
        if room is None:
            return
        room.discard(websocket)
        if not room:
            self._rooms.pop(retro_id, None)

    def connection_count(self, retro_id: str) -> int:
        return len(self._rooms.get(retro_id, ()))

    def rooms(self) -> list[str]:
        return list(self._rooms)

    async def broadcast(self, retro_id: str, event: str, data: dict[str, Any]) -> None:
        message = {"event": event, "data": data}
        for websocket in list(self._rooms.get(retro_id, ())):
            try:
                await websocket.send_json(message)
            except Exception:
                self.disconnect(retro_id, websocket)


manager = ConnectionManager()


async def broadcast(retro_id: str, event: str, data: dict[str, Any]) -> None:
    """Fan one `{event, data}` envelope out to a retro's room.

    Call it after the write, never before: an event that describes a change the
    database refused is worse than no event at all, because a client cannot tell
    the difference and will render it.
    """
    await manager.broadcast(retro_id, event, data)
