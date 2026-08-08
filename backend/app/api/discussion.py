"""The discussion phase: marking topics off, and recording what came out of them.

Nothing here creates a topic — topics are generated from the vote tally when the
retro enters `discuss` (`_docs/decisions.md`, "Topic auto-creation"), and the
only mutable fields on one are `status` and `notes`.

There is no GET in this module. `GET /api/retros/{id}` (#6) already carries
topics, decisions and actions, and that is the read path #11 assembles the
summary from — three more read-back endpoints would be three more things to keep
in sync with the one that already works.

Two check orders, and the asymmetry is forced rather than an oversight:

* Facilitator-only endpoints run **membership+facilitator → phase → id lookup**,
  so a non-facilitator member in the wrong phase gets 403, not 400.
* Action `PATCH` runs **membership → phase → action lookup → role → field
  permission**, because the action has to be loaded before anyone can know who
  its owner is. A non-owner member in the wrong phase therefore gets 400.

No endpoint in this module reports a conflict. There is no state conflict left
to express: double generation is unreachable and re-confirming a decision is a
legal no-op. The conflict status code appears nowhere below, deliberately.
"""

from uuid import uuid4

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.deps import get_current_user
from app.models.project import Project
from app.models.retro import DISCUSS, Action, Decision, Retrospective, Topic
from app.models.user import User
from app.schemas.discussion import (
    ActionResponse,
    CreateActionRequest,
    CreateDecisionRequest,
    DecisionResponse,
    TopicResponse,
    UpdateActionRequest,
    UpdateDecisionRequest,
    UpdateTopicRequest,
)
from app.services.access import get_retro_for_facilitator, get_retro_for_member, require_phase
from app.services.discussion import topic_name
from app.services.votes import load_project_for_retro

router = APIRouter(prefix="/api", tags=["discussion"])

# What an action's owner may change on their own item. Everything else on it is
# the facilitator's ("Action owner permissions", `_docs/decisions.md`).
OWNER_EDITABLE_FIELDS = frozenset({"status", "due_date"})


# --- lookups -----------------------------------------------------------------
#
# Topic, decision and action ids are UUID strings inside the retro document, not
# ObjectIds, so a malformed one is simply an id that is not there — 404 by the
# same path as an unknown one, with no `parse_object_id` in between.


def _find_topic(retro: Retrospective, topic_id: str) -> Topic:
    for topic in retro.topics:
        if topic.id == topic_id:
            return topic
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topic not found")


def _find_decision(retro: Retrospective, decision_id: str) -> Decision:
    for decision in retro.decisions:
        if decision.id == decision_id:
            return decision
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Decision not found")


def _find_action(retro: Retrospective, action_id: str) -> Action:
    for action in retro.actions:
        if action.id == action_id:
            return action
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action not found")


def _require_topic(retro: Retrospective, topic_id: str | None) -> None:
    """A decision or action may float free, but not hang off a topic that is not here.

    `None` is a legitimate value, not a missing one: #10 confirms AI drafts with
    no topic, and a retro with no clusters has no topics to attach to at all.
    """
    if topic_id is not None:
        _find_topic(retro, topic_id)


async def _require_current_member(retro: Retrospective, owner_id: PydanticObjectId | None) -> None:
    """An action can only be assigned to somebody currently on the project.

    404 with nothing written, the same call #8 makes for a cluster id that is not
    on the retro. `None` means unassigned, which is what #10 needs for an owner
    it could not match.
    """
    if owner_id is None:
        return
    project = await load_project_for_retro(retro)
    if not project.is_member(owner_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Owner is not a project member"
        )


async def _facilitator_retro(retro_id: str, user: User) -> Retrospective:
    """Membership and role first, then phase — the id lookup is the caller's next step."""
    retro = await get_retro_for_facilitator(retro_id, user)
    require_phase(retro, DISCUSS)
    return retro


# --- responses ---------------------------------------------------------------


def _topic_response(retro: Retrospective, topic: Topic) -> TopicResponse:
    return TopicResponse(
        id=topic.id,
        cluster_id=topic.cluster_id,
        name=topic_name(retro, topic),
        vote_count=topic.vote_count,
        rank=topic.rank,
        status=topic.status,
        notes=topic.notes,
    )


def _decision_response(decision: Decision) -> DecisionResponse:
    return DecisionResponse(
        id=decision.id,
        topic_id=decision.topic_id,
        text=decision.text,
        is_confirmed=decision.is_confirmed,
    )


def _action_response(action: Action) -> ActionResponse:
    return ActionResponse(
        id=action.id,
        topic_id=action.topic_id,
        description=action.description,
        owner_id=None if action.owner_id is None else str(action.owner_id),
        status=action.status,
        due_date=action.due_date,
    )


# --- topics ------------------------------------------------------------------


@router.patch("/retros/{retro_id}/topics/{topic_id}", response_model=TopicResponse)
async def update_topic(
    retro_id: str,
    topic_id: str,
    body: UpdateTopicRequest,
    user: User = Depends(get_current_user),
):
    """Mark a topic discussed, skipped or deferred, and keep notes against it.

    An empty body is a legal no-op rather than a 422: the facilitator is running
    a live meeting off this endpoint and a request that changes nothing is not
    an error.
    """
    retro = await _facilitator_retro(retro_id, user)
    topic = _find_topic(retro, topic_id)

    sent = body.model_fields_set
    if "status" in sent and body.status is not None:
        topic.status = body.status
    if "notes" in sent and body.notes is not None:
        topic.notes = body.notes

    await retro.save()
    return _topic_response(retro, topic)


