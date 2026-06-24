/**
 * Assignment routes (auth + role gated, server-authoritative). Ported from
 * functions/src/callable/assignments.ts.
 *
 *   GET  /api/assignable-tasks   listAssignableTasks
 *   POST /api/eligible-members   getEligibleMembers
 *   POST /api/reassign           requestReassign
 *   POST /api/assign             assignTask (fill UNFILLED, or add an internal task)
 *   POST /api/mark-ready         markReadyToPost
 *
 * Authorization is via serverRoles.canAssign (attached as req.roles).
 */

import { Router, Request, Response } from "express";
import { Document, WithId } from "mongodb";
import { col, oid } from "../db";
import { findRequest, findTask, loadTeam } from "../lib/docs";
import { requireAuth, attachRoles, getEmail } from "../auth/middleware";
import { asyncHandler, httpError } from "../lib/http";
import { getSettings, getTaskTypes } from "../config";
import { eligibleMembers } from "../engine/assign";
import { validateMember, awardPoints, notifyAssignee, performSwap, CONFIRMED_STATES } from "../engine/assignment";
import { computeDeadline } from "../engine/pipeline";
import { canAssign } from "../engine/serverRoles";
import { calendarService } from "../services/calendar";
import { schedulePosts } from "../services/workflow";
import { appendActivity } from "../lib/log";
import { RequestDoc, Settings, TaskType, TeamMember } from "../types";

export const assignmentsRouter = Router();

const TERMINAL = new Set(["Posted", "Rejected"]);

async function getRequestOr404(requestId: string): Promise<RequestDoc & WithId<Document>> {
  const r = (await findRequest(requestId)) as (RequestDoc & WithId<Document>) | null;
  if (!r) throw httpError("not-found", "Request not found.");
  return r;
}

async function taskTypeByName(name: string): Promise<TaskType> {
  const tt = (await getTaskTypes()).find((t) => t.task === name);
  if (!tt) throw httpError("not-found", `Unknown task type: ${name}`);
  return tt;
}

/** Resolve a member by auto-pick or manual choice, validated for the task. */
async function resolveMember(
  mode: string,
  memberEmail: string | undefined,
  requiredSkill: string,
  atEvent: boolean,
  request: RequestDoc,
  settings: Settings,
  exclude: string[]
): Promise<TeamMember> {
  if (mode === "manual") {
    if (!memberEmail) throw httpError("invalid-argument", "Pick a member for manual mode.");
    const v = await validateMember(memberEmail, requiredSkill, atEvent, request, settings);
    if (!v.ok || !v.member) throw httpError("failed-precondition", v.reason);
    return v.member;
  }
  const team = await loadTeam();
  const pool = await eligibleMembers(
    requiredSkill,
    atEvent,
    request,
    settings,
    team,
    calendarService,
    new Set(exclude.map((e) => e.toLowerCase()))
  );
  if (pool.length === 0) throw httpError("failed-precondition", `No eligible member with "${requiredSkill}".`);
  return pool[0];
}

