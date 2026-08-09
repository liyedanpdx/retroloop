import { api } from "./client";
import type { Cycle } from "./feedback";

/** One cycle, used by #16 to reach its project from a retro id. */
export async function getCycle(cycleId: string): Promise<Cycle> {
  return (await api.get<Cycle>(`/api/cycles/${cycleId}`)).data;
}
