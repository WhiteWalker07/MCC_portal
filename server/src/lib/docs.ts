/**
 * Document helpers — read requests/tasks by id and normalize Mongo `_id` (an
 * ObjectId) into the string `id` the client/API uses. Engine code passes request
 * ids around as hex strings; tasks store `requestId` as that same hex string.
 */

import { WithId, Document } from "mongodb";
import { col, oid } from "../db";
import { RequestDoc, TeamMember } from "../types";

/** Load all team members, normalizing `_id` (email) into `id`/`email`. */
export async function loadTeam(): Promise<TeamMember[]> {
  const docs = await col.team().find({}).toArray();
  return docs.map((d) => ({ ...(d as unknown as TeamMember), id: String(d._id), email: String(d._id) }));
}

/** A request as stored in Mongo (has `_id`), plus convenience string `id`. */
export type StoredRequest = WithId<Document> & RequestDoc;

export function withId<T extends Document>(doc: WithId<T> | null): (T & { id: string }) | null {
  if (!doc) return null;
  const { _id, ...rest } = doc as WithId<Document>;
  return { ...(rest as T), id: String(_id) };
}

/** Read a request by hex id; returns the raw Mongo doc (with `_id`) or null. */
export async function findRequest(reqId: string): Promise<StoredRequest | null> {
  const _id = oid(reqId);
  if (!_id) return null;
  return (await col.requests().findOne({ _id })) as StoredRequest | null;
}

/** Read a task by hex id; returns the raw Mongo doc (with `_id`) or null. */
export async function findTask(taskId: string): Promise<(WithId<Document>) | null> {
  const _id = oid(taskId);
  if (!_id) return null;
  return col.tasks().findOne({ _id });
}
