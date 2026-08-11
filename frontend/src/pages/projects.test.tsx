/**
 * The project list and the create form (#14).
 *
 * Rendered through `AppRoutes` with a live session, so every test goes through
 * #13's real guards and API client rather than a component in isolation.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { AppRoutes } from "../App";
import { setAccessToken } from "../api/client";
import { ALICE, callsTo, installHttp, type Handler, type Reply } from "../test-utils/http";

const SESSION: Record<string, Handler | Reply> = {
  "POST /api/auth/refresh": { status: 200, data: { access_token: "fresh" } },
  "GET /api/auth/me": { status: 200, data: ALICE },
  // #45: every render of this page also loads "My open actions". Empty by
  // default so tests that predate that section don't have to know about it.
  "GET /api/actions/mine": { status: 200, data: [] },
};

function project(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "p1",
    name: "Team Alpha",
    description: "our retro project",
    members: [{ user_id: ALICE.id, role: "facilitator", joined_at: "2026-01-01T00:00:00Z" }],
    created_at: "2026-02-01T00:00:00Z",
    created_by: ALICE.id,
    archived_at: null,
    ...overrides,
  };
}

function renderAt(path: string, routes: Record<string, Handler | Reply>) {
  const calls = installHttp({ ...SESSION, ...routes });
  render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>
  );
  return calls;
}

beforeEach(() => {
  setAccessToken(null);
});

describe("the project list", () => {
  it("shows a loading state and then every project, newest first", async () => {
    renderAt("/projects", {
      "GET /api/projects": {
        status: 200,
        data: [
          project({ id: "old", name: "Older", created_at: "2026-01-01T00:00:00Z" }),
          project({ id: "new", name: "Newer", created_at: "2026-03-01T00:00:00Z" }),
          project({ id: "tie-b", name: "Beta", created_at: "2026-03-01T00:00:00Z" }),
        ],
      },
    });

    expect(await screen.findByText("Loading your projects…")).toBeInTheDocument();

    // "My open actions" (#45) is also an <h2>, mounted before the project
    // list's own fetch resolves — wait for a project heading specifically
    // before reading "every" level-2 heading off the page.
    await screen.findByRole("heading", { name: "Beta" });
    const headings = screen.getAllByRole("heading", { level: 2 });
    const projectHeadings = headings.filter((heading) => heading.textContent !== "My open actions");
    expect(projectHeadings.map((heading) => heading.textContent)).toEqual(["Beta", "Newer", "Older"]);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("shows name, description, member count and the viewer's role", async () => {
    renderAt("/projects", {
      "GET /api/projects": {
        status: 200,
        data: [
          project({
            members: [
              { user_id: ALICE.id, role: "member", joined_at: "2026-01-01T00:00:00Z" },
              { user_id: "u2", role: "facilitator", joined_at: "2026-01-01T00:00:00Z" },
            ],
          }),
        ],
      },
    });

    const card = (await screen.findByRole("heading", { name: "Team Alpha" })).closest("li")!;
    expect(within(card).getByText("our retro project")).toBeInTheDocument();
    expect(within(card).getByText(/2 members/)).toBeInTheDocument();
    expect(within(card).getByText(/you are a member/)).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Team Alpha" })).toHaveAttribute(
      "href",
      "/projects/p1"
    );
  });

  it("says so when there are none, without a false loading state", async () => {
    renderAt("/projects", { "GET /api/projects": { status: 200, data: [] } });

    expect(await screen.findByText(/No projects yet/)).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New Project" })).toBeInTheDocument();
  });

  it("shows only active projects by default, archived ones under their own tab (#43)", async () => {
    const calls = renderAt("/projects", {
      "GET /api/projects": {
        status: 200,
        data: [
          project({ id: "a1", name: "Active One" }),
          project({ id: "z1", name: "Archived One", archived_at: "2026-04-01T00:00:00Z" }),
        ],
      },
    });

    await screen.findByRole("heading", { name: "Active One" });
    expect(screen.queryByRole("heading", { name: "Archived One" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Archived" }));

    expect(await screen.findByRole("heading", { name: "Archived One" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Active One" })).not.toBeInTheDocument();
    // Switching tabs is a client-side filter of what's already loaded, not a refetch.
    expect(callsTo(calls, "GET", "/api/projects")).toHaveLength(1);
  });

  it("has a distinct empty state per tab", async () => {
    renderAt("/projects", {
      "GET /api/projects": { status: 200, data: [project({ id: "a1" })] },
    });

    await screen.findByRole("heading", { name: "Team Alpha" });
    expect(screen.getByRole("button", { name: "New Project" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Archived" }));

    expect(await screen.findByText("No archived projects.")).toBeInTheDocument();
    expect(screen.queryByText(/No projects yet/)).not.toBeInTheDocument();
    // Creating one is not tied to either tab.
    expect(screen.getByRole("button", { name: "New Project" })).toBeInTheDocument();
  });

  it("offers Retry after a failure, and recovers", async () => {
    let status = 500;
    const calls = renderAt("/projects", {
      "GET /api/projects": () => (status === 500 ? { status } : { status: 200, data: [project()] }),
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("could not load your projects");
    status = 200;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("heading", { name: "Team Alpha" })).toBeInTheDocument();
    expect(callsTo(calls, "GET", "/api/projects")).toHaveLength(2);
  });
});

// --- my open actions (#45) ----------------------------------------------------

function myAction(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "a1",
    project_id: "p1",
    project_name: "Team Alpha",
    retro_id: "r1",
    description: "Write the runbook",
    due_date: null,
    ...overrides,
  };
}

describe("my open actions", () => {
  it("shows every action assigned to the caller, across projects", async () => {
    renderAt("/projects", {
      "GET /api/projects": { status: 200, data: [] },
      "GET /api/actions/mine": {
        status: 200,
        data: [
          myAction({ id: "a1", description: "From Alpha" }),
          myAction({ id: "a2", description: "From Beta", project_id: "p2", project_name: "Team Beta" }),
        ],
      },
    });

    expect(await screen.findByRole("heading", { name: "My open actions" })).toBeInTheDocument();
    const alpha = (await screen.findByText("From Alpha")).closest("li")!;
    expect(within(alpha).getByRole("link", { name: "Team Alpha" })).toHaveAttribute(
      "href",
      "/projects/p1"
    );
    expect(within(alpha).getByRole("link", { name: "From this retrospective" })).toHaveAttribute(
      "href",
      "/retros/r1"
    );

    const beta = (await screen.findByText("From Beta")).closest("li")!;
    expect(within(beta).getByRole("link", { name: "Team Beta" })).toHaveAttribute(
      "href",
      "/projects/p2"
    );
  });

  it("has its own empty state, independent of the projects list", async () => {
    renderAt("/projects", {
      "GET /api/projects": { status: 200, data: [] },
      "GET /api/actions/mine": { status: 200, data: [] },
    });

    expect(await screen.findByText("No open actions assigned to you.")).toBeInTheDocument();
    expect(screen.getByText(/No projects yet/)).toBeInTheDocument();
  });

  it("marks an action done and drops it from the list", async () => {
    const calls = renderAt("/projects", {
      "GET /api/projects": { status: 200, data: [] },
      "GET /api/actions/mine": { status: 200, data: [myAction()] },
      "PATCH /api/retros/r1/actions/a1": { status: 200, data: { ...myAction(), status: "done" } },
    });

    await screen.findByText("Write the runbook");
    fireEvent.click(screen.getByRole("button", { name: "Mark done" }));

    await waitFor(() =>
      expect(screen.queryByText("Write the runbook")).not.toBeInTheDocument()
    );
    expect(callsTo(calls, "PATCH", "/api/retros/r1/actions/a1")[0].body).toEqual({
      status: "done",
    });
    expect(await screen.findByText("No open actions assigned to you.")).toBeInTheDocument();
  });

  it("shows an error and keeps the action when marking done fails", async () => {
    renderAt("/projects", {
      "GET /api/projects": { status: 200, data: [] },
      "GET /api/actions/mine": { status: 200, data: [myAction()] },
      "PATCH /api/retros/r1/actions/a1": { status: 403 },
    });

    await screen.findByText("Write the runbook");
    fireEvent.click(screen.getByRole("button", { name: "Mark done" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("no longer make this change");
    expect(screen.getByText("Write the runbook")).toBeInTheDocument();
  });

  it("offers Retry on failure, independent of the projects list's own error", async () => {
    let status = 500;
    const calls = renderAt("/projects", {
      "GET /api/projects": { status: 200, data: [] },
      "GET /api/actions/mine": () =>
        status === 500 ? { status } : { status: 200, data: [myAction()] },
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("could not load your actions");
    status = 200;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("Write the runbook")).toBeInTheDocument();
    expect(callsTo(calls, "GET", "/api/actions/mine")).toHaveLength(2);
  });
});

describe("creating a project", () => {
  it("blocks a blank name without sending anything", async () => {
    const calls = renderAt("/projects", { "GET /api/projects": { status: 200, data: [] } });
    await screen.findByText(/No projects yet/);

    fireEvent.click(screen.getByRole("button", { name: "New Project" }));
    fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    expect(await screen.findByText("Project name is required")).toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/projects")).toHaveLength(0);
  });

  it("sends trimmed values and goes to the new project", async () => {
    const calls = renderAt("/projects", {
      "GET /api/projects": { status: 200, data: [] },
      "POST /api/projects": { status: 201, data: project({ id: "p9" }) },
      "GET /api/projects/p9": { status: 200, data: project({ id: "p9" }) },
      "GET /api/projects/p9/dashboard": {
        status: 200,
        data: { members: [], current_cycle: null, past_retros: [], open_actions: [] },
      },
    });
    await screen.findByText(/No projects yet/);

    fireEvent.click(screen.getByRole("button", { name: "New Project" }));
    fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "  Team Alpha  " } });
    fireEvent.change(screen.getByLabelText("Description (optional)"), {
      target: { value: "  our retro project  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    expect(await screen.findByRole("heading", { name: "Team Alpha", level: 1 })).toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/projects")[0].body).toEqual({
      name: "Team Alpha",
      description: "our retro project",
    });
  });

  it("sends a null description when none was typed", async () => {
    const calls = renderAt("/projects", {
      "GET /api/projects": { status: 200, data: [] },
      "POST /api/projects": { status: 201, data: project({ id: "p9" }) },
      "GET /api/projects/p9": { status: 200, data: project({ id: "p9" }) },
      "GET /api/projects/p9/dashboard": {
        status: 200,
        data: { members: [], current_cycle: null, past_retros: [], open_actions: [] },
      },
    });
    await screen.findByText(/No projects yet/);

    fireEvent.click(screen.getByRole("button", { name: "New Project" }));
    fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "Team Alpha" } });
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    await waitFor(() => expect(callsTo(calls, "POST", "/api/projects")).toHaveLength(1));
    expect(callsTo(calls, "POST", "/api/projects")[0].body).toEqual({
      name: "Team Alpha",
      description: null,
    });
  });

  it("keeps the typed values and refuses a duplicate submit on failure", async () => {
    let release: (reply: Reply) => void = () => {};
    const calls = renderAt("/projects", {
      "GET /api/projects": { status: 200, data: [] },
      "POST /api/projects": () => new Promise<Reply>((resolve) => (release = resolve)),
    });
    await screen.findByText(/No projects yet/);

    fireEvent.click(screen.getByRole("button", { name: "New Project" }));
    fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "Team Alpha" } });
    fireEvent.change(screen.getByLabelText("Description (optional)"), {
      target: { value: "keep me" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    const pending = await screen.findByRole("button", { name: "Creating…" });
    expect(pending).toBeDisabled();
    fireEvent.click(pending);
    expect(callsTo(calls, "POST", "/api/projects")).toHaveLength(1);

    release({ status: 422, data: { detail: "name must not be blank" } });

    expect(await screen.findByText(/Something went wrong/)).toBeInTheDocument();
    expect(screen.getByLabelText("Project name")).toHaveValue("Team Alpha");
    expect(screen.getByLabelText("Description (optional)")).toHaveValue("keep me");
    expect(screen.queryByText(/name must not be blank/)).not.toBeInTheDocument();
  });
});
