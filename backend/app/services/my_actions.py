"""Assembling one caller's open actions across every project they belong to
(#45), the same on-read pattern `_docs/decisions.md` already documents for
#31's dashboard and #11's summary — no cached collection to go stale.

Deliberately simpler than the per-project dashboard's `open_actions`: every
row here is filtered to `owner_id == the caller`, so there is no owner name
to resolve and no `owner_state` to compute — the caller is, by construction,
a current member of every project this touches, the state #23 calls
"assigned" and nothing else.
"""

from app.models.cycle import Cycle
from app.models.project import Project
from app.models.retro import Retrospective
from app.models.user import User
from app.schemas.actions import MyAction

OPEN = "open"


async def assemble_my_actions(user: User) -> list[MyAction]:
    projects = await Project.find(Project.members.user_id == user.id).to_list()
    if not projects:
        return []
    projects_by_id = {project.id: project for project in projects}

    cycles = await Cycle.find({"project_id": {"$in": list(projects_by_id)}}).to_list()
    cycles.sort(key=lambda cycle: (cycle.created_at, cycle.id), reverse=True)
    retros = await Retrospective.find(
        {"cycle_id": {"$in": [cycle.id for cycle in cycles]}}
    ).to_list()
    retros_by_cycle = {retro.cycle_id: retro for retro in retros}

    return [
        MyAction(
            id=action.id,
            project_id=str(cycle.project_id),
            project_name=projects_by_id[cycle.project_id].name,
            retro_id=str(retro.id),
            description=action.description,
            due_date=action.due_date,
        )
        # Newest cycle first, matching #31's dashboard ordering.
        for cycle in cycles
        for retro in [retros_by_cycle.get(cycle.id)]
        if retro is not None
        for action in retro.actions
        if action.owner_id == user.id and action.status == OPEN
    ]
