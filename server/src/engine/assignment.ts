/**
 * Shared assignment helpers used by the reassign/assign routes: validate a
 * specific member, award/move points, notify an assignee, and swap a task's
 * assignee. Ported from functions/src/engine/assignment.ts; Firestore → Mongo.
 *
 * Note: the old `reassignTo` field (a rule-checked signal for the client→trigger
 * path) is gone — routes call performSwap directly.
 */

import { Document, WithId } from "mongodb";
import { col, oid } from "../db";
import { calendarService } from "../services/calendar";
import { emailService } from "../services/email";
import { appendActivity } from "../lib/log";
import { isBaseEligible } from "./assign";
import { RequestDoc, Settings, TeamMember } from "../types";

/** Request states in which assigned work counts as confirmed (points awarded). */
export const CONFIRMED_STATES = ["Request Accepted", "Event Covered", "Ready To post", "Posted"];

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
  const doc = await col.team().findOne({ _id: e as never });
  if (!doc) {
    return { ok: false, reason: `${e} is not on the team`, member: null };
  }
  const member = { ...(doc as unknown as TeamMember), id: e, email: e } as TeamMember;

  if (!isBaseEligible(member, requiredSkill, request, settings)) {
    return {
      ok: false,
      reason: `${member.name} is not eligible (inactive, at strike limit, lacks "${requiredSkill}", or different campus)`,
      member: null,
    };
  }
  if (atEvent && request.eventStart && request.eventEnd) {
    const free = await calendarService.isFree(e, new Date(request.eventStart), new Date(request.eventEnd));
    if (!free) {
      return { ok: false, reason: `${member.name} is busy during the event window`, member: null };
    }
  }
  return { ok: true, reason: "", member };
}

/** Increment a member's points (negative to remove). */
export async function awardPoints(email: string, delta: number): Promise<void> {
  if (!email || !delta) return;
  await col.team().updateOne({ _id: email.toLowerCase() as never }, { $inc: { points: delta } });
}

/** Calendar hold/reminder + email for a newly confirmed assignee. */
export async function notifyAssignee(task: Document): Promise<void> {
  if (task.atEvent && task.eventStart && task.eventEnd) {
    await calendarService.createHold({
      email: task.email,
      title: `${task.refCode} ${task.task} — ${task.eventName}`,
      start: new Date(task.eventStart),
      end: new Date(task.eventEnd),
      description: task.venue || "",
    });
  } else if (task.deadline) {
    await calendarService.createReminder({
      email: task.email,
      title: `${task.refCode} ${task.task} due — ${task.eventName}`,
      due: new Date(task.deadline),
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
 * `task` is the stored task doc (must include `_id` and `requestId`).
 */
export async function performSwap(
  task: WithId<Document>,
  newMember: TeamMember,
  request: RequestDoc
): Promise<void> {
  const oldEmail = (task.email || "").toLowerCase();
  const confirmedState = CONFIRMED_STATES.includes(request.status);
  const newStatus = confirmedState ? "CONFIRMED" : "PROPOSED";
  const points = task.points || 0;

  await col.tasks().updateOne(
    { _id: task._id },
    {
      $set: {
        member: newMember.name,
        email: newMember.email,
        phone: newMember.phone || "",
        status: newStatus,
        pointsAwarded: confirmedState,
      },
    }
  );

  if (task.pointsAwarded && oldEmail) await awardPoints(oldEmail, -points);
  if (confirmedState) await awardPoints(newMember.email, points);

  if (task.task === "Event Coordinator") {
    const reqId = oid(task.requestId);
    if (reqId) {
      await col.requests().updateOne({ _id: reqId }, { $set: { coordinatorEmail: newMember.email } });
    }
    await col.tasks().updateMany(
      { requestId: task.requestId, _id: { $ne: task._id } },
      { $set: { coordinatorEmail: newMember.email } }
    );
  }

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
