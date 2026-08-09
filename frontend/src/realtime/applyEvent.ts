/**
 * Merging a #12 event into the board, idempotently (#16).
 *
 * Every merge is by stable id, so the WebSocket echo of a change this client
 * just made through REST lands on the same object and changes nothing. That is
 * what stops "create a cluster" from showing two clusters.
 *
 * Nothing here fetches. `phase_changed` and `voting_closed` are handled by the
 * page, because one needs a full reload — topics are generated server-side when
 * `discuss` begins, and no event carries them.
 */
import type { FeedbackCard } from "../api/feedback";
import type { Cluster, Retro, Topic } from "../api/retro";
import type { RetroEvent } from "./retroSocket";

export type BoardData = { retro: Retro; cards: FeedbackCard[] };

function replaceById<T extends { id: string }>(rows: T[], row: T): T[] {
  const index = rows.findIndex((existing) => existing.id === row.id);
  if (index === -1) {
    return [...rows, row];
  }
  const copy = [...rows];
  copy[index] = row;
  return copy;
}

export function applyEvent(board: BoardData, event: RetroEvent): BoardData {
  const data = event.data ?? {};
  switch (event.event) {
    case "cluster_created":
    case "cluster_renamed": {
      const cluster = data as unknown as Cluster;
      if (typeof cluster?.id !== "string") {
        return board;
      }
      return {
        ...board,
        retro: { ...board.retro, clusters: replaceById(board.retro.clusters, cluster) },
      };
    }

    case "cluster_deleted": {
      const id = data.id;
      const cardIds = Array.isArray(data.card_ids) ? (data.card_ids as string[]) : [];
      if (typeof id !== "string") {
        return board;
      }
      return {
        retro: {
          ...board.retro,
          clusters: board.retro.clusters.filter((cluster) => cluster.id !== id),
        },
        // The cards outlive the cluster, so they are ungrouped rather than lost.
        cards: board.cards.map((card) =>
          cardIds.includes(card.id) || card.cluster_id === id
            ? { ...card, cluster_id: null }
            : card
        ),
      };
    }

    case "card_moved": {
      const cardId = data.card_id;
      const clusterId = (data.cluster_id ?? null) as string | null;
      if (typeof cardId !== "string") {
        return board;
      }
      return {
        ...board,
        cards: board.cards.map((card) =>
          card.id === cardId ? { ...card, cluster_id: clusterId } : card
        ),
      };
    }

    case "vote_retracted": {
      // Only that member's submission disappears, and their choices were never
      // here to lose (#21). Idempotent: removing an id that is already gone
      // leaves the list alone, so an echoed event cannot decrement twice.
      const userId = data.user_id;
      if (typeof userId !== "string") {
        return board;
      }
      return {
        ...board,
        retro: {
          ...board.retro,
          votes: board.retro.votes.filter((vote) => vote.user_id !== userId),
        },
      };
    }

    case "vote_submitted": {
      const userId = data.user_id;
      if (typeof userId !== "string" || board.retro.votes.some((v) => v.user_id === userId)) {
        return board;
      }
      return {
        ...board,
        retro: {
          ...board.retro,
          votes: [...board.retro.votes, { user_id: userId, submitted_at: "" }],
        },
      };
    }

    case "topic_updated": {
      const topic = data as unknown as Topic;
      if (typeof topic?.id !== "string") {
        return board;
      }
      return {
        ...board,
        retro: { ...board.retro, topics: replaceById(board.retro.topics, topic) },
      };
    }

    default:
      // Including `phase_changed` and `voting_closed`, which the page handles,
      // and anything unknown, which is ignored rather than rendered.
      return board;
  }
}
