from fastapi import APIRouter, Depends, HTTPException, Response, status

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
from app.services.concurrency import save_retro
from app.services.access import get_retro_for_member, require_writable_phase
from app.services.realtime import broadcast

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
    await require_writable_phase(retro, VOTE)

    if vote_service.has_voted(retro, user.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already voted in this retrospective",
        )

    _reject_unknown_clusters(retro, body.cluster_ids)

    ballot = Vote(user_id=user.id, cluster_ids=list(body.cluster_ids))
    retro.votes.append(ballot)

    # A ballot that completes the current membership is one of the two things
    # that opens results, and the stamp goes in the same write (#21).
    project = await vote_service.load_project_for_retro(retro)
    opened = vote_service.everyone_has_voted(retro, project) and vote_service.open_results(
        retro, project
    )
    await save_retro(retro)

    # Who voted, never what they chose (#29). The room needs the participation
    # count to move; handing it the ids would undo #8 in one line.
    await broadcast(str(retro.id), "vote_submitted", {"user_id": str(user.id)})
    if opened:
        # The aggregate, never the ballots — the same payload #12's
        # `voting_closed` carries when the facilitator closes voting instead.
        await broadcast(
            str(retro.id), "voting_closed", vote_results(retro, project).model_dump(mode="json")
        )
    return _to_response(ballot)


@router.delete("/retros/{retro_id}/votes", status_code=status.HTTP_204_NO_CONTENT)
async def retract_ballot(retro_id: str, user: User = Depends(get_current_user)):
    """Withdraw your whole ballot, before anybody could have seen the tally (#21).

    Whole ballot or nothing. There is no way to remove one cluster id or reduce
    a stacked count: `_docs/decisions.md` records that a partial edit would be
    a second, quieter voting API, and after a successful delete the existing
    atomic POST is how a corrected ballot is submitted.

    The check order is #20's, with one addition: phase, then the open cycle,
    then whether results have ever been visible, then whether this caller has a
    ballot at all. So a member in `cluster` gets the phase 400 whatever else is
    true, and a member who never voted gets 404 rather than a hint about the
    tally.
    """
    retro = await get_retro_for_member(retro_id, user)
    await require_writable_phase(retro, VOTE)

    if retro.voting_results_opened_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Voting results are already open"
        )
    if not vote_service.has_voted(retro, user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You have not voted in this retrospective",
        )

    # Conditional on the ballot still being there and the results still being
    # closed, so a delete racing the submission that opens them loses rather
    # than both succeeding. One database operation, not a read then a write.
    result = await Retrospective.get_motor_collection().update_one(
        {
            "_id": retro.id,
            "voting_results_opened_at": None,
            "votes.user_id": user.id,
        },
        {"$pull": {"votes": {"user_id": user.id}}},
    )
    if result.modified_count != 1:
        # Somebody got there first. Which answer is right depends on what they
        # did — opened the results, or withdrew this same ballot — so the
        # document decides rather than a guess.
        fresh = await Retrospective.get(retro.id)
        if fresh is not None and fresh.voting_results_opened_at is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Voting results are already open"
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You have not voted in this retrospective",
        )

    # Who withdrew, never what they had chosen.
    await broadcast(str(retro.id), "vote_retracted", {"user_id": str(user.id)})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
