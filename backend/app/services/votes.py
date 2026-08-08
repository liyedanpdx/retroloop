"""Counting ballots, and deciding when the counts may be read.

Neither of these lives in `app/api/votes.py`, because #9 ranks one topic per
cluster off exactly this tally when the retro enters `discuss`. A handler cannot
make an HTTP call to itself, so the tally has to be importable. This module is
that seam.

Nothing here reveals who voted for what — a `ClusterTally` carries a count and
no user ids, which is the whole point of hiding results until voting closes.
"""

from collections import Counter
from dataclasses import dataclass

from beanie import PydanticObjectId
from fastapi import HTTPException, status

from app.models.project import Project
from app.models.retro import DISCUSS, DONE, Retrospective
from app.services.access import load_cycle

# Reaching either of these means the facilitator has closed voting.
RESULT_PHASES = (DISCUSS, DONE)


@dataclass(frozen=True)
class ClusterTally:
    """One cluster's standing: every cluster gets one, including zero-vote ones."""

    cluster_id: str
    name: str
    vote_count: int
    rank: int


async def load_project_for_retro(retro: Retrospective) -> Project:
    """The project whose members are entitled to vote on this retro.

    Callers reach this after `get_retro_for_member`, which has already proved the
    cycle and the project exist; the raise is a guard, not a reachable path.
    """
    cycle = await load_cycle(str(retro.cycle_id))
    project = await Project.get(cycle.project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    return project


def has_voted(retro: Retrospective, user_id: PydanticObjectId) -> bool:
    """One ballot per member, so presence is the whole question."""
    return any(ballot.user_id == user_id for ballot in retro.votes)


def total_votes(retro: Retrospective) -> int:
    """Votes cast, not ballots submitted — stacked votes each count."""
    return sum(len(ballot.cluster_ids) for ballot in retro.votes)


def members_voted(retro: Retrospective, project: Project) -> int:
    """How many *current* members have a ballot.

    Counted against the member list rather than off `len(retro.votes)` so that a
    ballot left behind by someone who has since left the project cannot make
    `members_voted` exceed `members_total`.
    """
    voters = {ballot.user_id for ballot in retro.votes}
    return sum(1 for member in project.members if member.user_id in voters)


def everyone_has_voted(retro: Retrospective, project: Project) -> bool:
    if not project.members:
        return False
    return members_voted(retro, project) == len(project.members)


def results_are_open(retro: Retrospective, project: Project) -> bool:
    """Results are readable once voting is over, by either route.

    The facilitator advancing to `discuss` is what closes voting, and a retro
    where every current member has already submitted has nothing left to wait
    for. Reaching the second case does not advance the phase — phases move only
    when the facilitator moves them (#6), and #9 hangs topic creation off
    entering `discuss`.
    """
    return retro.phase in RESULT_PHASES or everyone_has_voted(retro, project)


def tally(retro: Retrospective) -> list[ClusterTally]:
    """Every cluster on the retro, ranked.

    Zero-vote clusters stay in, because #9 creates one topic per cluster and
    would otherwise have to fetch the clusters separately and merge.

    The order is `vote_count` descending, then `created_at` ascending, then `id`
    ascending. The last two are not decoration: without a total order, two
    identical requests could rank ties differently and #9's output would flap.
    """
    counts = Counter()
    for ballot in retro.votes:
        counts.update(ballot.cluster_ids)

    ordered = sorted(
        retro.clusters, key=lambda cluster: (-counts[cluster.id], cluster.created_at, cluster.id)
    )
    return [
        ClusterTally(
            cluster_id=cluster.id,
            name=cluster.name,
            vote_count=counts[cluster.id],
            rank=rank,
        )
        for rank, cluster in enumerate(ordered, start=1)
    ]