// ── listAssignableTasks ──────────────────────────────────────────────────────
assignmentsRouter.get(
  "/api/assignable-tasks",
  requireAuth,
  attachRoles,
  asyncHandler(async (req: Request, res: Response) => {
    const roles = req.roles!;

    let taskDocs: WithId<Document>[] = [];
    if (roles.isSecretary || roles.isAdmin || roles.isSecondYear) {
      taskDocs = await col.tasks().find({}).toArray();
    } else {
      const seen = new Set<string>();
      const add = (docs: WithId<Document>[]) => {
        for (const d of docs) {
          const id = String(d._id);
          if (!seen.has(id)) {
            seen.add(id);
            taskDocs.push(d);
          }
        }
      };
      add(await col.tasks().find({ coordinatorEmail: roles.email }).toArray());
      if (roles.domainHeadOf) add(await col.tasks().find({ vertical: roles.domainHeadOf }).toArray());
    }
    if (taskDocs.length === 0) {
      res.json({ groups: [] });
      return;
    }

    const reqIds = [...new Set(taskDocs.map((d) => d.requestId as string))];
    const reqMap: Record<string, RequestDoc & WithId<Document>> = {};
    await Promise.all(
      reqIds.map(async (id) => {
        const r = (await findRequest(id)) as (RequestDoc & WithId<Document>) | null;
        if (r) reqMap[id] = r;
      })
    );

    const groups: Record<string, Record<string, unknown>> = {};
    for (const d of taskDocs) {
      const reqId = d.requestId as string;
      const request = reqMap[reqId];
      if (!request || TERMINAL.has(request.status)) continue;
      if (!groups[reqId]) {
        groups[reqId] = {
          requestId: reqId,
          refCode: request.refCode || "",
          eventName: request.eventName || "",
          type: request.type,
          status: request.status,
          campus: request.campus || "",
          tasks: [],
        };
      }
      (groups[reqId].tasks as unknown[]).push({
        id: String(d._id),
        task: d.task,
        status: d.status,
        member: d.member || "",
        email: d.email || "",
        vertical: d.vertical || "",
        requiredSkill: d.requiredSkill || "",
        atEvent: !!d.atEvent,
      });
    }
    res.json({ groups: Object.values(groups) });
  })
);

// ── getEligibleMembers ───────────────────────────────────────────────────────
assignmentsRouter.post(
  "/api/eligible-members",
  requireAuth,
  attachRoles,
  asyncHandler(async (req: Request, res: Response) => {
    const roles = req.roles!;
    const { requestId, taskType, excludeEmail } = req.body || {};
    const request = await getRequestOr404(requestId);
    const tt = await taskTypeByName(taskType);
    if (!canAssign(roles, tt.vertical || "", request.coordinatorEmail || "")) {
      throw httpError("permission-denied", "Not allowed to assign this task.");
    }
    const settings = await getSettings();
    const team = await loadTeam();
    const exclude = new Set<string>(excludeEmail ? [String(excludeEmail).toLowerCase()] : []);
    const pool = await eligibleMembers(tt.requiredSkill, tt.atEvent, request, settings, team, calendarService, exclude);
    res.json({
      members: pool.map((m) => ({
        email: m.email,
        name: m.name,
        vertical: m.vertical || "",
        campus: m.campus || "",
        points: m.points || 0,
        strikes: m.strikes || 0,
      })),
    });
  })
);

// ── requestReassign ──────────────────────────────────────────────────────────
assignmentsRouter.post(
  "/api/reassign",
  requireAuth,
  attachRoles,
  asyncHandler(async (req: Request, res: Response) => {
    const roles = req.roles!;
    const { taskId, mode, memberEmail } = req.body || {};
    if (!taskId) throw httpError("invalid-argument", "taskId required.");

    const task = await findTask(taskId);
    if (!task) throw httpError("not-found", "Task not found.");

    const request = await getRequestOr404(task.requestId);
    if (!canAssign(roles, task.vertical || "", request.coordinatorEmail || "")) {
      throw httpError("permission-denied", "Not allowed to reassign this task.");
    }

    const settings = await getSettings();
    const member = await resolveMember(mode, memberEmail, task.requiredSkill, task.atEvent, request, settings, [task.email]);
    await performSwap(task, member, request);
    res.json({ ok: true, member: { name: member.name, email: member.email } });
  })
);

