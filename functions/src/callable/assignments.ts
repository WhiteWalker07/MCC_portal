/**
 * Assignment callables (auth + role gated, server-authoritative).
 *
 *   getEligibleMembers  — eligible candidates for a task type on a request (picker)
 *   listAssignableTasks — tasks the caller may act on, scoped by role
 *   requestReassign     — reassign a filled task (auto picks / manual chooses)
 *   assignTask          — fill an UNFILLED task or add an internal task type
 *
 * Reads/writes use the Admin SDK (bypass rules); authorization is enforced here
 * via serverRoles.canAssign.
 */

import { onCall, HttpsError, CallableRequest } from "firebase-functions/v2/https";
import { FieldValue, Timestamp } from "firebase-admin/firestore";

import { db } from "../lib/setup";
import { getSettings, getTaskTypes } from "../config";
import { eligibleMembers } from "../engine/assign";
import {
  validateMember,
  awardPoints,
  notifyAssignee,
  performSwap,
  CONFIRMED_STATES,
} from "../engine/assignment";
import { computeDeadline } from "../engine/pipeline";
import { resolveCallerRoles, canAssign } from "../engine/serverRoles";
import { calendarService } from "../services/calendar";
import { appendActivity } from "../lib/log";
import { RequestDoc, Settings, TaskType, TeamMember } from "../types";

const TERMINAL = new Set(["Posted", "Rejected"]);

function callerEmail(request: CallableRequest): string {
  const email = request.auth?.token?.email;
  if (!email) throw new HttpsError("unauthenticated", "Sign in required.");
  return String(email).toLowerCase();
}

async function getRequest(requestId: string): Promise<RequestDoc> {
  const snap = await db.collection("requests").doc(requestId).get();
  if (!snap.exists) throw new HttpsError("not-found", "Request not found.");
  return snap.data() as RequestDoc;
}

async function taskTypeByName(name: string): Promise<TaskType> {
  const tt = (await getTaskTypes()).find((t) => t.task === name);
  if (!tt) throw new HttpsError("not-found", `Unknown task type: ${name}`);
  return tt;
}

async function loadTeam(): Promise<TeamMember[]> {
  const snap = await db.collection("team").get();
  return snap.docs.map((d) => ({ id: d.id, ...(d.data() as TeamMember) }));
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
    if (!memberEmail) {
      throw new HttpsError("invalid-argument", "Pick a member for manual mode.");
    }
    const v = await validateMember(memberEmail, requiredSkill, atEvent, request, settings);
    if (!v.ok || !v.member) throw new HttpsError("failed-precondition", v.reason);
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
  if (pool.length === 0) {
    throw new HttpsError("failed-precondition", `No eligible member with "${requiredSkill}".`);
  }
  return pool[0];
}

// ---------------------------------------------------------------------------

export const getEligibleMembers = onCall(async (request) => {
  const email = callerEmail(request);
  const { requestId, taskType, excludeEmail } = request.data || {};
  const roles = await resolveCallerRoles(email);
  const req = await getRequest(requestId);
  const tt = await taskTypeByName(taskType);
  if (!canAssign(roles, tt.vertical || "", req.coordinatorEmail || "")) {
    throw new HttpsError("permission-denied", "Not allowed to assign this task.");
  }
  const settings = await getSettings();
  const team = await loadTeam();
  const exclude = new Set<string>(excludeEmail ? [String(excludeEmail).toLowerCase()] : []);
  const pool = await eligibleMembers(
    tt.requiredSkill,
    tt.atEvent,
    req,
    settings,
    team,
    calendarService,
    exclude
  );
  return {
    members: pool.map((m) => ({
      email: m.email,
      name: m.name,
      vertical: m.vertical || "",
      campus: m.campus || "",
      points: m.points || 0,
      strikes: m.strikes || 0,
    })),
  };
});

