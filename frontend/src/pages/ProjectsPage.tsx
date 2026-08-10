import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import {
  createProject,
  listProjects,
  orderProjects,
  roleIn,
  type Project,
} from "../api/projects";
import { useAuth } from "../auth/AuthProvider";
import { formatDate } from "../lib/dates";

const GENERIC_FAILURE = "Something went wrong. Please try again.";

// #43: split on the one status the product already tracks (`archived_at`),
// client-side, so switching tabs is instant and never refetches.
type Tab = "active" | "archived";

export function ProjectsPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("active");
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const load = useCallback(async () => {
    setLoadError(null);
    setProjects(null);
    try {
      setProjects(orderProjects(await listProjects()));
    } catch {
      // Nothing from the response is rendered: the list either arrived or it
      // did not, and a raw error body on a dashboard helps nobody.
      setLoadError("We could not load your projects.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    setCreateError(null);
    if (!trimmed) {
      setNameError("Project name is required");
      return;
    }
    setNameError(null);

    setPending(true);
    try {
      const created = await createProject(trimmed, description.trim() || null);
      setCreating(false);
      navigate(`/projects/${created.id}`);
    } catch {
      // The form keeps what was typed. Retyping a description because the
      // server was briefly unhappy is the kind of thing that loses the entry.
      setCreateError(GENERIC_FAILURE);
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1>Projects</h1>
        <button
          type="button"
          onClick={() => setCreating((open) => !open)}
          className="rounded bg-gray-900 px-4 py-2 text-white"
        >
          New Project
        </button>
      </div>

      {creating && (
        <form onSubmit={onCreate} noValidate className="panel max-w-md space-y-3">
          <div>
            <label htmlFor="project-name">Project name</label>
            <input
              id="project-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              aria-invalid={nameError ? true : undefined}
              aria-describedby={nameError ? "project-name-error" : undefined}
            />
            {nameError && (
              <p id="project-name-error" role="alert">
                {nameError}
              </p>
            )}
          </div>

          <div>
            <label htmlFor="project-description">Description (optional)</label>
            <textarea
              id="project-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </div>

          {createError && (
            <p role="alert">
              {createError}
            </p>
          )}

          <button
            type="submit"
            disabled={pending}
            className="btn btn-primary"
          >
            {pending ? "Creating…" : "Create project"}
          </button>
        </form>
      )}

      {projects === null && !loadError && (
        <p role="status">Loading your projects…</p>
      )}

      {loadError && (
        <div role="alert" className="space-y-2">
          <p>{loadError}</p>
          <button type="button" onClick={() => void load()} className="btn-link">
            Retry
          </button>
        </div>
      )}

      {projects !== null && (
        <>
          <div role="tablist" aria-label="Projects" className="flex gap-4">
            <button
              type="button"
              role="tab"
              aria-selected={tab === "active"}
              onClick={() => setTab("active")}
              className={tab === "active" ? "font-semibold" : "btn-link"}
            >
              Active
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "archived"}
              onClick={() => setTab("archived")}
              className={tab === "archived" ? "font-semibold" : "btn-link"}
            >
              Archived
            </button>
          </div>

          <ProjectList projects={projects} tab={tab} currentUserId={user?.id} />
        </>
      )}
    </section>
  );
}

function ProjectList({
  projects,
  tab,
  currentUserId,
}: {
  projects: Project[];
  tab: Tab;
  currentUserId: string | undefined;
}) {
  // Already ordered by `orderProjects` before this ever renders; filtering
  // here keeps each tab's relative order rather than re-sorting it.
  const visible = projects.filter((project) =>
    tab === "active" ? project.archived_at === null : project.archived_at !== null
  );

  if (visible.length === 0) {
    return (
      <p className="empty">
        {tab === "active" ? "No projects yet. Create one to get started." : "No archived projects."}
      </p>
    );
  }

  return (
    <ul className="grid gap-4 sm:grid-cols-2">
      {visible.map((project) => {
        const role = roleIn(project, currentUserId);
        return (
          <li key={project.id} className="panel">
            <h2 className="text-lg font-semibold">
              <Link to={`/projects/${project.id}`}>{project.name}</Link>
            </h2>
            {project.description && <p className="break-words">{project.description}</p>}
            <p>
              {project.members.length}{" "}
              {project.members.length === 1 ? "member" : "members"}
              {role ? ` · you are ${role === "facilitator" ? "a facilitator" : "a member"}` : ""}
            </p>
            <p>Created {formatDate(project.created_at, "date unknown")}</p>
          </li>
        );
      })}
    </ul>
  );
}
