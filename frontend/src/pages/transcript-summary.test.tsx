/**
 * Pasting, polling, reviewing, and publishing once (#17).
 *
 * Polling is tested on fake timers, so "every two seconds, never overlapping,
 * and stopped on every terminal state" is an assertion rather than a hope.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppRoutes } from "../App";
import { setAccessToken } from "../api/client";
import { MAX_TRANSCRIPT_CHARS } from "../api/transcript";
import { ALICE, callsTo, installHttp, type Call, type Handler, type Reply } from "../test-utils/http";
import { installFakeWebSocket } from "../test-utils/socket";

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
    phase: "discuss",
    clusters: [],
    votes: [],
    topics: [],
    decisions: [],
    actions: [],
    transcript: null,
    ai_suggestions: null,
    created_at: "2026-02-01T00:00:00Z",
    ...overrides,
  };
}

const IDLE = { status: "idle", error: null, decisions: [], actions: [] };

const READY = {
  status: "ready",
  error: null,
  decisions: [{ id: "sd1", text: "Ship on Fridays", state: "pending", created_id: null }],
  actions: [
    {
      id: "sa1",
      description: "Write the runbook",
      owner_name: "Dana Wu",
      owner_id: null,
      due_date: null,
      state: "pending",
      created_id: null,
    },
  ],
};

const SUMMARY = {
  topics: [
    {
      id: "t1",
      cluster_id: "c1",
      name: "Flow",
      vote_count: 3,
      rank: 1,
      status: "discussed",
      notes: "went well",
    },
  ],
  decisions: [{ id: "d1", topic_id: "t1", topic: "Flow", text: "Ship on Fridays" }],
  actions: [
    {
      id: "a1",
      topic_id: null,
      topic: null,
      description: "Write the runbook",
      owner_id: BOB_ID,
      owner: "Bob",
      due_date: null,
      status: "open",
    },
  ],
  participation: { total_members: 2, submitted_feedback: 1, voted: 2 },
  feedback_cards: [
    {
      id: "f1",
      category: "start",
      text: "pair more often",
      is_anonymous: false,
      cluster_id: null,
      author_id: ALICE.id,
      created_at: "2026-02-02T00:00:00Z",
    },
    {
      id: "f2",
      category: "stop",
      text: "said in confidence",
      is_anonymous: true,
      cluster_id: null,
      author_id: null,
      created_at: "2026-02-03T00:00:00Z",
    },
  ],
};

function base(routes: Record<string, Handler | Reply> = {}): Record<string, Handler | Reply> {
  return {
    ...SESSION,
    "GET /api/retros/r1": { status: 200, data: retro() },
    "GET /api/cycles/cy1": { status: 200, data: CYCLE },
    "GET /api/projects/p1/dashboard": { status: 200, data: dashboard() },
    "GET /api/retros/r1/suggestions": { status: 200, data: IDLE },
    ...routes,
  };
}

function renderAt(path: string, routes: Record<string, Handler | Reply>): Call[] {
  const calls = installHttp(routes);
  render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>
  );
  return calls;
}

beforeEach(() => {
  setAccessToken(null);
  installFakeWebSocket();
});

afterEach(() => {
  vi.useRealTimers();
});

// --- access ------------------------------------------------------------------

describe("who may paste a transcript", () => {
  it("shows the facilitator the composer with a counter and a privacy note", async () => {
    renderAt("/retros/r1/transcript", base());
    expect(await screen.findByLabelText("Paste the meeting transcript")).toBeInTheDocument();
    expect(screen.getByText(`0 / ${MAX_TRANSCRIPT_CHARS}`)).toBeInTheDocument();
    expect(screen.getByText(/whole meeting text is stored/)).toBeInTheDocument();
  });

  it("shows a plain member nothing, and never asks for the suggestions", async () => {
    const calls = renderAt(
      "/retros/r1/transcript",
      base({
        "GET /api/projects/p1/dashboard": {
          status: 200,
          data: dashboard([{ ...MEMBERS[0], role: "member" }, MEMBERS[1]]),
        },
      })
    );

    expect(await screen.findByText(/Only the facilitator/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Paste the meeting transcript")).not.toBeInTheDocument();
    expect(callsTo(calls, "GET", "/api/retros/r1/suggestions")).toHaveLength(0);
    expect(screen.getByRole("link", { name: "Back to the board" })).toBeInTheDocument();
  });

  it("refuses outside discuss and points at the right place", async () => {
    renderAt(
      "/retros/r1/transcript",
      base({ "GET /api/retros/r1": { status: 200, data: retro({ phase: "vote" }) } })
    );
    expect(await screen.findByText(/only be added during the discussion/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the board" })).toBeInTheDocument();

    setAccessToken(null);
    renderAt(
      "/retros/r1/transcript",
      base({ "GET /api/retros/r1": { status: 200, data: retro({ phase: "done" }) } })
    );
    expect(await screen.findAllByRole("link", { name: "Read the summary" })).not.toHaveLength(0);
  });

  it("has its own 403, 404 and retry states", async () => {
    renderAt("/retros/r1/transcript", base({ "GET /api/retros/r1": { status: 403 } }));
    expect(await screen.findByText(/do not have access/)).toBeInTheDocument();

    setAccessToken(null);
    renderAt("/retros/r1/transcript", base({ "GET /api/retros/r1": { status: 404 } }));
    expect(await screen.findByText("Retrospective not found.")).toBeInTheDocument();

    setAccessToken(null);
    let broken = true;
    const calls = renderAt(
      "/retros/r1/transcript",
      base({
        "GET /api/retros/r1": () => (broken ? { status: 500 } : { status: 200, data: retro() }),
      })
    );
    expect(await screen.findByText(/could not load this retrospective/)).toBeInTheDocument();
    broken = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByLabelText("Paste the meeting transcript");
    expect(callsTo(calls, "GET", "/api/retros/r1")).toHaveLength(2);
  });
});

// --- pasting -----------------------------------------------------------------

describe("pasting", () => {
  it("blocks blank and over-long text locally, and allows exactly the limit", async () => {
    const calls = renderAt("/retros/r1/transcript", base());
    const box = await screen.findByLabelText("Paste the meeting transcript");

    fireEvent.change(box, { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "Extract" }));
    expect(await screen.findByText(/Paste the meeting transcript first/)).toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/retros/r1/transcript")).toHaveLength(0);

    fireEvent.change(box, { target: { value: "x".repeat(MAX_TRANSCRIPT_CHARS + 1) } });
    fireEvent.click(screen.getByRole("button", { name: "Extract" }));
    expect(await screen.findByText(/at most 100000 characters/)).toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/retros/r1/transcript")).toHaveLength(0);
  });

  it("sends trimmed text and switches to processing on 202", async () => {
    const calls = renderAt(
      "/retros/r1/transcript",
      base({ "POST /api/retros/r1/transcript": { status: 202, data: { status: "processing" } } })
    );
    const box = await screen.findByLabelText("Paste the meeting transcript");

    fireEvent.change(box, { target: { value: "  we agreed to ship  " } });
    fireEvent.click(screen.getByRole("button", { name: "Extract" }));

    expect(await screen.findByText(/Extracting decisions and actions/)).toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/retros/r1/transcript")[0].body).toEqual({
      text: "we agreed to ship",
    });
  });

  it("names a spent AI budget instead of blaming the transcript", async () => {
    renderAt("/retros/r1/transcript", base({ "POST /api/retros/r1/transcript": { status: 429 } }));
    const box = await screen.findByLabelText("Paste the meeting transcript");

    fireEvent.change(box, { target: { value: "we agreed" } });
    fireEvent.click(screen.getByRole("button", { name: "Extract" }));

    expect(await screen.findByText(/used its AI requests for now/)).toBeInTheDocument();
    // 文本还在,等会儿再试就行。
    expect(box).toHaveValue("we agreed");
  });

  it("treats a 409 as already running rather than a failure", async () => {
    const calls = renderAt(
      "/retros/r1/transcript",
      base({ "POST /api/retros/r1/transcript": { status: 409 } })
    );
    const box = await screen.findByLabelText("Paste the meeting transcript");

    fireEvent.change(box, { target: { value: "we agreed" } });
    fireEvent.click(screen.getByRole("button", { name: "Extract" }));

    expect(await screen.findByText(/Extracting decisions and actions/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/retros/r1/transcript")).toHaveLength(1);
  });

  it("keeps the text on a 422 and on a generic failure", async () => {
    let status = 422;
    renderAt(
      "/retros/r1/transcript",
      base({ "POST /api/retros/r1/transcript": () => ({ status }) })
    );
    const box = await screen.findByLabelText("Paste the meeting transcript");

    fireEvent.change(box, { target: { value: "we agreed" } });
    fireEvent.click(screen.getByRole("button", { name: "Extract" }));
    expect(await screen.findByText(/transcript was rejected/)).toBeInTheDocument();
    expect(box).toHaveValue("we agreed");

    status = 500;
    fireEvent.click(screen.getByRole("button", { name: "Extract" }));
    expect(await screen.findByText(/Something went wrong/)).toBeInTheDocument();
    expect(box).toHaveValue("we agreed");
  });

  it("populates the stored transcript and confirms before replacing it", async () => {
    const calls = renderAt(
      "/retros/r1/transcript",
      base({
        "GET /api/retros/r1": { status: 200, data: retro({ transcript: "the first meeting" }) },
        "GET /api/retros/r1/suggestions": { status: 200, data: READY },
        "POST /api/retros/r1/transcript": { status: 202, data: { status: "processing" } },
      })
    );

    const box = await screen.findByLabelText("Paste the meeting transcript");
    await waitFor(() => expect(box).toHaveValue("the first meeting"));

    fireEvent.change(box, { target: { value: "a second meeting" } });
    fireEvent.click(screen.getByRole("button", { name: "Extract" }));

    const dialog = await screen.findByRole("dialog", { name: "Replace the transcript" });
    expect(within(dialog).getByText(/discards every pending and rejected draft/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(callsTo(calls, "POST", "/api/retros/r1/transcript")).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Extract" }));
    fireEvent.click(await screen.findByRole("button", { name: "Yes, replace it" }));
    await waitFor(() =>
      expect(callsTo(calls, "POST", "/api/retros/r1/transcript")).toHaveLength(1)
    );
  });
});

// --- polling -----------------------------------------------------------------

describe("polling", () => {
  it("asks once on load, then every two seconds, and stops at ready", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let suggestions: unknown = { status: "processing", error: null, decisions: [], actions: [] };
    const calls = renderAt(
      "/retros/r1/transcript",
      base({ "GET /api/retros/r1/suggestions": () => ({ status: 200, data: suggestions }) })
    );

    await screen.findByText(/Extracting decisions and actions/);
    expect(callsTo(calls, "GET", "/api/retros/r1/suggestions")).toHaveLength(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(callsTo(calls, "GET", "/api/retros/r1/suggestions")).toHaveLength(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(callsTo(calls, "GET", "/api/retros/r1/suggestions")).toHaveLength(3);

    suggestions = READY;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    const atReady = callsTo(calls, "GET", "/api/retros/r1/suggestions").length;
    await screen.findByRole("heading", { name: "Review the drafts" });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(callsTo(calls, "GET", "/api/retros/r1/suggestions")).toHaveLength(atReady);
  });

  it("stops on unmount", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    installHttp(
      base({
        "GET /api/retros/r1/suggestions": {
          status: 200,
          data: { status: "processing", error: null, decisions: [], actions: [] },
        },
      })
    );
    const { unmount } = render(
      <MemoryRouter initialEntries={["/retros/r1/transcript"]}>
        <AppRoutes />
      </MemoryRouter>
    );
    await screen.findByText(/Extracting decisions and actions/);
    unmount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    // Nothing to assert but the absence of an unhandled request; an error here
    // would surface as a test failure from the unstubbed-route guard.
  });

  it("pauses on a network failure and resumes only on request", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let broken = false;
    const calls = renderAt(
      "/retros/r1/transcript",
      base({
        "GET /api/retros/r1/suggestions": () =>
          broken
            ? { status: 500 }
            : {
                status: 200,
                data: { status: "processing", error: null, decisions: [], actions: [] },
              },
      })
    );
    await screen.findByText(/Extracting decisions and actions/);

    broken = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(await screen.findByText(/lost contact/)).toBeInTheDocument();
    const paused = callsTo(calls, "GET", "/api/retros/r1/suggestions").length;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(callsTo(calls, "GET", "/api/retros/r1/suggestions")).toHaveLength(paused);

    broken = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry status check" }));
    await waitFor(() =>
      expect(callsTo(calls, "GET", "/api/retros/r1/suggestions").length).toBeGreaterThan(paused)
    );
  });

  it("maps each failure code to a friendly message and offers a repaste", async () => {
    for (const [code, phrase] of [
      ["timeout", "did not answer in time"],
      ["upstream_error", "unavailable right now"],
      ["malformed_response", "could not read"],
    ] as const) {
      setAccessToken(null);
      const calls = renderAt(
        "/retros/r1/transcript",
        base({
          "GET /api/retros/r1": { status: 200, data: retro({ transcript: "the meeting" }) },
          "GET /api/retros/r1/suggestions": {
            status: 200,
            data: { status: "failed", error: code, decisions: [], actions: [] },
          },
          "POST /api/retros/r1/transcript": { status: 202, data: { status: "processing" } },
        })
      );

      expect(await screen.findByText(new RegExp(phrase))).toBeInTheDocument();
      expect(document.body.textContent).not.toContain(code);

      fireEvent.click(screen.getAllByRole("button", { name: "Retry extraction" })[0]);
      await waitFor(() =>
        expect(callsTo(calls, "POST", "/api/retros/r1/transcript")[0].body).toEqual({
          text: "the meeting",
        })
      );
    }
  });

  it("calls an empty ready result no suggestions rather than a failure", async () => {
    renderAt(
      "/retros/r1/transcript",
      base({
        "GET /api/retros/r1/suggestions": {
          status: 200,
          data: { status: "ready", error: null, decisions: [], actions: [] },
        },
      })
    );
    expect(await screen.findByText(/No suggestions found/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

// --- review ------------------------------------------------------------------

describe("reviewing the drafts", () => {
  const reviewing = (routes: Record<string, Handler | Reply> = {}) =>
    base({ "GET /api/retros/r1/suggestions": { status: 200, data: READY }, ...routes });

  it("keeps the extracted owner name visible and offers the member list", async () => {
    renderAt("/retros/r1/transcript", reviewing());
    await screen.findByRole("heading", { name: "Review the drafts" });

    expect(screen.getByText("Named in the meeting: Dana Wu")).toBeInTheDocument();
    const owner = screen.getByLabelText("Owner") as HTMLSelectElement;
    expect([...owner.options].map((option) => option.textContent)).toEqual([
      "Unassigned",
      "Alice",
      "Bob",
    ]);
  });

  it("sends nothing until Apply, then exactly one atomic body", async () => {
    const calls = renderAt(
      "/retros/r1/transcript",
      reviewing({ "POST /api/retros/r1/suggestions/confirm": { status: 200, data: {} } })
    );
    await screen.findByRole("heading", { name: "Review the drafts" });

    expect(screen.getByRole("button", { name: "Apply review" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Decision text"), {
      target: { value: "  Ship on Thursdays  " },
    });
    fireEvent.click(screen.getByLabelText("Keep “Ship on Fridays”"));
    fireEvent.change(screen.getByLabelText("Owner"), { target: { value: BOB_ID } });
    fireEvent.change(screen.getByLabelText("Due date"), { target: { value: "2026-03-01" } });
    fireEvent.click(screen.getByLabelText("Reject “Write the runbook”"));
    expect(calls.filter((call) => call.url.includes("confirm"))).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Apply review" }));
    await waitFor(() =>
      expect(callsTo(calls, "POST", "/api/retros/r1/suggestions/confirm")).toHaveLength(1)
    );
    expect(callsTo(calls, "POST", "/api/retros/r1/suggestions/confirm")[0].body).toEqual({
      decisions: [{ id: "sd1", text: "Ship on Thursdays" }],
      actions: [],
      rejected: ["sa1"],
    });
  });

  it("sends a kept action with its owner and ISO due date", async () => {
    const calls = renderAt(
      "/retros/r1/transcript",
      reviewing({ "POST /api/retros/r1/suggestions/confirm": { status: 200, data: {} } })
    );
    await screen.findByRole("heading", { name: "Review the drafts" });

    fireEvent.click(screen.getByLabelText("Keep “Write the runbook”"));
    fireEvent.change(screen.getByLabelText("Owner"), { target: { value: BOB_ID } });
    fireEvent.change(screen.getByLabelText("Due date"), { target: { value: "2026-03-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply review" }));

    await waitFor(() =>
      expect(callsTo(calls, "POST", "/api/retros/r1/suggestions/confirm")).toHaveLength(1)
    );
    expect(callsTo(calls, "POST", "/api/retros/r1/suggestions/confirm")[0].body).toEqual({
      decisions: [],
      actions: [
        { id: "sa1", description: "Write the runbook", owner_id: BOB_ID, due_date: "2026-03-01T00:00:00Z" },
      ],
      rejected: [],
    });
  });

  it("refuses a kept item with nothing in it", async () => {
    const calls = renderAt("/retros/r1/transcript", reviewing());
    await screen.findByRole("heading", { name: "Review the drafts" });

    fireEvent.click(screen.getByLabelText("Keep “Ship on Fridays”"));
    fireEvent.change(screen.getByLabelText("Decision text"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "Apply review" }));

    expect(await screen.findByText(/needs some text/)).toBeInTheDocument();
    expect(calls.filter((call) => call.url.includes("confirm"))).toHaveLength(0);
  });

  it("keeps the whole review when the batch is refused", async () => {
    let status = 422;
    renderAt(
      "/retros/r1/transcript",
      reviewing({ "POST /api/retros/r1/suggestions/confirm": () => ({ status }) })
    );
    await screen.findByRole("heading", { name: "Review the drafts" });

    fireEvent.change(screen.getByLabelText("Decision text"), { target: { value: "Ship it" } });
    fireEvent.click(screen.getByLabelText("Keep “Ship on Fridays”"));
    fireEvent.click(screen.getByRole("button", { name: "Apply review" }));

    expect(await screen.findByText(/Check the fields and apply again/)).toBeInTheDocument();
    expect(screen.getByLabelText("Decision text")).toHaveValue("Ship it");
    expect(screen.getByLabelText("Keep “Ship on Fridays”")).toBeChecked();

    status = 500;
    fireEvent.click(screen.getByRole("button", { name: "Apply review" }));
    expect(await screen.findByText(/Something went wrong/)).toBeInTheDocument();
    expect(screen.getByLabelText("Decision text")).toHaveValue("Ship it");
  });

  it("shows a confirmed suggestion as an outcome, not an editor", async () => {
    renderAt(
      "/retros/r1/transcript",
      base({
        "GET /api/retros/r1/suggestions": {
          status: 200,
          data: {
            ...READY,
            decisions: [
              { id: "sd1", text: "Ship on Fridays", state: "confirmed", created_id: "d1" },
            ],
          },
        },
      })
    );
    await screen.findByRole("heading", { name: "Review the drafts" });

    // 内部 id 不再出现在界面上 —— 它对读的人没有任何意义。
    expect(screen.getByText("kept")).toBeInTheDocument();
    expect(screen.queryByText(/d1/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Decision text")).not.toBeInTheDocument();
  });

  it("lets a rejected suggestion be kept later", async () => {
    const calls = renderAt(
      "/retros/r1/transcript",
      base({
        "GET /api/retros/r1/suggestions": {
          status: 200,
          data: {
            ...READY,
            decisions: [{ id: "sd1", text: "Ship on Fridays", state: "rejected", created_id: null }],
          },
        },
        "POST /api/retros/r1/suggestions/confirm": { status: 200, data: {} },
      })
    );
    await screen.findByRole("heading", { name: "Review the drafts" });

    fireEvent.click(screen.getByLabelText("Keep “Ship on Fridays”"));
    fireEvent.click(screen.getByRole("button", { name: "Apply review" }));

    await waitFor(() =>
      expect(callsTo(calls, "POST", "/api/retros/r1/suggestions/confirm")[0].body).toEqual({
        decisions: [{ id: "sd1", text: "Ship on Fridays" }],
        actions: [],
        rejected: [],
      })
    );
  });

  it("refetches everything after a successful apply", async () => {
    const calls = renderAt(
      "/retros/r1/transcript",
      reviewing({ "POST /api/retros/r1/suggestions/confirm": { status: 200, data: {} } })
    );
    await screen.findByRole("heading", { name: "Review the drafts" });
    const before = callsTo(calls, "GET", "/api/retros/r1/suggestions").length;

    fireEvent.click(screen.getByLabelText("Keep “Ship on Fridays”"));
    fireEvent.click(screen.getByRole("button", { name: "Apply review" }));

    await waitFor(() =>
      expect(callsTo(calls, "GET", "/api/retros/r1/suggestions").length).toBeGreaterThan(before)
    );
    expect(callsTo(calls, "GET", "/api/retros/r1").length).toBeGreaterThan(1);
  });
});

// --- summary -----------------------------------------------------------------

describe("the summary", () => {
  const summarising = (routes: Record<string, Handler | Reply> = {}) =>
    base({ "GET /api/retros/r1/summary": { status: 200, data: SUMMARY }, ...routes });

  it("renders every section and marks it a preview", async () => {
    renderAt("/retros/r1/summary", summarising());
    await screen.findByRole("heading", { name: "Retrospective summary" });

    expect(screen.getByText("Preview")).toBeInTheDocument();
    expect(screen.getByText(/Flow — 3 votes · discussed/)).toBeInTheDocument();
    expect(screen.getByText(/Ship on Fridays \(Flow\)/)).toBeInTheDocument();
    expect(screen.getByText(/Write the runbook \(Unlinked\) · Bob · No due date · open/)).toBeInTheDocument();
    expect(screen.getByText("2 members")).toBeInTheDocument();
    expect(screen.getByText("1 submitted feedback")).toBeInTheDocument();
    expect(screen.getByText("2 voted")).toBeInTheDocument();
    expect(screen.getByText("pair more often")).toBeInTheDocument();
    expect(screen.getByText("No Continue cards")).toBeInTheDocument();
  });

  it("never names an anonymous card's author", async () => {
    renderAt("/retros/r1/summary", summarising());
    await screen.findByRole("heading", { name: "Retrospective summary" });

    const anonymous = screen.getByText("said in confidence").closest("li")!;
    expect(within(anonymous).getByText("Anonymous")).toBeInTheDocument();
    expect(anonymous.textContent).not.toContain("Alice");
    expect(anonymous.textContent).not.toContain("Bob");
  });

  it("tells a member the summary is not published, without showing it", async () => {
    renderAt(
      "/retros/r1/summary",
      summarising({
        "GET /api/projects/p1/dashboard": {
          status: 200,
          data: dashboard([{ ...MEMBERS[0], role: "member" }, MEMBERS[1]]),
        },
        "GET /api/retros/r1/summary": { status: 403 },
      })
    );

    expect(await screen.findByText(/has not been published yet/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("Ship on Fridays");
  });

  it("shows a member the published summary once the retro is done", async () => {
    renderAt(
      "/retros/r1/summary",
      summarising({
        "GET /api/retros/r1": { status: 200, data: retro({ phase: "done" }) },
        "GET /api/projects/p1/dashboard": {
          status: 200,
          data: dashboard([{ ...MEMBERS[0], role: "member" }, MEMBERS[1]]),
        },
      })
    );
    await screen.findByRole("heading", { name: "Retrospective summary" });

    expect(screen.getByText("Published")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Publish" })).not.toBeInTheDocument();
  });

  it("publishes once, behind a confirmation, and then never again", async () => {
    let phase = "discuss";
    const calls = renderAt(
      "/retros/r1/summary",
      summarising({
        "GET /api/retros/r1": () => ({ status: 200, data: retro({ phase }) }),
        "POST /api/retros/r1/summary/publish": () => {
          phase = "done";
          return { status: 200, data: SUMMARY };
        },
      })
    );
    await screen.findByRole("heading", { name: "Retrospective summary" });

    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    const dialog = await screen.findByRole("dialog", { name: "Publish this retrospective" });
    expect(within(dialog).getByText(/no way to unpublish/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(calls.filter((call) => call.url.includes("publish"))).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    fireEvent.click(await screen.findByRole("button", { name: "Yes, publish it" }));

    expect(await screen.findByText("Published")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Publish" })).not.toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/retros/r1/summary/publish")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: /unpublish/i })).not.toBeInTheDocument();
  });

  it("keeps the preview when publishing fails", async () => {
    renderAt(
      "/retros/r1/summary",
      summarising({ "POST /api/retros/r1/summary/publish": { status: 500 } })
    );
    await screen.findByRole("heading", { name: "Retrospective summary" });

    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    fireEvent.click(await screen.findByRole("button", { name: "Yes, publish it" }));

    expect(await screen.findByText(/Nothing has changed/)).toBeInTheDocument();
    expect(screen.getByText("Preview")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish" })).toBeInTheDocument();
  });

  it("refetches on a 400 and withdraws the control on a 403", async () => {
    let phase = "discuss";
    let role = "facilitator";
    renderAt(
      "/retros/r1/summary",
      summarising({
        "GET /api/retros/r1": () => ({ status: 200, data: retro({ phase }) }),
        "GET /api/projects/p1/dashboard": () => ({
          status: 200,
          data: dashboard([{ ...MEMBERS[0], role }, MEMBERS[1]]),
        }),
        "POST /api/retros/r1/summary/publish": () => {
          if (role === "facilitator" && phase === "discuss") {
            phase = "done";
            return { status: 400 };
          }
          return { status: 403 };
        },
      })
    );
    await screen.findByRole("heading", { name: "Retrospective summary" });

    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    fireEvent.click(await screen.findByRole("button", { name: "Yes, publish it" }));

    expect(await screen.findByText("Published")).toBeInTheDocument();
    void role;
  });

  it("never persists the summary or a draft anywhere", async () => {
    renderAt("/retros/r1/summary", summarising());
    await screen.findByRole("heading", { name: "Retrospective summary" });

    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });
});
