/**
 * Workflow engine — the former Firestore triggers, now plain async functions
 * called synchronously from the routes.
 *
 *   processNewRequest  ← onRequestCreated
 *   rejectRequest      ← onRequestDecided (reject branch; approve = confirmRequest)
 *   completeTask       ← onTaskCompleted
 *   schedulePosts      ← onReadyToPost
 *   runDeadlineCheck   ← scheduledDeadlineCheck
 *
 * confirmRequest lives in engine/confirm.ts (shared by auto-accept + approve).
 */

import { Document, WithId } from "mongodb";
import { col, oid } from "../db";
import { getSettings, getTaskTypes, getPoints, getPlatforms, getSlots } from "../config";
import { buildPipeline } from "../engine/pipeline";
import { chooseMember } from "../engine/assign";
import { allocateRefCodeAndCampus } from "../engine/refcode";
import { confirmRequest } from "../engine/confirm";
import { finalPoints } from "../engine/points";
import { findNextSlot } from "../engine/posting";
import { calendarService } from "../services/calendar";
import { emailService } from "../services/email";
import { appendActivity } from "../lib/log";
import { findRequest, loadTeam } from "../lib/docs";
import { RequestDoc, Settings, TeamMember } from "../types";

// ── onRequestCreated ────────────────────────────────────────────────────────

