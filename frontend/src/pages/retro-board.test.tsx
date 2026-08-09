/**
 * The retro board, phase by phase (#16).
 *
 * Rendered through `AppRoutes` with a live session and the fake WebSocket, so
 * the chained load, the guards, the auth client and the socket lifecycle are
 * all the real ones.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AppRoutes } from "../App";
import { setAccessToken } from "../api/client";
import { ALICE, callsTo, installHttp, type Call, type Handler, type Reply } from "../test-utils/http";
import { FakeWebSocket, installFakeWebSocket, lastSocket } from "../test-utils/socket";

const BOB_ID = "u2";

const SESSION: Record<string, Handler | Reply> = {
  "POST /api/auth/refresh": { status: 200, data: { access_token: "fresh" } },
  "GET /api/auth/me": { status: 200, data: ALICE },
};

const CYCLE = {
  id: "cy1",
  project_id: "p1",
  status: "retro",
  created_at: "2026-02-01T00:00:00Z",
  closed_at: null,
  created_by: ALICE.id,
};

const MEMBERS = [
  {
    user_id: ALICE.id,
    display_name: "Alice",
    email: "alice@example.com",
    role: "facilitator",
    joined_at: "2026-01-01T00:00:00Z",
  },
  {
    user_id: BOB_ID,
    display_name: "Bob",
    email: "bob@example.com",
    role: "member",
    joined_at: "2026-01-02T00:00:00Z",
  },
];

function dashboard(members = MEMBERS) {
  return { members, current_cycle: null, past_retros: [], open_actions: [] };
}

function retro(overrides: Record<string, unknown> = {}) {
  return {
    id: "r1",
    cycle_id: "cy1",
    phase: "reveal",
    clusters: [],
    votes: [],
    topics: [],
    decisions: [],
    actions: [],
    voting_results_opened_at: null,
    transcript: null,
    ai_suggestions: null,
    created_at: "2026-02-01T00:00:00Z",
    ...overrides,
  };
}

function card(overrides: Record<string, unknown> = {}) {
  return {
    id: "f1",
    cycle_id: "cy1",
    author_id: ALICE.id,
    category: "start",
    text: "pair more often",
    is_anonymous: false,
    cluster_id: null,
    created_at: "2026-02-02T00:00:00Z",
    ...overrides,
  };
}

const CLUSTER = { id: "c1", name: "Flow", created_at: "2026-02-03T00:00:00Z" };

function board(routes: Record<string, Handler | Reply> = {}): Record<string, Handler | Reply> {
  return {
    ...SESSION,
    "GET /api/retros/r1": { status: 200, data: retro() },
    "GET /api/cycles/cy1": { status: 200, data: CYCLE },
    "GET /api/cycles/cy1/feedback": { status: 200, data: [] },
    "GET /api/projects/p1/dashboard": { status: 200, data: dashboard() },
    ...routes,
  };
}

function renderBoard(routes: Record<string, Handler | Reply>, path = "/retros/r1"): Call[] {
  const calls = installHttp(routes);
  render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>
  );
  return calls;
}

async function ready() {
  await screen.findByRole("heading", { name: "Retrospective" });
}

beforeEach(() => {
  setAccessToken(null);
  installFakeWebSocket();
});

afterEach(() => {
  FakeWebSocket.instances = [];
});

// --- loading -----------------------------------------------------------------

describe("loading the board", () => {
  it("chains retro, cycle, cards and members, and takes the role from #31", async () => {
    const calls = renderBoard(board());
    expect(await screen.findByText("Loading this retrospective…")).toBeInTheDocument();
    await ready();

    expect(callsTo(calls, "GET", "/api/retros/r1")).toHaveLength(1);
    expect(callsTo(calls, "GET", "/api/cycles/cy1")).toHaveLength(1);
    expect(callsTo(calls, "GET", "/api/cycles/cy1/feedback")).toHaveLength(1);
    expect(callsTo(calls, "GET", "/api/projects/p1/dashboard")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Move to Cluster" })).toBeInTheDocument();
  });

  it("does not make a facilitator out of the cycle's creator", async () => {
    // Alice created the cycle but is only a member on the project.
    renderBoard(
      board({
        "GET /api/projects/p1/dashboard": {
          status: 200,
          data: dashboard([{ ...MEMBERS[0], role: "member" }, MEMBERS[1]]),
        },
      })
    );
    await ready();

    expect(screen.queryByRole("button", { name: /^Move to/ })).not.toBeInTheDocument();
  });

  it("shows access denied, not found, and a retryable failure", async () => {
    renderBoard(board({ "GET /api/retros/r1": { status: 403 } }));
    expect(await screen.findByText(/do not have access/)).toBeInTheDocument();

    setAccessToken(null);
    renderBoard(board({ "GET /api/retros/r1": { status: 404 } }));
    expect(await screen.findByText("Retrospective not found.")).toBeInTheDocument();

    setAccessToken(null);
    let broken = true;
    const calls = renderBoard(
      board({ "GET /api/retros/r1": () => (broken ? { status: 500 } : { status: 200, data: retro() }) })
    );
    expect(await screen.findByText(/could not load this retrospective/)).toBeInTheDocument();
    broken = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await ready();
    expect(callsTo(calls, "GET", "/api/retros/r1")).toHaveLength(2);
  });
});

// --- the phase indicator ------------------------------------------------------

describe("the phase indicator", () => {
  it("marks exactly one current phase and is not navigation", async () => {
    renderBoard(board({ "GET /api/retros/r1": { status: 200, data: retro({ phase: "vote" }) } }));
    await ready();

    const steps = screen.getAllByRole("listitem").slice(0, 4);
    expect(steps.map((step) => step.textContent)).toEqual([
      "Reveal",
      "Cluster",
      "Vote (current)",
      "Discuss",
    ]);
    expect(steps.filter((step) => step.getAttribute("aria-current") === "step")).toHaveLength(1);
    for (const step of steps) {
      expect(within(step).queryByRole("link")).toBeNull();
      expect(within(step).queryByRole("button")).toBeNull();
    }
  });

  it("advances one legal step, after confirmation, and never to done", async () => {
    const calls = renderBoard(
      board({
        "GET /api/retros/r1": { status: 200, data: retro({ phase: "cluster" }) },
        "PATCH /api/retros/r1/phase": { status: 200, data: retro({ phase: "vote" }) },
      })
    );
    await ready();

    fireEvent.click(screen.getByRole("button", { name: "Move to Vote" }));
    fireEvent.click(await screen.findByRole("button", { name: "Yes, move to Vote" }));

    await waitFor(() => expect(callsTo(calls, "PATCH", "/api/retros/r1/phase")).toHaveLength(1));
    expect(callsTo(calls, "PATCH", "/api/retros/r1/phase")[0].body).toEqual({ phase: "vote" });
  });

  it("offers nothing beyond discuss — publishing is #17's", async () => {
    renderBoard(board({ "GET /api/retros/r1": { status: 200, data: retro({ phase: "discuss" }) } }));
    await ready();
    expect(screen.queryByRole("button", { name: /^Move to/ })).not.toBeInTheDocument();
  });

  it("shows a completed retro with its summary link instead of a board", async () => {
    renderBoard(
      board({
        "GET /api/retros/r1": { status: 200, data: retro({ phase: "done" }) },
        "GET /api/retros/r1/votes/results": { status: 409 },
      })
    );
    await ready();

    expect(screen.getByText("This retrospective is complete.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Read the summary" })).toHaveAttribute(
      "href",
      "/retros/r1/summary"
    );
  });

  it("refetches and explains when the phase moved under the facilitator", async () => {
    const calls = renderBoard(
      board({
        "GET /api/retros/r1": { status: 200, data: retro({ phase: "cluster" }) },
        "PATCH /api/retros/r1/phase": { status: 400 },
      })
    );
    await ready();

    fireEvent.click(screen.getByRole("button", { name: "Move to Vote" }));
    fireEvent.click(await screen.findByRole("button", { name: "Yes, move to Vote" }));

    expect(await screen.findByText(/moved on/)).toBeInTheDocument();
    await waitFor(() => expect(callsTo(calls, "GET", "/api/retros/r1").length).toBeGreaterThan(1));
  });
});

// --- reveal -------------------------------------------------------------------

describe("reveal", () => {
  it("groups and orders the cards, names authors, and hides anonymous ones", async () => {
    renderBoard(
      board({
        "GET /api/cycles/cy1/feedback": {
          status: 200,
          data: [
            card({ id: "b", text: "second", created_at: "2026-02-03T00:00:00Z" }),
            card({ id: "a", text: "first", created_at: "2026-02-01T00:00:00Z" }),
            card({ id: "anon", text: "in confidence", author_id: null, is_anonymous: true, created_at: "2026-02-09T00:00:00Z" }),
            card({ id: "gone", text: "from a leaver", author_id: "u404", category: "stop" }),
          ],
        },
      })
    );
    await ready();

    const start = screen.getByRole("heading", { name: "Start" }).closest("section")!;
    const texts = within(start).getAllByRole("listitem").map((item) => item.textContent);
    expect(texts[0]).toContain("first");
    expect(texts[1]).toContain("second");
    expect(texts.join(" ")).toContain("Alice");

    const anonymous = screen.getByText("in confidence").closest("li")!;
    expect(within(anonymous).getByText("Anonymous")).toBeInTheDocument();
    expect(anonymous.textContent).not.toContain("Alice");

    expect(screen.getByText("from a leaver").closest("li")!.textContent).toContain(
      "Former member"
    );
    expect(screen.getByText("No Continue cards")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Delete/ })).not.toBeInTheDocument();
  });
});

// --- cluster ------------------------------------------------------------------

describe("cluster", () => {
  const clustering = (routes: Record<string, Handler | Reply> = {}) =>
    board({
      "GET /api/retros/r1": {
        status: 200,
        data: retro({ phase: "cluster", clusters: [CLUSTER] }),
      },
      "GET /api/cycles/cy1/feedback": { status: 200, data: [card()] },
      ...routes,
    });

  it("puts unclustered cards, and cards pointing at a cluster that is gone, in Unclustered", async () => {
    renderBoard(
      clustering({
        "GET /api/cycles/cy1/feedback": {
          status: 200,
          data: [card(), card({ id: "stale", text: "orphan", cluster_id: "deleted-cluster" })],
        },
      })
    );
    await ready();

    const unclustered = screen.getByRole("heading", { name: "Unclustered" }).closest("section")!;
    expect(within(unclustered).getByText("pair more often")).toBeInTheDocument();
    expect(within(unclustered).getByText("orphan")).toBeInTheDocument();
  });

  it("lets any member create, rename and delete a cluster", async () => {
    const calls = renderBoard(
      clustering({
        "GET /api/projects/p1/dashboard": {
          status: 200,
          data: dashboard([{ ...MEMBERS[0], role: "member" }, MEMBERS[1]]),
        },
        "POST /api/retros/r1/clusters": { status: 201, data: { ...CLUSTER, id: "c2" } },
        "PATCH /api/retros/r1/clusters/c1": { status: 200, data: { ...CLUSTER, name: "Focus" } },
        "DELETE /api/retros/r1/clusters/c1": { status: 200, data: [] },
      })
    );
    await ready();

    fireEvent.click(screen.getByRole("button", { name: "Add cluster" }));
    expect(await screen.findByText("Cluster name is required")).toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/retros/r1/clusters")).toHaveLength(0);

    fireEvent.change(screen.getByLabelText("New cluster name"), { target: { value: "  Tooling  " } });
    fireEvent.click(screen.getByRole("button", { name: "Add cluster" }));
    await waitFor(() => expect(callsTo(calls, "POST", "/api/retros/r1/clusters")).toHaveLength(1));
    expect(callsTo(calls, "POST", "/api/retros/r1/clusters")[0].body).toEqual({ name: "Tooling" });

    fireEvent.click(screen.getByRole("button", { name: "Rename Flow" }));
    fireEvent.change(await screen.findByLabelText("Rename Flow"), { target: { value: "Focus" } });
    fireEvent.click(screen.getByRole("button", { name: "Save name" }));
    await waitFor(() =>
      expect(callsTo(calls, "PATCH", "/api/retros/r1/clusters/c1")).toHaveLength(1)
    );

    fireEvent.click(screen.getByRole("button", { name: "Delete Flow" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete Flow" });
    expect(within(dialog).getByText(/cards become unclustered/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(calls.filter((call) => call.method === "DELETE")).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Delete Flow" }));
    fireEvent.click(await screen.findByRole("button", { name: "Yes, delete Flow" }));
    await waitFor(() =>
      expect(callsTo(calls, "DELETE", "/api/retros/r1/clusters/c1")).toHaveLength(1)
    );
  });

  it("moves a card, ignores a move to where it already is, and rolls back a failure", async () => {
    let stored = card();
    let broken = false;
    const calls = renderBoard(
      clustering({
        "GET /api/cycles/cy1/feedback": () => ({ status: 200, data: [stored] }),
        "PATCH /api/feedback/f1/cluster": (call) => {
          if (broken) {
            return { status: 500 };
          }
          stored = card({ cluster_id: (call.body as { cluster_id: string | null }).cluster_id });
          return { status: 200, data: stored };
        },
      })
    );
    await ready();

    fireEvent.change(screen.getByLabelText("Move this card to"), { target: { value: "c1" } });
    await waitFor(() =>
      expect(callsTo(calls, "PATCH", "/api/feedback/f1/cluster")).toHaveLength(1)
    );
    expect(callsTo(calls, "PATCH", "/api/feedback/f1/cluster")[0].body).toEqual({
      cluster_id: "c1",
    });
    await waitFor(() =>
      expect(
        within(screen.getByRole("heading", { name: "Flow" }).closest("section")!).getByText(
          "pair more often"
        )
      ).toBeInTheDocument()
    );

    // Same destination: no request.
    fireEvent.change(screen.getByLabelText("Move this card to"), { target: { value: "c1" } });
    expect(callsTo(calls, "PATCH", "/api/feedback/f1/cluster")).toHaveLength(1);

    broken = true;
    fireEvent.change(screen.getByLabelText("Move this card to"), {
      target: { value: "Unclustered" },
    });
    expect(await screen.findByText(/Something went wrong/)).toBeInTheDocument();
    expect(
      within(screen.getByRole("heading", { name: "Flow" }).closest("section")!).getByText(
        "pair more often"
      )
    ).toBeInTheDocument();
  });

  it("refetches the board when a move hits a phase or card conflict", async () => {
    const calls = renderBoard(
      clustering({ "PATCH /api/feedback/f1/cluster": { status: 400 } })
    );
    await ready();
    const before = callsTo(calls, "GET", "/api/retros/r1").length;

    fireEvent.change(screen.getByLabelText("Move this card to"), { target: { value: "c1" } });

    expect(await screen.findByText(/moved on/)).toBeInTheDocument();
    await waitFor(() =>
      expect(callsTo(calls, "GET", "/api/retros/r1").length).toBeGreaterThan(before)
    );
  });

  it("offers keyboard and pointer dragging, with instructions", async () => {
    renderBoard(clustering());
    await ready();

    expect(screen.getByText(/press Space to pick it up/)).toBeInTheDocument();
    const draggable = screen.getByText("pair more often");
    expect(draggable).toHaveAttribute("aria-describedby", "dnd-instructions");
    expect(draggable).toHaveAttribute("role", "button");
    expect(draggable).toHaveAttribute("tabindex", "0");
  });

  it("shows the AI suggestion to a facilitator only, and applies nothing", async () => {
    const calls = renderBoard(
      clustering({
        "POST /api/retros/r1/clusters/suggest": {
          status: 200,
          data: {
            clusters: [{ name: "Meetings", card_ids: ["f1"] }],
            ungrouped_card_ids: [],
          },
        },
      })
    );
    await ready();

    fireEvent.click(screen.getByRole("button", { name: "Suggest clusters" }));

    expect(await screen.findByText(/nothing has been created/)).toBeInTheDocument();
    expect(screen.getByText(/Meetings: 1 cards/)).toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/retros/r1/clusters/suggest")[0].body).toEqual({});
    // Not a single cluster was created and not a single card was moved.
    expect(callsTo(calls, "POST", "/api/retros/r1/clusters")).toHaveLength(0);
    expect(calls.filter((call) => call.url.includes("/cluster") && call.method === "PATCH")).toHaveLength(0);
  });

  it("hides Suggest clusters from a plain member", async () => {
    renderBoard(
      clustering({
        "GET /api/projects/p1/dashboard": {
          status: 200,
          data: dashboard([{ ...MEMBERS[0], role: "member" }, MEMBERS[1]]),
        },
      })
    );
    await ready();
    expect(screen.queryByRole("button", { name: "Suggest clusters" })).not.toBeInTheDocument();
  });
});

// --- vote ---------------------------------------------------------------------

describe("vote", () => {
  const voting = (routes: Record<string, Handler | Reply> = {}) =>
    board({
      "GET /api/retros/r1": {
        status: 200,
        data: retro({ phase: "vote", clusters: [CLUSTER, { ...CLUSTER, id: "c2", name: "Tooling" }] }),
      },
      "GET /api/retros/r1/votes/results": { status: 409 },
      ...routes,
    });

  it("stacks up to three votes and submits them atomically", async () => {
    const calls = renderBoard(
      voting({ "POST /api/retros/r1/votes": { status: 201, data: {} } })
    );
    await ready();

    fireEvent.click(screen.getByRole("button", { name: "Vote for Flow" }));
    fireEvent.click(screen.getByRole("button", { name: "Vote for Flow" }));
    fireEvent.click(screen.getByRole("button", { name: "Vote for Tooling" }));
    expect(screen.getByText("3 of 3 votes used")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Vote for Flow" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Remove a vote from Flow" }));
    expect(screen.getByText("2 of 3 votes used")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Vote for Flow" }));

    fireEvent.click(screen.getByRole("button", { name: "Submit ballot" }));
    await waitFor(() => expect(callsTo(calls, "POST", "/api/retros/r1/votes")).toHaveLength(1));
    expect(callsTo(calls, "POST", "/api/retros/r1/votes")[0].body).toEqual({
      cluster_ids: ["c1", "c2", "c1"],
    });
  });

  it("cannot submit an empty ballot", async () => {
    const calls = renderBoard(voting());
    await ready();
    expect(screen.getByRole("button", { name: "Submit ballot" })).toBeDisabled();
    expect(callsTo(calls, "POST", "/api/retros/r1/votes")).toHaveLength(0);
  });

  it("locks after a reload shows this user has voted, and reconstructs nothing", async () => {
    renderBoard(
      voting({
        "GET /api/retros/r1": {
          status: 200,
          data: retro({
            phase: "vote",
            clusters: [CLUSTER],
            votes: [{ user_id: ALICE.id, submitted_at: "2026-02-04T00:00:00Z" }],
          }),
        },
      })
    );
    await ready();

    expect(screen.getByText("Vote submitted")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Vote for Flow" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Submit ballot" })).not.toBeInTheDocument();
  });

  it("hides the tally until the server opens it", async () => {
    renderBoard(voting());
    await ready();
    expect(screen.getByText(/Results are hidden/)).toBeInTheDocument();
  });

  it("shows the ranked results once they open", async () => {
    renderBoard(
      voting({
        "GET /api/retros/r1/votes/results": {
          status: 200,
          data: {
            members_voted: 2,
            members_total: 2,
            total_votes: 4,
            results: [
              { cluster_id: "c1", name: "Flow", vote_count: 3, rank: 1 },
              { cluster_id: "c2", name: "Tooling", vote_count: 1, rank: 2 },
            ],
          },
        },
      })
    );
    await ready();

    expect(await screen.findByText(/2 of 2 members voted/)).toBeInTheDocument();
    expect(screen.getByText("1. Flow — 3")).toBeInTheDocument();
    expect(screen.getByText("2. Tooling — 1")).toBeInTheDocument();
  });

  it("reports a duplicate ballot and locks it", async () => {
    let votes: unknown[] = [];
    renderBoard(
      voting({
        "GET /api/retros/r1": () => ({
          status: 200,
          data: retro({ phase: "vote", clusters: [CLUSTER], votes }),
        }),
        "POST /api/retros/r1/votes": () => {
          votes = [{ user_id: ALICE.id, submitted_at: "2026-02-04T00:00:00Z" }];
          return { status: 409 };
        },
      })
    );
    await ready();

    fireEvent.click(screen.getByRole("button", { name: "Vote for Flow" }));
    fireEvent.click(screen.getByRole("button", { name: "Submit ballot" }));

    expect(await screen.findByText("You have already voted")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Vote submitted")).toBeInTheDocument());
  });

  it("explains an empty board rather than offering an impossible ballot", async () => {
    renderBoard(
      voting({
        "GET /api/retros/r1": { status: 200, data: retro({ phase: "vote", clusters: [] }) },
      })
    );
    await ready();
    expect(screen.getByText(/no clusters to vote on/)).toBeInTheDocument();
  });
});

// --- discuss ------------------------------------------------------------------

const TOPICS = [
  {
    id: "t2",
    cluster_id: "c2",
    name: "Tooling",
    vote_count: 1,
    rank: 2,
    status: "pending",
    notes: "",
  },
  {
    id: "t1",
    cluster_id: "c1",
    name: "Flow",
    vote_count: 3,
    rank: 1,
    status: "pending",
    notes: "",
  },
];

describe("discuss", () => {
  const discussing = (routes: Record<string, Handler | Reply> = {}, extra = {}) =>
    board({
      "GET /api/retros/r1": {
        status: 200,
        data: retro({
          phase: "discuss",
          clusters: [CLUSTER],
          topics: TOPICS,
          decisions: [
            { id: "d1", topic_id: "t1", text: "Ship it", is_confirmed: false },
            { id: "d2", topic_id: null, text: "General note", is_confirmed: true },
          ],
          actions: [
            {
              id: "a1",
              topic_id: "t1",
              description: "Pair on the flaky test",
              owner_id: BOB_ID,
              owner_name: null,
              status: "open",
              due_date: null,
            },
          ],
          ...extra,
        }),
      },
      "GET /api/retros/r1/votes/results": { status: 409 },
      ...routes,
    });

  it("orders topics by rank, keeps zero-vote ones, and files unlinked items", async () => {
    renderBoard(discussing());
    await ready();

    const headings = screen.getAllByRole("heading", { level: 3 });
    expect(headings.map((heading) => heading.textContent)).toEqual([
      "Flow — 3 votes",
      "Tooling — 1 votes",
      "Unlinked",
    ]);
    const unlinked = screen.getByRole("heading", { name: "Unlinked" }).closest("section")!;
    expect(within(unlinked).getByText("General note")).toBeInTheDocument();
  });

  it("says so when nothing was voted on", async () => {
    renderBoard(
      discussing({}, { topics: [], decisions: [], actions: [] })
    );
    await ready();
    expect(screen.getByText(/No topics were generated/)).toBeInTheDocument();
  });

  it("lets a facilitator set a topic's status and notes, and sends nothing unchanged", async () => {
    const calls = renderBoard(
      discussing({
        "PATCH /api/retros/r1/topics/t1": { status: 200, data: { ...TOPICS[1], status: "discussed" } },
      })
    );
    await ready();

    fireEvent.click(screen.getByRole("button", { name: "Save notes on Flow" }));
    expect(callsTo(calls, "PATCH", "/api/retros/r1/topics/t1")).toHaveLength(0);

    fireEvent.change(screen.getByLabelText("Status of Flow"), { target: { value: "discussed" } });
    await waitFor(() =>
      expect(callsTo(calls, "PATCH", "/api/retros/r1/topics/t1")).toHaveLength(1)
    );
    expect(callsTo(calls, "PATCH", "/api/retros/r1/topics/t1")[0].body).toEqual({
      status: "discussed",
    });

    fireEvent.change(screen.getByLabelText("Notes on Flow"), { target: { value: "shipped it" } });
    fireEvent.click(screen.getByRole("button", { name: "Save notes on Flow" }));
    await waitFor(() =>
      expect(callsTo(calls, "PATCH", "/api/retros/r1/topics/t1")).toHaveLength(2)
    );
    expect(callsTo(calls, "PATCH", "/api/retros/r1/topics/t1")[1].body).toEqual({
      notes: "shipped it",
    });
  });

  it("gives a plain member no topic, decision or action controls", async () => {
    renderBoard(
      discussing({
        "GET /api/projects/p1/dashboard": {
          status: 200,
          data: dashboard([{ ...MEMBERS[0], role: "member" }, MEMBERS[1]]),
        },
      })
    );
    await ready();

    expect(screen.getByText("Ship it")).toBeInTheDocument();
    expect(screen.queryByLabelText("Status of Flow")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Add decision/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Owner of this action")).not.toBeInTheDocument();
    // Bob owns the action, and Alice is not Bob.
    expect(screen.queryByLabelText("Status of this action")).not.toBeInTheDocument();
  });

  it("gives an action's owner only status and due date", async () => {
    renderBoard(
      discussing({
        "GET /api/auth/me": { status: 200, data: { ...ALICE, id: BOB_ID } },
        "GET /api/projects/p1/dashboard": {
          status: 200,
          data: dashboard([{ ...MEMBERS[0], role: "facilitator" }, MEMBERS[1]]),
        },
      })
    );
    await ready();

    expect(screen.getByLabelText("Status of this action")).toBeInTheDocument();
    expect(screen.getByLabelText("Due date for this action")).toBeInTheDocument();
    expect(screen.queryByLabelText("Owner of this action")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete this action" })).not.toBeInTheDocument();
  });

  it("creates, confirms and deletes a decision", async () => {
    const calls = renderBoard(
      discussing({
        "POST /api/retros/r1/decisions": { status: 201, data: {} },
        "PATCH /api/retros/r1/decisions/d1": { status: 200, data: {} },
        "DELETE /api/retros/r1/decisions/d1": { status: 204 },
      })
    );
    await ready();

    fireEvent.change(screen.getByLabelText("New decision for Flow"), { target: { value: "  Do it  " } });
    fireEvent.click(screen.getByRole("button", { name: "Add decision to Flow" }));
    await waitFor(() => expect(callsTo(calls, "POST", "/api/retros/r1/decisions")).toHaveLength(1));
    expect(callsTo(calls, "POST", "/api/retros/r1/decisions")[0].body).toEqual({
      topic_id: "t1",
      text: "Do it",
    });

    fireEvent.click(screen.getByRole("button", { name: "Confirm this decision" }));
    await waitFor(() =>
      expect(callsTo(calls, "PATCH", "/api/retros/r1/decisions/d1")).toHaveLength(1)
    );
    expect(callsTo(calls, "PATCH", "/api/retros/r1/decisions/d1")[0].body).toEqual({
      is_confirmed: true,
    });

    const row = screen.getByText("Ship it").closest("li")!;
    fireEvent.click(within(row).getByRole("button", { name: "Delete this decision" }));
    fireEvent.click(await within(row).findByRole("button", { name: "Yes, delete this decision" }));
    await waitFor(() =>
      expect(callsTo(calls, "DELETE", "/api/retros/r1/decisions/d1")).toHaveLength(1)
    );
  });

  it("creates an action with an owner or Unassigned, and sends an ISO due date", async () => {
    const calls = renderBoard(
      discussing({
        "POST /api/retros/r1/actions": { status: 201, data: {} },
        "PATCH /api/retros/r1/actions/a1": { status: 200, data: {} },
      })
    );
    await ready();

    fireEvent.change(screen.getByLabelText("New action for Flow"), { target: { value: "Write it up" } });
    fireEvent.change(screen.getByLabelText("New action owner for Flow"), { target: { value: BOB_ID } });
    fireEvent.click(screen.getByRole("button", { name: "Add action to Flow" }));
    await waitFor(() => expect(callsTo(calls, "POST", "/api/retros/r1/actions")).toHaveLength(1));
    expect(callsTo(calls, "POST", "/api/retros/r1/actions")[0].body).toEqual({
      topic_id: "t1",
      description: "Write it up",
      owner_id: BOB_ID,
    });

    fireEvent.change(screen.getByLabelText("Due date for this action"), {
      target: { value: "2026-03-01" },
    });
    await waitFor(() =>
      expect(callsTo(calls, "PATCH", "/api/retros/r1/actions/a1")).toHaveLength(1)
    );
    expect(callsTo(calls, "PATCH", "/api/retros/r1/actions/a1")[0].body).toEqual({
      due_date: "2026-03-01T00:00:00Z",
    });
  });

  it("blocks a blank decision and keeps the field", async () => {
    const calls = renderBoard(discussing());
    await ready();

    fireEvent.change(screen.getByLabelText("New decision for Flow"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "Add decision to Flow" }));

    expect(await screen.findByText("Decision text is required")).toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/retros/r1/decisions")).toHaveLength(0);
  });
});

// --- the socket ---------------------------------------------------------------

describe("live updates", () => {
  it("opens one socket, reports its status, and closes it on unmount", async () => {
    const { unmount } = renderWithUnmount(board());
    await ready();

    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    lastSocket().open();
    expect(await screen.findByText("Connection: Connected")).toBeInTheDocument();
    expect(lastSocket().url).toContain("/ws/retro/r1?token=");

    unmount();
    expect(FakeWebSocket.instances[0].closedWith).toBe(1000);
  });

  it("keeps the token out of the page", async () => {
    renderBoard(board());
    await ready();
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));

    expect(document.body.innerHTML).not.toContain("token=");
    expect(document.body.innerHTML).not.toContain("fresh");
    expect(window.localStorage.length).toBe(0);
  });

  it("merges a cluster event once, even after the REST call that caused it", async () => {
    const calls = renderBoard(
      board({
        "GET /api/retros/r1": {
          status: 200,
          data: retro({ phase: "cluster", clusters: [CLUSTER] }),
        },
        "POST /api/retros/r1/clusters": { status: 201, data: { ...CLUSTER, id: "c2", name: "Tooling" } },
      })
    );
    await ready();
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    lastSocket().open();

    fireEvent.change(screen.getByLabelText("New cluster name"), { target: { value: "Tooling" } });
    fireEvent.click(screen.getByRole("button", { name: "Add cluster" }));
    await waitFor(() => expect(callsTo(calls, "POST", "/api/retros/r1/clusters")).toHaveLength(1));

    lastSocket().emit("cluster_created", { id: "c2", name: "Tooling", created_at: "2026-02-05T00:00:00Z" });
    lastSocket().emit("cluster_created", { id: "c2", name: "Tooling", created_at: "2026-02-05T00:00:00Z" });

    await waitFor(() =>
      expect(screen.getAllByRole("heading", { name: "Tooling" })).toHaveLength(1)
    );
  });

  it("reloads on phase_changed and installs voting_closed results", async () => {
    let phase = "vote";
    const calls = renderBoard(
      board({
        "GET /api/retros/r1": () => ({
          status: 200,
          data: retro({ phase, clusters: [CLUSTER] }),
        }),
        "GET /api/retros/r1/votes/results": { status: 409 },
      })
    );
    await ready();
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    lastSocket().open();

    lastSocket().emit("voting_closed", {
      members_voted: 2,
      members_total: 2,
      total_votes: 3,
      results: [{ cluster_id: "c1", name: "Flow", vote_count: 3, rank: 1 }],
    });
    expect(await screen.findByText("1. Flow — 3")).toBeInTheDocument();

    const before = callsTo(calls, "GET", "/api/retros/r1").length;
    phase = "discuss";
    lastSocket().emit("phase_changed", { phase: "discuss" });
    await waitFor(() =>
      expect(callsTo(calls, "GET", "/api/retros/r1").length).toBeGreaterThan(before)
    );
    expect(await screen.findByText("Discuss (current)")).toBeInTheDocument();
  });

  it("ignores an event it does not know", async () => {
    renderBoard(board());
    await ready();
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    lastSocket().open();

    lastSocket().emit("something_new", { id: "x" });
    lastSocket().emitRaw("}{");

    expect(screen.getByRole("heading", { name: "Retrospective" })).toBeInTheDocument();
  });
});

function renderWithUnmount(routes: Record<string, Handler | Reply>) {
  installHttp(routes);
  return render(
    <MemoryRouter initialEntries={["/retros/r1"]}>
      <AppRoutes />
    </MemoryRouter>
  );
}

// --- withdrawing a ballot (#21) ----------------------------------------------

describe("withdrawing a ballot", () => {
  const voted = (overrides: Record<string, unknown> = {}) =>
    board({
      "GET /api/retros/r1": {
        status: 200,
        data: retro({
          phase: "vote",
          clusters: [CLUSTER],
          votes: [{ user_id: ALICE.id, submitted_at: "2026-02-04T00:00:00Z" }],
          ...overrides,
        }),
      },
      "GET /api/retros/r1/votes/results": { status: 409 },
      "DELETE /api/retros/r1/votes": { status: 204 },
    });

  it("is offered while the tally has never been visible", async () => {
    renderBoard(voted());
    await ready();
    expect(screen.getByRole("button", { name: "Withdraw my ballot" })).toBeInTheDocument();
  });

  it("is gone once the results have opened", async () => {
    renderBoard(voted({ voting_results_opened_at: "2026-02-05T00:00:00Z" }));
    await ready();
    expect(
      screen.queryByRole("button", { name: "Withdraw my ballot" })
    ).not.toBeInTheDocument();
    expect(screen.getByText("Vote submitted")).toBeInTheDocument();
  });

  it("is not offered to somebody who has not voted", async () => {
    renderBoard(
      board({
        "GET /api/retros/r1": {
          status: 200,
          data: retro({ phase: "vote", clusters: [CLUSTER] }),
        },
        "GET /api/retros/r1/votes/results": { status: 409 },
      })
    );
    await ready();
    expect(
      screen.queryByRole("button", { name: "Withdraw my ballot" })
    ).not.toBeInTheDocument();
  });

  it("confirms first, saying the whole ballot goes", async () => {
    const calls = renderBoard(voted());
    await ready();

    fireEvent.click(screen.getByRole("button", { name: "Withdraw my ballot" }));
    const dialog = await screen.findByRole("dialog", { name: "Withdraw my ballot" });
    expect(within(dialog).getByText(/no way to change just/)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(calls.filter((call) => call.method === "DELETE")).toHaveLength(0);
  });

  it("withdraws and gives the draft controls back", async () => {
    let votes: unknown[] = [{ user_id: ALICE.id, submitted_at: "2026-02-04T00:00:00Z" }];
    const calls = renderBoard(
      board({
        "GET /api/retros/r1": () => ({
          status: 200,
          data: retro({ phase: "vote", clusters: [CLUSTER], votes }),
        }),
        "GET /api/retros/r1/votes/results": { status: 409 },
        "DELETE /api/retros/r1/votes": () => {
          votes = [];
          return { status: 204 };
        },
      })
    );
    await ready();

    fireEvent.click(screen.getByRole("button", { name: "Withdraw my ballot" }));
    fireEvent.click(await screen.findByRole("button", { name: "Yes, withdraw it" }));

    expect(await screen.findByRole("button", { name: "Vote for Flow" })).toBeInTheDocument();
    expect(callsTo(calls, "DELETE", "/api/retros/r1/votes")).toHaveLength(1);
    expect(screen.queryByText("Vote submitted")).not.toBeInTheDocument();
  });

  it("refetches and keeps the ballot when the server refuses", async () => {
    const calls = renderBoard(
      board({
        "GET /api/retros/r1": {
          status: 200,
          data: retro({
            phase: "vote",
            clusters: [CLUSTER],
            votes: [{ user_id: ALICE.id, submitted_at: "2026-02-04T00:00:00Z" }],
          }),
        },
        "GET /api/retros/r1/votes/results": { status: 409 },
        "DELETE /api/retros/r1/votes": { status: 409 },
      })
    );
    await ready();
    const before = callsTo(calls, "GET", "/api/retros/r1").length;

    fireEvent.click(screen.getByRole("button", { name: "Withdraw my ballot" }));
    fireEvent.click(await screen.findByRole("button", { name: "Yes, withdraw it" }));

    expect(await screen.findByText("You have already voted")).toBeInTheDocument();
    await waitFor(() =>
      expect(callsTo(calls, "GET", "/api/retros/r1").length).toBeGreaterThan(before)
    );
    expect(screen.getByText("Vote submitted")).toBeInTheDocument();
  });
});

// --- 用完额度不是坏了 (#27) ---------------------------------------------------

describe("the AI budget", () => {
  it("says the project has used its requests rather than that something broke", async () => {
    renderBoard(
      board({
        "GET /api/retros/r1": {
          status: 200,
          data: retro({ phase: "cluster", clusters: [CLUSTER] }),
        },
        "POST /api/retros/r1/clusters/suggest": { status: 429 },
      })
    );
    await ready();

    fireEvent.click(screen.getByRole("button", { name: "Suggest clusters" }));

    expect(
      await screen.findByText(/used its AI requests for now/)
    ).toBeInTheDocument();
    expect(screen.queryByText(/Something went wrong/)).not.toBeInTheDocument();
    // 还能再试,只是现在不行。
    expect(screen.getByText("You can ask again.")).toBeInTheDocument();
  });
});
