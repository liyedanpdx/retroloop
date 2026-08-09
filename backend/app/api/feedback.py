from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.deps import get_current_user
from app.models.cycle import COLLECTING
from app.models.feedback import FeedbackCard
from app.models.user import User
from app.schemas.feedback import (
    CreateFeedbackRequest,
    FeedbackResponse,
    UpdateFeedbackRequest,
)
from app.services.access import get_cycle_for_member, parse_object_id

router = APIRouter(prefix="/api", tags=["feedback"])


def card_to_response(card: FeedbackCard) -> FeedbackResponse:
    return FeedbackResponse(
        id=str(card.id),
        cycle_id=str(card.cycle_id),
        author_id=str(card.author_id) if card.author_id else None,
        category=card.category,
        text=card.text,
        is_anonymous=card.is_anonymous,
        cluster_id=card.cluster_id,
        created_at=card.created_at,
    )


async def _get_own_card(card_id: str, user: User) -> FeedbackCard:
    """Load a card the caller is allowed to change.

    An anonymous card has no author_id, so nobody can prove they wrote it and
    nobody may edit or delete it. Once the cycle leaves collecting the cards are
    frozen for everyone. See _docs/decisions.md, Feedback.
    """
    card = await FeedbackCard.get(parse_object_id(card_id, "Card not found"))
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Card not found")
    if card.author_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="An anonymous card cannot be edited or deleted",
        )
    if card.author_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only the author can do this"
        )

    # Also raises if the caller has since been removed from the card's project.
    cycle = await get_cycle_for_member(str(card.cycle_id), user)
    if cycle.status != COLLECTING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cards are frozen once the retrospective has started",
        )
    return card


@router.post(
    "/cycles/{cycle_id}/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_card(
    cycle_id: str, body: CreateFeedbackRequest, user: User = Depends(get_current_user)
):
    cycle = await get_cycle_for_member(cycle_id, user)
    if cycle.status != COLLECTING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cards can only be added while the cycle is collecting",
        )

    card = FeedbackCard(
        cycle_id=cycle.id,
        author_id=None if body.is_anonymous else user.id,
        category=body.category,
        text=body.text,
        is_anonymous=body.is_anonymous,
    )
    await card.insert()

    # Participation is recorded once per member per cycle (#28). It is written
    # after the card so a failed insert cannot mark somebody as having
    # submitted something that does not exist.
    if user.id not in cycle.participants:
        cycle.participants.append(user.id)
        await cycle.save()

    return card_to_response(card)


@router.get("/cycles/{cycle_id}/feedback", response_model=list[FeedbackResponse])
async def list_cards(cycle_id: str, user: User = Depends(get_current_user)):
    cycle = await get_cycle_for_member(cycle_id, user)

    if cycle.status == COLLECTING:
        # Before the reveal nobody sees anyone else's cards, including their
        # own anonymous ones, which are unattributable by design.
        cards = await FeedbackCard.find(
            FeedbackCard.cycle_id == cycle.id, FeedbackCard.author_id == user.id
        ).to_list()
    else:
        cards = await FeedbackCard.find(FeedbackCard.cycle_id == cycle.id).to_list()

    return [card_to_response(card) for card in cards]


@router.patch("/feedback/{card_id}", response_model=FeedbackResponse)
async def update_card(
    card_id: str, body: UpdateFeedbackRequest, user: User = Depends(get_current_user)
):
    card = await _get_own_card(card_id, user)

    if body.text is not None:
        card.text = body.text
    if body.is_anonymous is True:
        # One-way: the author is dropped and cannot be recovered. Turning it back
        # off needs no branch here — a card that reached this point still has an
        # author_id, so it is not anonymous.
        card.is_anonymous = True
        card.author_id = None

    await card.save()
    return card_to_response(card)


@router.delete("/feedback/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_card(card_id: str, user: User = Depends(get_current_user)):
    card = await _get_own_card(card_id, user)
    await card.delete()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
