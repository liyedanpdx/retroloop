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
    """A topic's name is its cluster's name, looked up at read time.

    Clusters cannot be renamed or deleted after the `cluster` phase (#7), so the
    lookup can only ever find the one value the topic was generated from. The
    empty-string fallback is a guard against a document that predates this
    issue, not a reachable state.
    """
    for cluster in retro.clusters:
        if cluster.id == topic.cluster_id:
            return cluster.name
    return ""


def topic_to_dict(retro: Retrospective, topic: Topic) -> dict:
    """A topic as `GET /api/retros/{id}` carries it — stored fields plus `name`."""
    data = topic.model_dump(mode="json")
    data["name"] = topic_name(retro, topic)
    return data
