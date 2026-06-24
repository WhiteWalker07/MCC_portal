/**
 * onRequestDecided — applies a secretary's approve/reject decision on a request
 * that's pending POC approval.
 *
 * The secretary writes only a `decision` field (rule-checked); this trigger reacts:
 *   approved -> run the shared confirm routine (-> Request Accepted, roster, emails)
 *   rejected -> set status 'Rejected' and email the requester the reason.
 *
 * Acts only while the request is still 'Pending for POC approval', so it's
 * idempotent (confirm/reject move status off pending and re-fires no-op).
 */

import { onDocumentUpdated } from "firebase-functions/v2/firestore";
import { confirmRequest } from "../engine/confirm";
import { emailService } from "../services/email";
import { appendActivity } from "../lib/log";
import { RequestDoc } from "../types";

export const onRequestDecided = onDocumentUpdated("requests/{id}", async (event) => {
  const before = event.data?.before.data();
  const after = event.data?.after.data();
  if (!before || !after) return;

  const decision = after.decision;
  if (!decision) return;
  if (before.decision === decision) return; // not newly set
  if (after.status !== "Pending for POC approval") return; // only decide pending

  const requestId = event.params.id;
  const reqRef = event.data!.after.ref;
  const request = after as RequestDoc;
  const refCode = request.refCode || "";

  if (decision === "approved") {
    await appendActivity({
      event: "approved",
      requestId,
      refCode,
      actor: after.decisionBy || "secretary",
      detail: "Approved by POC",
    });
    await confirmRequest(requestId);
    return;
  }

  if (decision === "rejected") {
    await reqRef.update({ status: "Rejected" });
    await emailService.send({
      to: request.contactEmail,
      subject: `[Rejected] ${refCode} — ${request.eventName}`,
      text:
        `Your request ${refCode} (${request.eventName}) was not approved.` +
        (after.rejectReason ? `\n\nReason: ${after.rejectReason}` : ""),
    });
    await appendActivity({
      event: "rejected",
      requestId,
      refCode,
      actor: after.decisionBy || "secretary",
      detail: after.rejectReason || "Rejected by POC",
    });
  }
});
