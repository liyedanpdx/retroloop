import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { isAxiosError } from "axios";

import {
  ANONYMOUS_WARNING,
  CATEGORIES,
  CATEGORY_LABELS,
  COLLECTING,
  activeCycle,
  createCard,
  deleteCard,
  listCycles,
  listFeedback,
  makeCardAnonymous,
  ownCards,
  updateCardText,
  type Category,
  type Cycle,
  type FeedbackCard,
} from "../api/feedback";
import { useAuth } from "../auth/AuthProvider";
import { formatDate } from "../lib/dates";

const GENERIC_FAILURE = "Something went wrong. Please try again.";
const FROZEN_MESSAGE =
  "Feedback collection is closed. Cards are frozen once the retrospective starts.";

type PageState =
  | { kind: "loading" }
  | { kind: "denied" }
  | { kind: "missing" }
  | { kind: "failed" }
  | { kind: "no-cycle" }
  | { kind: "ready"; cycle: Cycle; cards: FeedbackCard[] };

export function FeedbackPage() {
  const { projectId = "" } = useParams();
  const { user } = useAuth();
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [announcement, setAnnouncement] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const cycle = activeCycle(await listCycles(projectId));
      if (cycle === null) {
        setState({ kind: "no-cycle" });
        return;
      }
      setState({ kind: "ready", cycle, cards: await listFeedback(cycle.id) });
    } catch (error) {
      const status = isAxiosError(error) ? error.response?.status : undefined;
      if (status === 403) {
        setState({ kind: "denied" });
      } else if (status === 404) {
        setState({ kind: "missing" });
      } else {
        setState({ kind: "failed" });
      }
    }
  }, [projectId]);

  useEffect(() => {
    setState({ kind: "loading" });
    void load();
  }, [load]);

  if (state.kind === "loading") {
    return <p role="status">Loading this cycle…</p>;
  }
  if (state.kind === "denied") {
    return <Unavailable message="You do not have access to this project." />;
  }
  if (state.kind === "missing") {
    return <Unavailable message="Project not found." />;
  }
  if (state.kind === "no-cycle") {
    return <Unavailable message="No active feedback cycle" />;
  }
  if (state.kind === "failed") {
    return (
      <div role="alert" className="space-y-2">
        <p>We could not load your feedback.</p>
        <button type="button" onClick={() => void load()} className="underline">
          Retry
        </button>
      </div>
    );
  }

  const { cycle, cards } = state;
  const collecting = cycle.status === COLLECTING;
  const mine = ownCards(cards, user?.id);

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-bold text-gray-900">Your feedback</h1>
        <p>
          <Link to={`/projects/${projectId}`}>Back to the project</Link>
        </p>
        {!collecting && <p role="status">{FROZEN_MESSAGE}</p>}
        {announcement && (
          <p role="status" className="text-green-800">
            {announcement}
          </p>
        )}
      </header>

      <div className="grid gap-6 md:grid-cols-3">
        {CATEGORIES.map((category) => (
          <CategoryColumn
            key={category}
            category={category}
            cycleId={cycle.id}
            collecting={collecting}
            cards={mine.filter((card) => card.category === category)}
            onReload={load}
            onAnnounce={setAnnouncement}
          />
        ))}
      </div>
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

/**
 * One category, with its own composer, its own draft and its own errors.
 *
 * State is per column on purpose: a failed Stop submission must not clear the
 * Start draft somebody is halfway through typing.
 */
