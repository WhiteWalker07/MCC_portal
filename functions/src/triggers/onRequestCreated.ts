/**
 * onRequestCreated — the entry point of the workflow engine.
 *
 *   1. Allocate refCode + default campus (transactional claim / idempotent).
 *   2. Build the pipeline (Coordinator + shoot roles + derived editors | Vetter).
 *   3. Choose a member per task; write PROPOSED (or UNFILLED) task docs with the
 *      coordinatorEmail + event context denormalized on.
 *   4. Gate: Coverage <48h (or requireApprovalAlways) => Pending for POC approval
 *      and email the secretaries. Otherwise auto-confirm.
 */

import { onDocumentCreated } from "firebase-functions/v2/firestore";
import { FieldValue, Timestamp } from "firebase-admin/firestore";

import { db } from "../lib/setup";
import { getSettings, getTaskTypes, getPoints } from "../config";
import { buildPipeline } from "../engine/pipeline";
import { chooseMember } from "../engine/assign";
import { allocateRefCodeAndCampus } from "../engine/refcode";
import { confirmRequest } from "../engine/confirm";
import { calendarService } from "../services/calendar";
import { emailService } from "../services/email";
import { appendActivity } from "../lib/log";
import { RequestDoc, Settings, TeamMember } from "../types";

export const onRequestCreated = onDocumentCreated(
  "requests/{id}",
  async (event) => {
    const snap = event.data;
    if (!snap) return;
    const requestId = event.params.id;
    const reqRef = snap.ref;
    const base = snap.data() as RequestDoc;

    // 1. refCode + campus (atomic claim — guards against retries).
    const alloc = await allocateRefCodeAndCampus(requestId, base);
    if (!alloc.ok) {
      if (alloc.skipped) {
        console.log(`[onRequestCreated] ${requestId} already processed; skipping`);
      } else if (alloc.noCommittee) {
        console.warn(
          `[onRequestCreated] no committee for "${base.contactEmail}"; cannot generate refCode`
        );
      }
      return;
    }
    const { refCode, campus } = alloc;
    const request: RequestDoc = { ...base, refCode, campus };

    // 2. config + team + pipeline
    const [settings, taskTypes, pointsCfg] = await Promise.all([
      getSettings(),
      getTaskTypes(),
      getPoints(),
    ]);
    const teamSnap = await db.collection("team").get();
    const team: TeamMember[] = teamSnap.docs.map((d) => ({
      id: d.id,
      ...(d.data() as TeamMember),
    }));
    const pipeline = buildPipeline(request, taskTypes, Timestamp.now(), pointsCfg);

    // 3. choose members
    const assigned: {
      task: string;
      requiredSkill: string;
      points: number;
      atEvent: boolean;
      vertical: string;
      deadline: Timestamp | null;
      member: TeamMember | null;
      reason: string;
    }[] = [];
    const alreadyAssigned = new Set<string>();
    let coordinatorEmail = "";

    for (const pt of pipeline) {
      const { member, reason } = await chooseMember(
        pt,
        request,
        settings,
        team,
        alreadyAssigned,
        calendarService
      );
      if (member) {
        alreadyAssigned.add(member.email);
        if (pt.task === "Event Coordinator") coordinatorEmail = member.email;
      }
      assigned.push({
        task: pt.task,
        requiredSkill: pt.requiredSkill,
        points: pt.points,
        atEvent: pt.atEvent,
        vertical: pt.vertical,
        deadline: pt.deadline,
        member,
        reason,
      });
    }

    // 3b. write task docs + stamp coordinatorEmail on the request
    const batch = db.batch();
    for (const a of assigned) {
      const tRef = db.collection("tasks").doc();
      batch.set(tRef, {
        requestId,
        reqType: request.type,
        refCode,
        task: a.task,
        requiredSkill: a.requiredSkill,
        atEvent: a.atEvent,
        vertical: a.vertical,
        platform: "",
        member: a.member?.name ?? "",
        email: a.member?.email ?? "",
        phone: a.member?.phone ?? "",
        points: a.points,
        pointsAwarded: false,
        deadline: a.deadline,
        status: a.member ? "PROPOSED" : "UNFILLED",
        reason: a.member ? "" : a.reason,
        reassignTo: "",
        coordinatorEmail,
        eventName: request.eventName ?? "",
        eventStart: request.eventStart ?? null,
        eventEnd: request.eventEnd ?? null,
        venue: request.venue ?? "",
        createdAt: FieldValue.serverTimestamp(),
      });
    }
    batch.update(reqRef, { coordinatorEmail });
    await batch.commit();

    await appendActivity({
      event: "created",
      requestId,
      refCode,
      actor: request.contactEmail,
      detail: `${request.type} request created`,
    });
    for (const a of assigned) {
      await appendActivity({
        event: a.member ? "proposed" : "unfilled",
        requestId,
        refCode,
        member: a.member?.email ?? "",
        detail: a.member
          ? `${a.task} -> ${a.member.name}`
          : `${a.task} UNFILLED: ${a.reason}`,
      });
    }

    // 4. gate
    if (shouldRequireApproval(request, settings)) {
      await reqRef.update({ status: "Pending for POC approval" });
      await emailService.send({
        to: settings.secretaryEmails,
        subject: `[Approval needed] ${refCode} — ${request.eventName}`,
        text: approvalEmailText(request, refCode, assigned),
      });
      await appendActivity({
        event: "pending",
        requestId,
        refCode,
        detail: "Awaiting POC approval (event <48h or approval-always)",
      });
      return;
    }

    await confirmRequest(requestId);
  }
);

function shouldRequireApproval(request: RequestDoc, settings: Settings): boolean {
  if (settings.requireApprovalAlways) return true;
  if (request.type !== "Coverage") return false;
  if (!request.eventStart) return false;
  const hoursUntil = (request.eventStart.toMillis() - Date.now()) / 3_600_000;
  return hoursUntil < settings.slaHours;
}

function approvalEmailText(
  request: RequestDoc,
  refCode: string,
  assigned: { task: string; member: TeamMember | null; reason: string }[]
): string {
  const lines = assigned.map((a) =>
    a.member
      ? `  ${a.task}: ${a.member.name} <${a.member.email}>`
      : `  ${a.task}: UNFILLED (${a.reason})`
  );
  return (
    `Approval needed for ${refCode} — ${request.eventName} (event under ` +
    `${settingsHoursLabel()}).\n\nProposed team:\n${lines.join("\n")}\n\n` +
    `Open the Approvals view to approve or reject.\n`
  );
}

function settingsHoursLabel(): string {
  return "the approval window";
}
