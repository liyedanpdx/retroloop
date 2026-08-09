/**
 * Cycles and feedback cards, typed once (#15).
 *
 * `ANONYMOUS_WARNING` lives here rather than in a component because it is said
 * in two places — before creating an anonymous card and before making an
 * existing one anonymous — and the two must not drift. What it promises is a
 * backend fact: `_docs/decisions.md`, "Anonymous cards cannot be edited or
 * deleted", and "Making a card anonymous is one-way".
 */
import { api } from "./client";

export const CATEGORIES = ["start", "stop", "continue"] as const;
export type Category = (typeof CATEGORIES)[number];

export const CATEGORY_LABELS: Record<Category, string> = {
  start: "Start",
  stop: "Stop",
  continue: "Continue",
};

export const COLLECTING = "collecting";

export const ANONYMOUS_WARNING =
  "Anonymous cards cannot be edited or deleted, and will disappear from your " +
  "private list after submission.";

export type Cycle = {
  id: string;
  project_id: string;
  status: string;
  created_at: string;
  closed_at: string | null;
  created_by: string;
};

export type FeedbackCard = {
  id: string;
  cycle_id: string;
  author_id: string | null;
  category: string;
  text: string;
  is_anonymous: boolean;
  cluster_id: string | null;
  created_at: string;
};

export async function listCycles(projectId: string): Promise<Cycle[]> {
  const response = await api.get<Cycle[]>(`/api/projects/${projectId}/cycles`);
  return response.data;
}

export async function listFeedback(cycleId: string): Promise<FeedbackCard[]> {
  const response = await api.get<FeedbackCard[]>(`/api/cycles/${cycleId}/feedback`);
  return response.data;
}

export async function createCard(
  cycleId: string,
  category: Category,
  text: string,
  isAnonymous: boolean
): Promise<FeedbackCard> {
  const response = await api.post<FeedbackCard>(`/api/cycles/${cycleId}/feedback`, {
    category,
    text,
    is_anonymous: isAnonymous,
  });
  return response.data;
}

export async function updateCardText(cardId: string, text: string): Promise<FeedbackCard> {
  const response = await api.patch<FeedbackCard>(`/api/feedback/${cardId}`, { text });
  return response.data;
}

/** One way, always. There is no call that sets `is_anonymous` back to false. */
export async function makeCardAnonymous(cardId: string): Promise<FeedbackCard> {
  const response = await api.patch<FeedbackCard>(`/api/feedback/${cardId}`, {
    is_anonymous: true,
  });
  return response.data;
}

export async function deleteCard(cardId: string): Promise<void> {
  await api.delete(`/api/feedback/${cardId}`);
}

/**
 * At most one cycle is open at a time, so "the active one" is a fact, not a
 * choice. A closed historical cycle is never it, and the route never carries a
 * cycle id to guess from.
 */
export function activeCycle(cycles: Cycle[]): Cycle | null {
  return cycles.find((cycle) => cycle.status === COLLECTING || cycle.status === "retro") ?? null;
}

/**
 * Only the signed-in user's attributable cards, oldest first.
 *
 * The filter is defensive rather than decorative: after reveal the same
 * endpoint returns the whole team's cards, and this page must never show
 * somebody else's writing or imply an anonymous card was theirs.
 */
export function ownCards(cards: FeedbackCard[], userId: string | undefined): FeedbackCard[] {
  return cards
    .filter((card) => !card.is_anonymous && card.author_id !== null && card.author_id === userId)
    .sort((left, right) => {
      if (left.created_at !== right.created_at) {
        return left.created_at < right.created_at ? -1 : 1;
      }
      return left.id < right.id ? -1 : 1;
    });
}
