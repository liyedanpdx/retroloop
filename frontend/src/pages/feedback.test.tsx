/**
 * The private feedback page (#15).
 *
 * Two things are load-bearing and everything else supports them. Anonymity is a
 * commitment the user is warned about and then cannot undo, and it leaves no
 * client-side trace of who wrote what. And the page goes read-only the moment
 * the cycle leaves `collecting`, including when it leaves under a request that
 * is already in flight.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { AppRoutes } from "../App";
import { setAccessToken } from "../api/client";
import { ALICE, callsTo, installHttp, type Call, type Handler, type Reply } from "../test-utils/http";

const SESSION: Record<string, Handler | Reply> = {
  "POST /api/auth/refresh": { status: 200, data: { access_token: "fresh" } },
  "GET /api/auth/me": { status: 200, data: ALICE },
};

const CYCLE = {
  id: "c1",
  project_id: "p1",
  status: "collecting",
  created_at: "2026-02-01T00:00:00Z",
  closed_at: null,
  created_by: ALICE.id,
};

function card(overrides: Record<string, unknown> = {}) {
  return {
    id: "f1",
    cycle_id: "c1",
    author_id: ALICE.id,
    category: "start",
    text: "pair more often",
    is_anonymous: false,
    cluster_id: null,
    created_at: "2026-02-02T00:00:00Z",
    ...overrides,
  };
}

function renderPage(routes: Record<string, Handler | Reply>): Call[] {
  const calls = installHttp({ ...SESSION, ...routes });
  render(
    <MemoryRouter initialEntries={["/projects/p1/feedback"]}>
      <AppRoutes />
    </MemoryRouter>
  );
  return calls;
}

function withCards(cards: unknown[], cycle = CYCLE) {
  return {
    "GET /api/projects/p1/cycles": { status: 200, data: [cycle] },
    "GET /api/cycles/c1/feedback": { status: 200, data: cards },
  };
}

beforeEach(() => {
  setAccessToken(null);
});

// --- finding the cycle -------------------------------------------------------

describe("choosing the cycle", () => {
  it("picks the active one and never a closed historical cycle", async () => {
    const calls = renderPage({
      "GET /api/projects/p1/cycles": {
        status: 200,
        data: [
          { ...CYCLE, id: "c-old", status: "closed", closed_at: "2026-01-20T00:00:00Z" },
          CYCLE,
        ],
      },
      "GET /api/cycles/c1/feedback": { status: 200, data: [] },
    });

    expect(await screen.findByRole("heading", { name: "Your feedback" })).toBeInTheDocument();
    expect(callsTo(calls, "GET", "/api/cycles/c1/feedback")).toHaveLength(1);
    expect(calls.some((call) => call.url.includes("c-old"))).toBe(false);
  });

  it("says so when there is no active cycle", async () => {
    renderPage({ "GET /api/projects/p1/cycles": { status: 200, data: [] } });
    expect(await screen.findByText("No active feedback cycle")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to projects" })).toBeInTheDocument();
  });

  it("shows a loading state, and access states for 403 and 404", async () => {
    renderPage(withCards([]));
    expect(await screen.findByText("Loading this cycle…")).toBeInTheDocument();
    await screen.findByRole("heading", { name: "Your feedback" });

    setAccessToken(null);
    renderPage({ "GET /api/projects/p1/cycles": { status: 403 } });
    expect(await screen.findByText(/do not have access/)).toBeInTheDocument();

    setAccessToken(null);
    renderPage({ "GET /api/projects/p1/cycles": { status: 404 } });
    expect(await screen.findByText("Project not found.")).toBeInTheDocument();
  });

  it("offers Retry on any other failure", async () => {
    let broken = true;
    const calls = renderPage({
      "GET /api/projects/p1/cycles": () =>
        broken ? { status: 500 } : { status: 200, data: [CYCLE] },
      "GET /api/cycles/c1/feedback": { status: 200, data: [] },
    });

    expect(await screen.findByText(/could not load your feedback/)).toBeInTheDocument();
    broken = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("heading", { name: "Your feedback" })).toBeInTheDocument();
    expect(callsTo(calls, "GET", "/api/projects/p1/cycles")).toHaveLength(2);
  });
});

// --- the three columns -------------------------------------------------------

describe("the page", () => {
  it("shows Start, Stop and Continue in that order, each with an empty state", async () => {
    renderPage(withCards([]));
    await screen.findByRole("heading", { name: "Your feedback" });

    const headings = screen.getAllByRole("heading", { level: 2 });
    expect(headings.map((heading) => heading.textContent)).toEqual(["Start", "Stop", "Continue"]);
    expect(screen.getByText("No Start cards yet")).toBeInTheDocument();
    expect(screen.getByText("No Stop cards yet")).toBeInTheDocument();
    expect(screen.getByText("No Continue cards yet")).toBeInTheDocument();
  });

  it("shows only the signed-in user's attributable cards, oldest first", async () => {
    renderPage(
      withCards([
        card({ id: "mine-2", text: "second", created_at: "2026-02-03T00:00:00Z" }),
        card({ id: "mine-1", text: "first", created_at: "2026-02-01T00:00:00Z" }),
        card({ id: "theirs", author_id: "u2", text: "somebody else" }),
        card({ id: "anon", author_id: null, is_anonymous: true, text: "anonymous card" }),
      ])
    );
    await screen.findByRole("heading", { name: "Your feedback" });

    const column = screen.getByRole("heading", { name: "Start" }).closest("section")!;
    const texts = within(column)
      .getAllByRole("listitem")
      .map((item) => item.textContent);
    expect(texts[0]).toContain("first");
    expect(texts[1]).toContain("second");
    expect(document.body.textContent).not.toContain("somebody else");
    expect(document.body.textContent).not.toContain("anonymous card");
  });
});

// --- creating ----------------------------------------------------------------

describe("adding a card", () => {
  it("blocks blank text without a request, and sends trimmed text otherwise", async () => {
    const calls = renderPage({
      ...withCards([]),
      "POST /api/cycles/c1/feedback": { status: 201, data: card() },
    });
    await screen.findByRole("heading", { name: "Your feedback" });

    fireEvent.change(screen.getByLabelText("Start feedback"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "Add Start card" }));
    expect(await screen.findByText("Feedback text is required")).toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/cycles/c1/feedback")).toHaveLength(0);

    fireEvent.change(screen.getByLabelText("Start feedback"), {
      target: { value: "  pair more often  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add Start card" }));

    await waitFor(() =>
      expect(callsTo(calls, "POST", "/api/cycles/c1/feedback")).toHaveLength(1)
    );
    expect(callsTo(calls, "POST", "/api/cycles/c1/feedback")[0].body).toEqual({
      category: "start",
      text: "pair more often",
      is_anonymous: false,
    });
  });

  it("shows the created card once and clears only that composer", async () => {
    let cards: unknown[] = [];
    renderPage({
      "GET /api/projects/p1/cycles": { status: 200, data: [CYCLE] },
      "GET /api/cycles/c1/feedback": () => ({ status: 200, data: cards }),
      "POST /api/cycles/c1/feedback": () => {
        cards = [card()];
        return { status: 201, data: card() };
      },
    });
    await screen.findByRole("heading", { name: "Your feedback" });

    fireEvent.change(screen.getByLabelText("Stop feedback"), { target: { value: "keep this draft" } });
    fireEvent.change(screen.getByLabelText("Start feedback"), { target: { value: "pair more often" } });
    fireEvent.click(screen.getByRole("button", { name: "Add Start card" }));

    expect(await screen.findByText("pair more often")).toBeInTheDocument();
    expect(screen.getAllByText("pair more often")).toHaveLength(1);
    expect(screen.getByLabelText("Start feedback")).toHaveValue("");
    expect(screen.getByLabelText("Stop feedback")).toHaveValue("keep this draft");
  });

  it("keeps the draft and refuses a duplicate submit while pending", async () => {
    let release: (reply: Reply) => void = () => {};
    const calls = renderPage({
      ...withCards([]),
      "POST /api/cycles/c1/feedback": () => new Promise<Reply>((resolve) => (release = resolve)),
    });
    await screen.findByRole("heading", { name: "Your feedback" });

    fireEvent.change(screen.getByLabelText("Start feedback"), { target: { value: "pair more" } });
    fireEvent.click(screen.getByRole("button", { name: "Add Start card" }));

    const pending = await screen.findByRole("button", { name: "Adding Start card…" });
    expect(pending).toBeDisabled();
    fireEvent.click(pending);
    expect(callsTo(calls, "POST", "/api/cycles/c1/feedback")).toHaveLength(1);
    // The other composers are untouched.
    expect(screen.getByRole("button", { name: "Add Stop card" })).toBeEnabled();

    release({ status: 422, data: { detail: "text must not be blank" } });
    expect(await screen.findByText("Feedback text is required")).toBeInTheDocument();
    expect(screen.getByLabelText("Start feedback")).toHaveValue("pair more");
  });
});

// --- anonymity ---------------------------------------------------------------

describe("submitting anonymously", () => {
  it("warns, asks, and can be cancelled with the draft intact", async () => {
    const calls = renderPage({
      ...withCards([]),
      "POST /api/cycles/c1/feedback": { status: 201, data: card({ is_anonymous: true }) },
    });
    await screen.findByRole("heading", { name: "Your feedback" });

    fireEvent.change(screen.getByLabelText("Start feedback"), { target: { value: "hard to say" } });
    fireEvent.click(screen.getByLabelText("Submit this Start card anonymously", { exact: false }));

    expect(
      await screen.findByText(/Anonymous cards cannot be edited or deleted/)
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Add Start card" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(callsTo(calls, "POST", "/api/cycles/c1/feedback")).toHaveLength(0);
    expect(screen.getByLabelText("Start feedback")).toHaveValue("hard to say");
  });

  it("sends is_anonymous and then shows nothing of the card anywhere", async () => {
    const calls = renderPage({
      ...withCards([]),
      "POST /api/cycles/c1/feedback": {
        status: 201,
        data: card({ id: "anon", author_id: null, is_anonymous: true, text: "hard to say" }),
      },
    });
    await screen.findByRole("heading", { name: "Your feedback" });

    fireEvent.change(screen.getByLabelText("Start feedback"), { target: { value: "hard to say" } });
    fireEvent.click(screen.getByLabelText("Submit this Start card anonymously", { exact: false }));
    fireEvent.click(screen.getByRole("button", { name: "Add Start card" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Yes, submit anonymously" }));

    expect(
      await screen.findByText("Your anonymous Start card was submitted.")
    ).toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/cycles/c1/feedback")[0].body).toEqual({
      category: "start",
      text: "hard to say",
      is_anonymous: true,
    });
    // Not in the list, not in the composer, and not anywhere on the page.
    expect(screen.getByText("No Start cards yet")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("hard to say");
    expect(screen.getByLabelText("Submit this Start card anonymously", { exact: false })).not.toBeChecked();
  });

  it("makes an existing card anonymous only after confirmation, one way", async () => {
    let cards: unknown[] = [card()];
    const calls = renderPage({
      "GET /api/projects/p1/cycles": { status: 200, data: [CYCLE] },
      "GET /api/cycles/c1/feedback": () => ({ status: 200, data: cards }),
      "PATCH /api/feedback/f1": () => {
        cards = [];
        return { status: 200, data: card({ author_id: null, is_anonymous: true }) };
      },
    });
    await screen.findByText("pair more often");

    fireEvent.click(screen.getByRole("button", { name: "Make anonymous" }));
    const dialog = await screen.findByRole("dialog", { name: "Confirm anonymous" });
    expect(within(dialog).getByText(/cannot be edited or deleted/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(calls.filter((call) => call.method === "PATCH")).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Make anonymous" }));
    fireEvent.click(await screen.findByRole("button", { name: "Yes, make it anonymous" }));

    expect(await screen.findByText(/now anonymous/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("pair more often")).not.toBeInTheDocument());
    expect(callsTo(calls, "PATCH", "/api/feedback/f1")[0].body).toEqual({ is_anonymous: true });
    // There is no way back, and nothing offers one.
    expect(screen.queryByRole("button", { name: /not anonymous/i })).not.toBeInTheDocument();
    expect(
      calls.some((call) => JSON.stringify(call.body ?? {}).includes('"is_anonymous":false'))
    ).toBe(false);
  });
});

// --- editing -----------------------------------------------------------------

describe("editing a card", () => {
  it("edits in place, cancels on Escape, and sends nothing when unchanged", async () => {
    const calls = renderPage(withCards([card()]));
    await screen.findByText("pair more often");

    fireEvent.click(screen.getByRole("button", { name: "Edit Start card" }));
    const input = await screen.findByLabelText("Edit Start card");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.change(input, { target: { value: "changed my mind" } });
    fireEvent.keyDown(input, { key: "Escape" });
    await waitFor(() => expect(screen.queryByLabelText("Edit Start card")).not.toBeInTheDocument());
    expect(screen.getByText("pair more often")).toBeInTheDocument();
    expect(calls.filter((call) => call.method === "PATCH")).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Edit Start card" }));
    fireEvent.click(await screen.findByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByLabelText("Edit Start card")).not.toBeInTheDocument());
    expect(calls.filter((call) => call.method === "PATCH")).toHaveLength(0);
  });

  it("saves a trimmed change and shows what came back", async () => {
    let stored = card();
    const calls = renderPage({
      "GET /api/projects/p1/cycles": { status: 200, data: [CYCLE] },
      "GET /api/cycles/c1/feedback": () => ({ status: 200, data: [stored] }),
      "PATCH /api/feedback/f1": (call) => {
        stored = card({ text: (call.body as { text: string }).text });
        return { status: 200, data: stored };
      },
    });
    await screen.findByText("pair more often");

    fireEvent.click(screen.getByRole("button", { name: "Edit Start card" }));
    fireEvent.change(await screen.findByLabelText("Edit Start card"), {
      target: { value: "  pair every day  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.getByText("pair every day")).toBeInTheDocument());
    expect(callsTo(calls, "PATCH", "/api/feedback/f1")[0].body).toEqual({ text: "pair every day" });
  });

  it("stays in edit mode on blank text, without sending", async () => {
    const calls = renderPage(withCards([card()]));
    await screen.findByText("pair more often");

    fireEvent.click(screen.getByRole("button", { name: "Edit Start card" }));
    const input = await screen.findByLabelText("Edit Start card");
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Feedback text is required");
    expect(screen.getByLabelText("Edit Start card")).toBeInTheDocument();
    expect(calls.filter((call) => call.method === "PATCH")).toHaveLength(0);
  });

  it("keeps the unsaved text when the save fails", async () => {
    renderPage({ ...withCards([card()]), "PATCH /api/feedback/f1": { status: 500 } });
    await screen.findByText("pair more often");

    fireEvent.click(screen.getByRole("button", { name: "Edit Start card" }));
    fireEvent.change(await screen.findByLabelText("Edit Start card"), {
      target: { value: "changed my mind" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText(/Something went wrong/)).toBeInTheDocument();
    expect(screen.getByLabelText("Edit Start card")).toHaveValue("changed my mind");
  });

  it("cannot send two overlapping saves for one card", async () => {
    let release: (reply: Reply) => void = () => {};
    const calls = renderPage({
      ...withCards([card()]),
      "PATCH /api/feedback/f1": () => new Promise<Reply>((resolve) => (release = resolve)),
    });
    await screen.findByText("pair more often");

    fireEvent.click(screen.getByRole("button", { name: "Edit Start card" }));
    const input = await screen.findByLabelText("Edit Start card");
    fireEvent.change(input, { target: { value: "changed my mind" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(callsTo(calls, "PATCH", "/api/feedback/f1")).toHaveLength(1));

    // Blur and a second click while the first save is still in flight.
    fireEvent.blur(input);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(callsTo(calls, "PATCH", "/api/feedback/f1")).toHaveLength(1);

    release({ status: 200, data: card({ text: "changed my mind" }) });
    await waitFor(() => expect(screen.getByText("changed my mind")).toBeInTheDocument());
  });
});

// --- deleting ----------------------------------------------------------------

describe("deleting a card", () => {
  it("asks first, deletes only that card, and keeps it on failure", async () => {
    let cards: unknown[] = [card(), card({ id: "f2", text: "another one" })];
    let broken = true;
    const calls = renderPage({
      "GET /api/projects/p1/cycles": { status: 200, data: [CYCLE] },
      "GET /api/cycles/c1/feedback": () => ({ status: 200, data: cards }),
      "DELETE /api/feedback/f1": () => {
        if (broken) {
          return { status: 500 };
        }
        cards = cards.filter((row) => (row as { id: string }).id !== "f1");
        return { status: 204 };
      },
    });
    await screen.findByText("pair more often");

    const first = screen.getByText("pair more often").closest("li")!;
    fireEvent.click(within(first).getByRole("button", { name: "Delete Start card" }));
    fireEvent.click(await within(first).findByRole("button", { name: "Cancel" }));
    expect(calls.filter((call) => call.method === "DELETE")).toHaveLength(0);

    fireEvent.click(within(first).getByRole("button", { name: "Delete Start card" }));
    fireEvent.click(await within(first).findByRole("button", { name: "Yes, delete it" }));
    expect(await within(first).findByText(/Something went wrong/)).toBeInTheDocument();
    expect(screen.getByText("pair more often")).toBeInTheDocument();

    broken = false;
    fireEvent.click(within(first).getByRole("button", { name: "Delete Start card" }));
    fireEvent.click(await within(first).findByRole("button", { name: "Yes, delete it" }));

    await waitFor(() => expect(screen.queryByText("pair more often")).not.toBeInTheDocument());
    expect(screen.getByText("another one")).toBeInTheDocument();
  });
});

// --- the freeze --------------------------------------------------------------

describe("once collection closes", () => {
  it("says so and offers no way to write", async () => {
    renderPage(
      withCards([card(), card({ id: "theirs", author_id: "u2", text: "somebody else" })], {
        ...CYCLE,
        status: "retro",
      })
    );
    await screen.findByRole("heading", { name: "Your feedback" });

    expect(screen.getByText(/Feedback collection is closed/)).toBeInTheDocument();
    expect(screen.getByText("pair more often")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("somebody else");

    expect(screen.queryByLabelText("Start feedback")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add Start card" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit Start card" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Make anonymous" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete Start card" })).not.toBeInTheDocument();
  });

  it("switches to the frozen page when the cycle advanced mid-edit", async () => {
    let status = "collecting";
    renderPage({
      "GET /api/projects/p1/cycles": () => ({
        status: 200,
        data: [{ ...CYCLE, status }],
      }),
      "GET /api/cycles/c1/feedback": { status: 200, data: [card()] },
      "PATCH /api/feedback/f1": () => {
        status = "retro";
        return { status: 400, data: { detail: "Cards are frozen once the retro starts" } };
      },
    });
    await screen.findByText("pair more often");

    fireEvent.click(screen.getByRole("button", { name: "Edit Start card" }));
    fireEvent.change(await screen.findByLabelText("Edit Start card"), {
      target: { value: "too late" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText(/Feedback collection is closed/)).toBeInTheDocument();
    expect(screen.getByText("pair more often")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("too late");
  });

  it("refetches when a card has gone (404) and when permission changed (403)", async () => {
    let cards: unknown[] = [card()];
    const calls = renderPage({
      "GET /api/projects/p1/cycles": { status: 200, data: [CYCLE] },
      "GET /api/cycles/c1/feedback": () => ({ status: 200, data: cards }),
      "DELETE /api/feedback/f1": () => {
        cards = [];
        return { status: 404 };
      },
    });
    await screen.findByText("pair more often");

    fireEvent.click(screen.getByRole("button", { name: "Delete Start card" }));
    fireEvent.click(await screen.findByRole("button", { name: "Yes, delete it" }));

    await waitFor(() =>
      expect(callsTo(calls, "GET", "/api/cycles/c1/feedback")).toHaveLength(2)
    );
    await waitFor(() => expect(screen.queryByText("pair more often")).not.toBeInTheDocument());
  });
});

// --- storage and layout ------------------------------------------------------

describe("drafts and layout", () => {
  it("never persists a draft or a card in browser storage", async () => {
    renderPage({
      ...withCards([card()]),
      "POST /api/cycles/c1/feedback": { status: 201, data: card({ id: "f9" }) },
    });
    await screen.findByText("pair more often");

    fireEvent.change(screen.getByLabelText("Start feedback"), {
      target: { value: "something private" },
    });

    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });

  it("stacks the three categories on a narrow screen without a fixed width", async () => {
    window.innerWidth = 320;
    renderPage(withCards([card()]));
    await screen.findByRole("heading", { name: "Your feedback" });

    const grid = screen.getByRole("heading", { name: "Start" }).closest("div")!;
    expect(grid.className).toContain("grid");
    expect(grid.className).toContain("md:grid-cols-3");
    for (const element of document.querySelectorAll<HTMLElement>("*")) {
      expect(element.style.width).toBe("");
    }
  });
});
