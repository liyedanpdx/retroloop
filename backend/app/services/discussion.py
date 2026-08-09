"""Turning a finished vote into a discussion agenda, and naming what came out.

Two things live here rather than in a handler. Topic generation hangs off the
phase advance in `app/api/retros.py`, which cannot make an HTTP call to its own
API — so it imports `tally()` from `app/services/votes.py` directly, the seam #8
put there for exactly this. And the cluster-name resolution is shared by the
topic endpoints and by `GET /api/retros/{id}`; #11 assembles its summary off the
same document and must not re-derive either.
"""

from uuid import uuid4

from app.models.retro import Retrospective, Topic
from app.services.votes import tally


def create_topics(retro: Retrospective) -> list[Topic]:
    """One topic per cluster, in tally order, once.

    A no-op when topics already exist. Over HTTP that is unreachable — `#6`'s
    `next_phase` makes a second advance into `discuss` a 400 — but the guard is
    what keeps "generated exactly once" true of the function rather than true
    only of the one caller that happens to exist today.

    A retro with no clusters gets an empty list and no complaint. Refusing would
    strand it permanently, because #6 has no way back to an earlier phase.
    """
    if retro.topics:
        return retro.topics

    retro.topics = [
        Topic(
            id=str(uuid4()),
            cluster_id=row.cluster_id,
            vote_count=row.vote_count,
            rank=row.rank,
        )
        for row in tally(retro)
    ]
    return retro.topics


def topic_name(retro: Retrospective, topic: Topic) -> str:
    """The facilitator's name for it, or its cluster's, resolved at read time.

    The override wins when it is there, which is what makes a rename a rename
    rather than a second source of truth (#22). Otherwise it is the cluster's
    name — clusters cannot be renamed or deleted after the `cluster` phase (#7),
    so that lookup can only find the value the topic was generated from.

    The empty-string fallback is now unreachable rather than a guard: a topic
    with no cluster is required to carry an override.
    """
    if topic.name_override is not None:
        return topic.name_override
    for cluster in retro.clusters:
        if cluster.id == topic.cluster_id:
            return cluster.name
    return ""


def topic_to_dict(retro: Retrospective, topic: Topic) -> dict:
    """A topic as `GET /api/retros/{id}` carries it — stored fields plus `name`."""
    data = topic.model_dump(mode="json")
    data["name"] = topic_name(retro, topic)
    return data


# --- who owns an action, and whether they are still here ----------------------

UNASSIGNED = "unassigned"
ASSIGNED = "assigned"
ORPHANED = "orphaned"


def owner_state(action, project) -> str:
    """`unassigned`, `assigned`, or `orphaned` (#23).

    #9 decided the storage question and does not reopen it here: an action
    whose owner leaves the project keeps its `owner_id`, is not reassigned and
    is not deleted, because rewriting history would destroy the record of what
    the team agreed. What #9 does not do is *say* so, and an orphaned action
    renders exactly like one somebody is still going to do.

    Three states rather than a boolean, because #10's best-effort matching
    already produces actions with no owner at all, and "nobody was assigned"
    and "the person assigned has left" are different problems for a facilitator
    to act on.
    """
    if action.owner_id is None:
        return UNASSIGNED
    return ASSIGNED if project.is_member(action.owner_id) else ORPHANED


# --- shaping the agenda by hand (#22) ----------------------------------------


def renumber_topics(retro: Retrospective) -> None:
    """Rewrite `rank` as 1..n over the current order.

    Called after every add, remove or move, so `rank` is always a dense
    sequence. A sparse or duplicated rank would make "third on the agenda"
    ambiguous, and #11 sorts on it.
    """
    ordered = sorted(retro.topics, key=lambda topic: (topic.rank, topic.id))
    for position, topic in enumerate(ordered, start=1):
        topic.rank = position
    retro.topics = ordered


def move_topic(retro: Retrospective, topic: Topic, position: int) -> None:
    """Put a topic at a 1-based position and close the gap it left."""
    renumber_topics(retro)
    remaining = [row for row in retro.topics if row.id != topic.id]
    index = max(0, min(position - 1, len(remaining)))
    retro.topics = [*remaining[:index], topic, *remaining[index:]]
    for order, row in enumerate(retro.topics, start=1):
        row.rank = order


def orphan_topic_items(retro: Retrospective, topic_id: str) -> None:
    """Deleting a topic unlinks its decisions and actions; it never deletes them.

    Cascading would destroy what the team agreed because somebody tidied an
    agenda, and refusing would make the delete useless exactly when it is
    wanted. #11 and #16 already render null-topic items under "Unlinked", so
    there is a place for them to land (#22).
    """
    for decision in retro.decisions:
        if decision.topic_id == topic_id:
            decision.topic_id = None
    for action in retro.actions:
        if action.topic_id == topic_id:
            action.topic_id = None