# --- decisions ---------------------------------------------------------------


@router.post(
    "/retros/{retro_id}/decisions",
    response_model=DecisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_decision(
    retro_id: str, body: CreateDecisionRequest, user: User = Depends(get_current_user)
):
    retro = await _facilitator_retro(retro_id, user)
    _require_topic(retro, body.topic_id)

    decision = Decision(id=str(uuid4()), topic_id=body.topic_id, text=body.text)
    retro.decisions.append(decision)
    await retro.save()
    return _decision_response(decision)


@router.patch("/retros/{retro_id}/decisions/{decision_id}", response_model=DecisionResponse)
async def update_decision(
    retro_id: str,
    decision_id: str,
    body: UpdateDecisionRequest,
    user: User = Depends(get_current_user),
):
    """Reword it, move it between topics, confirm it — or unconfirm it again.

    `is_confirmed` flips both ways and re-confirming an already-confirmed
    decision is a legal no-op, which is why nothing here reports a conflict.
    """
    retro = await _facilitator_retro(retro_id, user)
    decision = _find_decision(retro, decision_id)

    sent = body.model_fields_set
    if "topic_id" in sent:
        _require_topic(retro, body.topic_id)
        decision.topic_id = body.topic_id
    if "text" in sent and body.text is not None:
        decision.text = body.text
    if "is_confirmed" in sent and body.is_confirmed is not None:
        decision.is_confirmed = body.is_confirmed

    await retro.save()
    return _decision_response(decision)


@router.delete(
    "/retros/{retro_id}/decisions/{decision_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_decision(
    retro_id: str, decision_id: str, user: User = Depends(get_current_user)
):
    """#11's publish is one-way, so a wrongly created item needs a way out first."""
    retro = await _facilitator_retro(retro_id, user)
    _find_decision(retro, decision_id)

    retro.decisions = [d for d in retro.decisions if d.id != decision_id]
    await retro.save()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- actions -----------------------------------------------------------------


@router.post(
    "/retros/{retro_id}/actions",
    response_model=ActionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_action(
    retro_id: str, body: CreateActionRequest, user: User = Depends(get_current_user)
):
    """A due date in the past is accepted, stored as sent.

    A retro routinely records an already-late commitment, and "past" would
    depend on a clock and a timezone this API has never agreed on.
    """
    retro = await _facilitator_retro(retro_id, user)
    _require_topic(retro, body.topic_id)
    await _require_current_member(retro, body.owner_id)

    action = Action(
        id=str(uuid4()),
        topic_id=body.topic_id,
        description=body.description,
        owner_id=body.owner_id,
        due_date=body.due_date,
    )
    retro.actions.append(action)
    await retro.save()
    return _action_response(action)


@router.patch("/retros/{retro_id}/actions/{action_id}", response_model=ActionResponse)
async def update_action(
    retro_id: str,
    action_id: str,
    body: UpdateActionRequest,
    user: User = Depends(get_current_user),
):
    """The one endpoint here an owner may call, and only on their own item.

    The owner may move `status` and `due_date`. A restricted field in the body is
    a permission failure and not a body-shape one, so it is 403 — and *nothing*
    in that body is written, not even the fields they were allowed to send. All
    or nothing, the same rule #8 applies to a ballot mixing a real and an unknown
    cluster id.

    An action with `owner_id: null` has no owner, so every non-facilitator member
    is refused on it. A facilitator who happens to be the owner keeps full
    facilitator rights.
    """
    retro = await get_retro_for_member(retro_id, user)
    require_phase(retro, DISCUSS)
    action = _find_action(retro, action_id)

    project: Project = await load_project_for_retro(retro)
    sent = body.model_fields_set

    if not project.is_facilitator(user.id):
        if action.owner_id is None or action.owner_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the facilitator or this action's owner can change it",
            )
        if not sent <= OWNER_EDITABLE_FIELDS:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="An owner may only change status and due_date",
            )

    if "topic_id" in sent:
        _require_topic(retro, body.topic_id)
    if "owner_id" in sent:
        await _require_current_member(retro, body.owner_id)

    if "topic_id" in sent:
        action.topic_id = body.topic_id
    if "owner_id" in sent:
        action.owner_id = body.owner_id
    if "description" in sent and body.description is not None:
        action.description = body.description
    if "status" in sent and body.status is not None:
        action.status = body.status
    if "due_date" in sent:
        action.due_date = body.due_date

    await retro.save()
    return _action_response(action)


@router.delete("/retros/{retro_id}/actions/{action_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_action(retro_id: str, action_id: str, user: User = Depends(get_current_user)):
    """Facilitator only — an owner cannot delete their way out of a commitment."""
    retro = await _facilitator_retro(retro_id, user)
    _find_action(retro, action_id)

    retro.actions = [a for a in retro.actions if a.id != action_id]
    await retro.save()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
