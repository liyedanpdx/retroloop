import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { isAxiosError } from "axios";

import { getCycle } from "../api/cycles";
import { FACILITATOR, getDashboard, type DashboardMember } from "../api/projects";
import { getRetro, type Retro } from "../api/retro";
import {
  MAX_TRANSCRIPT_CHARS,
  POLL_MS,
  confirmSuggestions,
  extractionError,
  getSuggestions,
  pasteTranscript,
  type ActionSuggestion,
  type ConfirmBody,
  type DecisionSuggestion,
  type Suggestions,
} from "../api/transcript";
import { useAuth } from "../auth/AuthProvider";

const GENERIC_FAILURE = "Something went wrong. Please try again.";
const REPLACE_WARNING =
  "Pasting again replaces the stored transcript and discards every pending and " +
  "rejected draft. Decisions and actions you have already confirmed stay.";

type Choice = "pending" | "keep" | "reject";

type Loaded = {
  retro: Retro;
  members: DashboardMember[];
  isFacilitator: boolean;
  suggestions: Suggestions;
};

type PageState =
  | { kind: "loading" }
  | { kind: "denied" }
  | { kind: "missing" }
  | { kind: "failed" }
  | ({ kind: "ready" } & Loaded);

/**
 * Pasting a transcript, watching it extract, and reviewing the drafts (#17).
 *
 * Facilitator-only in `discuss`, and the gate is a real one: a plain member is
 * never sent the transcript, because a member never reaches the page that asks
 * for it and #10's suggestions endpoint refuses them anyway.
 *
 * The status comes from polling, not a socket. `_docs/decisions.md` calls that
 * explicitly: extraction is a one-off background task, not a collaborative
 * one, and two seconds of polling is cheaper than an event nobody else needs.
 */
