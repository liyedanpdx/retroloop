/**
 * The socket's own rules, and the event merge, without rendering anything (#16).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAccessToken } from "../api/client";
import type { FeedbackCard } from "../api/feedback";
import type { Retro } from "../api/retro";
import { applyEvent, type BoardData } from "./applyEvent";
import { BACKOFF_MS, RetroSocket, retroSocketUrl } from "./retroSocket";
import { FakeWebSocket, installFakeWebSocket, lastSocket } from "../test-utils/socket";
import { installHttp } from "../test-utils/http";

function handlers() {
  const events: unknown[] = [];
  const statuses: string[] = [];
  const connects: boolean[] = [];
  return {
    events,
    statuses,
    connects,
    handlers: {
      onEvent: (event: unknown) => events.push(event),
      onStatus: (status: string) => statuses.push(status),
      onConnected: (isReconnect: boolean) => connects.push(isReconnect),
    },
  };
}

beforeEach(() => {
  installFakeWebSocket();
  setAccessToken("token-1");
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("the retro socket", () => {
  it("connects to #12's URL with the current access token", () => {
    const { handlers: sink, connects } = handlers();
    const socket = new RetroSocket("r1", sink);
    socket.start();

    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(lastSocket().url).toContain("/ws/retro/r1?token=token-1");
    expect(lastSocket().url.startsWith("ws://")).toBe(true);

    lastSocket().open();
    expect(connects).toEqual([false]);
    socket.stop();
  });

  it("hands every well-formed event to its caller and ignores the rest", () => {
    const { handlers: sink, events } = handlers();
    const socket = new RetroSocket("r1", sink);
    socket.start();
    lastSocket().open();

    lastSocket().emit("cluster_created", { id: "c1", name: "Flow" });
    lastSocket().emitRaw("not json at all");
    lastSocket().emitRaw(JSON.stringify({ nonsense: true }));

    expect(events).toEqual([
      { event: "cluster_created", data: { id: "c1", name: "Flow" } },
    ]);
    socket.stop();
  });

  it("backs off 1s, 2s, 4s, 8s, 10s while it cannot get back in", () => {
    const { handlers: sink, connects } = handlers();
    const socket = new RetroSocket("r1", sink);
    socket.start();
    lastSocket().open();

    // Consecutive failures with no successful connection in between: the delay
    // grows to the ceiling and stays there.
    for (const [index, delay] of BACKOFF_MS.entries()) {
      lastSocket().drop();
      expect(FakeWebSocket.instances).toHaveLength(index + 1);
      vi.advanceTimersByTime(delay - 1);
      expect(FakeWebSocket.instances).toHaveLength(index + 1);
      vi.advanceTimersByTime(1);
      expect(FakeWebSocket.instances).toHaveLength(index + 2);
    }

    lastSocket().drop();
    vi.advanceTimersByTime(9999);
    expect(FakeWebSocket.instances).toHaveLength(BACKOFF_MS.length + 1);
    vi.advanceTimersByTime(1);
    expect(FakeWebSocket.instances).toHaveLength(BACKOFF_MS.length + 2);

    expect(connects).toEqual([false]);
    socket.stop();
  });

  it("starts the backoff over after a connection that worked", () => {
    const { handlers: sink } = handlers();
    const socket = new RetroSocket("r1", sink);
    socket.start();
    lastSocket().open();

    lastSocket().drop();
    vi.advanceTimersByTime(1000);
    lastSocket().open();

    lastSocket().drop();
    vi.advanceTimersByTime(999);
    expect(FakeWebSocket.instances).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(FakeWebSocket.instances).toHaveLength(3);
    socket.stop();
  });

  it("treats 1008 as the token, refreshes once, and then stops", async () => {
    installHttp({ "POST /api/auth/refresh": { status: 200, data: { access_token: "second" } } });
    const { handlers: sink, statuses } = handlers();
    const socket = new RetroSocket("r1", sink);
    socket.start();
    lastSocket().open();

    lastSocket().drop(1008);
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(2));
    expect(lastSocket().url).toContain("token=second");

    // A second 1008 is not another refresh — that would be a loop.
    lastSocket().drop(1008);
    await Promise.resolve();
    vi.advanceTimersByTime(60000);
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(statuses.at(-1)).toBe("offline");
    socket.stop();
  });

  it("stops for good when told to, cancelling any pending retry", () => {
    const { handlers: sink } = handlers();
    const socket = new RetroSocket("r1", sink);
    socket.start();
    lastSocket().open();
    lastSocket().drop();

    socket.stop();
    vi.advanceTimersByTime(60000);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("uses wss where the page is https", () => {
    const url = retroSocketUrl("r1", "abc");
    expect(url.startsWith(window.location.protocol === "https:" ? "wss:" : "ws:")).toBe(true);
  });
});

// --- the merge ---------------------------------------------------------------

const RETRO = {
  id: "r1",
  cycle_id: "cy1",
  phase: "cluster",
  clusters: [{ id: "c1", name: "Flow", created_at: "2026-02-01T00:00:00Z" }],
  votes: [],
  topics: [
    {
      id: "t1",
      cluster_id: "c1",
      name: "Flow",
      vote_count: 2,
      rank: 1,
      status: "pending",
      notes: "",
    },
  ],
  decisions: [],
  actions: [],
  voting_results_opened_at: null,
  transcript: null,
  ai_suggestions: null,
  created_at: "2026-02-01T00:00:00Z",
} as Retro;

const CARD = {
  id: "f1",
  cycle_id: "cy1",
  author_id: "u1",
  category: "start",
  text: "pair more often",
  is_anonymous: false,
  cluster_id: "c1",
  created_at: "2026-02-02T00:00:00Z",
} as FeedbackCard;

const BOARD: BoardData = { retro: RETRO, cards: [CARD] };

describe("applying an event", () => {
  it("adds a cluster once, however many times the event arrives", () => {
    const event = { event: "cluster_created", data: { id: "c2", name: "Tooling" } };
    const once = applyEvent(BOARD, event);
    const twice = applyEvent(once, event);

    expect(once.retro.clusters.map((cluster) => cluster.id)).toEqual(["c1", "c2"]);
    expect(twice.retro.clusters).toEqual(once.retro.clusters);
  });

  it("replaces a renamed cluster by id", () => {
    const merged = applyEvent(BOARD, {
      event: "cluster_renamed",
      data: { id: "c1", name: "Flow and focus", created_at: "2026-02-01T00:00:00Z" },
    });
    expect(merged.retro.clusters).toHaveLength(1);
    expect(merged.retro.clusters[0].name).toBe("Flow and focus");
  });

  it("removes a deleted cluster and ungroups its cards", () => {
    const merged = applyEvent(BOARD, {
      event: "cluster_deleted",
      data: { id: "c1", card_ids: ["f1"] },
    });
    expect(merged.retro.clusters).toEqual([]);
    expect(merged.cards[0].cluster_id).toBeNull();
  });

  it("moves a card, including out of a cluster", () => {
    const into = applyEvent(BOARD, {
      event: "card_moved",
      data: { card_id: "f1", cluster_id: "c2" },
    });
    expect(into.cards[0].cluster_id).toBe("c2");

    const out = applyEvent(into, {
      event: "card_moved",
      data: { card_id: "f1", cluster_id: null },
    });
    expect(out.cards[0].cluster_id).toBeNull();
  });

  it("replaces an updated topic", () => {
    const merged = applyEvent(BOARD, {
      event: "topic_updated",
      data: { ...RETRO.topics[0], status: "discussed", notes: "shipped it" },
    });
    expect(merged.retro.topics).toHaveLength(1);
    expect(merged.retro.topics[0].status).toBe("discussed");
  });

  it("ignores what it does not handle rather than breaking the board", () => {
    for (const event of [
      { event: "phase_changed", data: { phase: "vote" } },
      { event: "voting_closed", data: {} },
      { event: "invented_by_nobody", data: { id: "x" } },
      { event: "cluster_created", data: {} },
      { event: "card_moved", data: {} },
    ]) {
      expect(applyEvent(BOARD, event)).toEqual(BOARD);
    }
  });
});
