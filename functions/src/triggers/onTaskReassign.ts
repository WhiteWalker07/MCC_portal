/**
 * onTaskReassign — processes a requested reassignment.
 *
 * Fires when `reassignTo` becomes a (new) non-empty email. Re-validates the new
 * member; if valid, swaps member/email/phone, sets the right status, moves points
 * (only if they were awarded), propagates coordinatorEmail when the Event
 * Coordinator changes, notifies both people, and clears `reassignTo`. If invalid,
 * clears `reassignTo` and emails the coordinator/secretaries the reason.
 *
 * This is the rule-backed path (a coordinator/secretary may write `reassignTo`
 * directly per security rules; the assignment callables also use it).
 */

import { onDocumentUpdated } from "firebase-functions/v2/firestore";
import { db } from "../lib/setup";
import { getSettings } from "../config";
import { validateMember, performSwap } from "../engine/assignment";
import { emailService } from "../services/email";
import { appendActivity } from "../lib/log";
import { RequestDoc } from "../types";

export const onTaskReassign = onDocumentUpdated("tasks/{id}", async (event) => {
  const before = event.data?.before.data();
  const after = event.data?.after.data();
  if (!before || !after) return;

  const target = (after.reassignTo || "").toLowerCase();
  if (!target) return;
  if ((before.reassignTo || "").toLowerCase() === target) return; // not newly set

  const taskRef = event.data!.after.ref;
  const requestId: string = after.requestId;
  const refCode: string = after.refCode;

  const [settings, reqSnap] = await Promise.all([
    getSettings(),
    db.collection("requests").doc(requestId).get(),
  ]);
  const request = (reqSnap.data() as RequestDoc) || ({} as RequestDoc);

  const check = await validateMember(
    target,
    after.requiredSkill,
    after.atEvent,
    request,
    settings
  );

  // ---- invalid: back out and notify -------------------------------------
  if (!check.ok || !check.member) {
    await taskRef.update({ reassignTo: "" });
    const to = [after.coordinatorEmail, ...(settings.secretaryEmails || [])].filter(
      Boolean
    );
    await emailService.send({
      to,
      subject: `[Reassign rejected] ${refCode} ${after.task}`,
      text: `Reassignment of ${after.task} on ${refCode} to ${target} was rejected: ${check.reason}.`,
    });
    await appendActivity({
      event: "reassign-rejected",
      requestId,
      refCode,
      member: target,
      detail: `${after.task}: ${check.reason}`,
    });
    return;
  }

  // ---- valid: swap -------------------------------------------------------
  const newMember = check.member;
  if (newMember.email.toLowerCase() === (after.email || "").toLowerCase()) {
    await taskRef.update({ reassignTo: "" }); // no-op reassign
    return;
  }

  await performSwap(taskRef, after, newMember, request);
});