export function TranscriptPage() {
  const { retroId = "" } = useParams();
  const { user } = useAuth();
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [text, setText] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [pollError, setPollError] = useState<string | null>(null);

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inFlight = useRef(false);
  const stopped = useRef(false);

  const load = useCallback(async () => {
    try {
      const retro = await getRetro(retroId);
      const cycle = await getCycle(retro.cycle_id);
      const dashboard = await getDashboard(cycle.project_id);
      const me = dashboard.members.find((member) => member.user_id === user?.id);
      const isFacilitator = me?.role === FACILITATOR;

      // A member is never handed suggestions, and never asks for them.
      const suggestions: Suggestions = isFacilitator
        ? await getSuggestions(retroId)
        : { status: "idle", error: null, decisions: [], actions: [] };

      setState({ kind: "ready", retro, members: dashboard.members, isFacilitator, suggestions });
      if (isFacilitator && suggestions.status !== "processing" && retro.transcript) {
        setText(retro.transcript);
      }
    } catch (error) {
      const code = isAxiosError(error) ? error.response?.status : undefined;
      setState(
        code === 403 ? { kind: "denied" } : code === 404 ? { kind: "missing" } : { kind: "failed" }
      );
    }
  }, [retroId, user?.id]);

  useEffect(() => {
    setState({ kind: "loading" });
    setText("");
    void load();
  }, [load]);

  const stopPolling = useCallback(() => {
    if (timer.current !== null) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  const poll = useCallback(async () => {
    if (inFlight.current || stopped.current) {
      return;
    }
    inFlight.current = true;
    try {
      const suggestions = await getSuggestions(retroId);
      setPollError(null);
      setState((current) => (current.kind === "ready" ? { ...current, suggestions } : current));
    } catch {
      // Pause rather than hammer. The last known status stays on screen and the
      // facilitator gets a button rather than a silent stall.
      setPollError("We lost contact while checking the extraction.");
      stopPolling();
    } finally {
      inFlight.current = false;
    }
  }, [retroId, stopPolling]);

  const status = state.kind === "ready" ? state.suggestions.status : "idle";
  const isFacilitator = state.kind === "ready" && state.isFacilitator;
  const inDiscuss = state.kind === "ready" && state.retro.phase === "discuss";
  const shouldPoll = status === "processing" && isFacilitator && inDiscuss && pollError === null;

  useEffect(() => {
    stopped.current = false;
    if (!shouldPoll) {
      stopPolling();
      return;
    }
    timer.current = setTimeout(function tick() {
      void poll().then(() => {
        if (!stopped.current) {
          timer.current = setTimeout(tick, POLL_MS);
        }
      });
    }, POLL_MS);
    return () => {
      stopped.current = true;
      stopPolling();
    };
  }, [shouldPoll, poll, stopPolling]);

  if (state.kind === "loading") {
    return <p role="status">Loading this retrospective…</p>;
  }
  if (state.kind === "denied") {
    return <Unavailable message="You do not have access to this retrospective." />;
  }
  if (state.kind === "missing") {
    return <Unavailable message="Retrospective not found." />;
  }
  if (state.kind === "failed") {
    return (
      <div role="alert" className="space-y-2">
        <p>We could not load this retrospective.</p>
        <button type="button" onClick={() => void load()} className="btn-link">
          Retry
        </button>
      </div>
    );
  }

  const { retro, members, suggestions } = state;

  if (!state.isFacilitator) {
    return (
      <div className="space-y-2">
        <p role="status">Only the facilitator can paste a transcript.</p>
        <Link to={`/retros/${retro.id}`}>Back to the board</Link>
      </div>
    );
  }
  if (retro.phase !== "discuss") {
    return (
      <div className="space-y-2">
        <p role="status">A transcript can only be added during the discussion.</p>
        <Link to={retro.phase === "done" ? `/retros/${retro.id}/summary` : `/retros/${retro.id}`}>
          {retro.phase === "done" ? "Read the summary" : "Back to the board"}
        </Link>
      </div>
    );
  }

  async function extract(confirmedReplace: boolean) {
    const trimmed = text.trim();
    setFormError(null);
    if (!trimmed) {
      setFormError("Paste the meeting transcript first");
      return;
    }
    if (text.length > MAX_TRANSCRIPT_CHARS) {
      setFormError(`A transcript can be at most ${MAX_TRANSCRIPT_CHARS} characters`);
      return;
    }
    if (suggestions.status === "ready" && !confirmedReplace) {
      setConfirming(true);
      return;
    }

    setPending(true);
    try {
      await pasteTranscript(retroId, trimmed);
      setState((current) =>
        current.kind === "ready"
          ? {
              ...current,
              suggestions: { status: "processing", error: null, decisions: [], actions: [] },
            }
          : current
      );
      setPollError(null);
    } catch (error) {
      const code = isAxiosError(error) ? error.response?.status : undefined;
      if (code === 409) {
        // Already running. Not a failure, and posting again would not help.
        setState((current) =>
          current.kind === "ready"
            ? {
                ...current,
                suggestions: { status: "processing", error: null, decisions: [], actions: [] },
              }
            : current
        );
        setPollError(null);
      } else if (code === 429) {
        setFormError(
          "This project has used its AI requests for now. Try again later."
        );
      } else if (code === 422) {
        setFormError("That transcript was rejected. Check the text and try again.");
      } else if (code === 400 || code === 403) {
        setFormError("The retrospective moved on. Nothing was saved.");
        await load();
      } else {
        setFormError(GENERIC_FAILURE);
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <h1>Meeting transcript</h1>
        <p>
          <Link to={`/retros/${retro.id}`}>Back to the board</Link> ·{" "}
          <Link to={`/retros/${retro.id}/summary`}>Summary</Link>
        </p>
      </header>

      {suggestions.status === "processing" ? (
        <p role="status">Extracting decisions and actions…</p>
      ) : (
        <section className="space-y-2">
          <label htmlFor="transcript">Paste the meeting transcript</label>
          <textarea
            id="transcript"
            value={text}
            disabled={pending}
            onChange={(event) => setText(event.target.value)}
            className="min-h-48"
          />
          <p>
            {text.length} / {MAX_TRANSCRIPT_CHARS}
          </p>
          <p>
            The whole meeting text is stored on this retrospective and sent to the
            project's AI service.
          </p>
          {formError && (
            <p role="alert">
              {formError}
            </p>
          )}
          <button
            type="button"
            disabled={pending}
            onClick={() => void extract(false)}
            className="btn btn-primary"
          >
            {pending ? "Sending…" : "Extract"}
          </button>
        </section>
      )}

      {confirming && (
        <div role="dialog" aria-modal="true" aria-label="Replace the transcript">
          <p>{REPLACE_WARNING}</p>
          <button
            type="button"
            className="btn-link"
            onClick={() => {
              setConfirming(false);
              void extract(true);
            }}
          >
            Yes, replace it
          </button>
          <button type="button" className="btn-link ml-3" onClick={() => setConfirming(false)}>
            Cancel
          </button>
        </div>
      )}

      {pollError && (
        <div role="alert">
          <p>{pollError}</p>
          <button
            type="button"
            className="btn-link"
            onClick={() => {
              setPollError(null);
              void poll();
            }}
          >
            Retry status check
          </button>
        </div>
      )}

      {suggestions.status === "failed" && (
        <div role="alert" className="space-y-2">
          <p>{extractionError(suggestions.error)}</p>
          <button type="button" onClick={() => void extract(true)} className="btn-link">
            Retry extraction
          </button>
        </div>
      )}

      {suggestions.status === "ready" && (
        <Review
          retroId={retro.id}
          suggestions={suggestions}
          members={members}
          onApplied={load}
          onConflict={load}
        />
      )}
    </div>
  );
}

function Unavailable({ message }: { message: string }) {
  return (
    <div role="alert" className="space-y-2">
      <p>{message}</p>
      <Link to="/projects">Back to projects</Link>
    </div>
  );
}

type DecisionDraft = { choice: Choice; text: string };
type ActionDraft = {
  choice: Choice;
  description: string;
  ownerId: string;
  dueDate: string;
};

/**
 * The review pass, applied all at once (#17).
 *
 * Nothing is marked kept or rejected until the server says `200`. #10 makes the
 * whole batch all-or-nothing, and a UI that showed half of it applied would be
 * describing a state the database never had.
 */
function Review({
  retroId,
  suggestions,
  members,
  onApplied,
  onConflict,
}: {
  retroId: string;
  suggestions: Suggestions;
  members: DashboardMember[];
  onApplied: () => Promise<void>;
  onConflict: () => Promise<void>;
}) {
  const [decisions, setDecisions] = useState<Record<string, DecisionDraft>>(() =>
    initialDecisions(suggestions.decisions)
  );
  const [actions, setActions] = useState<Record<string, ActionDraft>>(() =>
    initialActions(suggestions.actions)
  );
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const unconfirmed = (state: string) => state !== "confirmed";
  const chosenDecisions = suggestions.decisions.filter(
    (row) => unconfirmed(row.state) && decisions[row.id]?.choice !== "pending"
  );
  const chosenActions = suggestions.actions.filter(
    (row) => unconfirmed(row.state) && actions[row.id]?.choice !== "pending"
  );
  const anyChosen = chosenDecisions.length + chosenActions.length > 0;

  if (suggestions.decisions.length === 0 && suggestions.actions.length === 0) {
    return <p role="status">No suggestions found in that transcript.</p>;
  }

  async function apply() {
    const body: ConfirmBody = { decisions: [], actions: [], rejected: [] };
    for (const row of chosenDecisions) {
      const draft = decisions[row.id];
      if (draft.choice === "reject") {
        body.rejected.push(row.id);
        continue;
      }
      if (!draft.text.trim()) {
        setError("A kept decision needs some text.");
        return;
      }
      body.decisions.push({ id: row.id, text: draft.text.trim() });
    }
    for (const row of chosenActions) {
      const draft = actions[row.id];
      if (draft.choice === "reject") {
        body.rejected.push(row.id);
        continue;
      }
      if (!draft.description.trim()) {
        setError("A kept action needs a description.");
        return;
      }
      body.actions.push({
        id: row.id,
        description: draft.description.trim(),
        owner_id: draft.ownerId || null,
        due_date: draft.dueDate ? `${draft.dueDate}T00:00:00Z` : null,
      });
    }

    setPending(true);
    setError(null);
    try {
      await confirmSuggestions(retroId, body);
      await onApplied();
    } catch (failure) {
      const code = isAxiosError(failure) ? failure.response?.status : undefined;
      if (code === 422) {
        setError("One of these was rejected. Check the fields and apply again.");
      } else if (code === 404) {
        setError("One of these suggestions is no longer there.");
        await onConflict();
      } else if (code === 400 || code === 403) {
        setError("The retrospective moved on. Nothing was applied.");
        await onConflict();
      } else {
        setError(GENERIC_FAILURE);
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="space-y-4">
      <h2>Review the drafts</h2>
      {error && <p role="alert">{error}</p>}

      <h3 className="font-semibold">Decisions</h3>
      {suggestions.decisions.length === 0 ? (
        <p className="empty">No decisions were extracted</p>
      ) : (
        <ul className="space-y-2">
          {suggestions.decisions.map((row) => (
            <li key={row.id} className="card">
              {row.state === "confirmed" ? (
                <p>
                  {row.text} — kept as {row.created_id}
                </p>
              ) : (
                <>
                  <label htmlFor={`decision-${row.id}`}>Decision text</label>
                  <input
                    id={`decision-${row.id}`}
                    value={decisions[row.id]?.text ?? row.text}
                    disabled={pending}
                    onChange={(event) =>
                      setDecisions({
                        ...decisions,
                        [row.id]: { ...decisions[row.id], text: event.target.value },
                      })
                    }
                   
                  />
                  <ChoiceControls
                    name={`decision-choice-${row.id}`}
                    label={row.text}
                    value={decisions[row.id]?.choice ?? "pending"}
                    disabled={pending}
                    onChange={(choice) =>
                      setDecisions({ ...decisions, [row.id]: { ...decisions[row.id], choice } })
                    }
                  />
                </>
              )}
            </li>
          ))}
        </ul>
      )}

      <h3 className="font-semibold">Actions</h3>
      {suggestions.actions.length === 0 ? (
        <p className="empty">No actions were extracted</p>
      ) : (
        <ul className="space-y-2">
          {suggestions.actions.map((row) => (
            <li key={row.id} className="card">
              {row.state === "confirmed" ? (
                <p>
                  {row.description} — kept as {row.created_id}
                </p>
              ) : (
                <>
                  <label htmlFor={`action-${row.id}`}>Action description</label>
                  <input
                    id={`action-${row.id}`}
                    value={actions[row.id]?.description ?? row.description}
                    disabled={pending}
                    onChange={(event) =>
                      setActions({
                        ...actions,
                        [row.id]: { ...actions[row.id], description: event.target.value },
                      })
                    }
                   
                  />
                  {/* The extracted name stays visible even when it matched
                      nothing — it is the only record of who the meeting named. */}
                  <p>Named in the meeting: {row.owner_name ?? "nobody"}</p>

                  <label htmlFor={`action-owner-${row.id}`}>Owner</label>
                  <select
                    id={`action-owner-${row.id}`}
                    value={actions[row.id]?.ownerId ?? ""}
                    disabled={pending}
                    onChange={(event) =>
                      setActions({
                        ...actions,
                        [row.id]: { ...actions[row.id], ownerId: event.target.value },
                      })
                    }
                   
                  >
                    <option value="">Unassigned</option>
                    {members.map((member) => (
                      <option key={member.user_id} value={member.user_id}>
                        {member.display_name}
                      </option>
                    ))}
                  </select>

                  <label htmlFor={`action-due-${row.id}`}>Due date</label>
                  <input
                    id={`action-due-${row.id}`}
                    type="date"
                    value={actions[row.id]?.dueDate ?? ""}
                    disabled={pending}
                    onChange={(event) =>
                      setActions({
                        ...actions,
                        [row.id]: { ...actions[row.id], dueDate: event.target.value },
                      })
                    }
                   
                  />

                  <ChoiceControls
                    name={`action-choice-${row.id}`}
                    label={row.description}
                    value={actions[row.id]?.choice ?? "pending"}
                    disabled={pending}
                    onChange={(choice) =>
                      setActions({ ...actions, [row.id]: { ...actions[row.id], choice } })
                    }
                  />
                </>
              )}
            </li>
          ))}
        </ul>
      )}

      <button
        type="button"
        disabled={!anyChosen || pending}
        onClick={() => void apply()}
        className="btn btn-primary"
      >
        {pending ? "Applying…" : "Apply review"}
      </button>
    </section>
  );
}

function ChoiceControls({
  name,
  label,
  value,
  disabled,
  onChange,
}: {
  name: string;
  label: string;
  value: Choice;
  disabled: boolean;
  onChange: (choice: Choice) => void;
}) {
  return (
    <fieldset disabled={disabled}>
      <legend>What to do with “{label}”</legend>
      {(["keep", "reject", "pending"] as Choice[]).map((choice) => (
        <span key={choice}>
          <input
            id={`${name}-${choice}`}
            type="radio"
            name={name}
            checked={value === choice}
            onChange={() => onChange(choice)}
          />
          {/* The item's own words are in the name: three radios called "Keep"
              are ambiguous to anyone navigating by label. */}
          <label htmlFor={`${name}-${choice}`}>
            {choice === "keep"
              ? `Keep “${label}”`
              : choice === "reject"
                ? `Reject “${label}”`
                : `Leave “${label}” pending`}
          </label>
        </span>
      ))}
    </fieldset>
  );
}

function initialDecisions(rows: DecisionSuggestion[]): Record<string, DecisionDraft> {
  return Object.fromEntries(
    rows.map((row) => [row.id, { choice: "pending" as Choice, text: row.text }])
  );
}

function initialActions(rows: ActionSuggestion[]): Record<string, ActionDraft> {
  return Object.fromEntries(
    rows.map((row) => [
      row.id,
      {
        choice: "pending" as Choice,
        description: row.description,
        ownerId: row.owner_id ?? "",
        dueDate: row.due_date ? row.due_date.slice(0, 10) : "",
      },
    ])
  );
}
