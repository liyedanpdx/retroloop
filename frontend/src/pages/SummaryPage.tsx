import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { isAxiosError } from "axios";

import { getCycle } from "../api/cycles";
import { CATEGORIES, CATEGORY_LABELS } from "../api/feedback";
import { FACILITATOR, getDashboard, type DashboardMember } from "../api/projects";
import { getRetro, type Retro } from "../api/retro";
import { getSummary, publishSummary, type Summary } from "../api/transcript";
import { useAuth } from "../auth/AuthProvider";
import { formatDate } from "../lib/dates";

const PUBLISH_WARNING =
  "Publishing closes this retrospective and its cycle. Every member will be able " +
  "to read the summary, and there is no way to unpublish it.";

type PageState =
  | { kind: "loading" }
  | { kind: "denied" }
  | { kind: "missing" }
  | { kind: "unpublished" }
  | { kind: "failed" }
  | {
      kind: "ready";
      retro: Retro;
      members: DashboardMember[];
      isFacilitator: boolean;
      summary: Summary;
    };

/**
 * The assembled summary, previewed and then published once (#17).
 *
 * Nothing is cached and nothing is assembled here: #11 builds it from the
 * current documents on every read, so a fresh `GET` after a confirm or a
 * publish is the only way to be sure the page is showing what the team will.
 */
export function SummaryPage() {
  const { retroId = "" } = useParams();
  const { user } = useAuth();
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [publishError, setPublishError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);

  const load = useCallback(async () => {
    try {
      const retro = await getRetro(retroId);
      const cycle = await getCycle(retro.cycle_id);
      const dashboard = await getDashboard(cycle.project_id);
      const me = dashboard.members.find((member) => member.user_id === user?.id);

      let summary: Summary;
      try {
        summary = await getSummary(retroId);
      } catch (error) {
        // A member during `discuss` is told the summary is not published yet —
        // and told nothing about what is in it.
        if (isAxiosError(error) && error.response?.status === 403) {
          setState({ kind: "unpublished" });
          return;
        }
        throw error;
      }

      setState({
        kind: "ready",
        retro,
        members: dashboard.members,
        isFacilitator: me?.role === FACILITATOR,
        summary,
      });
    } catch (error) {
      const code = isAxiosError(error) ? error.response?.status : undefined;
      setState(
        code === 403 ? { kind: "denied" } : code === 404 ? { kind: "missing" } : { kind: "failed" }
      );
    }
  }, [retroId, user?.id]);

  useEffect(() => {
    setState({ kind: "loading" });
    void load();
  }, [load]);

  if (state.kind === "loading") {
    return <p role="status">Loading the summary…</p>;
  }
  if (state.kind === "denied") {
    return <Unavailable message="You do not have access to this retrospective." />;
  }
  if (state.kind === "missing") {
    return <Unavailable message="Retrospective not found." />;
  }
  if (state.kind === "unpublished") {
    return (
      <div role="status" className="space-y-2">
        <p>This summary has not been published yet.</p>
        <Link to="/projects">Back to projects</Link>
      </div>
    );
  }
  if (state.kind === "failed") {
    return (
      <div role="alert" className="space-y-2">
        <p>We could not load the summary.</p>
        <button type="button" onClick={() => void load()} className="btn-link">
          Retry
        </button>
      </div>
    );
  }

  const { retro, members, isFacilitator, summary } = state;
  const published = retro.phase === "done";
  const nameOf = (id: string | null) =>
    members.find((member) => member.user_id === id)?.display_name ?? "Former member";

  async function publish() {
    setPending(true);
    setPublishError(null);
    try {
      await publishSummary(retroId);
      await load();
    } catch (error) {
      const code = isAxiosError(error) ? error.response?.status : undefined;
      if (code === 400) {
        setPublishError("This retrospective could not be published in its current state.");
        await load();
      } else if (code === 403) {
        setPublishError("Only the facilitator can publish this retrospective.");
        await load();
      } else {
        setPublishError("We could not publish it. Nothing has changed — you can try again.");
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <h1>Retrospective summary</h1>
        <p role="status">{published ? "Published" : "Preview"}</p>
        <p>
          <Link to={`/retros/${retro.id}`}>Back to the board</Link>
        </p>
      </header>

      {isFacilitator && !published && (
        <div>
          <button
            type="button"
            disabled={pending}
            onClick={() => setConfirming(true)}
            className="btn btn-primary"
          >
            {pending ? "Publishing…" : "Publish"}
          </button>
          {publishError && <p role="alert">{publishError}</p>}
        </div>
      )}

      {confirming && (
        <div role="dialog" aria-modal="true" aria-label="Publish this retrospective">
          <p>{PUBLISH_WARNING}</p>
          <button
            type="button"
            className="btn-link"
            onClick={() => {
              setConfirming(false);
              void publish();
            }}
          >
            Yes, publish it
          </button>
          <button type="button" className="btn-link ml-3" onClick={() => setConfirming(false)}>
            Cancel
          </button>
        </div>
      )}

      <section className="space-y-2">
        <h2>Topics</h2>
        {summary.topics.length === 0 ? (
          <p className="empty">No topics</p>
        ) : (
          <ol className="space-y-1">
            {summary.topics.map((topic) => (
              <li key={topic.id}>
                {topic.name} — {topic.vote_count} votes · {topic.status}
                {topic.notes && <span className="break-words"> · {topic.notes}</span>}
              </li>
            ))}
          </ol>
        )}
      </section>

      <section className="space-y-2">
        <h2>Decisions</h2>
        {summary.decisions.length === 0 ? (
          <p className="empty">No decisions</p>
        ) : (
          <ul className="space-y-1">
            {summary.decisions.map((decision) => (
              <li key={decision.id} className="break-words">
                {decision.text} ({decision.topic ?? "Unlinked"})
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-2">
        <h2>Actions</h2>
        {summary.actions.length === 0 ? (
          <p className="empty">No actions</p>
        ) : (
          <ul className="space-y-1">
            {summary.actions.map((action) => (
              <li key={action.id} className="break-words">
                {action.description} ({action.topic ?? "Unlinked"}) · {action.owner ?? "Unassigned"}{" "}
                · {formatDate(action.due_date, "No due date")} · {action.status}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-1">
        <h2>Participation</h2>
        {/* Counts only. Naming who submitted or voted is exactly what #5 and #8
            were built to prevent. */}
        <p>{summary.participation.total_members} members</p>
        <p>{summary.participation.submitted_feedback} submitted feedback</p>
        <p>{summary.participation.voted} voted</p>
      </section>

      <section className="space-y-2">
        <h2>Feedback</h2>
        <div className="grid gap-4 md:grid-cols-3">
          {CATEGORIES.map((category) => {
            const cards = summary.feedback_cards.filter((card) => card.category === category);
            const label = CATEGORY_LABELS[category];
            return (
              <div key={category}>
                <h3 className="font-semibold">{label}</h3>
                {cards.length === 0 ? (
                  <p className="empty">No {label} cards</p>
                ) : (
                  <ul className="space-y-1">
                    {cards.map((card) => (
                      <li key={card.id} className="card">
                        <p className="break-words">{card.text}</p>
                        <p className="meta">
                          {card.is_anonymous || card.author_id === null
                            ? "Anonymous"
                            : nameOf(card.author_id)}
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      </section>
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
