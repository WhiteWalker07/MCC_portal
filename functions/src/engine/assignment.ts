/**
 * Shared assignment helpers used by the reassign trigger and the assignment
 * callables: validate a specific member, award/move points, and notify an
 * assignee (calendar + email, stubbed until their phases).
 */

import { FieldValue } from "firebase-admin/firestore";
import { db } from "../lib/setup";
import { calendarService } from "../services/calendar";
import { emailService } from "../services/email";
import { appendActivity } from "../lib/log";
import { isBaseEligible } from "./assign";
import { RequestDoc, Settings, TeamMember } from "../types";

/** Request states in which assigned work counts as confirmed (points awarded). */
export const CONFIRMED_STATES = [
  "Request Accepted",
  "Event Covered",
  "Ready To post",
  "Posted",
];

export interface ValidateResult {
  ok: boolean;
  reason: string;
  member: TeamMember | null;
}

/** Validate a chosen member for a task (skill, active, strikes, campus, calendar). */
export async function validateMember(
  email: string,
  requiredSkill: string,
  atEvent: boolean,
  request: RequestDoc,
  settings: Settings
): Promise<ValidateResult> {
  const e = (email || "").toLowerCase();
  const snap = await db.doc(`team/${e}`).get();
  if (!snap.exists) {
    return { ok: false, reason: `${e} is not on the team`, member: null };
  }
  const member = { id: snap.id, ...(snap.data() as TeamMember) } as TeamMember;

  if (!isBaseEligible(member, requiredSkill, request, settings)) {
    return {
      ok: false,
      reason: `${member.name} is not eligible (inactive, at strike limit, lacks "${requiredSkill}", or different campus)`,
      member: null,
    };
  }
  if (atEvent && request.eventStart && request.eventEnd) {
    const free = await calendarService.isFree(
      e,
      request.eventStart,
      request.eventEnd
    );
    if (!free) {
      return {
        ok: false,
        reason: `${member.name} is busy during the event window`,
        member: null,
      };
    }
  }
  return { ok: true, reason: "", member };
}

/** Increment a member's points (negative to remove). Adds to an existing batch. */
export function awardPoints(
  batch: FirebaseFirestore.WriteBatch,
  email: string,
  delta: number
): void {
  if (!email || !delta) return;
  batch.update(db.collection("team").doc(email.toLowerCase()), {
    points: FieldValue.increment(delta),
  });
}

/** Calendar hold/reminder + email for a newly confirmed assignee (stubbed). */
export async function notifyAssignee(task: FirebaseFirestore.DocumentData): Promise<void> {
  if (task.atEvent && task.eventStart && task.eventEnd) {
    await calendarService.createHold({
      email: task.email,
      title: `${task.refCode} ${task.task} — ${task.eventName}`,
      start: task.eventStart,
      end: task.eventEnd,
      description: task.venue || "",
    });
  } else if (task.deadline) {
    await calendarService.createReminder({
      email: task.email,
      title: `${task.refCode} ${task.task} due — ${task.eventName}`,
      due: task.deadline,
    });
  }
  await emailService.send({
    to: task.email,
    subject: `[Assigned] ${task.refCode} ${task.task} — ${task.eventName}`,
    text: `You've been assigned as ${task.task} for ${task.eventName} (${task.refCode}).`,
  });
}

/**
 * Swap (or fill) a task's assignee to `newMember` and apply the side effects:
 * status, point move/award, coordinatorEmail propagation, notifications, log.
 * Used by both the reassign trigger and the assignment callables.
 */
export async function performSwap(
  taskRef: FirebaseFirestore.DocumentReference,
  task: FirebaseFirestore.DocumentData,
  newMember: TeamMember,
  request: RequestDoc
): Promise<void> {
  const oldEmail = (task.email || "").toLowerCase();
  const confirmedState = CONFIRMED_STATES.includes(request.status);
  const newStatus = confirmedState ? "CONFIRMED" : "PROPOSED";
  const points = task.points || 0;

  const batch = db.batch();
  batch.update(taskRef, {
    member: newMember.name,
    email: newMember.email,
    phone: newMember.phone || "",
    status: newStatus,
    pointsAwarded: confirmedState,
    reassignTo: "",
  });
  if (task.pointsAwarded && oldEmail) awardPoints(batch, oldEmail, -points);
  if (confirmedState) awardPoints(batch, newMember.email, points);

  if (task.task === "Event Coordinator") {
    batch.update(db.collection("requests").doc(task.requestId), {
      coordinatorEmail: newMember.email,
    });
    const siblings = await db
      .collection("tasks")
      .where("requestId", "==", task.requestId)
      .get();
    for (const d of siblings.docs) {
      if (d.id !== taskRef.id) {
        batch.update(d.ref, { coordinatorEmail: newMember.email });
      }
    }
  }
  await batch.commit();

  await notifyAssignee({
    ...task,
    member: newMember.name,
    email: newMember.email,
    phone: newMember.phone || "",
  });
  if (oldEmail && oldEmail !== newMember.email.toLowerCase()) {
    await emailService.send({
      to: oldEmail,
      subject: `[Reassigned] ${task.refCode} ${task.task}`,
      text: `Your ${task.task} task on ${task.refCode} (${task.eventName}) has been reassigned to ${newMember.name}.`,
    });
  }
  await appendActivity({
    event: "reassigned",
    requestId: task.requestId,
    refCode: task.refCode,
    member: newMember.email,
    detail: `${task.task}: ${oldEmail || "unfilled"} -> ${newMember.email}`,
  });
}
