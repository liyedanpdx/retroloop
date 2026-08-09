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

export function ProjectsPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
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

      {projects !== null && projects.length === 0 && (
        <p className="empty">No projects yet. Create one to get started.</p>
      )}

      {projects !== null && projects.length > 0 && (
        <ul className="grid gap-4 sm:grid-cols-2">
          {projects.map((project) => {
            const role = roleIn(project, user?.id);
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
      )}
    </section>
  );
}
