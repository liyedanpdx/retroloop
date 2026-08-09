import { useState, type FormEvent } from "react";

import type { DashboardMember } from "../../api/projects";
import {
  TOPIC_STATUSES,
  createAction,
  createDecision,
  deleteAction,
  deleteDecision,
  updateAction,
  updateDecision,
  updateTopic,
  type Action,
  type Decision,
  type Retro,
  type Topic,
} from "../../api/retro";
import { describe } from "./ClusterPanel";

export const UNLINKED = "Unlinked";
export const UNASSIGNED = "Unassigned";

/**
 * The agenda, and what came out of it (#16).
 *
 * Topics are generated from the tally (#9) and only their status and notes are
 * editable here; creating or reordering one is #22's. Decisions and actions
 * whose `topic_id` is null — or points at a topic that is not there — go under
 * Unlinked rather than being dropped, because a decision nobody can see is
 * worse than one filed oddly.
 *
 * Permissions mirror `_docs/decisions.md` exactly: the facilitator edits
 * everything; an action's own owner may change only its status and due date;
 * everybody else reads.
 */
export function DiscussPanel({
  retro,
  members,
  currentUserId,
  isFacilitator,
  onChanged,
  onConflict,
}: {
  retro: Retro;
  members: DashboardMember[];
  currentUserId: string | undefined;
  isFacilitator: boolean;
  onChanged: () => Promise<void>;
  onConflict: () => Promise<void>;
}) {
  const topics = [...retro.topics].sort((left, right) => left.rank - right.rank);
  const known = new Set(topics.map((topic) => topic.id));
  const linked = (topicId: string | null) => topicId !== null && known.has(topicId);

  const groups: { key: string; title: string; topic: Topic | null }[] = [
    ...topics.map((topic) => ({
      key: topic.id,
      title: `${topic.name} — ${topic.vote_count} votes`,
      topic,
    })),
    { key: UNLINKED, title: UNLINKED, topic: null },
  ];

  const unlinkedDecisions = retro.decisions.filter((row) => !linked(row.topic_id));
  const unlinkedActions = retro.actions.filter((row) => !linked(row.topic_id));

  return (
    <div className="space-y-6">
      {topics.length === 0 && <p className="empty">No topics were generated — nothing was voted on.</p>}

      {groups.map(({ key, title, topic }) => {
        const decisions =
          topic === null
            ? unlinkedDecisions
            : retro.decisions.filter((row) => row.topic_id === topic.id);
        const actions =
          topic === null
            ? unlinkedActions
            : retro.actions.filter((row) => row.topic_id === topic.id);
        if (topic === null && decisions.length === 0 && actions.length === 0) {
          return null;
        }
        return (
          <section key={key} className="panel space-y-2">
            <h3 className="break-words">{title}</h3>
            {topic && (
              <TopicControls
                retroId={retro.id}
                topic={topic}
                isFacilitator={isFacilitator}
                onChanged={onChanged}
                onConflict={onConflict}
              />
            )}

            <h4 className="font-semibold">Decisions</h4>
            {decisions.length === 0 ? (
              <p className="empty">No decisions</p>
            ) : (
              <ul className="space-y-1">
                {decisions.map((decision) => (
                  <DecisionRow
                    key={decision.id}
                    retroId={retro.id}
                    decision={decision}
                    isFacilitator={isFacilitator}
                    onChanged={onChanged}
                    onConflict={onConflict}
                  />
                ))}
              </ul>
            )}

            <h4 className="font-semibold">Actions</h4>
            {actions.length === 0 ? (
              <p className="empty">No actions</p>
            ) : (
              <ul className="space-y-1">
                {actions.map((action) => (
                  <ActionRow
                    key={action.id}
                    retroId={retro.id}
                    action={action}
                    members={members}
                    currentUserId={currentUserId}
                    isFacilitator={isFacilitator}
                    onChanged={onChanged}
                    onConflict={onConflict}
                  />
                ))}
              </ul>
            )}

            {isFacilitator && (
              <Composers
                retroId={retro.id}
                topicId={topic === null ? null : topic.id}
                topicLabel={topic === null ? UNLINKED : topic.name}
                members={members}
                onChanged={onChanged}
                onConflict={onConflict}
              />
            )}
          </section>
        );
      })}
    </div>
  );
}

function useMutation(onConflict: () => Promise<void>) {
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function run(action: () => Promise<void>) {
    if (pending) {
      return;
    }
    setPending(true);
    setError(null);
    try {
      await action();
    } catch (failure) {
      const status = (failure as { response?: { status?: number } }).response?.status;
      setError(describe(failure));
      if (status === 400 || status === 403 || status === 404) {
        await onConflict();
      }
    } finally {
      setPending(false);
    }
  }

  return { error, pending, run, setError };
}

