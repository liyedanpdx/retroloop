import { CARD_INK, CATEGORIES, CATEGORY_LABELS, type FeedbackCard } from "../../api/feedback";
import { sortCards } from "../../api/retro";
import type { DashboardMember } from "../../api/projects";

export const ANONYMOUS = "Anonymous";
export const FORMER_MEMBER = "Former member";

/**
 * Who wrote a card, as far as this client is allowed to know (#16).
 *
 * An anonymous card is `Anonymous` and nothing else — there is no lookup to
 * attempt, because #5 erased the reference on purpose. A card whose author has
 * left the project is `Former member` rather than a bare id.
 */
export function authorLabel(card: FeedbackCard, members: DashboardMember[]): string {
  if (card.is_anonymous || card.author_id === null) {
    return ANONYMOUS;
  }
  const member = members.find((row) => row.user_id === card.author_id);
  return member ? member.display_name : FORMER_MEMBER;
}

/** Reveal is read-only. No edit, no delete, no move — the cards are frozen. */
export function RevealPanel({
  cards,
  members,
}: {
  cards: FeedbackCard[];
  members: DashboardMember[];
}) {
  return (
    <div className="grid gap-6 md:grid-cols-3">
      {CATEGORIES.map((category) => {
        const inCategory = sortCards(cards.filter((card) => card.category === category));
        const label = CATEGORY_LABELS[category];
        return (
          <section key={category} className="space-y-2">
            <h2>{label}</h2>
            {inCategory.length === 0 ? (
              <p className="empty">No {label} cards</p>
            ) : (
              <ul className="space-y-2">
                {inCategory.map((card) => (
                  <li key={card.id} className={`card ${CARD_INK[category]}`}>
                    <p className="break-words">{card.text}</p>
                    <p className="meta">{authorLabel(card, members)}</p>
                  </li>
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}
