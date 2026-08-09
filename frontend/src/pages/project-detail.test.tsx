/**
 * The project dashboard, and who may change its membership (#14).
 *
 * The privacy assertion is the one to keep: the page shows "N of M members
 * submitted" and must never name who has or has not. Everything else here is
 * about the four ways an invite or a removal can fail.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { AppRoutes } from "../App";
import { setAccessToken } from "../api/client";
import { ALICE, callsTo, installHttp, type Handler, type Reply } from "../test-utils/http";

const BOB = {
  user_id: "u2",
  display_name: "Bob",
  email: "bob@example.com",
  role: "member",
  joined_at: "2026-01-05T00:00:00Z",
};

const ALICE_MEMBER = {
  user_id: ALICE.id,
  display_name: "Alice",
  email: "alice@example.com",
  role: "facilitator",
  joined_at: "2026-01-01T00:00:00Z",
};

const SESSION: Record<string, Handler | Reply> = {
  "POST /api/auth/refresh": { status: 200, data: { access_token: "fresh" } },
  "GET /api/auth/me": { status: 200, data: ALICE },
};

function projectDoc(role = "facilitator") {
  return {
    id: "p1",
    name: "Team Alpha",
    description: "our retro project",
    members: [
      { user_id: ALICE.id, role, joined_at: "2026-01-01T00:00:00Z" },
      { user_id: "u2", role: "member", joined_at: "2026-01-05T00:00:00Z" },
    ],
    created_at: "2026-02-01T00:00:00Z",
    created_by: ALICE.id,
  };
}

const EMPTY_DASHBOARD = {
  members: [ALICE_MEMBER, BOB],
  current_cycle: null,
  past_retros: [],
  open_actions: [],
};

const FULL_DASHBOARD = {
  members: [ALICE_MEMBER, BOB],
  current_cycle: {
    id: "c1",
    status: "retro",
    created_at: "2026-02-01T00:00:00Z",
    closed_at: null,
    progress: { submitted_members: 1, total_members: 2 },
    retro: { id: "r1", phase: "cluster" },
  },
  past_retros: [
    {
      id: "r0",
      cycle_id: "c0",
      phase: "done",
      created_at: "2026-01-01T00:00:00Z",
      closed_at: "2026-01-20T00:00:00Z",
    },
  ],
  open_actions: [
    {
      id: "a1",
      retro_id: "r0",
      description: "Pair on the flaky test",
      owner_id: "u2",
      owner: "Bob",
      due_date: "2026-03-01T00:00:00Z",
      status: "open",
    },
    {
      id: "a2",
      retro_id: "r0",
      description: "Nobody yet",
      owner_id: null,
      owner: null,
      due_date: null,
      status: "open",
    },
  ],
};

function renderDetail(routes: Record<string, Handler | Reply>) {
  const calls = installHttp({ ...SESSION, ...routes });
  render(
    <MemoryRouter initialEntries={["/projects/p1"]}>
      <AppRoutes />
    </MemoryRouter>
  );
  return calls;
}

beforeEach(() => {
  setAccessToken(null);
});

// --- reading it --------------------------------------------------------------

describe("the dashboard", () => {
  it("loads, then shows every section", async () => {
    renderDetail({
      "GET /api/projects/p1": { status: 200, data: projectDoc() },
      "GET /api/projects/p1/dashboard": { status: 200, data: FULL_DASHBOARD },
    });

    expect(await screen.findByText("Loading this project…")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Team Alpha" })).toBeInTheDocument();

    expect(screen.getByText("our retro project")).toBeInTheDocument();
    expect(screen.getByText(/You are a facilitator/)).toBeInTheDocument();
    expect(screen.getByText("Status: retro")).toBeInTheDocument();
    expect(screen.getByText("1 of 2 members submitted")).toBeInTheDocument();
    expect(screen.getByText(/Retrospective phase: cluster/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the retrospective" })).toHaveAttribute(
      "href",
      "/retros/r1"
    );
    expect(screen.getByRole("link", { name: /Retrospective of/ })).toHaveAttribute(
      "href",
      "/retros/r0/summary"
    );
    expect(screen.getByText("Owner: Bob")).toBeInTheDocument();
    expect(screen.getByText("Owner: Unassigned")).toBeInTheDocument();
    expect(screen.getByText("Due: No due date")).toBeInTheDocument();
    expect(screen.queryByText(/Invalid Date/)).not.toBeInTheDocument();
  });

  it("links to feedback while a cycle is collecting, and not otherwise", async () => {
    renderDetail({
      "GET /api/projects/p1": { status: 200, data: projectDoc() },
      "GET /api/projects/p1/dashboard": {
        status: 200,
        data: {
          ...FULL_DASHBOARD,
          current_cycle: {
            ...FULL_DASHBOARD.current_cycle,
            status: "collecting",
            retro: null,
          },
        },
      },
    });

    expect(await screen.findByRole("link", { name: "Give feedback" })).toHaveAttribute(
      "href",
      "/projects/p1/feedback"
    );
    expect(screen.getByText("No retrospective started")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open the retrospective" })).not.toBeInTheDocument();
  });

  it("has an empty state for every section rather than a blank region", async () => {
    renderDetail({
      "GET /api/projects/p1": { status: 200, data: projectDoc() },
      "GET /api/projects/p1/dashboard": {
        status: 200,
        data: { ...EMPTY_DASHBOARD, members: [] },
      },
    });

    expect(await screen.findByText("No active cycle")).toBeInTheDocument();
    expect(screen.getByText("No past retrospectives")).toBeInTheDocument();
    expect(screen.getByText("No open actions")).toBeInTheDocument();
    expect(screen.getByText("No members")).toBeInTheDocument();
  });

  it("names members rather than showing their ids", async () => {
    renderDetail({
      "GET /api/projects/p1": { status: 200, data: projectDoc() },
      "GET /api/projects/p1/dashboard": { status: 200, data: EMPTY_DASHBOARD },
    });

    const row = (await screen.findByText("Bob")).closest("li")!;
    expect(within(row).getByText("bob@example.com")).toBeInTheDocument();
    expect(within(row).getByText(/Member · joined/)).toBeInTheDocument();
    expect(row.textContent).not.toContain("u2");
  });

  it("never says who has and has not submitted", async () => {
    renderDetail({
      "GET /api/projects/p1": { status: 200, data: projectDoc() },
      "GET /api/projects/p1/dashboard": { status: 200, data: FULL_DASHBOARD },
    });
    await screen.findByRole("heading", { name: "Team Alpha" });

    expect(screen.getByText("1 of 2 members submitted")).toBeInTheDocument();
    expect(screen.queryByText(/has not submitted/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/waiting on/i)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/submitted:\s*(Alice|Bob)/);
  });

  it("shows access denied for 403 and not found for 404, with a way back", async () => {
    renderDetail({
      "GET /api/projects/p1": { status: 403 },
      "GET /api/projects/p1/dashboard": { status: 403 },
    });
    expect(await screen.findByText(/do not have access/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to projects" })).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("Team Alpha");

    setAccessToken(null);
    renderDetail({
      "GET /api/projects/p1": { status: 404 },
      "GET /api/projects/p1/dashboard": { status: 404 },
    });
    expect(await screen.findByText("Project not found.")).toBeInTheDocument();
  });

  it("offers Retry on any other failure", async () => {
    let status = 500;
    const calls = renderDetail({
      "GET /api/projects/p1": () =>
        status === 500 ? { status } : { status: 200, data: projectDoc() },
      "GET /api/projects/p1/dashboard": () =>
        status === 500 ? { status } : { status: 200, data: EMPTY_DASHBOARD },
    });

    expect(await screen.findByText(/could not load this project/)).toBeInTheDocument();
    status = 200;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("heading", { name: "Team Alpha" })).toBeInTheDocument();
    expect(callsTo(calls, "GET", "/api/projects/p1/dashboard")).toHaveLength(2);
  });
});

// --- who may change membership ------------------------------------------------

describe("facilitator controls", () => {
  it("are hidden from a plain member", async () => {
    renderDetail({
      "GET /api/projects/p1": { status: 200, data: projectDoc("member") },
      "GET /api/projects/p1/dashboard": { status: 200, data: EMPTY_DASHBOARD },
    });
    await screen.findByRole("heading", { name: "Team Alpha" });

    expect(screen.getByText("Bob")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Remove/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Email")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Role")).not.toBeInTheDocument();
  });

  it("never offer to remove the signed-in facilitator", async () => {
    renderDetail({
      "GET /api/projects/p1": { status: 200, data: projectDoc() },
      "GET /api/projects/p1/dashboard": { status: 200, data: EMPTY_DASHBOARD },
    });
    await screen.findByRole("heading", { name: "Team Alpha" });

    expect(screen.getByRole("button", { name: "Remove Bob" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove Alice" })).not.toBeInTheDocument();
  });
});

describe("inviting a member", () => {
  it("validates the email before sending", async () => {
    const calls = renderDetail({
      "GET /api/projects/p1": { status: 200, data: projectDoc() },
      "GET /api/projects/p1/dashboard": { status: 200, data: EMPTY_DASHBOARD },
    });
    await screen.findByRole("heading", { name: "Team Alpha" });

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "not-an-email" } });
    fireEvent.click(screen.getByRole("button", { name: "Invite" }));

    expect(await screen.findByText("Enter a valid email address")).toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/projects/p1/members")).toHaveLength(0);
  });

  it("sends the chosen role, refetches, and resets", async () => {
    let members = [ALICE_MEMBER, BOB];
    const calls = renderDetail({
      "GET /api/projects/p1": { status: 200, data: projectDoc() },
      "GET /api/projects/p1/dashboard": () => ({
        status: 200,
        data: { ...EMPTY_DASHBOARD, members },
      }),
      "POST /api/projects/p1/members": () => {
        members = [
          ...members,
          {
            user_id: "u3",
            display_name: "Carol",
            email: "carol@example.com",
            role: "facilitator",
            joined_at: "2026-02-02T00:00:00Z",
          },
        ];
        return { status: 201, data: [] };
      },
    });
    await screen.findByRole("heading", { name: "Team Alpha" });

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "carol@example.com" } });
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "facilitator" } });
    fireEvent.click(screen.getByRole("button", { name: "Invite" }));

    expect(await screen.findByText("Carol")).toBeInTheDocument();
    expect(screen.getAllByText("Carol")).toHaveLength(1);
    expect(callsTo(calls, "POST", "/api/projects/p1/members")[0].body).toEqual({
      email: "carol@example.com",
      role: "facilitator",
    });
    expect(callsTo(calls, "GET", "/api/projects/p1/dashboard")).toHaveLength(2);
    expect(screen.getByLabelText("Email")).toHaveValue("");
    expect(screen.getByLabelText("Role")).toHaveValue("member");
  });

  it("defaults to member", async () => {
    const calls = renderDetail({
      "GET /api/projects/p1": { status: 200, data: projectDoc() },
      "GET /api/projects/p1/dashboard": { status: 200, data: EMPTY_DASHBOARD },
      "POST /api/projects/p1/members": { status: 201, data: [] },
    });
    await screen.findByRole("heading", { name: "Team Alpha" });

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "carol@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Invite" }));

    await waitFor(() =>
      expect(callsTo(calls, "POST", "/api/projects/p1/members")).toHaveLength(1)
    );
    expect(callsTo(calls, "POST", "/api/projects/p1/members")[0].body).toEqual({
      email: "carol@example.com",
      role: "member",
    });
  });

  it("names 404 and 409, and hides the controls on 403", async () => {
    let status = 404;
    renderDetail({
      "GET /api/projects/p1": { status: 200, data: projectDoc() },
      "GET /api/projects/p1/dashboard": { status: 200, data: EMPTY_DASHBOARD },
      "POST /api/projects/p1/members": () => ({ status }),
    });
    await screen.findByRole("heading", { name: "Team Alpha" });

    const invite = async (email: string) => {
      fireEvent.change(screen.getByLabelText("Email"), { target: { value: email } });
      fireEvent.click(screen.getByRole("button", { name: "Invite" }));
    };

    await invite("nobody@example.com");
    expect(await screen.findByText("No user with that email")).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveValue("nobody@example.com");

    status = 409;
    await invite("bob@example.com");
    expect(await screen.findByText("Already a project member")).toBeInTheDocument();

    status = 403;
    await invite("carol@example.com");
    expect(await screen.findByText(/no longer a facilitator/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByLabelText("Email")).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Remove Bob" })).not.toBeInTheDocument();
  });

  it("refuses a duplicate submit while pending", async () => {
    let release: (reply: Reply) => void = () => {};
    const calls = renderDetail({
      "GET /api/projects/p1": { status: 200, data: projectDoc() },
      "GET /api/projects/p1/dashboard": { status: 200, data: EMPTY_DASHBOARD },
      "POST /api/projects/p1/members": () => new Promise<Reply>((resolve) => (release = resolve)),
    });
    await screen.findByRole("heading", { name: "Team Alpha" });

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "carol@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Invite" }));

    const pending = await screen.findByRole("button", { name: "Inviting…" });
    expect(pending).toBeDisabled();
    fireEvent.click(pending);
    expect(callsTo(calls, "POST", "/api/projects/p1/members")).toHaveLength(1);
    release({ status: 201, data: [] });
    await screen.findByRole("button", { name: "Invite" });
  });
});

describe("removing a member", () => {
  it("asks first, and sends nothing when cancelled", async () => {
    const calls = renderDetail({
      "GET /api/projects/p1": { status: 200, data: projectDoc() },
      "GET /api/projects/p1/dashboard": { status: 200, data: EMPTY_DASHBOARD },
    });
    await screen.findByRole("heading", { name: "Team Alpha" });

    fireEvent.click(screen.getByRole("button", { name: "Remove Bob" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Remove Bob from this project/)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(calls.filter((call) => call.method === "DELETE")).toHaveLength(0);
    expect(screen.getByText("Bob")).toBeInTheDocument();
  });

  it("removes on confirmation and refetches", async () => {
    let members = [ALICE_MEMBER, BOB];
    const calls = renderDetail({
      "GET /api/projects/p1": { status: 200, data: projectDoc() },
      "GET /api/projects/p1/dashboard": () => ({
        status: 200,
        data: { ...EMPTY_DASHBOARD, members },
      }),
      "DELETE /api/projects/p1/members/u2": () => {
        members = [ALICE_MEMBER];
        return { status: 200, data: [] };
      },
    });
    await screen.findByRole("heading", { name: "Team Alpha" });

    fireEvent.click(screen.getByRole("button", { name: "Remove Bob" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, remove Bob" }));

    await waitFor(() => expect(screen.queryByText("Bob")).not.toBeInTheDocument());
    expect(callsTo(calls, "DELETE", "/api/projects/p1/members/u2")).toHaveLength(1);
    expect(callsTo(calls, "GET", "/api/projects/p1/dashboard")).toHaveLength(2);
  });

  it("keeps the member visible when the removal fails", async () => {
    renderDetail({
      "GET /api/projects/p1": { status: 200, data: projectDoc() },
      "GET /api/projects/p1/dashboard": { status: 200, data: EMPTY_DASHBOARD },
      "DELETE /api/projects/p1/members/u2": { status: 500 },
    });
    await screen.findByRole("heading", { name: "Team Alpha" });

    fireEvent.click(screen.getByRole("button", { name: "Remove Bob" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, remove Bob" }));

    expect(await screen.findByText(/Something went wrong/)).toBeInTheDocument();
    expect(screen.getByText("Bob")).toBeInTheDocument();
  });

  it("reports an already-absent member after a 404 and refetches", async () => {
    let members = [ALICE_MEMBER, BOB];
    const calls = renderDetail({
      "GET /api/projects/p1": { status: 200, data: projectDoc() },
      "GET /api/projects/p1/dashboard": () => ({
        status: 200,
        data: { ...EMPTY_DASHBOARD, members },
      }),
      "DELETE /api/projects/p1/members/u2": () => {
        members = [ALICE_MEMBER];
        return { status: 404 };
      },
    });
    await screen.findByRole("heading", { name: "Team Alpha" });

    fireEvent.click(screen.getByRole("button", { name: "Remove Bob" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, remove Bob" }));

    expect(await screen.findByText(/no longer on this project/)).toBeInTheDocument();
    expect(callsTo(calls, "GET", "/api/projects/p1/dashboard")).toHaveLength(2);
    await waitFor(() => expect(screen.queryByText("Bob")).not.toBeInTheDocument());
  });

  it("blocks the controls after a 403", async () => {
    renderDetail({
      "GET /api/projects/p1": { status: 200, data: projectDoc() },
      "GET /api/projects/p1/dashboard": { status: 200, data: EMPTY_DASHBOARD },
      "DELETE /api/projects/p1/members/u2": { status: 403 },
    });
    await screen.findByRole("heading", { name: "Team Alpha" });

    fireEvent.click(screen.getByRole("button", { name: "Remove Bob" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, remove Bob" }));

    expect(await screen.findByText(/no longer a facilitator/)).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Remove Bob" })).not.toBeInTheDocument()
    );
    expect(screen.getByText("Bob")).toBeInTheDocument();
  });
});

// --- narrow viewports ---------------------------------------------------------

describe("at 320px", () => {
  it("uses no fixed width that could force the page to scroll sideways", async () => {
    window.innerWidth = 320;
    renderDetail({
      "GET /api/projects/p1": { status: 200, data: projectDoc() },
      "GET /api/projects/p1/dashboard": { status: 200, data: FULL_DASHBOARD },
    });
    await screen.findByRole("heading", { name: "Team Alpha" });

    // jsdom does not lay anything out, so this is what can honestly be checked:
    // nothing sets a width in pixels, and the two places long unbroken text
    // arrives from the server — the project name and an action description —
    // are told to wrap.
    for (const element of document.querySelectorAll<HTMLElement>("*")) {
      expect(element.style.width).toBe("");
      expect(element.style.minWidth).toBe("");
      expect(element.className).not.toMatch(/\bw-\[\d+px\]/);
    }
    expect(screen.getByRole("heading", { name: "Team Alpha" }).className).toContain("break-words");
    expect(screen.getByText("Pair on the flaky test").className).toContain("break-words");
  });
});