function TopicControls({
  retroId,
  topic,
  isFacilitator,
  onChanged,
  onConflict,
}: {
  retroId: string;
  topic: Topic;
  isFacilitator: boolean;
  onChanged: () => Promise<void>;
  onConflict: () => Promise<void>;
}) {
  const [notes, setNotes] = useState(topic.notes);
  const { error, pending, run } = useMutation(onConflict);

  if (!isFacilitator) {
    return (
      <div>
        <p>Status: {topic.status}</p>
        {topic.notes && <p className="break-words">{topic.notes}</p>}
      </div>
    );
  }

  return (
    <div className="space-y-1">
      <label htmlFor={`status-${topic.id}`}>Status of {topic.name}</label>
      <select
        id={`status-${topic.id}`}
        value={topic.status}
        disabled={pending}
        onChange={(event) =>
          void run(async () => {
            await updateTopic(retroId, topic.id, { status: event.target.value });
            await onChanged();
          })
        }
       
      >
        {TOPIC_STATUSES.map((status) => (
          <option key={status} value={status}>
            {status}
          </option>
        ))}
      </select>

      <label htmlFor={`notes-${topic.id}`}>Notes on {topic.name}</label>
      <textarea
        id={`notes-${topic.id}`}
        value={notes}
        disabled={pending}
        onChange={(event) => setNotes(event.target.value)}
       
      />
      <button
        type="button"
        disabled={pending}
        className="btn-link"
        onClick={() => {
          // An empty string is a legal note. Only an unchanged value is a no-op.
          if (notes === topic.notes) {
            return;
          }
          void run(async () => {
            await updateTopic(retroId, topic.id, { notes });
            await onChanged();
          });
        }}
      >
        Save notes on {topic.name}
      </button>

      {error && <p role="alert">{error}</p>}
    </div>
  );
}

function DecisionRow({
  retroId,
  decision,
  isFacilitator,
  onChanged,
  onConflict,
}: {
  retroId: string;
  decision: Decision;
  isFacilitator: boolean;
  onChanged: () => Promise<void>;
  onConflict: () => Promise<void>;
}) {
  const [confirming, setConfirming] = useState(false);
  const { error, pending, run } = useMutation(onConflict);

  return (
    <li className="card">
      <p className="break-words">{decision.text}</p>
      <p>{decision.is_confirmed ? "Confirmed" : "Draft"}</p>
      {error && <p role="alert">{error}</p>}

      {isFacilitator && (
        <div className="flex flex-wrap gap-3">
          <button
            type="button"
            disabled={pending}
            className="btn-link"
            onClick={() =>
              void run(async () => {
                await updateDecision(retroId, decision.id, {
                  is_confirmed: !decision.is_confirmed,
                });
                await onChanged();
              })
            }
          >
            {decision.is_confirmed ? "Unconfirm this decision" : "Confirm this decision"}
          </button>
          <button
            type="button"
            disabled={pending}
            className="btn-link"
            onClick={() => setConfirming(true)}
          >
            Delete this decision
          </button>
        </div>
      )}

      {confirming && (
        <div role="dialog" aria-modal="true" aria-label="Delete decision">
          <p>Delete “{decision.text}”?</p>
          <button
            type="button"
            className="btn-link"
            onClick={() => {
              setConfirming(false);
              void run(async () => {
                await deleteDecision(retroId, decision.id);
                await onChanged();
              });
            }}
          >
            Yes, delete this decision
          </button>
          <button type="button" className="btn-link ml-3" onClick={() => setConfirming(false)}>
            Cancel
          </button>
        </div>
      )}
    </li>
  );
}