function CategoryColumn({
  category,
  cycleId,
  collecting,
  cards,
  onReload,
  onAnnounce,
}: {
  category: Category;
  cycleId: string;
  collecting: boolean;
  cards: FeedbackCard[];
  onReload: () => Promise<void>;
  onAnnounce: (message: string) => void;
}) {
  const label = CATEGORY_LABELS[category];
  const [text, setText] = useState("");
  const [anonymous, setAnonymous] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(isAnonymous: boolean) {
    const trimmed = text.trim();
    setError(null);
    if (!trimmed) {
      setError("Feedback text is required");
      return;
    }

    setPending(true);
    try {
      await createCard(cycleId, category, trimmed, isAnonymous);
      setText("");
      setAnonymous(false);
      if (isAnonymous) {
        // Not added to the list, and nothing kept client-side that could be
        // used to work out who wrote it. The refetch below simply will not
        // return it, which is the whole point of `author_id: null`.
        onAnnounce(`Your anonymous ${label} card was submitted.`);
      }
      await onReload();
    } catch (failure) {
      setError(messageFor(failure));
      if (isTransition(failure)) {
        await onReload();
      }
    } finally {
      setPending(false);
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (anonymous) {
      if (!text.trim()) {
        setError("Feedback text is required");
        return;
      }
      setConfirming(true);
      return;
    }
    void submit(false);
  }

  return (
    <section className="space-y-3">
      <h2 className="text-xl font-semibold">{label}</h2>

      {collecting && (
        <form onSubmit={onSubmit} noValidate className="space-y-2 rounded border p-3">
          <label htmlFor={`${category}-text`}>{label} feedback</label>
          <textarea
            id={`${category}-text`}
            value={text}
            onChange={(event) => setText(event.target.value)}
            disabled={pending}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? `${category}-error` : undefined}
            className="w-full rounded border px-3 py-2"
          />

          <div>
            <input
              id={`${category}-anonymous`}
              type="checkbox"
              checked={anonymous}
              disabled={pending}
              onChange={(event) => setAnonymous(event.target.checked)}
            />
            <label htmlFor={`${category}-anonymous`}> Submit this {label} card anonymously</label>
          </div>

          {anonymous && <p className="text-amber-800">{ANONYMOUS_WARNING}</p>}

          {error && (
            <p id={`${category}-error`} role="alert" className="text-red-700">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={pending}
            className="rounded bg-gray-900 px-3 py-2 text-white disabled:opacity-50"
          >
            {pending ? `Adding ${label} card…` : `Add ${label} card`}
          </button>
        </form>
      )}

      {confirming && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`Confirm anonymous ${label} card`}
          className="rounded border p-3"
        >
          <p>{ANONYMOUS_WARNING}</p>
          <div className="flex gap-3">
            <button
              type="button"
              onClick={() => {
                setConfirming(false);
                void submit(true);
              }}
              className="underline"
            >
              Yes, submit anonymously
            </button>
            <button type="button" onClick={() => setConfirming(false)} className="underline">
              Cancel
            </button>
          </div>
        </div>
      )}

      {cards.length === 0 ? (
        <p>No {label} cards yet</p>
      ) : (
        <ul className="space-y-2">
          {cards.map((card) => (
            <CardRow
              key={card.id}
              card={card}
              label={label}
              collecting={collecting}
              onReload={onReload}
              onAnnounce={onAnnounce}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function CardRow({
  card,
  label,
  collecting,
  onReload,
  onAnnounce,
}: {
  card: FeedbackCard;
  label: string;
  collecting: boolean;
  onReload: () => Promise<void>;
  onAnnounce: (message: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(card.text);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [confirming, setConfirming] = useState<"anonymous" | "delete" | null>(null);

  async function run(action: () => Promise<void>) {
    if (pending) {
      return;
    }
    setPending(true);
    setError(null);
    try {
      await action();
    } catch (failure) {
      setError(messageFor(failure));
      if (isTransition(failure) || isMissing(failure)) {
        setEditing(false);
        await onReload();
      }
    } finally {
      setPending(false);
    }
  }

  async function save() {
    const trimmed = draft.trim();
    if (!trimmed) {
      setError("Feedback text is required");
      return;
    }
    if (trimmed === card.text) {
      // Nothing changed, so there is nothing to send. A PATCH here would be a
      // write every time a card lost focus.
      setEditing(false);
      return;
    }
    await run(async () => {
      await updateCardText(card.id, trimmed);
      setEditing(false);
      await onReload();
    });
  }

  return (
    <li className="rounded border p-3">
      {editing ? (
        <div className="space-y-2">
          <label htmlFor={`edit-${card.id}`}>Edit {label} card</label>
          <textarea
            id={`edit-${card.id}`}
            value={draft}
            disabled={pending}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                setDraft(card.text);
                setEditing(false);
                setError(null);
              }
            }}
            onBlur={() => void save()}
            className="w-full rounded border px-3 py-2"
          />
          <button type="button" onClick={() => void save()} disabled={pending} className="underline">
            Save
          </button>
        </div>
      ) : (
        <p className="break-words">{card.text}</p>
      )}

      <p>Added {formatDate(card.created_at, "date unknown")}</p>

      {error && (
        <p role="alert" className="text-red-700">
          {error}
        </p>
      )}

      {collecting && !editing && (
        <div className="flex flex-wrap gap-3">
          <button
            type="button"
            disabled={pending}
            onClick={() => {
              setDraft(card.text);
              setEditing(true);
            }}
            className="underline disabled:opacity-50"
          >
            Edit {label} card
          </button>
          <button
            type="button"
            disabled={pending}
            onClick={() => setConfirming("anonymous")}
            className="underline disabled:opacity-50"
          >
            Make anonymous
          </button>
          <button
            type="button"
            disabled={pending}
            onClick={() => setConfirming("delete")}
            className="underline disabled:opacity-50"
          >
            Delete {label} card
          </button>
        </div>
      )}

      {confirming === "anonymous" && (
        <div role="dialog" aria-modal="true" aria-label="Confirm anonymous" className="mt-2">
          <p>{ANONYMOUS_WARNING}</p>
          <button
            type="button"
            className="underline"
            onClick={() => {
              setConfirming(null);
              void run(async () => {
                await makeCardAnonymous(card.id);
                onAnnounce("Your card is now anonymous and has left your private list.");
                await onReload();
              });
            }}
          >
            Yes, make it anonymous
          </button>
          <button type="button" onClick={() => setConfirming(null)} className="ml-3 underline">
            Cancel
          </button>
        </div>
      )}

      {confirming === "delete" && (
        <div role="dialog" aria-modal="true" aria-label="Confirm delete" className="mt-2">
          <p>Delete this {label} card?</p>
          <button
            type="button"
            className="underline"
            onClick={() => {
              setConfirming(null);
              void run(async () => {
                await deleteCard(card.id);
                await onReload();
              });
            }}
          >
            Yes, delete it
          </button>
          <button type="button" onClick={() => setConfirming(null)} className="ml-3 underline">
            Cancel
          </button>
        </div>
      )}
    </li>
  );
}

function statusOf(error: unknown): number | undefined {
  return isAxiosError(error) ? error.response?.status : undefined;
}

/** The cycle moved on under us — reload and show the frozen page. */
function isTransition(error: unknown): boolean {
  const status = statusOf(error);
  return status === 400 || status === 403;
}

function isMissing(error: unknown): boolean {
  return statusOf(error) === 404;
}

function messageFor(error: unknown): string {
  const status = statusOf(error);
  if (status === 400) {
    return "Feedback collection has closed. Your change was not saved.";
  }
  if (status === 403) {
    return "You can no longer change this card.";
  }
  if (status === 404) {
    return "That card no longer exists.";
  }
  if (status === 422) {
    return "Feedback text is required";
  }
  return GENERIC_FAILURE;
}
