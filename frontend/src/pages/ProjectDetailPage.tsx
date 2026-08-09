import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { isAxiosError } from "axios";

import {
  FACILITATOR,
  addMember,
  getDashboard,
  getProject,
  removeMember,
  roleIn,
  type Dashboard,
  type DashboardMember,
  type Project,
} from "../api/projects";
import { useAuth } from "../auth/AuthProvider";
import { emailError } from "../auth/validation";
import { formatDate } from "../lib/dates";

const GENERIC_FAILURE = "Something went wrong. Please try again.";
const PERMISSION_CHANGED =
  "Your permissions on this project changed. You are no longer a facilitator.";

type LoadState =
  | { kind: "loading" }
  | { kind: "denied" }
  | { kind: "missing" }
  | { kind: "failed" }
  | { kind: "ready"; project: Project; dashboard: Dashboard };

export function ProjectDetailPage() {
  const { projectId = "" } = useParams();
  const { user } = useAuth();
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  // Set when the server tells us the facilitator controls we are showing are no
  // longer ours. Sticky until the next successful load, so the message is not
  // wiped by the refetch that follows it.
  const [demoted, setDemoted] = useState(false);

  const load = useCallback(async () => {
    try {
      const [project, dashboard] = await Promise.all([
        getProject(projectId),
        getDashboard(projectId),
      ]);
      setState({ kind: "ready", project, dashboard });
    } catch (error) {
      if (isAxiosError(error) && error.response?.status === 403) {
        setState({ kind: "denied" });
      } else if (isAxiosError(error) && error.response?.status === 404) {
        setState({ kind: "missing" });
      } else {
        setState({ kind: "failed" });
      }
    }
  }, [projectId]);

  useEffect(() => {
    setState({ kind: "loading" });
    void load();
  }, [load]);

  if (state.kind === "loading") {
    return <p role="status">Loading this project…</p>;
  }
  if (state.kind === "denied") {
    return <NotAvailable message="You do not have access to this project." />;
  }
  if (state.kind === "missing") {
    return <NotAvailable message="Project not found." />;
  }
  if (state.kind === "failed") {
    return (
      <div role="alert" className="space-y-2">
        <p>We could not load this project.</p>
        <button type="button" onClick={() => void load()} className="underline">
          Retry
        </button>
      </div>
    );
  }

  const { project, dashboard } = state;
  const role = roleIn(project, user?.id);
  const isFacilitator = role === FACILITATOR && !demoted;
  const cycle = dashboard.current_cycle;

  return (
    <div className="space-y-8">
      <header className="space-y-1">
        <h1 className="text-2xl font-bold text-gray-900 break-words">{project.name}</h1>
        {project.description && <p className="break-words">{project.description}</p>}
        <p>{role ? `You are ${role === FACILITATOR ? "a facilitator" : "a member"}` : ""}</p>
        {demoted && <p role="alert">{PERMISSION_CHANGED}</p>}
      </header>

      <section className="space-y-2">
        <h2 className="text-xl font-semibold">Current cycle</h2>
        {cycle === null ? (
          <p>No active cycle</p>
        ) : (
          <div className="space-y-1">
            <p>Status: {cycle.status}</p>
            {/* A count, never a list. Naming who has not submitted yet is the
                thing #5's anonymity exists to prevent. */}
            <p>
              {cycle.progress.submitted_members} of {cycle.progress.total_members} members
              submitted
            </p>
            {cycle.status === "collecting" && (
              <p>
                <Link to={`/projects/${project.id}/feedback`}>Give feedback</Link>
              </p>
            )}
            {cycle.retro ? (
              <p>
                Retrospective phase: {cycle.retro.phase} ·{" "}
                <Link to={`/retros/${cycle.retro.id}`}>Open the retrospective</Link>
              </p>
            ) : (
              <p>No retrospective started</p>
            )}
          </div>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-xl font-semibold">Past retrospectives</h2>
        {dashboard.past_retros.length === 0 ? (
          <p>No past retrospectives</p>
        ) : (
          <ul className="space-y-1">
            {dashboard.past_retros.map((retro) => (
              <li key={retro.id}>
                <Link to={`/retros/${retro.id}/summary`}>
                  Retrospective of {formatDate(retro.closed_at ?? retro.created_at, "unknown date")}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-xl font-semibold">Open actions</h2>
        {dashboard.open_actions.length === 0 ? (
          <p>No open actions</p>
        ) : (
          <ul className="space-y-2">
            {dashboard.open_actions.map((action) => (
              <li key={action.id} className="rounded border p-3">
                <p className="break-words">{action.description}</p>
                <p>Owner: {action.owner ?? "Unassigned"}</p>
                <p>Due: {formatDate(action.due_date, "No due date")}</p>
                <p>
                  <Link to={`/retros/${action.retro_id}`}>From this retrospective</Link>
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <Members
        projectId={project.id}
        members={dashboard.members}
        currentUserId={user?.id}
        isFacilitator={isFacilitator}
        onChanged={load}
        onDemoted={() => setDemoted(true)}
      />
    </div>
  );
}

function NotAvailable({ message }: { message: string }) {
  return (
    <div role="alert" className="space-y-2">
      <p>{message}</p>
      <Link to="/projects">Back to projects</Link>
    </div>
  );
}

function Members({
  projectId,
  members,
  currentUserId,
  isFacilitator,
  onChanged,
  onDemoted,
}: {
  projectId: string;
  members: DashboardMember[];
  currentUserId: string | undefined;
  isFacilitator: boolean;
  onChanged: () => Promise<void>;
  onDemoted: () => void;
}) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [inviting, setInviting] = useState(false);
  const [confirming, setConfirming] = useState<DashboardMember | null>(null);
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);

  async function onInvite(event: FormEvent) {
    event.preventDefault();
    const found = emailError(email);
    setFieldError(found);
    setInviteError(null);
    if (found) {
      return;
    }

    setInviting(true);
    try {
      await addMember(projectId, email.trim(), role);
      await onChanged();
      setEmail("");
      setRole("member");
    } catch (error) {
      const status = isAxiosError(error) ? error.response?.status : undefined;
      if (status === 403) {
        // The banner at the top of the page says it once. Repeating it beside
        // a form that is about to disappear would be two copies of one fact.
        onDemoted();
        setInviteError(null);
        return;
      }
      setInviteError(messageFor(status));
    } finally {
      setInviting(false);
    }
  }

  async function onRemove(member: DashboardMember) {
    setConfirming(null);
    setRemoveError(null);
    setRemovingId(member.user_id);
    try {
      await removeMember(projectId, member.user_id);
      await onChanged();
    } catch (error) {
      const status = isAxiosError(error) ? error.response?.status : undefined;
      if (status === 403) {
        onDemoted();
        setRemoveError(null);
        return;
      }
      // The row is never removed optimistically. A refetch after a 404 is what
      // makes "already gone" and "still there" the same code path.
      if (status === 404) {
        await onChanged();
      }
      setRemoveError(removalMessageFor(status));
    } finally {
      setRemovingId(null);
    }
  }

  return (
    <section className="space-y-3">
      <h2 className="text-xl font-semibold">Members</h2>

      {members.length === 0 ? (
        <p>No members</p>
      ) : (
        <ul className="space-y-2">
          {members.map((member) => (
            <li
              key={member.user_id}
              className="flex flex-wrap items-center justify-between gap-2 rounded border p-3"
            >
              <div className="min-w-0">
                <p className="break-words font-medium">{member.display_name}</p>
                <p className="break-words">{member.email}</p>
                <p>
                  {member.role === FACILITATOR ? "Facilitator" : "Member"} · joined{" "}
                  {formatDate(member.joined_at, "unknown date")}
                </p>
              </div>
              {/* No Remove on your own row: the API forbids a facilitator
                  removing themselves, so offering it would only ever fail. */}
              {isFacilitator && member.user_id !== currentUserId && (
                <button
                  type="button"
                  disabled={removingId === member.user_id}
                  onClick={() => setConfirming(member)}
                  className="underline disabled:opacity-50"
                >
                  {removingId === member.user_id ? "Removing…" : `Remove ${member.display_name}`}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {removeError && <p role="alert">{removeError}</p>}

      {confirming && (
        <div role="dialog" aria-modal="true" aria-label="Confirm removal" className="rounded border p-4">
          <p>Remove {confirming.display_name} from this project?</p>
          <div className="flex gap-3">
            <button type="button" onClick={() => void onRemove(confirming)} className="underline">
              Yes, remove {confirming.display_name}
            </button>
            <button type="button" onClick={() => setConfirming(null)} className="underline">
              Cancel
            </button>
          </div>
        </div>
      )}

      {isFacilitator && (
        <form onSubmit={onInvite} noValidate className="max-w-md space-y-3 rounded border p-4">
          <h3 className="font-semibold">Invite a member</h3>
          <div>
            <label htmlFor="invite-email">Email</label>
            <input
              id="invite-email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              aria-invalid={fieldError ? true : undefined}
              aria-describedby={fieldError ? "invite-email-error" : undefined}
              className="w-full rounded border px-3 py-2"
            />
            {fieldError && (
              <p id="invite-email-error" role="alert">
                {fieldError}
              </p>
            )}
          </div>

          <div>
            <label htmlFor="invite-role">Role</label>
            <select
              id="invite-role"
              value={role}
              onChange={(event) => setRole(event.target.value)}
              className="w-full rounded border px-3 py-2"
            >
              <option value="member">Member</option>
              <option value="facilitator">Facilitator</option>
            </select>
          </div>

          {inviteError && <p role="alert">{inviteError}</p>}

          <button
            type="submit"
            disabled={inviting}
            className="rounded bg-gray-900 px-4 py-2 text-white disabled:opacity-50"
          >
            {inviting ? "Inviting…" : "Invite"}
          </button>
        </form>
      )}
    </section>
  );
}

function messageFor(status: number | undefined): string {
  if (status === 404) {
    return "No user with that email";
  }
  if (status === 409) {
    return "Already a project member";
  }
  return GENERIC_FAILURE;
}

function removalMessageFor(status: number | undefined): string {
  if (status === 404) {
    return "That member is no longer on this project.";
  }
  return GENERIC_FAILURE;
}
