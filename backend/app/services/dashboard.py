"""Assembling the project dashboard from the documents that already exist (#31).

No dashboard collection and no cached snapshot, the same call `_docs/decisions.md`
made for #11's summary: every field here is derived from the project, its cycles,
their retros and the feedback cards on each read, so the dashboard cannot go
stale against the thing it is describing.

The ordering rules are all "newest first, with a tiebreak that is not time",
because two cycles created in the same second must not swap places between two
requests and make #14 flicker.
"""

from app.models.cycle import ACTIVE_STATUSES, Cycle
from app.models.feedback import FeedbackCard
from app.models.project import Project
from app.models.retro import Retrospective
from app.models.user import User
from app.schemas.dashboard import (
    DashboardCycle,
    DashboardMember,
    DashboardResponse,
    DashboardRetro,
    OpenAction,
    PastRetro,
    SubmissionProgress,
)

OPEN = "open"


async def _members(project: Project) -> list[DashboardMember]:
    """Membership order, with each row's identity filled in from `users`.

    A member row whose user document has been deleted is dropped rather than
    rendered as a blank name: the dashboard is a list of people to show, and
    there is nobody there to show.
    """
    ids = [member.user_id for member in project.members]
    users = {user.id: user for user in await User.find({"_id": {"$in": ids}}).to_list()}
    return [
        DashboardMember(
            user_id=str(member.user_id),
            display_name=users[member.user_id].display_name,
            email=users[member.user_id].email,
            role=member.role,
            joined_at=member.joined_at,
        )
        for member in project.members
        if member.user_id in users
    ]


async def _progress(cycle: Cycle, project: Project) -> SubmissionProgress:
    cards = await FeedbackCard.find(FeedbackCard.cycle_id == cycle.id).to_list()
    current = {member.user_id for member in project.members}
    submitters = {
        card.author_id
        for card in cards
        if card.author_id is not None and card.author_id in current
    }
    return SubmissionProgress(
        submitted_members=len(submitters), total_members=len(project.members)
    )


def _owner_name(action, owners: dict) -> str | None:
    matched = owners.get(action.owner_id)
    if matched:
        return matched
    return (action.owner_name or "").strip() or None


async def assemble_dashboard(project: Project) -> DashboardResponse:
    cycles = await Cycle.find(Cycle.project_id == project.id).to_list()
    cycles.sort(key=lambda cycle: (cycle.created_at, cycle.id), reverse=True)
    retros = {
        retro.cycle_id: retro
        for retro in await Retrospective.find(
            {"cycle_id": {"$in": [cycle.id for cycle in cycles]}}
        ).to_list()
    }

    # At most one cycle is active — #4 refuses to open a second — so the newest
    # active one is *the* current one rather than a pick among several.
    current = next((cycle for cycle in cycles if cycle.status in ACTIVE_STATUSES), None)
    current_cycle = None
    if current is not None:
        retro = retros.get(current.id)
        current_cycle = DashboardCycle(
            id=str(current.id),
            status=current.status,
            created_at=current.created_at,
            closed_at=current.closed_at,
            progress=await _progress(current, project),
            retro=None if retro is None else DashboardRetro(id=str(retro.id), phase=retro.phase),
        )

    past = [
        PastRetro(
            id=str(retros[cycle.id].id),
            cycle_id=str(cycle.id),
            phase=retros[cycle.id].phase,
            created_at=retros[cycle.id].created_at,
            closed_at=cycle.closed_at,
        )
        for cycle in cycles
        if cycle.id in retros and (current is None or cycle.id != current.id)
    ]

    # Owner names come from the current member list only, the rule #9 and #11
    # both apply: somebody who has left the project is not resolved back into a
    # display name here.
    member_ids = [member.user_id for member in project.members]
    owners = {
        user.id: user.display_name
        for user in await User.find({"_id": {"$in": member_ids}}).to_list()
    }

    open_actions = [
        OpenAction(
            id=action.id,
            retro_id=str(retro.id),
            description=action.description,
            owner_id=None if action.owner_id is None else str(action.owner_id),
            owner=_owner_name(action, owners),
            due_date=action.due_date,
            status=action.status,
        )
        # Newest retro first, and within one retro the order #9 stored them in.
        for retro in (retros[cycle.id] for cycle in cycles if cycle.id in retros)
        for action in retro.actions
        if action.status == OPEN
    ]

    return DashboardResponse(
        members=await _members(project),
        current_cycle=current_cycle,
        past_retros=past,
        open_actions=open_actions,
    )
