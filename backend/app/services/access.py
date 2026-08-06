"""Shared project access checks, used by every router that hangs off a project."""

from beanie import PydanticObjectId
from fastapi import HTTPException, status

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
