from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.feedback import card_to_response
from app.deps import get_current_user
from app.models.feedback import FeedbackCard
from app.models.retro import CLUSTER, Cluster, Retrospective
from app.models.user import User
from app.schemas.cluster import (
    ClusterNameRequest,
    ClusterResponse,
    ClusterSuggestionResponse,
    MoveCardRequest,
    SuggestClustersRequest,
)
from app.schemas.feedback import FeedbackResponse
from app.services.access import (
    get_cycle_for_member,
    get_retro_for_facilitator,
    get_retro_for_member,
    parse_object_id,
    require_writable_phase,
)
from app.services.ai import ProxyError, ProxyMalformedResponse, ProxyTimeout
from app.services.cluster_suggestions import suggest_clusters
from app.services.realtime import broadcast

router = APIRouter(prefix="/api", tags=["clusters"])


def _to_response(cluster: Cluster) -> ClusterResponse:
    return ClusterResponse(id=cluster.id, name=cluster.name, created_at=cluster.created_at)


def _find_cluster(retro: Retrospective, cluster_id: str) -> Cluster:
    for cluster in retro.clusters:
        if cluster.id == cluster_id:
            return cluster
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cluster not found")


async def _get_clustering_retro(retro_id: str, user: User) -> Retrospective:
    retro = await get_retro_for_member(retro_id, user)
    await require_writable_phase(retro, CLUSTER)
    return retro


@router.post(
    "/retros/{retro_id}/clusters",
    response_model=ClusterResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_cluster(
    retro_id: str, body: ClusterNameRequest, user: User = Depends(get_current_user)
):
    retro = await _get_clustering_retro(retro_id, user)

    cluster = Cluster(id=str(uuid4()), name=body.name)
    retro.clusters.append(cluster)
    await retro.save()

    response = _to_response(cluster)
    await broadcast(str(retro.id), "cluster_created", response.model_dump(mode="json"))
    return response


@router.post("/retros/{retro_id}/clusters/suggest", response_model=ClusterSuggestionResponse)
async def suggest_clusters_for_retro(
    retro_id: str,
    body: SuggestClustersRequest,
    user: User = Depends(get_current_user),
):
    """Ask the proxy how it would group this cycle's cards. Changes nothing.

    Facilitator-only, unlike the rest of this module: creating and renaming a
    cluster is teamwork, but spending the project's AI budget is not something
    any member should be able to do on a loop.

    The three failures are told apart because the client can act on them
    differently — a timeout is worth retrying, an invalid answer is worth
    retrying once, and an unavailable proxy is worth telling somebody about. The
    details are fixed strings: an upstream body or URL on a 502 would be a proxy
    endpoint leaked to every member of the project.
    """
    retro = await get_retro_for_facilitator(retro_id, user)
    await require_writable_phase(retro, CLUSTER)

    try:
        return await suggest_clusters(retro)
    except ProxyTimeout:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="AI suggestion timed out"
        )
    except ProxyMalformedResponse:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI returned invalid cluster suggestions",
        )
    except ProxyError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="AI suggestion unavailable"
        )


@router.patch("/retros/{retro_id}/clusters/{cluster_id}", response_model=ClusterResponse)
async def rename_cluster(
    retro_id: str,
    cluster_id: str,
    body: ClusterNameRequest,
    user: User = Depends(get_current_user),
):
    retro = await _get_clustering_retro(retro_id, user)

    cluster = _find_cluster(retro, cluster_id)
    cluster.name = body.name
    await retro.save()

    response = _to_response(cluster)
    await broadcast(str(retro.id), "cluster_renamed", response.model_dump(mode="json"))
    return response


@router.delete("/retros/{retro_id}/clusters/{cluster_id}", response_model=list[ClusterResponse])
async def delete_cluster(
    retro_id: str, cluster_id: str, user: User = Depends(get_current_user)
):
    retro = await _get_clustering_retro(retro_id, user)
    _find_cluster(retro, cluster_id)

    retro.clusters = [c for c in retro.clusters if c.id != cluster_id]
    await retro.save()

    # The cards outlive the cluster, so they are ungrouped rather than deleted.
    orphans = await FeedbackCard.find(
        FeedbackCard.cycle_id == retro.cycle_id, FeedbackCard.cluster_id == cluster_id
    ).to_list()
    for card in orphans:
        card.cluster_id = None
        await card.save()

    # The card ids travel with the event because the delete moved them too. A
    # client told only that a cluster is gone would have to guess which cards
    # came loose, or refetch the whole board to find out.
    await broadcast(
        str(retro.id),
        "cluster_deleted",
        {"id": cluster_id, "card_ids": [str(card.id) for card in orphans]},
    )
    return [_to_response(c) for c in retro.clusters]


@router.patch("/feedback/{card_id}/cluster", response_model=FeedbackResponse)
async def move_card(
    card_id: str, body: MoveCardRequest, user: User = Depends(get_current_user)
):
    """Move a card into a cluster, or out of one with cluster_id: null.

    Deliberately does not go through the author and freeze checks in
    app/api/feedback.py — clustering is a team activity during a phase where the
    cards are frozen to their own authors.
    """
    card = await FeedbackCard.get(parse_object_id(card_id, "Card not found"))
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Card not found")

    await get_cycle_for_member(str(card.cycle_id), user)

    retro = await Retrospective.find_one(Retrospective.cycle_id == card.cycle_id)
    if retro is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="This cycle has no retrospective"
        )
    await require_writable_phase(retro, CLUSTER)

    if body.cluster_id is not None:
        _find_cluster(retro, body.cluster_id)

    # Dropping a card back where it already was is a legal request and a 200,
    # but it is not news. Broadcasting it would make every client re-render a
    # move that never happened, and during clustering that is a card visibly
    # jumping under someone else's cursor.
    moved = card.cluster_id != body.cluster_id
    card.cluster_id = body.cluster_id
    await card.save()

    if moved:
        await broadcast(
            str(retro.id),
            "card_moved",
            {"card_id": str(card.id), "cluster_id": card.cluster_id},
        )
    return card_to_response(card)
