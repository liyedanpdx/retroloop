from fastapi import APIRouter, Depends, HTTPException, status

from app.deps import get_current_user
from datetime import datetime, timezone

from app.models.cycle import ACTIVE_STATUSES, Cycle
from app.models.project import FACILITATOR, Member, Project
from app.models.user import User
from app.schemas.dashboard import DashboardResponse
from app.schemas.project import (
    AddMemberRequest,
    ArchiveProjectRequest,
    CreateProjectRequest,
    MemberResponse,
    ProjectResponse,
    UpdateMemberRoleRequest,
    UpdateProjectRequest,
)
from app.services.access import (
    get_project_for_facilitator,
    get_project_for_member,
    parse_object_id,
)
from app.services.dashboard import assemble_dashboard

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _require_active(project: Project) -> None:
    """An archived project is read-only. 400, the same as any other wrong state."""
    if project.is_archived:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This project is archived"
        )


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
        archived_at=project.archived_at,
    )


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
    project = await get_project_for_member(project_id, user)
    return _to_response(project)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str, body: UpdateProjectRequest, user: User = Depends(get_current_user)
):
    """Rename or re-describe. Facilitator only, and not while archived (#32).

    An empty body is a legal no-op rather than a 422: `model_fields_set` is what
    separates "not sent" from "sent as null", so clearing a description stays
    expressible.
    """
    project = await get_project_for_facilitator(project_id, user)
    _require_active(project)

    sent = body.model_fields_set
    if "name" in sent and body.name is not None:
        project.name = body.name
    if "description" in sent:
        project.description = body.description

    await project.save()
    return _to_response(project)


@router.patch("/{project_id}/archive", response_model=ProjectResponse)
async def set_archived(
    project_id: str, body: ArchiveProjectRequest, user: User = Depends(get_current_user)
):
    """Archive or restore. Nothing is deleted, ever (#32).

    Archiving is reversible by the same people who can do it, which is the whole
    reason it is archiving: a project's cycles, retrospectives, feedback and
    actions are the record of what a team did, and no button in this product
    destroys that.

    Idempotent both ways — archiving an archived project is a 200 that changes
    nothing, because the caller asked for a state, not for a transition.
    """
    project = await get_project_for_facilitator(project_id, user)

    if body.archived and project.archived_at is None:
        # Refusing here is what makes "archived" mean something: with no active
        # cycle, nothing is in flight, and no guard has to be sprinkled through
        # the feedback and retro paths to stop work continuing under an
        # archived project.
        active = await Cycle.find_one(
            Cycle.project_id == project.id, {"status": {"$in": list(ACTIVE_STATUSES)}}
        )
        if active is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Close the current cycle before archiving this project",
            )
        project.archived_at = datetime.now(timezone.utc)
        await project.save()
    elif not body.archived and project.archived_at is not None:
        project.archived_at = None
        await project.save()

    return _to_response(project)


@router.patch("/{project_id}/members/{user_id}", response_model=list[MemberResponse])
async def update_member_role(
    project_id: str,
    user_id: str,
    body: UpdateMemberRoleRequest,
    user: User = Depends(get_current_user),
):
    """Promote or demote a member. Any facilitator, including themselves (#32).

    The one rule is that a project can never be left without a facilitator.
    Demoting the last one is refused — a project nobody can run is a project
    whose cycles nobody can close, and there is no admin above this to fix it.

    Self-demotion is allowed when somebody else is a facilitator, so handing
    over and stepping back is possible without a third party.
    """
    project = await get_project_for_facilitator(project_id, user)
    _require_active(project)

    target_id = parse_object_id(user_id, "Not a project member")
    member = project.member_for(target_id)
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not a project member"
        )

    demoting_the_last = (
        member.role == FACILITATOR
        and body.role != FACILITATOR
        and len(project.facilitators()) == 1
    )
    if demoting_the_last:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A project must always have a facilitator",
        )

    member.role = body.role
    await project.save()
    return _to_response(project).members


@router.get("/{project_id}/dashboard", response_model=DashboardResponse)
async def get_dashboard(project_id: str, user: User = Depends(get_current_user)):
    """Everything #14's project page needs, in one authorized read (#31).

    Any current member, not only the facilitator: this is the page a member
    lands on, and none of it is privileged — the counts are counts, and the
    actions are the ones the team agreed to in the open.
    """
    project = await get_project_for_member(project_id, user)
    return await assemble_dashboard(project)


@router.post(
    "/{project_id}/members",
    response_model=list[MemberResponse],
    status_code=status.HTTP_201_CREATED,
)
async def add_member(
    project_id: str, body: AddMemberRequest, user: User = Depends(get_current_user)
):
    project = await get_project_for_facilitator(project_id, user)
    _require_active(project)

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
    project = await get_project_for_facilitator(project_id, user)
    _require_active(project)
    target_id = parse_object_id(user_id, "Not a project member")

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
