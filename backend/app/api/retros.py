from fastapi import APIRouter, Depends, HTTPException, status

from app.api.votes import vote_results
from app.deps import get_current_user
from app.models.cycle import CLOSED, RETRO
from app.models.retro import DISCUSS, VOTE, Retrospective, next_phase
from app.models.user import User
from app.schemas.retro import RetroResponse, UpdatePhaseRequest
from app.services.access import (
    get_cycle_for_facilitator,
    get_retro_for_facilitator,
    get_retro_for_member,
    require_cycle_open,
)
from app.models.project import Project
from app.services.discussion import create_topics, owner_state, topic_to_dict
from app.services.realtime import broadcast
from app.services.votes import load_project_for_retro, open_results

router = APIRouter(prefix="/api", tags=["retros"])


def _to_response(retro: Retrospective, project: Project, is_facilitator: bool = True) -> RetroResponse:
    """The retro as a caller is allowed to see it.

    `is_facilitator` defaults to True because the two facilitator-only handlers
    below have already proved it; only the member-readable GET passes it.
    """
    return RetroResponse(
        id=str(retro.id),
        cycle_id=str(retro.cycle_id),
        phase=retro.phase,
        clusters=[c.model_dump(mode="json") for c in retro.clusters],
        # Who has submitted, never what they submitted. Handing the raw ballots
        # to any member would make hiding the results until voting closes (#8)
        # theatre — during the vote phase you could read every ballot off here.
        votes=[
            v.model_dump(mode="json", include={"user_id", "submitted_at"}) for v in retro.votes
        ],
        # Each topic carries its cluster's name, resolved here rather than
        # stored on the topic (#9). #11 and #16 read it off this payload.
        topics=[topic_to_dict(retro, t) for t in retro.topics],
        decisions=[d.model_dump(mode="json") for d in retro.decisions],
        # Each action says whether its owner is still on the team (#23): an
        # action left behind by somebody who has gone must not render like one
        # that is still going to happen.
        actions=[
            {**a.model_dump(mode="json"), "owner_state": owner_state(a, project)}
            for a in retro.actions
        ],
        # Both are the facilitator's, and this is where that has to be true
        # (#26). #10 made `GET /suggestions` facilitator-only; leaving the same
        # bytes on a payload every member can fetch would have made that a
        # gesture rather than a boundary — the argument #6 already made when it
        # stripped ballots out of this same response for #8.
        voting_results_opened_at=retro.voting_results_opened_at,
        transcript=retro.transcript if is_facilitator else None,
        ai_suggestions=retro.ai_suggestions if is_facilitator else None,
        created_at=retro.created_at,
    )


@router.post(
    "/cycles/{cycle_id}/retro",
    response_model=RetroResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_retro(cycle_id: str, user: User = Depends(get_current_user)):
    cycle = await get_cycle_for_facilitator(cycle_id, user)

    if cycle.status == CLOSED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A closed cycle cannot start a retrospective",
        )

    existing = await Retrospective.find_one(Retrospective.cycle_id == cycle.id)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This cycle already has a retrospective",
        )

    retro = Retrospective(cycle_id=cycle.id)
    await retro.insert()

    cycle.status = RETRO
    await cycle.save()

    return _to_response(retro, await load_project_for_retro(retro))


@router.get("/retros/{retro_id}", response_model=RetroResponse)
async def get_retro(retro_id: str, user: User = Depends(get_current_user)):
    retro = await get_retro_for_member(retro_id, user)
    project = await load_project_for_retro(retro)
    return _to_response(retro, project, project.is_facilitator(user.id))


@router.patch("/retros/{retro_id}/phase", response_model=RetroResponse)
async def update_phase(
    retro_id: str, body: UpdatePhaseRequest, user: User = Depends(get_current_user)
):
    retro = await get_retro_for_facilitator(retro_id, user)

    allowed = next_phase(retro.phase)
    if allowed is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The retrospective is already done",
        )
    if body.phase != allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Phase moves one step at a time; {retro.phase} can only become {allowed}",
        )

    # After "is this a legal next phase" and before anything is written, so a
    # closed cycle and an illegal jump each keep their own 400 detail (#20).
    await require_cycle_open(retro)

    closes_voting = retro.phase == VOTE and allowed == DISCUSS
    if closes_voting:
        # The other route to visible results (#21), stamped in the same write
        # that changes the phase.
        open_results(retro, await load_project_for_retro(retro))
    retro.phase = allowed
    # Entering `discuss` is what closes voting, so it is also what turns the
    # tally into an agenda (#9). In-process, not over HTTP — a handler cannot
    # call its own API, which is why `tally()` lives in a service at all.
    if allowed == DISCUSS:
        create_topics(retro)
    await retro.save()

    # After the save, never before (#12). `voting_closed` goes first because it
    # explains the transition that `phase_changed` then announces — a client
    # that saw them the other way round would render the discuss phase for a
    # moment with no results behind it.
    room = str(retro.id)
    if closes_voting:
        project = await load_project_for_retro(retro)
        await broadcast(room, "voting_closed", vote_results(retro, project).model_dump(mode="json"))
    await broadcast(room, "phase_changed", {"phase": allowed})

    return _to_response(retro, await load_project_for_retro(retro))