// ── assignTask ───────────────────────────────────────────────────────────────
assignmentsRouter.post(
  "/api/assign",
  requireAuth,
  attachRoles,
  asyncHandler(async (req: Request, res: Response) => {
    const roles = req.roles!;
    const { taskId, requestId, taskType, mode, memberEmail } = req.body || {};
    const settings = await getSettings();

    // (a) Fill an existing UNFILLED task
    if (taskId) {
      const task = await findTask(taskId);
      if (!task) throw httpError("not-found", "Task not found.");
      const request = await getRequestOr404(task.requestId);
      if (!canAssign(roles, task.vertical || "", request.coordinatorEmail || "")) {
        throw httpError("permission-denied", "Not allowed to assign this task.");
      }
      if (task.email) throw httpError("failed-precondition", "Task already assigned — use reassign.");
      const member = await resolveMember(mode, memberEmail, task.requiredSkill, task.atEvent, request, settings, []);
      await performSwap(task, member, request);
      res.json({ ok: true });
      return;
    }

    // (b) Add a new internal task type
    if (!requestId || !taskType) {
      throw httpError("invalid-argument", "taskId, or requestId + taskType, required.");
    }
    const request = await getRequestOr404(requestId);
    const tt = await taskTypeByName(taskType);
    if (!tt.internalAssignable) throw httpError("failed-precondition", `${taskType} can't be added manually.`);
    if (!canAssign(roles, tt.vertical || "", request.coordinatorEmail || "")) {
      throw httpError("permission-denied", "Not allowed to add this task.");
    }

    // Event Coordinator is unique per request — reassign the existing one.
    if (taskType === "Event Coordinator") {
      const existing = await col.tasks().findOne({ requestId, task: "Event Coordinator" });
      if (existing) {
        const member = await resolveMember(mode, memberEmail, tt.requiredSkill, tt.atEvent, request, settings, [existing.email]);
        await performSwap(existing, member, request);
        res.json({ ok: true, reassignedExisting: true });
        return;
      }
    }

    const member = await resolveMember(mode, memberEmail, tt.requiredSkill, tt.atEvent, request, settings, []);
    const confirmedState = CONFIRMED_STATES.includes(request.status);
    const taskDoc = {
      requestId,
      reqType: request.type,
      refCode: request.refCode || "",
      task: tt.task,
      requiredSkill: tt.requiredSkill,
      atEvent: tt.atEvent,
      vertical: tt.vertical || "",
      platform: "",
      member: member.name,
      email: member.email,
      phone: member.phone || "",
      points: tt.points,
      pointsAwarded: confirmedState,
      deadline: computeDeadline(tt, request, new Date()),
      status: confirmedState ? "CONFIRMED" : "PROPOSED",
      reason: "",
      coordinatorEmail: request.coordinatorEmail || "",
      eventName: request.eventName || "",
      eventStart: request.eventStart || null,
      eventEnd: request.eventEnd || null,
      venue: request.venue || "",
      createdAt: new Date(),
    };

    await col.tasks().insertOne(taskDoc);
    if (confirmedState) await awardPoints(member.email, tt.points);

    await notifyAssignee(taskDoc);
    await appendActivity({
      event: "manual-assign",
      requestId,
      refCode: request.refCode,
      member: member.email,
      detail: `${tt.task} -> ${member.name} (${mode})`,
    });
    res.json({ ok: true });
  })
);

// ── markReadyToPost ──────────────────────────────────────────────────────────
assignmentsRouter.post(
  "/api/mark-ready",
  requireAuth,
  attachRoles,
  asyncHandler(async (req: Request, res: Response) => {
    const roles = req.roles!;
    const { requestId } = req.body || {};
    if (!requestId) throw httpError("invalid-argument", "requestId required.");

    const _id = oid(requestId);
    if (!_id) throw httpError("invalid-argument", "Bad id.");
    const request = await col.requests().findOne({ _id });
    if (!request) throw httpError("not-found", "Request not found.");

    if (!canAssign(roles, "", request.coordinatorEmail || "")) {
      throw httpError("permission-denied", "Not allowed to mark this request ready.");
    }
    if (request.status !== "Event Covered") {
      throw httpError("failed-precondition", "Request is not Event Covered.");
    }

    await col.requests().updateOne(
      { _id },
      { $set: { status: "Ready To post", readyBy: getEmail(req), readyAt: new Date() } }
    );
    await schedulePosts(requestId);
    res.json({ ok: true });
  })
);
