from fastapi import APIRouter, Depends, HTTPException, status

from app.deps import get_current_user
from app.models.cycle import CLOSED, RETRO
from app.models.retro import Retrospective, next_phase
from app.models.user import User
from app.schemas.retro import RetroResponse, UpdatePhaseRequest
from app.services.access import (
    get_cycle_for_facilitator,
    get_retro_for_facilitator,
    get_retro_for_member,
)

router = APIRouter(prefix="/api", tags=["retros"])


def _to_response(retro: Retrospective) -> RetroResponse:
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
        topics=[t.model_dump(mode="json") for t in retro.topics],
        decisions=[d.model_dump(mode="json") for d in retro.decisions],
        actions=[a.model_dump(mode="json") for a in retro.actions],
        transcript=retro.transcript,
        ai_suggestions=retro.ai_suggestions,
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

    return _to_response(retro)


@router.get("/retros/{retro_id}", response_model=RetroResponse)
async def get_retro(retro_id: str, user: User = Depends(get_current_user)):
    retro = await get_retro_for_member(retro_id, user)
    return _to_response(retro)


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

    retro.phase = allowed
    await retro.save()
    return _to_response(retro)
