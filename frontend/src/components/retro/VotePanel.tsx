import { useState } from "react";

import { MAX_VOTES, submitVotes, type Cluster, type VoteResults } from "../../api/retro";
import { describe } from "./ClusterPanel";

/**
 * One ballot, spent once (#16).
 *
 * `_docs/decisions.md`: submission is atomic and there is no retraction, so
 * there is no revote control here and none is planned before #21. Stacked votes
 * are repeated cluster ids in one array — the running total is what the member
 * sees, never anybody else's choices.
 *
 * Results appear only when the server says they may: a `409` is "hidden", not
 * an error to fix.
 */
export function VotePanel({
  retroId,
  clusters,
  hasVoted,
  results,
  onSubmitted,
  onConflict,
}: {
  retroId: string;
  clusters: Cluster[];
  hasVoted: boolean;
  results: VoteResults | null;
  onSubmitted: () => Promise<void>;
  onConflict: () => Promise<void>;
}) {
  const [ballot, setBallot] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const spent = ballot.length;
  const locked = hasVoted;

  function add(clusterId: string) {
    if (spent >= MAX_VOTES) {
      return;
    }
    setBallot([...ballot, clusterId]);
  }

  function remove(clusterId: string) {
    const index = ballot.lastIndexOf(clusterId);
    if (index === -1) {
      return;
    }
    setBallot([...ballot.slice(0, index), ...ballot.slice(index + 1)]);
  }

  async function submit() {
    if (ballot.length === 0 || pending) {
      return;
    }
    setPending(true);
    setError(null);
    try {
      await submitVotes(retroId, ballot);
      setBallot([]);
      await onSubmitted();
    } catch (failure) {
      const status = (failure as { response?: { status?: number } }).response?.status;
      setError(describe(failure));
      if (status === 409 || status === 400 || status === 404) {
        await onConflict();
      }
    } finally {
      setPending(false);
    }
  }

  if (clusters.length === 0) {
    return (
      <p>
        There are no clusters to vote on. The facilitator can still move the
        retrospective on.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      {error && <p role="alert">{error}</p>}

      {locked ? (
        <p role="status">Vote submitted</p>
      ) : (
        <>
          <p role="status">
            {spent} of {MAX_VOTES} votes used
          </p>
          <ul className="grid gap-3 md:grid-cols-3">
            {clusters.map((cluster) => {
              const count = ballot.filter((id) => id === cluster.id).length;
              return (
                <li key={cluster.id} className="rounded border p-3">
                  <p className="font-semibold break-words">{cluster.name}</p>
                  <p>{count} of your votes</p>
                  <button
                    type="button"
                    disabled={spent >= MAX_VOTES || pending}
                    onClick={() => add(cluster.id)}
                    className="underline disabled:opacity-50"
                  >
                    Vote for {cluster.name}
                  </button>
                  <button
                    type="button"
                    disabled={count === 0 || pending}
                    onClick={() => remove(cluster.id)}
                    className="ml-3 underline disabled:opacity-50"
                  >
                    Remove a vote from {cluster.name}
                  </button>
                </li>
              );
            })}
          </ul>

          <button
            type="button"
            disabled={ballot.length === 0 || pending}
            onClick={() => void submit()}
            className="rounded bg-gray-900 px-4 py-2 text-white disabled:opacity-50"
          >
            {pending ? "Submitting…" : "Submit ballot"}
          </button>
        </>
      )}

      {results === null ? (
        <p>Results are hidden until voting closes.</p>
      ) : (
        <div>
          <h3 className="font-semibold">Results</h3>
          <p>
            {results.members_voted} of {results.members_total} members voted ·{" "}
            {results.total_votes} votes cast
          </p>
          <ol>
            {results.results.map((row) => (
              <li key={row.cluster_id}>
                {row.rank}. {row.name} — {row.vote_count}
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
