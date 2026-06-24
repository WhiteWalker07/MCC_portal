/**
 * refCode + campus allocation.
 *
 * Ported from functions/src/engine/refcode.ts. The Firestore transaction that
 * atomically claimed the next sequence number becomes a single atomic Mongo
 * `findOneAndUpdate({$inc})` — race-safe without an explicit transaction. There
 * is no trigger-retry concern here (the route calls this exactly once), but we
 * still short-circuit if a refCode already exists, for idempotency.
 */

import { col, oid } from "../db";
import { Committee, RequestDoc, Settings } from "../types";

export type AllocResult =
  | { ok: true; refCode: string; campus: string }
  | { ok: false; skipped?: boolean; noCommittee?: boolean };

export async function allocateRefCodeAndCampus(
  requestId: string,
  request: RequestDoc
): Promise<AllocResult> {
  if (request.refCode) return { ok: false, skipped: true };

  const _id = oid(requestId);
  if (!_id) return { ok: false };
  const email = (request.contactEmail || "").toLowerCase();

  // Committee path — atomically bump the committee's lastSeq.
  const comm = (await col.committees().findOneAndUpdate(
    { _id: email as never },
    { $inc: { lastSeq: 1 } },
    { returnDocument: "after" }
  )) as unknown as Committee | null;

  if (comm) {
    const refCode = `${comm.acronym}_${comm.lastSeq}`;
    const campus = comm.campus || "";
    await col.requests().updateOne({ _id }, { $set: { refCode, campus, coordinatorEmail: "" } });
    return { ok: true, refCode, campus };
  }

  // General (non-committee) institute requester — Post requests only (Coverage is
  // reserved to committees by the create route). Use a default prefix + a global
  // counter on config/settings; campus left blank.
  const set = (await col.config().findOneAndUpdate(
    { _id: "settings" as never },
    { $inc: { generalSeq: 1 } },
    { returnDocument: "after" }
  )) as unknown as Settings | null;

  const acronym = set?.defaultAcronym || "MEDIA";
  const seq = set?.generalSeq || 1;
  const refCode = `${acronym}_${seq}`;
  await col.requests().updateOne({ _id }, { $set: { refCode, campus: "", coordinatorEmail: "" } });
  return { ok: true, refCode, campus: "" };
}
