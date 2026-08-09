"""Shared project access checks, used by every router that hangs off a project."""

from beanie import PydanticObjectId
from fastapi import HTTPException, status

from app.models.cycle import CLOSED, Cycle
from app.models.project import Project
from app.models.retro import Retrospective
from app.models.user import User


def parse_object_id(value: str, detail: str) -> PydanticObjectId:
    """Path ids arrive as strings so a malformed one is a 404, not a 422."""
    try:
        return PydanticObjectId(value)
    except Exception:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


async def get_project_for_member(project_id: str, user: User) -> Project:
    project = await Project.get(parse_object_id(project_id, "Project not found"))
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if not project.is_member(user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a project member")
    return project


async def get_project_for_facilitator(project_id: str, user: User) -> Project:
    project = await get_project_for_member(project_id, user)
    if not project.is_facilitator(user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only the facilitator can do this"
        )
    return project


async def load_cycle(cycle_id: str) -> Cycle:
    cycle = await Cycle.get(parse_object_id(cycle_id, "Cycle not found"))
    if cycle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cycle not found")
    return cycle


async def get_cycle_for_member(cycle_id: str, user: User) -> Cycle:
    cycle = await load_cycle(cycle_id)
    await get_project_for_member(str(cycle.project_id), user)
    return cycle


async def get_cycle_for_facilitator(cycle_id: str, user: User) -> Cycle:
    cycle = await load_cycle(cycle_id)
    await get_project_for_facilitator(str(cycle.project_id), user)
    return cycle


async def load_retro(retro_id: str) -> Retrospective:
    retro = await Retrospective.get(parse_object_id(retro_id, "Retrospective not found"))
    if retro is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Retrospective not found"
        )
    return retro


async def get_retro_for_member(retro_id: str, user: User) -> Retrospective:
    retro = await load_retro(retro_id)
    await get_cycle_for_member(str(retro.cycle_id), user)
    return retro


async def get_retro_for_facilitator(retro_id: str, user: User) -> Retrospective:
    retro = await load_retro(retro_id)
    await get_cycle_for_facilitator(str(retro.cycle_id), user)
    return retro


def require_phase(retro: Retrospective, phase: str) -> None:
    """Phase-gated work is refused outside its phase, for everyone."""
    if retro.phase != phase:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only available during the {phase} phase",
        )


async def require_cycle_open(retro: Retrospective) -> None:
    """A closed cycle's retrospective takes no more writes (#20).

    Closing is what #11's publish does last, and it is one-way. Everything the
    retro is made of — cards, clusters, ballots, topics, decisions, actions — is
    therefore final, and a command arriving afterwards is editing a record the
    team has already signed off.

    400 rather than 409, because this is the same kind of refusal as the wrong
    phase: the request is well-formed and the caller is allowed to make it, the
    retrospective is simply not in a state that accepts it. The two share a
    status code and are told apart by their detail, which is why this one is a
    fixed string rather than assembled per endpoint.

    Only writes. Reading a finished retro is the point of having published it,
    so no GET calls this.
    """
    cycle = await load_cycle(str(retro.cycle_id))
    if cycle.status == CLOSED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The retrospective's cycle is closed",
        )


async def require_writable_phase(retro: Retrospective, phase: str) -> None:
    """The gate every phase-gated retro command goes through.

    Phase first, then the cycle: a caller in the wrong phase is told that,
    whether or not the cycle behind it is closed. That ordering is what keeps
    the wrong-phase 400 meaning what it meant before #20 existed.
    """
    require_phase(retro, phase)
    await require_cycle_open(retro)
