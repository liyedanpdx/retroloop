import { useState, type FormEvent } from "react";
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  TouchSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";

import type { FeedbackCard } from "../../api/feedback";
import {
  createCluster,
  deleteCluster,
  moveCard,
  renameCluster,
  sortCards,
  suggestClusters,
  type Cluster,
  type SuggestedGrouping,
} from "../../api/retro";

export const UNCLUSTERED = "Unclustered";

/**
 * Grouping the revealed cards (#16).
 *
 * Two ways to move a card, both required. dnd-kit provides pointer, touch and
 * keyboard dragging; every card also carries a labelled "Move … to" select,
 * because a drag is not an operation everyone can perform and the select is
 * what makes the board usable with a screen reader.
 *
 * Nothing is optimistic beyond the drag preview. A move that fails puts the
 * card back where it was and says so; the server's returned card is what the
 * board believes.
 */
export function ClusterPanel({
  retroId,
  clusters,
  cards,
  isFacilitator,
  onChanged,
  onConflict,
}: {
  retroId: string;
  clusters: Cluster[];
  cards: FeedbackCard[];
  isFacilitator: boolean;
  onChanged: () => Promise<void>;
  onConflict: () => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [movingId, setMovingId] = useState<string | null>(null);
  const [moveError, setMoveError] = useState<string | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(TouchSensor),
    useSensor(KeyboardSensor)
  );

  const known = new Set(clusters.map((cluster) => cluster.id));
  // A card pointing at a cluster that is gone belongs in Unclustered. Dropping
  // it, or trusting the stale id, would lose somebody's feedback.
  const inCluster = (clusterId: string | null) =>
    sortCards(
      cards.filter((card) =>
        clusterId === null
          ? card.cluster_id === null || !known.has(card.cluster_id)
          : card.cluster_id === clusterId
      )
    );

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    setCreateError(null);
    if (!trimmed) {
      setCreateError("Cluster name is required");
      return;
    }
    setCreating(true);
    try {
      await createCluster(retroId, trimmed);
      setName("");
      await onChanged();
    } catch (error) {
      setCreateError(describe(error));
      if (conflicting(error)) {
        await onConflict();
      }
    } finally {
      setCreating(false);
    }
  }

  async function move(cardId: string, clusterId: string | null) {
    const card = cards.find((row) => row.id === cardId);
    if (!card || card.cluster_id === clusterId || movingId !== null) {
      return; // Dropping a card where it already is is not a request.
    }
    setMovingId(cardId);
    setMoveError(null);
    try {
      await moveCard(cardId, clusterId);
      await onChanged();
    } catch (error) {
      setMoveError(describe(error));
      if (conflicting(error)) {
        await onConflict();
      }
    } finally {
      setMovingId(null);
    }
  }

  function onDragEnd(event: DragEndEvent) {
    const cardId = String(event.active.id);
    const over = event.over === null ? null : String(event.over.id);
    void move(cardId, over === UNCLUSTERED || over === null ? null : over);
  }

  return (
    <div className="space-y-4">
      <form onSubmit={onCreate} noValidate className="flex flex-wrap items-end gap-2">
        <div>
          <label htmlFor="cluster-name">New cluster name</label>
          <input
            id="cluster-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="rounded border px-3 py-2"
          />
        </div>
        <button
          type="submit"
          disabled={creating}
          className="rounded bg-gray-900 px-3 py-2 text-white disabled:opacity-50"
        >
          {creating ? "Adding cluster…" : "Add cluster"}
        </button>
        {isFacilitator && <SuggestButton retroId={retroId} />}
      </form>

      {createError && <p role="alert">{createError}</p>}
      {moveError && <p role="alert">{moveError}</p>}

      <p id="dnd-instructions">
        Drag a card onto a cluster, or use its “Move to” control. With the
        keyboard, focus a card, press Space to pick it up, arrow keys to choose a
        destination, and Space again to drop it.
      </p>

      <DndContext sensors={sensors} onDragEnd={onDragEnd}>
        <div className="grid gap-6 md:grid-cols-3">
          <Bucket
            id={UNCLUSTERED}
            title={UNCLUSTERED}
            cards={inCluster(null)}
            clusters={clusters}
            currentId={null}
            movingId={movingId}
            onMove={move}
          />
          {clusters.map((cluster) => (
            <Bucket
              key={cluster.id}
              id={cluster.id}
              title={cluster.name}
              cards={inCluster(cluster.id)}
              clusters={clusters}
              currentId={cluster.id}
              movingId={movingId}
              onMove={move}
              controls={
                <ClusterControls
                  retroId={retroId}
                  cluster={cluster}
                  onChanged={onChanged}
                  onConflict={onConflict}
                />
              }
            />
          ))}
        </div>
      </DndContext>
    </div>
  );
}