export async function processNewRequest(requestId: string): Promise<void> {
  const base = (await findRequest(requestId)) as (RequestDoc & WithId<Document>) | null;
  if (!base) return;

  // 1. refCode + campus (atomic claim).
  const alloc = await allocateRefCodeAndCampus(requestId, base);
  if (!alloc.ok) {
    if (alloc.noCommittee) {
      console.warn(`[processNewRequest] no committee for "${base.contactEmail}"; cannot generate refCode`);
    }
    return;
  }
  const { refCode, campus } = alloc;
  const request: RequestDoc = { ...base, id: requestId, refCode, campus };

  // 2. config + team + pipeline
  const [settings, taskTypes, pointsCfg] = await Promise.all([getSettings(), getTaskTypes(), getPoints()]);
  const team = await loadTeam();
  const pipeline = buildPipeline(request, taskTypes, new Date(), pointsCfg);

  // 3. choose members
  const assigned: Array<{
    task: string;
    requiredSkill: string;
    points: number;
    atEvent: boolean;
    vertical: string;
    deadline: Date | null;
    member: TeamMember | null;
    reason: string;
  }> = [];
  const alreadyAssigned = new Set<string>();
  let coordinatorEmail = "";

  for (const pt of pipeline) {
    const { member, reason } = await chooseMember(pt, request, settings, team, alreadyAssigned, calendarService);
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
  const now = new Date();
  const taskDocs = assigned.map((a) => ({
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
    coordinatorEmail,
    eventName: request.eventName ?? "",
    eventStart: request.eventStart ?? null,
    eventEnd: request.eventEnd ?? null,
    venue: request.venue ?? "",
    createdAt: now,
  }));
  if (taskDocs.length) await col.tasks().insertMany(taskDocs);
  await col.requests().updateOne({ _id: base._id }, { $set: { coordinatorEmail } });

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
      detail: a.member ? `${a.task} -> ${a.member.name}` : `${a.task} UNFILLED: ${a.reason}`,
    });
  }

  // 4. gate
  if (shouldRequireApproval(request, settings)) {
    await col.requests().updateOne({ _id: base._id }, { $set: { status: "Pending for POC approval" } });
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

function shouldRequireApproval(request: RequestDoc, settings: Settings): boolean {
  if (settings.requireApprovalAlways) return true;
  if (request.type !== "Coverage") return false;
  if (!request.eventStart) return false;
  const hoursUntil = (new Date(request.eventStart).getTime() - Date.now()) / 3_600_000;
  return hoursUntil < settings.slaHours;
}

function approvalEmailText(
  request: RequestDoc,
  refCode: string,
  assigned: Array<{ task: string; member: TeamMember | null; reason: string }>
): string {
  const lines = assigned.map((a) =>
    a.member ? `  ${a.task}: ${a.member.name} <${a.member.email}>` : `  ${a.task}: UNFILLED (${a.reason})`
  );
  return (
    `Approval needed for ${refCode} — ${request.eventName} (event under the approval window).\n\n` +
    `Proposed team:\n${lines.join("\n")}\n\nOpen the Approvals view to approve or reject.\n`
  );
}

// ── onRequestDecided (reject branch) ─────────────────────────────────────────

export async function rejectRequest(requestId: string, reason: string, by: string): Promise<void> {
  const _id = oid(requestId);
  if (!_id) return;
  const request = await col.requests().findOne({ _id });
  if (!request) return;
  const refCode = request.refCode || "";

  await col.requests().updateOne(
    { _id },
    { $set: { status: "Rejected", decisionBy: by, rejectReason: reason || "", decisionAt: new Date() } }
  );
  await emailService.send({
    to: request.contactEmail,
    subject: `[Rejected] ${refCode} — ${request.eventName}`,
    text:
      `Your request ${refCode} (${request.eventName}) was not approved.` +
      (reason ? `\n\nReason: ${reason}` : ""),
  });
  await appendActivity({
    event: "rejected",
    requestId,
    refCode,
    actor: by || "secretary",
    detail: reason || "Rejected by POC",
  });
}

// ── onTaskCompleted ──────────────────────────────────────────────────────────

/** Advance a request after one of its tasks transitions to DONE. */
export async function completeTask(taskId: string): Promise<void> {
  const _id = oid(taskId);
  if (!_id) return;
  const after = await col.tasks().findOne({ _id });
  if (!after || after.status !== "DONE") return;

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
    const baseP = after.points || 0;
    const completedMs = after.completedAt ? new Date(after.completedAt).getTime() : Date.now();
    const refTs = reqType === "Coverage" ? after.eventEnd : after.createdAt;
    const refMs = refTs ? new Date(refTs).getTime() : null;
    if (refMs) {
      const turnaroundHours = (completedMs - refMs) / 3_600_000;
      const final = finalPoints(baseP, turnaroundHours, cfg);
      const delta = final - baseP;
      await col.tasks().updateOne({ _id }, { $set: { points: final, timingApplied: true } });
      if (delta !== 0) {
        await col.team().updateOne(
          { _id: String(after.email).toLowerCase() as never },
          { $inc: { points: delta } }
        );
        await appendActivity({
          event: "points-adjust",
          requestId,
          refCode,
          member: after.email,
          detail: `${after.task}: ${delta > 0 ? "+" : ""}${delta} pts (turnaround ${Math.round(turnaroundHours)}h)`,
        });
      }
    } else {
      await col.tasks().updateOne({ _id }, { $set: { timingApplied: true } });
    }
  }

  const reqId = oid(requestId);
  if (!reqId) return;
  const request = await col.requests().findOne({ _id: reqId });
  if (!request) return;
  if (request.status !== "Request Accepted") return; // only advance from accepted

  const tasks = await col.tasks().find({ requestId }).toArray();

  if (reqType === "Coverage") {
    const deliverables = tasks.filter((t) => t.task !== "Event Coordinator" && t.status !== "UNFILLED");
    if (deliverables.length === 0) return;
    if (deliverables.every((t) => t.status === "DONE")) {
      await col.requests().updateOne({ _id: reqId }, { $set: { status: "Event Covered" } });
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
      await col.requests().updateOne({ _id: reqId }, { $set: { status: "Ready To post" } });
      await appendActivity({
        event: "ready",
        requestId,
        refCode,
        actor: "engine",
        detail: "Vetting done; ready to post",
      });
      await schedulePosts(requestId);
    }
  }
}

// ── onReadyToPost ────────────────────────────────────────────────────────────

