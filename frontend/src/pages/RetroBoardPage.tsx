import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { isAxiosError } from "axios";

import { getCycle } from "../api/cycles";
import { listFeedback, type FeedbackCard } from "../api/feedback";
import { FACILITATOR, getDashboard, type DashboardMember } from "../api/projects";
import {
  DONE,
  PHASES,
  advancePhase,
  getResults,
  getRetro,
  nextPhase,
  type Phase,
  type Retro,
  type VoteResults,
} from "../api/retro";
import { useAuth } from "../auth/AuthProvider";
import { ClusterPanel, describe } from "../components/retro/ClusterPanel";
import { DiscussPanel } from "../components/retro/DiscussPanel";
import { RevealPanel } from "../components/retro/RevealPanel";
import { VotePanel } from "../components/retro/VotePanel";
import { applyEvent, type BoardData } from "../realtime/applyEvent";
import { RetroSocket, type ConnectionStatus, type RetroEvent } from "../realtime/retroSocket";

type Loaded = {
  retro: Retro;
  cards: FeedbackCard[];
  members: DashboardMember[];
  isFacilitator: boolean;
  results: VoteResults | null;
};

type PageState =
  | { kind: "loading" }
  | { kind: "denied" }
  | { kind: "missing" }
  | { kind: "failed" }
  | ({ kind: "ready" } & Loaded);

const STATUS_LABELS: Record<ConnectionStatus, string> = {
  connecting: "Connecting",
  connected: "Connected",
  reconnecting: "Reconnecting",
  offline: "Offline",
};

/**
 * One retrospective, from reveal to discuss (#16).
 *
 * Everything the board believes comes from a full authoritative load: the
 * retro, its cycle, that cycle's cards, and #31's dashboard for member
 * identities and whether this user is the facilitator. Facilitator is never
 * inferred from `created_by`.
 *
 * Events refine that snapshot but never replace the load. While a reload is in
 * flight, arriving events are buffered and applied on top afterwards — the
 * other order would let a snapshot taken before an event overwrite it.
 */
export function RetroBoardPage() {
  const { retroId = "" } = useParams();
  const { user } = useAuth();
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [status, setStatus] = useState<ConnectionStatus>("connecting");

  const loading = useRef(false);
  const buffered = useRef<RetroEvent[]>([]);

  const load = useCallback(async (): Promise<void> => {
    loading.current = true;
    buffered.current = [];
    try {
      const retro = await getRetro(retroId);
      const cycle = await getCycle(retro.cycle_id);
      const [cards, dashboard] = await Promise.all([
        listFeedback(retro.cycle_id),
        getDashboard(cycle.project_id),
      ]);

      const me = dashboard.members.find((member) => member.user_id === user?.id);
      let results: VoteResults | null = null;
      if (retro.phase === "vote" || retro.phase === "discuss" || retro.phase === DONE) {
        try {
          results = await getResults(retroId);
        } catch {
          // 409 means "hidden until voting closes", which is a state, not a
          // failure. Anything else here is not worth failing the whole board.
          results = null;
        }
      }

      setState({
        kind: "ready",
        retro,
        cards,
        members: dashboard.members,
        isFacilitator: me?.role === FACILITATOR,
        results,
      });
    } catch (error) {
      const code = isAxiosError(error) ? error.response?.status : undefined;
      setState(
        code === 403
          ? { kind: "denied" }
          : code === 404
            ? { kind: "missing" }
            : { kind: "failed" }
      );
    } finally {
      loading.current = false;
      const queued = buffered.current;
      buffered.current = [];
      if (queued.length > 0) {
        setState((current) =>
          current.kind === "ready"
            ? { ...current, ...mergeAll({ retro: current.retro, cards: current.cards }, queued) }
            : current
        );
      }
    }
  }, [retroId, user?.id]);

  // A route change starts over: no data from the previous retro may survive it.
  useEffect(() => {
    setState({ kind: "loading" });
    void load();
  }, [load]);

  useEffect(() => {
    const socket = new RetroSocket(retroId, {
      onStatus: setStatus,
      onConnected: (isReconnect) => {
        if (isReconnect) {
          // Full authoritative reload before saying Connected. Missed events are
          // never requested — `_docs/decisions.md`, no replay on reconnect.
          void load().then(() => setStatus("connected"));
        } else {
          setStatus("connected");
        }
      },
      onEvent: (event) => {
        if (loading.current) {
          buffered.current.push(event);
          return;
        }
        if (event.event === "phase_changed") {
          void load();
          return;
        }
        if (event.event === "voting_closed") {
          setState((current) =>
            current.kind === "ready"
              ? { ...current, results: event.data as unknown as VoteResults }
              : current
          );
          return;
        }
        setState((current) =>
          current.kind === "ready"
            ? { ...current, ...applyEvent({ retro: current.retro, cards: current.cards }, event) }
            : current
        );
      },
    });
    socket.start();
    return () => socket.stop();
  }, [retroId, load]);

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
        <button type="button" onClick={() => void load()} className="underline">
          Retry
        </button>
      </div>
    );
  }

  const { retro, cards, members, isFacilitator, results } = state;
  const hasVoted = retro.votes.some((vote) => vote.user_id === user?.id);

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-bold text-gray-900">Retrospective</h1>
        <PhaseIndicator phase={retro.phase} />
        <p role="status">Connection: {STATUS_LABELS[status]}</p>
        {status !== "connected" && (
          <p role="status">
            Live updates are paused. Your own changes are still saved by the server.
          </p>
        )}
        {isFacilitator && retro.phase !== DONE && (
          <AdvanceControl retro={retro} onChanged={load} />
        )}
      </header>

      {retro.phase === DONE ? (
        <div>
          <p>This retrospective is complete.</p>
          <Link to={`/retros/${retro.id}/summary`}>Read the summary</Link>
        </div>
      ) : retro.phase === "reveal" ? (
        <RevealPanel cards={cards} members={members} />
      ) : retro.phase === "cluster" ? (
        <ClusterPanel
          retroId={retro.id}
          clusters={retro.clusters}
          cards={cards}
          isFacilitator={isFacilitator}
          onChanged={load}
          onConflict={load}
        />
      ) : retro.phase === "vote" ? (
        <VotePanel
          retroId={retro.id}
          clusters={retro.clusters}
          hasVoted={hasVoted}
          results={results}
          onSubmitted={load}
          onConflict={load}
        />
      ) : (
        <DiscussPanel
          retro={retro}
          members={members}
          currentUserId={user?.id}
          isFacilitator={isFacilitator}
          onChanged={load}
          onConflict={load}
        />
      )}
    </div>
  );
}

