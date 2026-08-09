from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from app.deps import get_current_user
from app.models.cycle import ACTIVE_STATUSES, CLOSED, COLLECTING, Cycle
from app.models.user import User
from app.schemas.cycle import CycleResponse, UpdateCycleRequest
from app.services.access import (
    get_cycle_for_facilitator,
    get_cycle_for_member,
    get_project_for_facilitator,
    get_project_for_member,
)

router = APIRouter(prefix="/api", tags=["cycles"])


def _to_response(cycle: Cycle) -> CycleResponse:
    return CycleResponse(
        id=str(cycle.id),
        project_id=str(cycle.project_id),
        status=cycle.status,
        created_at=cycle.created_at,
        closed_at=cycle.closed_at,
        created_by=str(cycle.created_by),
    )


@router.post(
    "/projects/{project_id}/cycles",
    response_model=CycleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_cycle(project_id: str, user: User = Depends(get_current_user)):
    project = await get_project_for_facilitator(project_id, user)
    if project.is_archived:
        # Archiving already required no active cycle; this stops a new one
        # starting afterwards, which is the other half of "read-only" (#32).
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This project is archived"
        )

    existing = await Cycle.find(
        Cycle.project_id == project.id, {"status": {"$in": list(ACTIVE_STATUSES)}}
    ).first_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This project already has an open cycle",
        )

    cycle = Cycle(project_id=project.id, status=COLLECTING, created_by=user.id)
    await cycle.insert()
    return _to_response(cycle)


@router.get("/projects/{project_id}/cycles", response_model=list[CycleResponse])
async def list_cycles(project_id: str, user: User = Depends(get_current_user)):
    project = await get_project_for_member(project_id, user)
    cycles = await Cycle.find(Cycle.project_id == project.id).sort("-created_at").to_list()
    return [_to_response(cycle) for cycle in cycles]


@router.get("/cycles/{cycle_id}", response_model=CycleResponse)
async def get_cycle(cycle_id: str, user: User = Depends(get_current_user)):
    cycle = await get_cycle_for_member(cycle_id, user)
    return _to_response(cycle)


@router.patch("/cycles/{cycle_id}", response_model=CycleResponse)
async def update_cycle(
    cycle_id: str, body: UpdateCycleRequest, user: User = Depends(get_current_user)
):
    cycle = await get_cycle_for_facilitator(cycle_id, user)

    # Only closing happens here. collecting -> retro is issue #6, via POST /cycles/{id}/retro.
    if body.status != CLOSED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This endpoint only closes a cycle",
        )
    if cycle.status == CLOSED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Cycle is already closed"
        )

    cycle.status = CLOSED
    cycle.closed_at = datetime.now(timezone.utc)
    await cycle.save()
    return _to_response(cycle)
