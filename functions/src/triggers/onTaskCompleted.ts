/**
 * onTaskCompleted — advances a request when its work finishes.
 *
 * Fires only on the status -> DONE transition. Then:
 *   Coverage: when all deliverables (everything except the Event Coordinator row
 *             and UNFILLED rows; at least one) are DONE => 'Event Covered'.
 *   Post:     when the Vetter is DONE => 'Ready To post'.
 *
 * Only advances from 'Request Accepted', so it's idempotent and won't move a
 * request backwards.
 */

import { onDocumentUpdated } from "firebase-functions/v2/firestore";
import { FieldValue } from "firebase-admin/firestore";
import { db } from "../lib/setup";
import { getPoints } from "../config";
import { finalPoints } from "../engine/points";
import { appendActivity } from "../lib/log";

export const onTaskCompleted = onDocumentUpdated(
  "tasks/{id}",
  async (event) => {
    const before = event.data?.before.data();
    const after = event.data?.after.data();
    if (!before || !after) return;
    if (before.status === "DONE" || after.status !== "DONE") return;

    const requestId: string = after.requestId;
    const refCode: string = after.refCode;
    const reqType: string = after.reqType;

    await appendActivity({
      event: "completed",
      requestId,
      refCode,
      member: after.email,
      detail: `${after.task} marked done`,
    });

    // Completion-timing points modifier (deliverables only, applied once).
    if (
      after.task !== "Event Coordinator" &&
      after.pointsAwarded === true &&
      after.timingApplied !== true &&
      after.email
    ) {
      const cfg = await getPoints();
      const base = after.points || 0;
      const completedMs = after.completedAt?.toMillis
        ? after.completedAt.toMillis()
        : Date.now();
      const refTs = reqType === "Coverage" ? after.eventEnd : after.createdAt;
      const refMs = refTs?.toMillis ? refTs.toMillis() : null;
      if (refMs) {
        const turnaroundHours = (completedMs - refMs) / 3_600_000;
        const final = finalPoints(base, turnaroundHours, cfg);
        const delta = final - base;
        const batch = db.batch();
        batch.update(event.data!.after.ref, { points: final, timingApplied: true });
        if (delta !== 0) {
          batch.update(db.doc(`team/${after.email.toLowerCase()}`), {
            points: FieldValue.increment(delta),
          });
        }
        await batch.commit();
        if (delta !== 0) {
          await appendActivity({
            event: "points-adjust",
            requestId,
            refCode,
            member: after.email,
            detail: `${after.task}: ${delta > 0 ? "+" : ""}${delta} pts (turnaround ${Math.round(turnaroundHours)}h)`,
          });
        }
      } else {
        await event.data!.after.ref.update({ timingApplied: true });
      }
    }

    const reqRef = db.collection("requests").doc(requestId);
    const [reqSnap, tasksSnap] = await Promise.all([
      reqRef.get(),
      db.collection("tasks").where("requestId", "==", requestId).get(),
    ]);
    if (!reqSnap.exists) return;
    const request = reqSnap.data() as FirebaseFirestore.DocumentData;
    if (request.status !== "Request Accepted") return; // only advance from accepted

    const tasks = tasksSnap.docs.map((d) => d.data());

    if (reqType === "Coverage") {
      const deliverables = tasks.filter(
        (t) => t.task !== "Event Coordinator" && t.status !== "UNFILLED"
      );
      if (deliverables.length === 0) return;
      if (deliverables.every((t) => t.status === "DONE")) {
        await reqRef.update({ status: "Event Covered" });
        await appendActivity({
          event: "event-covered",
          requestId,
          refCode,
          actor: "engine",
          detail: "All coverage deliverables done",
        });
      }
    } else if (reqType === "Post") {
      const vetter = tasks.find((t) => t.task === "Vetter");
      if (vetter && vetter.status === "DONE") {
        await reqRef.update({ status: "Ready To post" });
        await appendActivity({
          event: "ready",
          requestId,
          refCode,
          actor: "engine",
          detail: "Vetting done; ready to post",
        });
      }
    }
  }
);
