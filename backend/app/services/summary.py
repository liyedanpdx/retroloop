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
from app.services.discussion import topic_name
from app.services.votes import members_voted


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
    submitters = {
        card.author_id
        for card in cards
        if card.author_id is not None and card.author_id in current_member_ids
    }

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
                due_date=action.due_date,
                status=action.status,
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
