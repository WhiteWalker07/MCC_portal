/**
 * Confirm routine (shared) — runs on auto-accept (Phase 5) and when a secretary
 * approves a pending request (Phase 7).
 *
 * For every filled, not-yet-confirmed task: set CONFIRMED, award its points to
 * the assignee, add it to the roster (contact-facing roles only). Then write the
 * roster onto the request, set status 'Request Accepted', send invites + emails
 * (stubbed), and log. Idempotent: already-CONFIRMED/DONE tasks are not
 * re-awarded, so re-running is safe.
 */

import { FieldValue } from "firebase-admin/firestore";
import { db } from "../lib/setup";
import { calendarService } from "../services/calendar";
import { emailService } from "../services/email";
import { appendActivity } from "../lib/log";
import { RosterEntry } from "../types";

const ROSTER_ROLES = new Set([
  "Event Coordinator",
  "Photographer",
  "Videographer",
  "Content Writer",
]);

export async function confirmRequest(requestId: string): Promise<void> {
  const reqRef = db.collection("requests").doc(requestId);
  const [reqSnap, tasksSnap] = await Promise.all([
    reqRef.get(),
    db.collection("tasks").where("requestId", "==", requestId).get(),
  ]);
  if (!reqSnap.exists) return;
  const request = reqSnap.data() as FirebaseFirestore.DocumentData;

  const batch = db.batch();
  const pointsByEmail: Record<string, number> = {};
  const roster: RosterEntry[] = [];
  const sideEffects: FirebaseFirestore.DocumentData[] = [];

  for (const d of tasksSnap.docs) {
    const t = d.data();
    if (t.status === "UNFILLED" || !t.email) continue;

    if (ROSTER_ROLES.has(t.task)) {
      roster.push({ role: t.task, name: t.member, email: t.email, phone: t.phone || "" });
    }

    // Already confirmed/done — don't re-award or re-notify.
    if (t.status === "CONFIRMED" || t.status === "DONE") continue;

    batch.update(d.ref, { status: "CONFIRMED", pointsAwarded: true });
    pointsByEmail[t.email] = (pointsByEmail[t.email] || 0) + (t.points || 0);
    sideEffects.push(t);
  }

  for (const [email, pts] of Object.entries(pointsByEmail)) {
    batch.update(db.collection("team").doc(email), {
      points: FieldValue.increment(pts),
    });
  }

  batch.update(reqRef, { status: "Request Accepted", roster });
  await batch.commit();

  // Side effects after the state is committed (stubbed providers for now).
  for (const t of sideEffects) {
    if (t.atEvent && t.eventStart && t.eventEnd) {
      await calendarService.createHold({
        email: t.email,
        title: `${t.refCode} ${t.task} — ${t.eventName}`,
        start: t.eventStart,
        end: t.eventEnd,
        description: t.venue || "",
      });
    } else if (t.deadline) {
      await calendarService.createReminder({
        email: t.email,
        title: `${t.refCode} ${t.task} due — ${t.eventName}`,
        due: t.deadline,
      });
    }
    await emailService.send({
      to: t.email,
      subject: `[Assigned] ${t.refCode} ${t.task} — ${t.eventName}`,
      text:
        `You've been assigned as ${t.task} for ${t.eventName} (${t.refCode}).\n` +
        (t.deadline ? `Deadline: ${t.deadline.toDate().toLocaleString()}\n` : ""),
    });
    await appendActivity({
      event: "confirmed",
      requestId,
      refCode: t.refCode,
      member: t.email,
      detail: `${t.task} confirmed (+${t.points || 0} pts)`,
    });
  }

  // Email the requester the assigned roster.
  await emailService.send({
    to: request.contactEmail,
    subject: `[Accepted] ${request.refCode} — ${request.eventName}`,
    text: rosterEmailText(request, roster),
  });
  await appendActivity({
    event: "accepted",
    requestId,
    refCode: request.refCode,
    actor: "engine",
    detail: `Request Accepted; ${roster.length} contact(s) in roster`,
  });
}

function rosterEmailText(
  request: FirebaseFirestore.DocumentData,
  roster: RosterEntry[]
): string {
  const lines = roster.map(
    (m) => `  ${m.role}: ${m.name} <${m.email}>${m.phone ? ` · ${m.phone}` : ""}`
  );
  return (
    `Your request ${request.refCode} (${request.eventName}) has been accepted.\n\n` +
    `Assigned team:\n${lines.join("\n") || "  (none)"}\n`
  );
}
