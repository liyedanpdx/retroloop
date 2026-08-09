/**
 * The project endpoints, typed once (#14).
 *
 * Every call goes through #13's `api`, so the bearer token, the refresh-and-
 * retry interceptor and the redirect on a dead session are inherited rather
 * than reimplemented. Nothing in this file handles a 401.
 *
 * Two shapes, deliberately: `Project` is what `GET /api/projects` returns, with
 * opaque member ids, and `Dashboard` is #31's read model, with the identities
 * and aggregates the detail page needs. The list page cannot show a name for a
 * member and does not try.
 */
import { api } from "./client";

export type ProjectMember = {
  user_id: string;
  role: string;
  joined_at: string;
};

export type Project = {
  id: string;
  name: string;
  description: string | null;
  members: ProjectMember[];
  created_at: string;
  created_by: string;
  /** Non-null once archived (#32). Archiving is reversible; nothing is deleted. */
  archived_at: string | null;
};

export type DashboardMember = {
  user_id: string;
  display_name: string;
  email: string;
  role: string;
  joined_at: string;
};

export type DashboardCycle = {
  id: string;
  status: string;
  created_at: string;
  closed_at: string | null;
  progress: { submitted_members: number; total_members: number };
  retro: { id: string; phase: string } | null;
};

export type PastRetro = {
  id: string;
  cycle_id: string;
  phase: string;
  created_at: string;
  closed_at: string | null;
};

export type OpenAction = {
  id: string;
  retro_id: string;
  description: string;
  owner_id: string | null;
  owner: string | null;
  due_date: string | null;
  status: string;
};

export type Dashboard = {
  members: DashboardMember[];
  current_cycle: DashboardCycle | null;
  past_retros: PastRetro[];
  open_actions: OpenAction[];
};

export const FACILITATOR = "facilitator";

export async function listProjects(): Promise<Project[]> {
  const response = await api.get<Project[]>("/api/projects");
  return response.data;
}

export async function createProject(name: string, description: string | null): Promise<Project> {
  const response = await api.post<Project>("/api/projects", { name, description });
  return response.data;
}

export async function getDashboard(projectId: string): Promise<Dashboard> {
  const response = await api.get<Dashboard>(`/api/projects/${projectId}/dashboard`);
  return response.data;
}

export async function getProject(projectId: string): Promise<Project> {
  const response = await api.get<Project>(`/api/projects/${projectId}`);
  return response.data;
}

export async function addMember(projectId: string, email: string, role: string): Promise<void> {
  await api.post(`/api/projects/${projectId}/members`, { email, role });
}

export async function removeMember(projectId: string, userId: string): Promise<void> {
  await api.delete(`/api/projects/${projectId}/members/${userId}`);
}

/**
 * Newest first, then name, then id.
 *
 * The last two are not decoration. Two projects created in the same second
 * would otherwise swap places between renders, and a list that reorders itself
 * under the pointer is worse than one in an arbitrary but fixed order.
 */
export function orderProjects(projects: Project[]): Project[] {
  return [...projects].sort((left, right) => {
    if (left.created_at !== right.created_at) {
      return left.created_at < right.created_at ? 1 : -1;
    }
    if (left.name !== right.name) {
      return left.name < right.name ? -1 : 1;
    }
    return left.id < right.id ? -1 : 1;
  });
}

export function roleIn(project: Project, userId: string | undefined): string | null {
  const member = project.members.find((row) => row.user_id === userId);
  return member ? member.role : null;
}

export async function updateProject(
  projectId: string,
  body: { name?: string; description?: string | null }
): Promise<Project> {
  return (await api.patch<Project>(`/api/projects/${projectId}`, body)).data;
}

export async function setArchived(projectId: string, archived: boolean): Promise<Project> {
  return (await api.patch<Project>(`/api/projects/${projectId}/archive`, { archived })).data;
}

export async function updateMemberRole(
  projectId: string,
  userId: string,
  role: string
): Promise<void> {
  await api.patch(`/api/projects/${projectId}/members/${userId}`, { role });
}

export type NewCycle = { id: string; project_id: string; status: string };

/** 开一个 collecting 周期 (#4)。一个项目同时只能有一个开着的,重复开是 409。 */
export async function createCycle(projectId: string): Promise<NewCycle> {
  return (await api.post<NewCycle>(`/api/projects/${projectId}/cycles`)).data;
}

/**
 * 开始回顾 (#6):揭示所有卡片,并把它们冻结。
 *
 * 单向的,所以调用它的地方必须先问一句 —— 没有任何接口能退回 collecting。
 */
export async function startRetro(cycleId: string): Promise<{ id: string }> {
  return (await api.post<{ id: string }>(`/api/cycles/${cycleId}/retro`)).data;
}