function postTaskDoc(
  requestId: string,
  req: Document,
  platform: string,
  email: string,
  points: number,
  scheduledAt: Date | null,
  status: string,
  reason: string
) {
  return {
    requestId,
    reqType: req.type,
    refCode: req.refCode || "",
    task: "Post",
    requiredSkill: "",
    atEvent: false,
    vertical: "",
    platform,
    member: email || "",
    email: email || "",
    phone: "",
    points,
    pointsAwarded: false,
    deadline: scheduledAt,
    scheduledAt,
    status,
    reason: reason || "",
    coordinatorEmail: req.coordinatorEmail || "",
    eventName: req.eventName || "",
    eventStart: req.eventStart || null,
    eventEnd: req.eventEnd || null,
    venue: req.venue || "",
    createdAt: new Date(),
  };
}

/** Schedule social posts for a request now at 'Ready To post'. Idempotent. */
export async function schedulePosts(requestId: string): Promise<void> {
  const _id = oid(requestId);
  if (!_id) return;
  const request = await col.requests().findOne({ _id });
  if (!request || request.status !== "Ready To post") return;
  const refCode = request.refCode || "";

  // Idempotency: skip if posts were already created.
  const existing = await col.tasks().find({ requestId }).toArray();
  if (existing.some((d) => d.task === "Post")) return;

  const platforms = [...new Set((request.platforms || []).filter(Boolean))] as string[];
  if (platforms.length === 0) {
    await col.requests().updateOne({ _id }, { $set: { status: "Posted", posts: [] } });
    await appendActivity({ event: "posted", requestId, refCode, actor: "engine", detail: "No platforms selected" });
    return;
  }

  const [platformRows, slots] = await Promise.all([getPlatforms(), getSlots()]);
  const nowMs = Date.now();

  // Existing scheduled posts -> taken slots per platform + handler load.
  const scheduled = await col.tasks().find({ status: "SCHEDULED", task: "Post" }).toArray();
  const takenByPlatform = new Map<string, Set<number>>();
  const loadByEmail = new Map<string, number>();
  for (const t of scheduled) {
    if (!takenByPlatform.has(t.platform)) takenByPlatform.set(t.platform, new Set());
    if (t.scheduledAt) takenByPlatform.get(t.platform)!.add(new Date(t.scheduledAt).getTime());
    const e = (t.email || "").toLowerCase();
    if (e) loadByEmail.set(e, (loadByEmail.get(e) || 0) + 1);
  }

  const toInsert: ReturnType<typeof postTaskDoc>[] = [];
  const posts: Array<{ platform: string; handlerEmail: string; scheduledAt: Date | null; status: string }> = [];
  const toNotify: ReturnType<typeof postTaskDoc>[] = [];
  const handlerEmails = new Set<string>();

  for (const platform of platforms) {
    const handlers = platformRows.filter((r) => r.platform === platform && r.active);

    if (handlers.length === 0) {
      toInsert.push(postTaskDoc(requestId, request, platform, "", 0, null, "UNFILLED", "no active handler"));
      posts.push({ platform, handlerEmail: "", scheduledAt: null, status: "UNFILLED" });
      continue;
    }

    // Load-balance only when there are multiple handlers (fewest current posts).
    let handler = handlers[0];
    let bestLoad = Infinity;
    for (const h of handlers) {
      const load = loadByEmail.get((h.handlerEmail || "").toLowerCase()) || 0;
      if (load < bestLoad) {
        bestLoad = load;
        handler = h;
      }
    }

    if (!takenByPlatform.has(platform)) takenByPlatform.set(platform, new Set());
    const taken = takenByPlatform.get(platform)!;
    const slot = findNextSlot(slots, taken, nowMs);
    taken.add(slot.getTime());
    loadByEmail.set((handler.handlerEmail || "").toLowerCase(), bestLoad + 1);

    const doc = postTaskDoc(requestId, request, platform, handler.handlerEmail, handler.points || 0, slot, "SCHEDULED", "");
    toInsert.push(doc);
    posts.push({ platform, handlerEmail: handler.handlerEmail, scheduledAt: slot, status: "SCHEDULED" });
    toNotify.push(doc);
    if (handler.handlerEmail) handlerEmails.add(handler.handlerEmail.toLowerCase());
  }

  if (toInsert.length) await col.tasks().insertMany(toInsert);

  // Award post points to handlers who are team members.
  const teamHandlers = new Set(
    (await col.team().find({ _id: { $in: [...handlerEmails] as never[] } }).toArray()).map((d) => String(d._id))
  );
  for (const post of posts) {
    if (post.status !== "SCHEDULED" || !post.handlerEmail) continue;
    const e = post.handlerEmail.toLowerCase();
    if (!teamHandlers.has(e)) continue;
    const row = platformRows.find((r) => r.platform === post.platform && r.handlerEmail === post.handlerEmail);
    const pts = row?.points || 0;
    if (pts) await col.team().updateOne({ _id: e as never }, { $inc: { points: pts } });
  }

  await col.requests().updateOne({ _id }, { $set: { status: "Posted", posts } });

  for (const t of toNotify) {
    if (t.scheduledAt) {
      await calendarService.createHold({
        email: t.email,
        title: `${refCode} Post (${t.platform}) — ${t.eventName}`,
        start: t.scheduledAt,
        end: t.scheduledAt,
        description: t.eventName,
      });
    }
    await emailService.send({
      to: t.email,
      subject: `[Scheduled] ${refCode} ${t.platform} post — ${t.eventName}`,
      text: `A ${t.platform} post for ${t.eventName} (${refCode}) is scheduled${
        t.scheduledAt ? ` for ${t.scheduledAt.toLocaleString()}` : ""
      }.`,
    });
  }
  await appendActivity({
    event: "posted",
    requestId,
    refCode,
    actor: "engine",
    detail: `${posts.filter((p) => p.status === "SCHEDULED").length} post(s) scheduled`,
  });
}

