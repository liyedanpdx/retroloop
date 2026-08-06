"""Shared project access checks, used by every router that hangs off a project."""

from beanie import PydanticObjectId
from fastapi import HTTPException, status

from app.models.cycle import Cycle
from app.models.project import Project
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
