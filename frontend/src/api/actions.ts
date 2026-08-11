/**
 * One caller's own open actions, across every project (#45).
 *
 * Its own file rather than a corner of `api/projects.ts` or `api/retro.ts`,
 * matching the backend: `/api/actions/mine` is user-scoped, not project- or
 * retro-scoped, so it isn't really either of those resources.
 */
import { api } from "./client";

export type MyAction = {
  id: string;
  project_id: string;
  project_name: string;
  retro_id: string;
  description: string;
  due_date: string | null;
};

export async function getMyActions(): Promise<MyAction[]> {
  const response = await api.get<MyAction[]>("/api/actions/mine");
  return response.data;
}
