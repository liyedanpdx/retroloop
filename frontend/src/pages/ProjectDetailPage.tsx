import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { isAxiosError } from "axios";

import {
  FACILITATOR,
  addMember,
  closeCycle,
  createCycle,
  getDashboard,
  getProject,
  removeMember,
  roleIn,
  setArchived,
  startRetro,
  updateMemberRole,
  updateProject,
  type Dashboard,
  type DashboardCycle,
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
        <button type="button" onClick={() => void load()} className="btn-link">
          Retry
        </button>
      </div>
    );
  }

  const { project, dashboard } = state;
  const role = roleIn(project, user?.id);
  const archived = Boolean(project.archived_at);
  // An archived project is read-only for everyone (#32), so the controls go
  // rather than failing when pressed.
  const isFacilitator = role === FACILITATOR && !demoted && !archived;
  const cycle = dashboard.current_cycle;

  return (
    <div className="space-y-8">
      <header className="space-y-1">
        <h1 className="break-words">{project.name}</h1>
        {project.description && <p className="break-words">{project.description}</p>}
        <p>{role ? `You are ${role === FACILITATOR ? "a facilitator" : "a member"}` : ""}</p>
        {archived && <p role="status">This project is archived. It is read-only.</p>}
        {demoted && <p role="alert">{PERMISSION_CHANGED}</p>}
      </header>

      <section className="space-y-2">
        <h2>Current cycle</h2>
        {cycle === null ? (
          <div className="space-y-2">
            <p className="empty">No active cycle</p>
            {isFacilitator && (
              <CycleLifecycle projectId={project.id} cycle={null} onChanged={load} />
            )}
          </div>
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
              <>
                <p className="empty">No retrospective started</p>
                {isFacilitator && (
                  <CycleLifecycle projectId={project.id} cycle={cycle} onChanged={load} />
                )}
              </>
            )}
          </div>
        )}
      </section>

      <section className="space-y-2">
        <h2>Past retrospectives</h2>
        {dashboard.past_retros.length === 0 ? (
          <p className="empty">No past retrospectives</p>
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
        <h2>Open actions</h2>
        {dashboard.open_actions.length === 0 ? (
          <p className="empty">No open actions</p>
        ) : (
          <ul className="space-y-2">
            {dashboard.open_actions.map((action) => (
              <li key={action.id} className="card">
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

      {role === FACILITATOR && !demoted && (
        <Settings project={project} archived={archived} onChanged={load} />
      )}
    </div>
  );
}

/**
 * Rename, and archive or restore (#32).
 *
 * Archiving is the only "remove a project" this product has, and it is
 * reversible on purpose: cycles, retrospectives, feedback and actions are the
 * record of what a team did, and no button here destroys that. The confirmation
 * says as much, so nobody is looking for an undo that does not exist.
 */
function Settings({
  project,
  archived,
  onChanged,
}: {
  project: Project;
  archived: boolean;
  onChanged: () => Promise<void>;
}) {
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description ?? "");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [confirming, setConfirming] = useState(false);

  async function run(action: () => Promise<void>) {
    if (pending) {
      return;
    }
    setPending(true);
    setError(null);
    try {
      await action();
      await onChanged();
    } catch (failure) {
      const status = isAxiosError(failure) ? failure.response?.status : undefined;
      setError(
        status === 400
          ? "That is not possible in the project's current state."
          : status === 403
            ? PERMISSION_CHANGED
            : GENERIC_FAILURE
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="panel space-y-3">
      <h2>Project settings</h2>

      {!archived && (
        <form
          noValidate
          className="max-w-md space-y-2"
          onSubmit={(event) => {
            event.preventDefault();
            const trimmed = name.trim();
            if (!trimmed) {
              setFieldError("Project name is required");
              return;
            }
            setFieldError(null);
            void run(async () => {
              await updateProject(project.id, {
                name: trimmed,
                description: description.trim() || null,
              });
            });
          }}
        >
          <div>
            <label htmlFor="settings-name">Project name</label>
            <input
              id="settings-name"
              value={name}
              disabled={pending}
              onChange={(event) => setName(event.target.value)}
              aria-invalid={fieldError ? true : undefined}
              aria-describedby={fieldError ? "settings-name-error" : undefined}
             
            />
            {fieldError && (
              <p id="settings-name-error" role="alert">
                {fieldError}
              </p>
            )}
          </div>
          <div>
            <label htmlFor="settings-description">Description</label>
            <textarea
              id="settings-description"
              value={description}
              disabled={pending}
              onChange={(event) => setDescription(event.target.value)}
             
            />
          </div>
          <button
            type="submit"
            disabled={pending}
            className="btn btn-primary"
          >
            {pending ? "Saving…" : "Save project details"}
          </button>
        </form>
      )}

      {error && <p role="alert">{error}</p>}

      {archived ? (
        <button
          type="button"
          disabled={pending}
          className="btn-link"
          onClick={() => void run(async () => void (await setArchived(project.id, false)))}
        >
          Restore this project
        </button>
      ) : (
        <button
          type="button"
          disabled={pending}
          className="btn-link"
          onClick={() => setConfirming(true)}
        >
          Archive this project
        </button>
      )}

      {confirming && (
        <div role="dialog" aria-modal="true" aria-label="Archive this project">
          <p>
            Archive {project.name}? It becomes read-only and disappears from
            everyday use. Nothing is deleted — its cycles, retrospectives and
            feedback stay, and a facilitator can restore it.
          </p>
          <button
            type="button"
            className="btn-link"
            onClick={() => {
              setConfirming(false);
              void run(async () => void (await setArchived(project.id, true)));
            }}
          >
            Yes, archive it
          </button>
          <button type="button" className="btn-link ml-3" onClick={() => setConfirming(false)}>
            Cancel
          </button>
        </div>
      )}
    </section>
  );
}

/**
 * 开一个周期,和开始回顾 (#36)。
 *
 * 这两个接口从 #4 和 #6 就在了,但三个前端 issue 各自把它们划给了另外两个,
 * 结果谁也没做 —— 于是一个新项目除了改名和邀请人之外无路可走。
 *
 * 开始回顾要先问一句:它会揭示所有卡片并把它们冻结,而且没有任何接口能退回
 * collecting。开周期不用问,一个空周期删掉它的代价只是关掉它。
 */
function CycleLifecycle({
  projectId,
  cycle,
  onChanged,
}: {
  projectId: string;
  cycle: DashboardCycle | null;
  onChanged: () => Promise<void>;
}) {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [closing, setClosing] = useState(false);

  function fail(failure: unknown) {
    const status = isAxiosError(failure) ? failure.response?.status : undefined;
    setError(
      status === 409
        ? "This project already has an open cycle."
        : status === 403
          ? PERMISSION_CHANGED
          : status === 400
            ? "现在的状态下做不了这件事。"
            : GENERIC_FAILURE
    );
    // 409 和 400 说明我们看到的状态已经旧了,重新取一次比留着旧的诚实。
    if (status === 409 || status === 400 || status === 403) {
      void onChanged();
    }
  }

  if (cycle === null) {
    return (
      <div>
        <button
          type="button"
          disabled={pending}
          className="btn btn-primary"
          onClick={() => {
            setPending(true);
            setError(null);
            createCycle(projectId)
              .then(() => {
                // 直接送到写反馈的地方 —— 开周期的目的就是让人开始写。
                navigate(`/projects/${projectId}/feedback`);
              })
              .catch(fail)
              .finally(() => setPending(false));
          }}
        >
          {pending ? "Opening…" : "Start a feedback cycle"}
        </button>
        {error && <p role="alert">{error}</p>}
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-4">
      {cycle.status === "collecting" && (
        <button
          type="button"
          disabled={pending}
          className="btn btn-primary"
          onClick={() => setConfirming(true)}
        >
          {pending ? "Starting…" : "Start the retrospective"}
        </button>
      )}

      {/* 结束一个不打算走到回顾的周期,也是归档项目之前必须做的一步 (#32)。 */}
      <button
        type="button"
        disabled={pending}
        className="btn-link"
        onClick={() => setClosing(true)}
      >
        Close this cycle
      </button>

      {closing && (
        <div role="dialog" aria-modal="true" aria-label="Close this cycle">
          <p>
            Close this cycle? Nobody will be able to add feedback to it
            {cycle.status === "collecting"
              ? ", and it will never become a retrospective"
              : ""}
            . This cannot be undone.
          </p>
          <button
            type="button"
            className="btn-link"
            onClick={() => {
              setClosing(false);
              setPending(true);
              setError(null);
              closeCycle(cycle.id)
                .then(() => onChanged())
                .catch(fail)
                .finally(() => setPending(false));
            }}
          >
            Yes, close it
          </button>
          <button type="button" className="btn-link ml-3" onClick={() => setClosing(false)}>
            Cancel
          </button>
        </div>
      )}
      {error && <p role="alert">{error}</p>}

      {confirming && (
        <div role="dialog" aria-modal="true" aria-label="Start the retrospective">
          <p>
            Starting the retrospective reveals everyone’s cards to the whole
            team and freezes them — nobody can edit or delete their own after
            this. There is no way back to collecting.
          </p>
          <button
            type="button"
            className="btn-link"
            onClick={() => {
              setConfirming(false);
              setPending(true);
              setError(null);
              startRetro(cycle.id)
                .then((retro) => navigate(`/retros/${retro.id}`))
                .catch(fail)
                .finally(() => setPending(false));
            }}
          >
            Yes, reveal the cards
          </button>
          <button type="button" className="btn-link ml-3" onClick={() => setConfirming(false)}>
            Cancel
          </button>
        </div>
      )}
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

  async function onRole(member: DashboardMember, role: string) {
    setRemoveError(null);
    setRemovingId(member.user_id);
    try {
      await updateMemberRole(projectId, member.user_id, role);
      await onChanged();
    } catch (error) {
      const status = isAxiosError(error) ? error.response?.status : undefined;
      if (status === 403) {
        onDemoted();
        return;
      }
      // A 400 here is the one rule the backend will not bend: a project can
      // never be left without a facilitator.
      setRemoveError(
        status === 400
          ? "A project must always have a facilitator."
          : status === 404
            ? "That member is no longer on this project."
            : GENERIC_FAILURE
      );
      if (status === 404) {
        await onChanged();
      }
    } finally {
      setRemovingId(null);
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
      <h2>Members</h2>

      {members.length === 0 ? (
        <p className="empty">No members</p>
      ) : (
        <ul className="space-y-2">
          {members.map((member) => (
            <li
              key={member.user_id}
              className="card flex flex-wrap items-center justify-between gap-2"
            >
              <div className="min-w-0">
                <p className="break-words font-medium">{member.display_name}</p>
                <p className="break-words">{member.email}</p>
                <p>
                  {member.role === FACILITATOR ? "Facilitator" : "Member"} · joined{" "}
                  {formatDate(member.joined_at, "unknown date")}
                </p>
              </div>
              {/* 右侧控件包在一起,否则 justify-between 会把角色下拉和移除
                  按钮拆到两头,每一行的对齐还取决于那行有没有移除按钮。 */}
              <div className="ml-auto flex flex-wrap items-end gap-4">
              {isFacilitator && (
                <div>
                  <label htmlFor={`role-${member.user_id}`}>
                    Role of {member.display_name}
                  </label>
                  <select
                    id={`role-${member.user_id}`}
                    value={member.role}
                    disabled={removingId === member.user_id}
                    onChange={(event) => void onRole(member, event.target.value)}
                   
                  >
                    <option value="member">Member</option>
                    <option value="facilitator">Facilitator</option>
                  </select>
                </div>
              )}
              {/* No Remove on your own row: the API forbids a facilitator
                  removing themselves, so offering it would only ever fail. */}
              {isFacilitator && member.user_id !== currentUserId && (
                <button
                  type="button"
                  disabled={removingId === member.user_id}
                  onClick={() => setConfirming(member)}
                  className="btn-link"
                >
                  {removingId === member.user_id ? "Removing…" : `Remove ${member.display_name}`}
                </button>
              )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {removeError && <p role="alert">{removeError}</p>}

      {confirming && (
        <div role="dialog" aria-modal="true" aria-label="Confirm removal" className="rounded border p-4">
          <p>Remove {confirming.display_name} from this project?</p>
          <div className="flex gap-3">
            <button type="button" onClick={() => void onRemove(confirming)} className="btn-link">
              Yes, remove {confirming.display_name}
            </button>
            <button type="button" onClick={() => setConfirming(null)} className="btn-link">
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
             
            >
              <option value="member">Member</option>
              <option value="facilitator">Facilitator</option>
            </select>
          </div>

          {inviteError && <p role="alert">{inviteError}</p>}

          <button
            type="submit"
            disabled={inviting}
            className="btn btn-primary"
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
