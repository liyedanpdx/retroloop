/**
 * The retrospective endpoints, typed once (#16).
 *
 * `GET /api/retros/{id}` is authoritative for phase, clusters, who has
 * submitted a ballot, topics, decisions and actions. Cards come from the
 * cycle's feedback endpoint, and member identities from #31's dashboard — this
 * page never derives a display name or a facilitator from `created_by`.
 *
 * `votes` here is a list of submitters and their timestamps. It carries no
 * cluster ids, by design (#8), and nothing in this file asks for any.
 */
import { api } from "./client";
import type { FeedbackCard } from "./feedback";

export const PHASES = ["reveal", "cluster", "vote", "discuss"] as const;
export type Phase = (typeof PHASES)[number];
export const DONE = "done";

export const TOPIC_STATUSES = ["pending", "discussed", "skipped", "deferred"] as const;
export const MAX_VOTES = 3;

export type Cluster = { id: string; name: string; created_at: string };
export type VoteSubmitter = { user_id: string; submitted_at: string };
export type Topic = {
  id: string;
  cluster_id: string | null;
  name_override: string | null;
  name: string;
  vote_count: number;
  rank: number;
  status: string;
  notes: string;
};
export type Decision = {
  id: string;
  topic_id: string | null;
  text: string;
  is_confirmed: boolean;
};
export type Action = {
  id: string;
  topic_id: string | null;
  description: string;
  owner_id: string | null;
  owner_name: string | null;
  status: string;
  due_date: string | null;
};

export type Retro = {
  id: string;
  cycle_id: string;
  phase: string;
  clusters: Cluster[];
  votes: VoteSubmitter[];
  topics: Topic[];
  decisions: Decision[];
  actions: Action[];
  /** When the tally first became visible; null while it is still hidden (#21). */
  voting_results_opened_at: string | null;
  transcript: string | null;
  ai_suggestions: unknown;
  created_at: string;
};

export type VoteResults = {
  members_voted: number;
  members_total: number;
  total_votes: number;
  results: { cluster_id: string; name: string; vote_count: number; rank: number }[];
};

export type SuggestedGrouping = {
  clusters: { name: string; card_ids: string[] }[];
  ungrouped_card_ids: string[];
};

export async function getRetro(retroId: string): Promise<Retro> {
  return (await api.get<Retro>(`/api/retros/${retroId}`)).data;
}

export async function advancePhase(retroId: string, phase: Phase): Promise<Retro> {
  return (await api.patch<Retro>(`/api/retros/${retroId}/phase`, { phase })).data;
}

export async function createCluster(retroId: string, name: string): Promise<Cluster> {
  return (await api.post<Cluster>(`/api/retros/${retroId}/clusters`, { name })).data;
}

export async function renameCluster(
  retroId: string,
  clusterId: string,
  name: string
): Promise<Cluster> {
  return (await api.patch<Cluster>(`/api/retros/${retroId}/clusters/${clusterId}`, { name })).data;
}

export async function deleteCluster(retroId: string, clusterId: string): Promise<Cluster[]> {
  return (await api.delete<Cluster[]>(`/api/retros/${retroId}/clusters/${clusterId}`)).data;
}

export async function moveCard(cardId: string, clusterId: string | null): Promise<FeedbackCard> {
  return (await api.patch<FeedbackCard>(`/api/feedback/${cardId}/cluster`, {
    cluster_id: clusterId,
  })).data;
}

export async function suggestClusters(retroId: string): Promise<SuggestedGrouping> {
  return (await api.post<SuggestedGrouping>(`/api/retros/${retroId}/clusters/suggest`, {})).data;
}

/** One atomic ballot, repetitions preserved — stacked votes are repeated ids. */
export async function submitVotes(retroId: string, clusterIds: string[]): Promise<void> {
  await api.post(`/api/retros/${retroId}/votes`, { cluster_ids: clusterIds });
}

/** Withdraw the caller's whole ballot. There is no partial version (#21). */
export async function retractBallot(retroId: string): Promise<void> {
  await api.delete(`/api/retros/${retroId}/votes`);
}

export async function getResults(retroId: string): Promise<VoteResults> {
  return (await api.get<VoteResults>(`/api/retros/${retroId}/votes/results`)).data;
}

export async function createTopic(
  retroId: string,
  body: { name: string; rank?: number }
): Promise<Topic> {
  return (await api.post<Topic>(`/api/retros/${retroId}/topics`, body)).data;
}

export async function deleteTopic(retroId: string, topicId: string): Promise<void> {
  await api.delete(`/api/retros/${retroId}/topics/${topicId}`);
}

export async function updateTopic(
  retroId: string,
  topicId: string,
  // name 是覆盖,传 null 清掉就回到 cluster 的名字;rank 是 1 起的位置 (#22)
  body: { status?: string; notes?: string; name?: string | null; rank?: number }
): Promise<Topic> {
  return (await api.patch<Topic>(`/api/retros/${retroId}/topics/${topicId}`, body)).data;
}

export async function createDecision(
  retroId: string,
  body: { topic_id: string | null; text: string }
): Promise<Decision> {
  return (await api.post<Decision>(`/api/retros/${retroId}/decisions`, body)).data;
}

export async function updateDecision(
  retroId: string,
  decisionId: string,
  body: { text?: string; is_confirmed?: boolean; topic_id?: string | null }
): Promise<Decision> {
  return (await api.patch<Decision>(`/api/retros/${retroId}/decisions/${decisionId}`, body)).data;
}

export async function deleteDecision(retroId: string, decisionId: string): Promise<void> {
  await api.delete(`/api/retros/${retroId}/decisions/${decisionId}`);
}

export async function createAction(
  retroId: string,
  body: { topic_id: string | null; description: string; owner_id: string | null }
): Promise<Action> {
  return (await api.post<Action>(`/api/retros/${retroId}/actions`, body)).data;
}

export async function updateAction(
  retroId: string,
  actionId: string,
  body: Record<string, unknown>
): Promise<Action> {
  return (await api.patch<Action>(`/api/retros/${retroId}/actions/${actionId}`, body)).data;
}

export async function deleteAction(retroId: string, actionId: string): Promise<void> {
  await api.delete(`/api/retros/${retroId}/actions/${actionId}`);
}

/** The one legal next step, or null at `done`. Never a skip, never a reverse. */
export function nextPhase(phase: string): Phase | typeof DONE | null {
  const index = (PHASES as readonly string[]).indexOf(phase);
  if (index === -1) {
    return null;
  }
  return index + 1 < PHASES.length ? PHASES[index + 1] : DONE;
}

export function sortCards(cards: FeedbackCard[]): FeedbackCard[] {
  return [...cards].sort((left, right) => {
    if (left.created_at !== right.created_at) {
      return left.created_at < right.created_at ? -1 : 1;
    }
    return left.id < right.id ? -1 : 1;
  });
}