function Bucket({
  id,
  title,
  cards,
  clusters,
  currentId,
  movingId,
  onMove,
  controls,
}: {
  id: string;
  title: string;
  cards: FeedbackCard[];
  clusters: Cluster[];
  currentId: string | null;
  movingId: string | null;
  onMove: (cardId: string, clusterId: string | null) => Promise<void>;
  controls?: React.ReactNode;
}) {
  const { setNodeRef } = useDroppable({ id });
  return (
    <section ref={setNodeRef} className="space-y-2 rounded border p-3">
      <h3 className="font-semibold">{title}</h3>
      {controls}
      {cards.length === 0 ? (
        <p>No cards in {title}</p>
      ) : (
        <ul className="space-y-2">
          {cards.map((card) => (
            <CardTile
              key={card.id}
              card={card}
              clusters={clusters}
              currentId={currentId}
              pending={movingId === card.id}
              onMove={onMove}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function CardTile({
  card,
  clusters,
  currentId,
  pending,
  onMove,
}: {
  card: FeedbackCard;
  clusters: Cluster[];
  currentId: string | null;
  pending: boolean;
  onMove: (cardId: string, clusterId: string | null) => Promise<void>;
}) {
  const { attributes, listeners, setNodeRef } = useDraggable({ id: card.id, disabled: pending });
  return (
    <li ref={setNodeRef} className="rounded border p-2">
      <p
        {...attributes}
        {...listeners}
        aria-describedby="dnd-instructions"
        className="break-words"
      >
        {card.text}
      </p>
      <label htmlFor={`move-${card.id}`}>Move this card to</label>
      <select
        id={`move-${card.id}`}
        value={currentId ?? UNCLUSTERED}
        disabled={pending}
        onChange={(event) =>
          void onMove(
            card.id,
            event.target.value === UNCLUSTERED ? null : event.target.value
          )
        }
        className="w-full rounded border px-2 py-1"
      >
        <option value={UNCLUSTERED}>{UNCLUSTERED}</option>
        {clusters.map((cluster) => (
          <option key={cluster.id} value={cluster.id}>
            {cluster.name}
          </option>
        ))}
      </select>
    </li>
  );
}

function ClusterControls({
  retroId,
  cluster,
  onChanged,
  onConflict,
}: {
  retroId: string;
  cluster: Cluster;
  onChanged: () => Promise<void>;
  onConflict: () => Promise<void>;
}) {
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(cluster.name);
  const [confirming, setConfirming] = useState(false);
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
      setError(describe(failure));
      if (conflicting(failure) || missing(failure)) {
        await onConflict();
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-1">
      {renaming ? (
        <div>
          <label htmlFor={`rename-${cluster.id}`}>Rename {cluster.name}</label>
          <input
            id={`rename-${cluster.id}`}
            value={draft}
            disabled={pending}
            onChange={(event) => setDraft(event.target.value)}
            className="w-full rounded border px-2 py-1"
          />
          <button
            type="button"
            disabled={pending}
            className="underline"
            onClick={() => {
              const trimmed = draft.trim();
              if (!trimmed) {
                setError("Cluster name is required");
                return;
              }
              if (trimmed === cluster.name) {
                setRenaming(false);
                return;
              }
              void run(async () => {
                await renameCluster(retroId, cluster.id, trimmed);
                setRenaming(false);
                await onChanged();
              });
            }}
          >
            Save name
          </button>
        </div>
      ) : (
        <div className="flex gap-3">
          <button
            type="button"
            disabled={pending}
            onClick={() => {
              setDraft(cluster.name);
              setRenaming(true);
            }}
            className="underline disabled:opacity-50"
          >
            Rename {cluster.name}
          </button>
          <button
            type="button"
            disabled={pending}
            onClick={() => setConfirming(true)}
            className="underline disabled:opacity-50"
          >
            Delete {cluster.name}
          </button>
        </div>
      )}

      {error && <p role="alert">{error}</p>}

      {confirming && (
        <div role="dialog" aria-modal="true" aria-label={`Delete ${cluster.name}`}>
          <p>Delete {cluster.name}? Its cards become unclustered.</p>
          <button
            type="button"
            className="underline"
            onClick={() => {
              setConfirming(false);
              void run(async () => {
                await deleteCluster(retroId, cluster.id);
                await onChanged();
              });
            }}
          >
            Yes, delete {cluster.name}
          </button>
          <button type="button" onClick={() => setConfirming(false)} className="ml-3 underline">
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * #19's proposal, shown for review and never applied (#16).
 *
 * No button here creates a cluster or moves a card. Applying a suggestion is the
 * facilitator doing it with the controls above, which is the whole point of
 * "Suggestions are always drafts".
 */
function SuggestButton({ retroId }: { retroId: string }) {
  const [grouping, setGrouping] = useState<SuggestedGrouping | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  return (
    <div>
      <button
        type="button"
        disabled={pending}
        className="rounded border px-3 py-2 disabled:opacity-50"
        onClick={() => {
          setPending(true);
          setError(null);
          suggestClusters(retroId)
            .then(setGrouping)
            .catch((failure) => setError(describe(failure)))
            .finally(() => setPending(false));
        }}
      >
        {pending ? "Asking…" : "Suggest clusters"}
      </button>

      {error && (
        <p role="alert">
          {error}{" "}
          <span>You can ask again.</span>
        </p>
      )}

      {grouping && (
        <div role="status">
          <p>Suggested grouping — nothing has been created.</p>
          <ul>
            {grouping.clusters.map((suggested) => (
              <li key={suggested.name}>
                {suggested.name}: {suggested.card_ids.length} cards
              </li>
            ))}
            <li>Ungrouped: {grouping.ungrouped_card_ids.length} cards</li>
          </ul>
        </div>
      )}
    </div>
  );
}

// --- failures ----------------------------------------------------------------

function statusOf(error: unknown): number | undefined {
  const response = (error as { response?: { status?: number } }).response;
  return response?.status;
}

function conflicting(error: unknown): boolean {
  const status = statusOf(error);
  return status === 400 || status === 403;
}

function missing(error: unknown): boolean {
  return statusOf(error) === 404;
}

export function describe(error: unknown): string {
  const status = statusOf(error);
  if (status === 400) {
    return "The retrospective moved on. Your change was not saved.";
  }
  if (status === 403) {
    return "You are no longer allowed to do that.";
  }
  if (status === 404) {
    return "That is no longer there.";
  }
  if (status === 409) {
    return "You have already voted";
  }
  if (status === 422) {
    return "That value is not valid.";
  }
  if (status === 502 || status === 504) {
    return "The AI suggestion is unavailable right now.";
  }
  return "Something went wrong. Please try again.";
}