function mergeAll(board: BoardData, events: RetroEvent[]): BoardData {
  return events.reduce(applyEvent, board);
}

function Unavailable({ message }: { message: string }) {
  return (
    <div role="alert" className="space-y-2">
      <p>{message}</p>
      <Link to="/projects">Back to projects</Link>
    </div>
  );
}

/** An indicator, not tabs. Phases move forward only, and only the facilitator moves them (#6). */
function PhaseIndicator({ phase }: { phase: string }) {
  return (
    <ol className="flex flex-wrap gap-3">
      {PHASES.map((step) => (
        <li key={step} aria-current={step === phase ? "step" : undefined}>
          {step === phase ? `${label(step)} (current)` : label(step)}
        </li>
      ))}
    </ol>
  );
}

function label(phase: string): string {
  return phase.charAt(0).toUpperCase() + phase.slice(1);
}

function AdvanceControl({ retro, onChanged }: { retro: Retro; onChanged: () => Promise<void> }) {
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const next = nextPhase(retro.phase);
  // `done` is publishing, and publishing is #11/#17's. This control stops at
  // `discuss` rather than quietly closing the retrospective.
  if (next === null || next === DONE) {
    return null;
  }

  return (
    <div>
      <button
        type="button"
        disabled={pending}
        onClick={() => setConfirming(true)}
        className="rounded bg-gray-900 px-3 py-2 text-white disabled:opacity-50"
      >
        {pending ? "Moving on…" : `Move to ${label(next)}`}
      </button>
      {error && <p role="alert">{error}</p>}

      {confirming && (
        <div role="dialog" aria-modal="true" aria-label={`Move to ${label(next)}`}>
          <p>Move this retrospective to {label(next)}? There is no way back.</p>
          <button
            type="button"
            className="underline"
            onClick={() => {
              setConfirming(false);
              setPending(true);
              setError(null);
              advancePhase(retro.id, next as Phase)
                .then(() => onChanged())
                .catch(async (failure) => {
                  setError(describe(failure));
                  const code = isAxiosError(failure) ? failure.response?.status : undefined;
                  if (code === 400 || code === 403) {
                    await onChanged();
                  }
                })
                .finally(() => setPending(false));
            }}
          >
            Yes, move to {label(next)}
          </button>
          <button type="button" className="ml-3 underline" onClick={() => setConfirming(false)}>
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}
