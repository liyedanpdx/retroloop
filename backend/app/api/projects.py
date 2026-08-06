from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, status

from app.deps import get_current_user
from app.models.project import FACILITATOR, Member, Project
from app.models.user import User
from app.schemas.project import (
    AddMemberRequest,
    CreateProjectRequest,
    MemberResponse,
    ProjectResponse,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _to_response(project: Project) -> ProjectResponse:
    return ProjectResponse(
        id=str(project.id),
        name=project.name,
        description=project.description,
        members=[
            MemberResponse(
                user_id=str(member.user_id),
                role=member.role,
                joined_at=member.joined_at,
            )
            for member in project.members
        ],
        created_at=project.created_at,
        created_by=str(project.created_by),
    )


def _parse_object_id(value: str, detail: str) -> PydanticObjectId:
    """Path ids arrive as strings so a malformed one is a 404, not a 422."""
    try:
        return PydanticObjectId(value)
    except Exception:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


async def _get_project_for_member(project_id: str, user: User) -> Project:
    project = await Project.get(_parse_object_id(project_id, "Project not found"))
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if not project.is_member(user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a project member")
    return project


async def _get_project_for_facilitator(project_id: str, user: User) -> Project:
    project = await _get_project_for_member(project_id, user)
    if not project.is_facilitator(user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only the facilitator can do this"
        )
    return project


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(body: CreateProjectRequest, user: User = Depends(get_current_user)):
    project = Project(
        name=body.name,
        description=body.description,
        created_by=user.id,
        members=[Member(user_id=user.id, role=FACILITATOR)],
    )
    await project.insert()
    return _to_response(project)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(user: User = Depends(get_current_user)):
    projects = await Project.find(Project.members.user_id == user.id).to_list()
    return [_to_response(project) for project in projects]


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, user: User = Depends(get_current_user)):
    project = await _get_project_for_member(project_id, user)
    return _to_response(project)


@router.post(
    "/{project_id}/members",
    response_model=list[MemberResponse],
    status_code=status.HTTP_201_CREATED,
)
async def add_member(
    project_id: str, body: AddMemberRequest, user: User = Depends(get_current_user)
):
    project = await _get_project_for_facilitator(project_id, user)

    invitee = await User.find_one(User.email == body.email)
    if invitee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No user with that email")
    if project.is_member(invitee.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Already a project member"
        )

    project.members.append(Member(user_id=invitee.id, role=body.role))
    await project.save()
    return _to_response(project).members


@router.delete("/{project_id}/members/{user_id}", response_model=list[MemberResponse])
async def remove_member(project_id: str, user_id: str, user: User = Depends(get_current_user)):
    project = await _get_project_for_facilitator(project_id, user)
    target_id = _parse_object_id(user_id, "Not a project member")

    if target_id == user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The facilitator cannot remove themselves",
        )
    if not project.is_member(target_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not a project member")

    project.members = [member for member in project.members if member.user_id != target_id]
    await project.save()
    return _to_response(project).members
