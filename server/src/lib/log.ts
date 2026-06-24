/** Appends an entry to activityLog. Best-effort: never throws into the caller. */

import { col } from "../db";

export interface ActivityEntry {
  event: string;
  requestId?: string;
  refCode?: string;
  actor?: string;
  member?: string;
  detail?: string;
}

export async function appendActivity(entry: ActivityEntry): Promise<void> {
  try {
    await col.activityLog().insertOne({
      timestamp: new Date(),
      requestId: "",
      refCode: "",
      actor: "",
      member: "",
      detail: "",
      ...entry,
    });
  } catch (err) {
    console.error("[activityLog] failed to append:", (err as Error).message);
  }
}
