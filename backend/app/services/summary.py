from app.models.feedback import FeedbackCard
from app.models.project import Project
from app.models.retro import Retrospective
from app.models.user import User
from app.schemas.summary import (
    SummaryAction,
    SummaryDecision,
    SummaryFeedbackCard,
    SummaryParticipation,
    SummaryResponse,
    SummaryTopic,
)
from app.services.access import load_cycle
from app.services.discussion import owner_state, topic_name
from app.services.votes import members_voted


def _confirmed_from_transcript(retro: Retrospective) -> set[str]:
    """The decision and action ids that a confirmed draft created (#10).

    `ai_suggestions` is a plain dict, so this reads defensively: a retro that
    never ran an extraction, one still processing, and one whose drafts were
    deleted along with the transcript (#25) all have to come back empty rather
    than raise. Losing the marks is a cosmetic loss; failing the summary is not.
    """
    drafts = retro.ai_suggestions or {}
    created: set[str] = set()
    for key in ("decisions", "actions"):
        for row in drafts.get(key) or []:
            if isinstance(row, dict) and isinstance(row.get("created_id"), str):
                created.add(row["created_id"])
    return created


async def assemble_summary(retro: Retrospective, project: Project) -> SummaryResponse:
    """Assemble the current source documents; no summary snapshot is stored."""
    ordered_topics = sorted(retro.topics, key=lambda topic: topic.rank)
    names = {topic.id: topic_name(retro, topic) for topic in retro.topics}
    current_member_ids = {member.user_id for member in project.members}

    owner_ids = {
        action.owner_id
        for action in retro.actions
        if action.owner_id is not None and action.owner_id in current_member_ids
    }
    owners = {}
    if owner_ids:
        users = await User.find({"_id": {"$in": list(owner_ids)}}).to_list()
        owners = {user.id: user.display_name for user in users}

    cards = await FeedbackCard.find(FeedbackCard.cycle_id == retro.cycle_id).sort(
        "+created_at", "+_id"
    ).to_list()
    # The cycle's participation marker (#28), not the cards: a member whose
    # cards were all anonymous has no author reference to count, and counting
    # cards would have understated them.
    cycle = await load_cycle(str(retro.cycle_id))
    submitters = set(cycle.participants) & current_member_ids

    from_transcript = _confirmed_from_transcript(retro)

    return SummaryResponse(
        topics=[
            SummaryTopic(
                id=topic.id,
                cluster_id=topic.cluster_id,
                name=names[topic.id],
                vote_count=topic.vote_count,
                rank=topic.rank,
                status=topic.status,
                notes=topic.notes,
            )
            for topic in ordered_topics
        ],
        decisions=[
            SummaryDecision(
                id=decision.id,
                topic_id=decision.topic_id,
                topic=names.get(decision.topic_id),
                text=decision.text,
                from_transcript=decision.id in from_transcript,
            )
            for decision in retro.decisions
            if decision.is_confirmed
        ],
        actions=[
            SummaryAction(
                id=action.id,
                topic_id=action.topic_id,
                topic=names.get(action.topic_id),
                description=action.description,
                owner_id=None if action.owner_id is None else str(action.owner_id),
                owner=(
                    owners.get(action.owner_id)
                    or ((action.owner_name or "").strip() or None)
                ),
                owner_state=owner_state(action, project),
                due_date=action.due_date,
                status=action.status,
                from_transcript=action.id in from_transcript,
            )
            for action in retro.actions
        ],
        participation=SummaryParticipation(
            total_members=len(project.members),
            submitted_feedback=len(submitters),
            voted=members_voted(retro, project),
        ),
        feedback_cards=[
            SummaryFeedbackCard(
                id=str(card.id),
                category=card.category,
                text=card.text,
                is_anonymous=card.is_anonymous,
                cluster_id=card.cluster_id,
                author_id=None if card.author_id is None else str(card.author_id),
                created_at=card.created_at,
            )
            for card in cards
        ],
    )