export const listAssignableTasks = onCall(async (request) => {
  const email = callerEmail(request);
  const roles = await resolveCallerRoles(email);

  let taskDocs: FirebaseFirestore.QueryDocumentSnapshot[] = [];
  if (roles.isSecretary || roles.isAdmin || roles.isSecondYear) {
    taskDocs = (await db.collection("tasks").get()).docs;
  } else {
    const seen = new Set<string>();
    const add = (snap: FirebaseFirestore.QuerySnapshot) => {
      for (const d of snap.docs) {
        if (!seen.has(d.id)) {
          seen.add(d.id);
          taskDocs.push(d);
        }
      }
    };
    add(await db.collection("tasks").where("coordinatorEmail", "==", roles.email).get());
    if (roles.domainHeadOf) {
      add(await db.collection("tasks").where("vertical", "==", roles.domainHeadOf).get());
    }
  }
  if (taskDocs.length === 0) return { groups: [] };

  const reqIds = [...new Set(taskDocs.map((d) => d.data().requestId))];
  const reqMap: Record<string, RequestDoc> = {};
  await Promise.all(
    reqIds.map(async (id) => {
      const s = await db.collection("requests").doc(id).get();
      if (s.exists) reqMap[id] = s.data() as RequestDoc;
    })
  );

  const groups: Record<string, any> = {};
  for (const d of taskDocs) {
    const t = d.data();
    const req = reqMap[t.requestId];
    if (!req || TERMINAL.has(req.status)) continue;
    if (!groups[t.requestId]) {
      groups[t.requestId] = {
        requestId: t.requestId,
        refCode: req.refCode || "",
        eventName: req.eventName || "",
        type: req.type,
        status: req.status,
        campus: req.campus || "",
        tasks: [],
      };
    }
    groups[t.requestId].tasks.push({
      id: d.id,
      task: t.task,
      status: t.status,
      member: t.member || "",
      email: t.email || "",
      vertical: t.vertical || "",
      requiredSkill: t.requiredSkill || "",
      atEvent: !!t.atEvent,
    });
  }
  return { groups: Object.values(groups) };
});

export const requestReassign = onCall(async (request) => {
  const email = callerEmail(request);
  const { taskId, mode, memberEmail } = request.data || {};
  if (!taskId) throw new HttpsError("invalid-argument", "taskId required.");

  const tRef = db.collection("tasks").doc(taskId);
  const tSnap = await tRef.get();
  if (!tSnap.exists) throw new HttpsError("not-found", "Task not found.");
  const task = tSnap.data() as FirebaseFirestore.DocumentData;

  const roles = await resolveCallerRoles(email);
  const req = await getRequest(task.requestId);
  if (!canAssign(roles, task.vertical || "", req.coordinatorEmail || "")) {
    throw new HttpsError("permission-denied", "Not allowed to reassign this task.");
  }

  const settings = await getSettings();
  const member = await resolveMember(
    mode,
    memberEmail,
    task.requiredSkill,
    task.atEvent,
    req,
    settings,
    [task.email]
  );
  await performSwap(tRef, task, member, req);
  return { ok: true, member: { name: member.name, email: member.email } };
});

