/**
 * scheduledDeadlineCheck — hourly sweep for overdue deliverables.
 *
 * Any CONFIRMED deliverable task (everything except the Event Coordinator row)
 * past its deadline becomes LATE and records a strike against that request's
 * Event Coordinator (and the assignee too if `strikeAssigneeToo`). Each task is
 * marked `struck` so it is never re-struck. Optionally CCs `headEmail`.
 *
 * Scheduled functions don't auto-run in the emulator — invoke for testing via:
 *   npm --prefix functions run shell   ->   scheduledDeadlineCheck()
 */

import { onSchedule } from "firebase-functions/v2/scheduler";
import { FieldValue, Timestamp } from "firebase-admin/firestore";
import { db } from "../lib/setup";
import { getSettings } from "../config";
import { emailService } from "../services/email";
import { appendActivity } from "../lib/log";

export const scheduledDeadlineCheck = onSchedule(
  { schedule: "every 60 minutes", timeZone: "Asia/Kolkata" },
  async () => {
    const settings = await getSettings();
    const now = Timestamp.now();

    const snap = await db
      .collection("tasks")
      .where("status", "==", "CONFIRMED")
      .where("deadline", "<", now)
      .get();

    if (snap.empty) {
      console.log("[deadlineCheck] nothing overdue");
      return;
    }

    let lateCount = 0;
    for (const d of snap.docs) {
      const t = d.data();
      if (t.struck === true) continue;
      if (t.task === "Event Coordinator") continue; // coordinator isn't a deliverable

      const coord = (t.coordinatorEmail || "").toLowerCase();
      const assignee = (t.email || "").toLowerCase();
      try {
        const batch = db.batch();
        batch.update(d.ref, { status: "LATE", struck: true });
        if (coord) {
          batch.update(db.doc(`team/${coord}`), { strikes: FieldValue.increment(1) });
        }
        if (settings.strikeAssigneeToo && assignee) {
          batch.update(db.doc(`team/${assignee}`), { strikes: FieldValue.increment(1) });
        }
        await batch.commit();
        lateCount++;

        const to = [coord, settings.strikeAssigneeToo ? assignee : "", settings.headEmail || ""].filter(Boolean);
        await emailService.send({
          to,
          subject: `[Late] ${t.refCode} ${t.task}`,
          text: `${t.task} on ${t.refCode} (${t.eventName}) is past its deadline and is now marked LATE.`,
        });
        await appendActivity({
          event: "late",
          requestId: t.requestId,
          refCode: t.refCode,
          member: assignee,
          detail: `${t.task} marked LATE`,
        });
        if (coord) {
          await appendActivity({
            event: "strike",
            requestId: t.requestId,
            refCode: t.refCode,
            member: coord,
            detail: `Strike to coordinator for late ${t.task}`,
          });
        }
      } catch (err) {
        console.error(`[deadlineCheck] failed for task ${d.id}:`, (err as Error).message);
      }
    }
    console.log(`[deadlineCheck] marked ${lateCount} task(s) LATE`);
  }
);
