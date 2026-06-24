/**
 * refCode + campus allocation.
 *
 * Runs in a transaction that also CLAIMS the request (writes refCode/campus). If
 * the request already has a refCode, the transaction aborts with `skipped` — this
 * is the idempotency guard against at-least-once trigger retries. A concurrent
 * retry will see the claim and back off rather than double-processing.
 */

import { db } from "../lib/setup";
import { RequestDoc } from "../types";

export type AllocResult =
  | { ok: true; refCode: string; campus: string }
  | { ok: false; skipped?: boolean; noCommittee?: boolean };

export async function allocateRefCodeAndCampus(
  requestId: string,
  request: RequestDoc
): Promise<AllocResult> {
  const reqRef = db.collection("requests").doc(requestId);
  const email = (request.contactEmail || "").toLowerCase();
  const commRef = db.collection("committees").doc(email);

  return db.runTransaction(async (tx) => {
    const reqSnap = await tx.get(reqRef);
    const cur = reqSnap.data() || {};
    if (cur.refCode) {
      return { ok: false, skipped: true };
    }

    const commSnap = await tx.get(commRef);
    if (commSnap.exists) {
      const comm = commSnap.data() as { acronym: string; campus: string; lastSeq?: number };
      const seq = (comm.lastSeq || 0) + 1;
      const refCode = `${comm.acronym}_${seq}`;
      const campus = comm.campus || "";
      tx.update(commRef, { lastSeq: seq });
      tx.update(reqRef, { refCode, campus, coordinatorEmail: "" });
      return { ok: true, refCode, campus };
    }

    // General (non-committee) institute requester — Post requests only (the
    // security rule reserves Coverage to committees). Use a default prefix and a
    // global counter on config/settings; campus is left blank.
    const setRef = db.doc("config/settings");
    const setSnap = await tx.get(setRef);
    const s = (setSnap.data() || {}) as { defaultAcronym?: string; generalSeq?: number };
    const acronym = s.defaultAcronym || "MEDIA";
    const seq = (s.generalSeq || 0) + 1;
    const refCode = `${acronym}_${seq}`;
    tx.update(setRef, { generalSeq: seq });
    tx.update(reqRef, { refCode, campus: "", coordinatorEmail: "" });
    return { ok: true, refCode, campus: "" };
  });
}
