/**
 * Confirm routine (shared) — runs on auto-accept and when a secretary approves a
 * pending request. Ported from functions/src/engine/confirm.ts; Firestore
 * batch/reads → Mongo. Idempotent: already-CONFIRMED/DONE tasks are not
 * re-awarded.
 *
 * For every filled, not-yet-confirmed task: set CONFIRMED, award its points to
 * the assignee, add it to the roster (contact-facing roles only). Then write the
 * roster onto the request, set status 'Request Accepted', send invites + emails.
 */

import { Document, WithId } from "mongodb";
import { col, oid } from "../db";
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
  const _id = oid(requestId);
  if (!_id) return;
  const request = await col.requests().findOne({ _id });
  if (!request) return;
  const tasks = await col.tasks().find({ requestId }).toArray();

  const pointsByEmail: Record<string, number> = {};
  const roster: RosterEntry[] = [];
  const sideEffects: WithId<Document>[] = [];

  for (const t of tasks) {
    if (t.status === "UNFILLED" || !t.email) continue;

    if (ROSTER_ROLES.has(t.task)) {
      roster.push({ role: t.task, name: t.member, email: t.email, phone: t.phone || "" });
    }

    // Already confirmed/done — don't re-award or re-notify.
    if (t.status === "CONFIRMED" || t.status === "DONE") continue;

    await col.tasks().updateOne(
      { _id: t._id },
      { $set: { status: "CONFIRMED", pointsAwarded: true } }
    );
    const e = String(t.email).toLowerCase();
    pointsByEmail[e] = (pointsByEmail[e] || 0) + (t.points || 0);
    sideEffects.push(t);
  }

  for (const [email, pts] of Object.entries(pointsByEmail)) {
    await col.team().updateOne({ _id: email as never }, { $inc: { points: pts } });
  }

  await col.requests().updateOne({ _id }, { $set: { status: "Request Accepted", roster } });

  // Side effects after the state is committed.
  for (const t of sideEffects) {
    if (t.atEvent && t.eventStart && t.eventEnd) {
      await calendarService.createHold({
        email: t.email,
        title: `${t.refCode} ${t.task} — ${t.eventName}`,
        start: new Date(t.eventStart),
        end: new Date(t.eventEnd),
        description: t.venue || "",
      });
    } else if (t.deadline) {
      await calendarService.createReminder({
        email: t.email,
        title: `${t.refCode} ${t.task} due — ${t.eventName}`,
        due: new Date(t.deadline),
      });
    }
    await emailService.send({
      to: t.email,
      subject: `[Assigned] ${t.refCode} ${t.task} — ${t.eventName}`,
      text:
        `You've been assigned as ${t.task} for ${t.eventName} (${t.refCode}).\n` +
        (t.deadline ? `Deadline: ${new Date(t.deadline).toLocaleString()}\n` : ""),
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

function rosterEmailText(request: Document, roster: RosterEntry[]): string {
  const lines = roster.map(
    (m) => `  ${m.role}: ${m.name} <${m.email}>${m.phone ? ` · ${m.phone}` : ""}`
  );
  return (
    `Your request ${request.refCode} (${request.eventName}) has been accepted.\n\n` +
    `Assigned team:\n${lines.join("\n") || "  (none)"}\n`
  );
}