function ActionRow({
  retroId,
  action,
  members,
  currentUserId,
  isFacilitator,
  onChanged,
  onConflict,
}: {
  retroId: string;
  action: Action;
  members: DashboardMember[];
  currentUserId: string | undefined;
  isFacilitator: boolean;
  onChanged: () => Promise<void>;
  onConflict: () => Promise<void>;
}) {
  const [confirming, setConfirming] = useState(false);
  const { error, pending, run } = useMutation(onConflict);
  const isOwner = action.owner_id !== null && action.owner_id === currentUserId;
  const ownerName =
    members.find((member) => member.user_id === action.owner_id)?.display_name ??
    action.owner_name ??
    UNASSIGNED;

  return (
    <li className="card">
      <p className="break-words">{action.description}</p>
      <p>Owner: {ownerName}</p>
      {error && <p role="alert">{error}</p>}

      {(isFacilitator || isOwner) && (
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label htmlFor={`action-status-${action.id}`}>Status of this action</label>
            <select
              id={`action-status-${action.id}`}
              value={action.status}
              disabled={pending}
              onChange={(event) =>
                void run(async () => {
                  await updateAction(retroId, action.id, { status: event.target.value });
                  await onChanged();
                })
              }
            >
              <option value="open">open</option>
              <option value="done">done</option>
            </select>
          </div>

          <div>
            <label htmlFor={`action-due-${action.id}`}>Due date for this action</label>
            <input
              id={`action-due-${action.id}`}
              type="date"
              defaultValue={action.due_date ? action.due_date.slice(0, 10) : ""}
              disabled={pending}
              onChange={(event) =>
                void run(async () => {
                  const value = event.target.value;
                  // The API wants an ISO datetime or null, never "".
                  await updateAction(retroId, action.id, {
                    due_date: value ? `${value}T00:00:00Z` : null,
                  });
                  await onChanged();
                })
              }
            />
          </div>
        </div>
      )}

      {isFacilitator && (
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label htmlFor={`action-owner-${action.id}`}>Owner of this action</label>
            <select
              id={`action-owner-${action.id}`}
              value={action.owner_id ?? ""}
              disabled={pending}
              onChange={(event) =>
                void run(async () => {
                  await updateAction(retroId, action.id, {
                    owner_id: event.target.value || null,
                  });
                  await onChanged();
                })
              }
            >
              <option value="">{UNASSIGNED}</option>
              {members.map((member) => (
                <option key={member.user_id} value={member.user_id}>
                  {member.display_name}
                </option>
              ))}
            </select>
          </div>
          <button
            type="button"
            disabled={pending}
            className="btn-link"
            onClick={() => setConfirming(true)}
          >
            Delete this action
          </button>
        </div>
      )}

      {confirming && (
        <div role="dialog" aria-modal="true" aria-label="Delete action">
          <p>Delete “{action.description}”?</p>
          <button
            type="button"
            className="btn-link"
            onClick={() => {
              setConfirming(false);
              void run(async () => {
                await deleteAction(retroId, action.id);
                await onChanged();
              });
            }}
          >
            Yes, delete this action
          </button>
          <button type="button" className="btn-link ml-3" onClick={() => setConfirming(false)}>
            Cancel
          </button>
        </div>
      )}
    </li>
  );
}

function Composers({
  retroId,
  topicId,
  topicLabel,
  members,
  onChanged,
  onConflict,
}: {
  retroId: string;
  topicId: string | null;
  topicLabel: string;
  members: DashboardMember[];
  onChanged: () => Promise<void>;
  onConflict: () => Promise<void>;
}) {
  const suffix = topicId ?? UNLINKED;
  const [decisionText, setDecisionText] = useState("");
  const [description, setDescription] = useState("");
  const [ownerId, setOwnerId] = useState("");
  const { error, pending, run, setError } = useMutation(onConflict);

  function onDecision(event: FormEvent) {
    event.preventDefault();
    const trimmed = decisionText.trim();
    if (!trimmed) {
      setError("Decision text is required");
      return;
    }
    void run(async () => {
      await createDecision(retroId, { topic_id: topicId, text: trimmed });
      setDecisionText("");
      await onChanged();
    });
  }

  function onAction(event: FormEvent) {
    event.preventDefault();
    const trimmed = description.trim();
    if (!trimmed) {
      setError("Action description is required");
      return;
    }
    void run(async () => {
      await createAction(retroId, {
        topic_id: topicId,
        description: trimmed,
        owner_id: ownerId || null,
      });
      setDescription("");
      setOwnerId("");
      await onChanged();
    });
  }

  return (
    <div className="space-y-2">
      {error && <p role="alert">{error}</p>}

      <form onSubmit={onDecision} noValidate className="flex flex-wrap items-end gap-2">
        <div>
          <label htmlFor={`new-decision-${suffix}`}>New decision for {topicLabel}</label>
          <input
            id={`new-decision-${suffix}`}
            value={decisionText}
            onChange={(event) => setDecisionText(event.target.value)}
          />
        </div>
        <button type="submit" disabled={pending} className="btn-link">
          Add decision to {topicLabel}
        </button>
      </form>

      <form onSubmit={onAction} noValidate className="flex flex-wrap items-end gap-2">
        <div>
          <label htmlFor={`new-action-${suffix}`}>New action for {topicLabel}</label>
          <input
            id={`new-action-${suffix}`}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </div>
        <div>
          <label htmlFor={`new-action-owner-${suffix}`}>New action owner for {topicLabel}</label>
          <select
            id={`new-action-owner-${suffix}`}
            value={ownerId}
            onChange={(event) => setOwnerId(event.target.value)}
          >
            <option value="">{UNASSIGNED}</option>
            {members.map((member) => (
              <option key={member.user_id} value={member.user_id}>
                {member.display_name}
              </option>
            ))}
          </select>
        </div>
        <button type="submit" disabled={pending} className="btn-link">
          Add action to {topicLabel}
        </button>
      </form>
    </div>
  );
}
