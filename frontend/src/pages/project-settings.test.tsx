/**
 * Rename, archive and role changes on the project page (#32).
 *
 * The confirmation wording is asserted, not just its presence: archiving is the
 * only "remove a project" this product has, and a user pressing it needs to
 * know from the dialog that nothing is being destroyed.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { AppRoutes } from "../App";
import { setAccessToken } from "../api/client";
import { ALICE, callsTo, installHttp, type Call, type Handler, type Reply } from "../test-utils/http";

const BOB_ID = "u2";

const SESSION: Record<string, Handler | Reply> = {
  "POST /api/auth/refresh": { status: 200, data: { access_token: "fresh" } },
  "GET /api/auth/me": { status: 200, data: ALICE },
};

function projectDoc(overrides: Record<string, unknown> = {}) {
  return {
    id: "p1",
    name: "Team Alpha",
    description: "our retro project",
    members: [
      { user_id: ALICE.id, role: "facilitator", joined_at: "2026-01-01T00:00:00Z" },
      { user_id: BOB_ID, role: "member", joined_at: "2026-01-05T00:00:00Z" },
    ],
    created_at: "2026-02-01T00:00:00Z",
    created_by: ALICE.id,
    archived_at: null,
    ...overrides,
  };
}

const DASHBOARD = {
  members: [
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
      joined_at: "2026-01-05T00:00:00Z",
    },
  ],
  current_cycle: null,
  past_retros: [],
  open_actions: [],
};

function renderDetail(routes: Record<string, Handler | Reply>): Call[] {
  const calls = installHttp({
    ...SESSION,
    "GET /api/projects/p1": { status: 200, data: projectDoc() },
    "GET /api/projects/p1/dashboard": { status: 200, data: DASHBOARD },
    ...routes,
  });
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

describe("renaming", () => {
  it("sends trimmed values and refetches", async () => {
    const calls = renderDetail({
      "PATCH /api/projects/p1": { status: 200, data: projectDoc({ name: "Team Beta" }) },
    });
    await screen.findByRole("heading", { name: "Project settings" });

    fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "  Team Beta  " } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "  words  " } });
    fireEvent.click(screen.getByRole("button", { name: "Save project details" }));

    await waitFor(() => expect(callsTo(calls, "PATCH", "/api/projects/p1")).toHaveLength(1));
    expect(callsTo(calls, "PATCH", "/api/projects/p1")[0].body).toEqual({
      name: "Team Beta",
      description: "words",
    });
    expect(callsTo(calls, "GET", "/api/projects/p1").length).toBeGreaterThan(1);
  });

  it("blocks a blank name without sending", async () => {
    const calls = renderDetail({});
    await screen.findByRole("heading", { name: "Project settings" });

    fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "Save project details" }));

    expect(await screen.findByText("Project name is required")).toBeInTheDocument();
    expect(callsTo(calls, "PATCH", "/api/projects/p1")).toHaveLength(0);
  });

  it("is invisible to a plain member", async () => {
    renderDetail({
      "GET /api/projects/p1": {
        status: 200,
        data: projectDoc({
          members: [
            { user_id: ALICE.id, role: "member", joined_at: "2026-01-01T00:00:00Z" },
            { user_id: BOB_ID, role: "facilitator", joined_at: "2026-01-05T00:00:00Z" },
          ],
        }),
      },
    });
    await screen.findByRole("heading", { name: "Team Alpha" });

    expect(screen.queryByRole("heading", { name: "Project settings" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Archive this project" })).not.toBeInTheDocument();
  });
});

describe("archiving", () => {
  it("asks first, says nothing is deleted, and can be cancelled", async () => {
    const calls = renderDetail({});
    await screen.findByRole("heading", { name: "Project settings" });

    fireEvent.click(screen.getByRole("button", { name: "Archive this project" }));
    const dialog = await screen.findByRole("dialog", { name: "Archive this project" });
    expect(within(dialog).getByText(/Nothing is deleted/)).toBeInTheDocument();
    expect(within(dialog).getByText(/a facilitator can restore it/)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(callsTo(calls, "PATCH", "/api/projects/p1/archive")).toHaveLength(0);
  });

  it("archives, then shows a read-only project with a restore control", async () => {
    let archived: string | null = null;
    const calls = renderDetail({
      "GET /api/projects/p1": () => ({
        status: 200,
        data: projectDoc({ archived_at: archived }),
      }),
      "PATCH /api/projects/p1/archive": (call) => {
        archived = (call.body as { archived: boolean }).archived
          ? "2026-03-01T00:00:00Z"
          : null;
        return { status: 200, data: projectDoc({ archived_at: archived }) };
      },
    });
    await screen.findByRole("heading", { name: "Project settings" });

    fireEvent.click(screen.getByRole("button", { name: "Archive this project" }));
    fireEvent.click(await screen.findByRole("button", { name: "Yes, archive it" }));

    expect(await screen.findByText(/This project is archived/)).toBeInTheDocument();
    expect(callsTo(calls, "PATCH", "/api/projects/p1/archive")[0].body).toEqual({
      archived: true,
    });
    // Read-only: every write control is gone, and the page still reads.
    expect(screen.queryByLabelText("Project name")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove Bob" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Role of Bob")).not.toBeInTheDocument();
    expect(screen.getByText("Bob")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Restore this project" }));
    await waitFor(() =>
      expect(screen.queryByText(/This project is archived/)).not.toBeInTheDocument()
    );
    expect(callsTo(calls, "PATCH", "/api/projects/p1/archive")[1].body).toEqual({
      archived: false,
    });
  });

  it("explains a refusal without claiming it worked", async () => {
    renderDetail({ "PATCH /api/projects/p1/archive": { status: 400 } });
    await screen.findByRole("heading", { name: "Project settings" });

    fireEvent.click(screen.getByRole("button", { name: "Archive this project" }));
    fireEvent.click(await screen.findByRole("button", { name: "Yes, archive it" }));

    expect(await screen.findByText(/not possible in the project's current state/)).toBeInTheDocument();
    expect(screen.queryByText(/This project is archived/)).not.toBeInTheDocument();
  });
});

describe("member roles", () => {
  it("promotes a member and refetches", async () => {
    const calls = renderDetail({
      "PATCH /api/projects/p1/members/u2": { status: 200, data: [] },
    });
    await screen.findByRole("heading", { name: "Project settings" });

    fireEvent.change(screen.getByLabelText("Role of Bob"), { target: { value: "facilitator" } });

    await waitFor(() =>
      expect(callsTo(calls, "PATCH", "/api/projects/p1/members/u2")).toHaveLength(1)
    );
    expect(callsTo(calls, "PATCH", "/api/projects/p1/members/u2")[0].body).toEqual({
      role: "facilitator",
    });
    expect(callsTo(calls, "GET", "/api/projects/p1/dashboard").length).toBeGreaterThan(1);
  });

  it("says why the last facilitator cannot be demoted", async () => {
    renderDetail({ [`PATCH /api/projects/p1/members/${ALICE.id}`]: { status: 400 } });
    await screen.findByRole("heading", { name: "Project settings" });

    fireEvent.change(screen.getByLabelText("Role of Alice"), { target: { value: "member" } });

    expect(
      await screen.findByText("A project must always have a facilitator.")
    ).toBeInTheDocument();
  });

  it("withdraws the controls when permission has changed", async () => {
    renderDetail({ "PATCH /api/projects/p1/members/u2": { status: 403 } });
    await screen.findByRole("heading", { name: "Project settings" });

    fireEvent.change(screen.getByLabelText("Role of Bob"), { target: { value: "facilitator" } });

    expect(await screen.findByText(/no longer a facilitator/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByLabelText("Role of Bob")).not.toBeInTheDocument());
  });

  it("is invisible to a plain member", async () => {
    renderDetail({
      "GET /api/projects/p1": {
        status: 200,
        data: projectDoc({
          members: [
            { user_id: ALICE.id, role: "member", joined_at: "2026-01-01T00:00:00Z" },
            { user_id: BOB_ID, role: "facilitator", joined_at: "2026-01-05T00:00:00Z" },
          ],
        }),
      },
    });
    await screen.findByRole("heading", { name: "Team Alpha" });

    expect(screen.queryByLabelText("Role of Bob")).not.toBeInTheDocument();
  });
});
