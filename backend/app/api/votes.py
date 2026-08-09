from fastapi import APIRouter, Depends, HTTPException, status

from app.deps import get_current_user
from app.models.project import Project
from app.models.retro import VOTE, Retrospective, Vote
from app.models.user import User
from app.schemas.vote import (
    SubmitVoteRequest,
    VoteResponse,
    VoteResultRow,
    VoteResultsResponse,
)
from app.services import votes as vote_service
from app.services.access import get_retro_for_member, require_phase

router = APIRouter(prefix="/api", tags=["votes"])


def _to_response(ballot: Vote) -> VoteResponse:
    return VoteResponse(
        user_id=str(ballot.user_id),
        cluster_ids=list(ballot.cluster_ids),
        submitted_at=ballot.submitted_at,
    )


def vote_results(retro: Retrospective, project: Project) -> VoteResultsResponse:
    """The tally as a member is allowed to see it, results-visibility aside.

    Shared rather than inlined in the GET below, because #12 broadcasts
    `voting_closed` with this exact payload when the facilitator leaves the vote
    phase. Two constructions of the same shape would drift the moment one gained
    a field, and a client would be reading a different tally over the socket
    than over HTTP.
    """
    return VoteResultsResponse(
        members_voted=vote_service.members_voted(retro, project),
        members_total=len(project.members),
        total_votes=vote_service.total_votes(retro),
        results=[
            VoteResultRow(
                cluster_id=row.cluster_id,
                name=row.name,
                vote_count=row.vote_count,
                rank=row.rank,
            )
            for row in vote_service.tally(retro)
        ],
    )


def _reject_unknown_clusters(retro: Retrospective, cluster_ids: list[str]) -> None:
    """A ballot is all or nothing: one unknown id and none of it is written."""
    known = {cluster.id for cluster in retro.clusters}
    if any(cluster_id not in known for cluster_id in cluster_ids):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cluster not found"
        )


@router.post(
    "/retros/{retro_id}/votes",
    response_model=VoteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_votes(
    retro_id: str, body: SubmitVoteRequest, user: User = Depends(get_current_user)
):
    """Spend a member's whole vote budget in one submission, once.

    The check order is fixed — membership, phase, already-voted, cluster lookup —
    so a non-member gets 403 whatever else is wrong with the request, and a
    second submission is 409 even when its ids are garbage. Reordering the last
    two would leak "that cluster exists" to someone whose ballot is already in.

    Results are hidden, so the echoed ballot here is the only place a voter ever
    sees their own choices back.
    """
    retro = await get_retro_for_member(retro_id, user)
    require_phase(retro, VOTE)

    if vote_service.has_voted(retro, user.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already voted in this retrospective",
        )

    _reject_unknown_clusters(retro, body.cluster_ids)

    ballot = Vote(user_id=user.id, cluster_ids=list(body.cluster_ids))
    retro.votes.append(ballot)
    await retro.save()
    return _to_response(ballot)


@router.get("/retros/{retro_id}/votes/results", response_model=VoteResultsResponse)
async def get_vote_results(retro_id: str, user: User = Depends(get_current_user)):
    """Every cluster on the retro, ranked, once voting is over.

    409 rather than 403 while voting is still running: 403 already means "you are
    not on this project" on this endpoint, and the two have to be tellable apart
    for "results are hidden until voting closes" to be verifiable at all.
    """
    retro = await get_retro_for_member(retro_id, user)
    project = await vote_service.load_project_for_retro(retro)

    if not vote_service.results_are_open(retro, project):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Results are hidden until voting closes",
        )

    return vote_results(retro, project)