// ── scheduledDeadlineCheck ───────────────────────────────────────────────────

export async function runDeadlineCheck(): Promise<{ late: number }> {
  const settings = await getSettings();
  const now = new Date();

  const overdue = await col.tasks().find({ status: "CONFIRMED", deadline: { $lt: now } }).toArray();
  if (overdue.length === 0) {
    console.log("[deadlineCheck] nothing overdue");
    return { late: 0 };
  }

  let lateCount = 0;
  for (const t of overdue) {
    if (t.struck === true) continue;
    if (t.task === "Event Coordinator") continue; // coordinator isn't a deliverable

    const coord = (t.coordinatorEmail || "").toLowerCase();
    const assignee = (t.email || "").toLowerCase();
    try {
      await col.tasks().updateOne({ _id: t._id }, { $set: { status: "LATE", struck: true } });
      if (coord) await col.team().updateOne({ _id: coord as never }, { $inc: { strikes: 1 } });
      if (settings.strikeAssigneeToo && assignee) {
        await col.team().updateOne({ _id: assignee as never }, { $inc: { strikes: 1 } });
      }
      lateCount++;

      const to = [coord, settings.strikeAssigneeToo ? assignee : "", settings.headEmail || ""].filter(Boolean);
      await emailService.send({
        to,
        subject: `[Late] ${t.refCode} ${t.task}`,
        text: `${t.task} on ${t.refCode} (${t.eventName}) is past its deadline and is now marked LATE.`,
      });
      await appendActivity({ event: "late", requestId: t.requestId, refCode: t.refCode, member: assignee, detail: `${t.task} marked LATE` });
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
      console.error(`[deadlineCheck] failed for task ${String(t._id)}:`, (err as Error).message);
    }
  }
  console.log(`[deadlineCheck] marked ${lateCount} task(s) LATE`);
  return { late: lateCount };
}
