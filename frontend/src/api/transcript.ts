/**
 * #10's extraction endpoints and #11's summary, typed once (#17).
 *
 * The two constants matter as much as the calls. `MAX_TRANSCRIPT_CHARS` is
 * #10's raw pre-trim limit, checked here so a 100,001-character paste never
 * leaves the browser. `POLL_MS` is `_docs/decisions.md`'s two seconds — the
 * decision is that extraction polls rather than getting a WebSocket event,
 * because it is a one-off background task and not a collaborative one.
 */
import { api } from "./client";

export const MAX_TRANSCRIPT_CHARS = 100_000;
export const POLL_MS = 2000;

export type SuggestionState = "pending" | "confirmed" | "rejected";

export type DecisionSuggestion = {
  id: string;
  text: string;
  state: SuggestionState;
  created_id: string | null;
};

export type ActionSuggestion = {
  id: string;
  description: string;
  owner_name: string | null;
  owner_id: string | null;
  due_date: string | null;
  state: SuggestionState;
  created_id: string | null;
};

export type Suggestions = {
  status: "idle" | "processing" | "ready" | "failed";
  error: string | null;
  decisions: DecisionSuggestion[];
  actions: ActionSuggestion[];
};

export type ConfirmBody = {
  decisions: { id: string; text?: string }[];
  actions: {
    id: string;
    description?: string;
    owner_id?: string | null;
    due_date?: string | null;
  }[];
  rejected: string[];
};

export type SummaryTopic = {
  id: string;
  cluster_id: string;
  name: string;
  vote_count: number;
  rank: number;
  status: string;
  notes: string;
};

export type Summary = {
  topics: SummaryTopic[];
  decisions: { id: string; topic_id: string | null; topic: string | null; text: string }[];
  actions: {
    id: string;
    topic_id: string | null;
    topic: string | null;
    description: string;
    owner_id: string | null;
    owner: string | null;
    due_date: string | null;
    status: string;
  }[];
  participation: { total_members: number; submitted_feedback: number; voted: number };
  feedback_cards: {
    id: string;
    category: string;
    text: string;
    is_anonymous: boolean;
    cluster_id: string | null;
    author_id: string | null;
    created_at: string;
  }[];
};

/** The three codes #10 can store, and nothing about the proxy itself. */
export const EXTRACTION_ERRORS: Record<string, string> = {
  timeout: "The AI did not answer in time. You can try again.",
  upstream_error: "The AI service is unavailable right now. You can try again.",
  malformed_response: "The AI returned something we could not read. You can try again.",
};

export function extractionError(code: string | null): string {
  return code === null
    ? "The extraction failed. You can try again."
    : (EXTRACTION_ERRORS[code] ?? "The extraction failed. You can try again.");
}

export async function pasteTranscript(retroId: string, text: string): Promise<void> {
  await api.post(`/api/retros/${retroId}/transcript`, { text });
}

export async function getSuggestions(retroId: string): Promise<Suggestions> {
  return (await api.get<Suggestions>(`/api/retros/${retroId}/suggestions`)).data;
}

export async function confirmSuggestions(retroId: string, body: ConfirmBody): Promise<unknown> {
  return (await api.post(`/api/retros/${retroId}/suggestions/confirm`, body)).data;
}

export async function getSummary(retroId: string): Promise<Summary> {
  return (await api.get<Summary>(`/api/retros/${retroId}/summary`)).data;
}

export async function publishSummary(retroId: string): Promise<Summary> {
  return (await api.post<Summary>(`/api/retros/${retroId}/summary/publish`)).data;
}