export const assignTask = onCall(async (request) => {
  const email = callerEmail(request);
  const { taskId, requestId, taskType, mode, memberEmail } = request.data || {};
  const roles = await resolveCallerRoles(email);
  const settings = await getSettings();

  // (a) Fill an existing UNFILLED task
  if (taskId) {
    const tRef = db.collection("tasks").doc(taskId);
    const tSnap = await tRef.get();
    if (!tSnap.exists) throw new HttpsError("not-found", "Task not found.");
    const task = tSnap.data() as FirebaseFirestore.DocumentData;
    const req = await getRequest(task.requestId);
    if (!canAssign(roles, task.vertical || "", req.coordinatorEmail || "")) {
      throw new HttpsError("permission-denied", "Not allowed to assign this task.");
    }
    if (task.email) {
      throw new HttpsError("failed-precondition", "Task already assigned — use reassign.");
    }
    const member = await resolveMember(
      mode,
      memberEmail,
      task.requiredSkill,
      task.atEvent,
      req,
      settings,
      []
    );
    await performSwap(tRef, task, member, req);
    return { ok: true };
  }

  // (b) Add a new internal task type
  if (!requestId || !taskType) {
    throw new HttpsError("invalid-argument", "taskId, or requestId + taskType, required.");
  }
  const req = await getRequest(requestId);
  const tt = await taskTypeByName(taskType);
  if (!tt.internalAssignable) {
    throw new HttpsError("failed-precondition", `${taskType} can't be added manually.`);
  }
  if (!canAssign(roles, tt.vertical || "", req.coordinatorEmail || "")) {
    throw new HttpsError("permission-denied", "Not allowed to add this task.");
  }

  // Event Coordinator is unique per request — reassign the existing one.
  if (taskType === "Event Coordinator") {
    const existing = await db
      .collection("tasks")
      .where("requestId", "==", requestId)
      .where("task", "==", "Event Coordinator")
      .limit(1)
      .get();
    if (!existing.empty) {
      const exDoc = existing.docs[0];
      const member = await resolveMember(
        mode,
        memberEmail,
        tt.requiredSkill,
        tt.atEvent,
        req,
        settings,
        [exDoc.data().email]
      );
      await performSwap(exDoc.ref, exDoc.data(), member, req);
      return { ok: true, reassignedExisting: true };
    }
  }

  const member = await resolveMember(
    mode,
    memberEmail,
    tt.requiredSkill,
    tt.atEvent,
    req,
    settings,
    []
  );
  const confirmedState = CONFIRMED_STATES.includes(req.status);
  const tRef = db.collection("tasks").doc();
  const taskDoc = {
    requestId,
    reqType: req.type,
    refCode: req.refCode || "",
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
    deadline: computeDeadline(tt, req, Timestamp.now()),
    status: confirmedState ? "CONFIRMED" : "PROPOSED",
    reason: "",
    reassignTo: "",
    coordinatorEmail: req.coordinatorEmail || "",
    eventName: req.eventName || "",
    eventStart: req.eventStart || null,
    eventEnd: req.eventEnd || null,
    venue: req.venue || "",
    createdAt: FieldValue.serverTimestamp(),
  };

  const batch = db.batch();
  batch.set(tRef, taskDoc);
  if (confirmedState) awardPoints(batch, member.email, tt.points);
  await batch.commit();

  await notifyAssignee(taskDoc);
  await appendActivity({
    event: "manual-assign",
    requestId,
    refCode: req.refCode,
    member: member.email,
    detail: `${tt.task} -> ${member.name} (${mode})`,
  });
  return { ok: true };
});

/**
 * markReadyToPost — the human gate that advances a Coverage request from
 * 'Event Covered' to 'Ready To post' (which triggers onReadyToPost scheduling).
 * Gated to the event's coordinator, a secretary, an admin, or a 2nd-year.
 */
export const markReadyToPost = onCall(async (request) => {
  const email = callerEmail(request);
  const { requestId } = request.data || {};
  if (!requestId) throw new HttpsError("invalid-argument", "requestId required.");

  const roles = await resolveCallerRoles(email);
  const reqRef = db.collection("requests").doc(requestId);
  const snap = await reqRef.get();
  if (!snap.exists) throw new HttpsError("not-found", "Request not found.");
  const req = snap.data() as RequestDoc;

  if (!canAssign(roles, "", req.coordinatorEmail || "")) {
    throw new HttpsError("permission-denied", "Not allowed to mark this request ready.");
  }
  if (req.status !== "Event Covered") {
    throw new HttpsError("failed-precondition", "Request is not Event Covered.");
  }

  await reqRef.update({
    status: "Ready To post",
    readyBy: email,
    readyAt: FieldValue.serverTimestamp(),
  });
  return { ok: true };
});
