/**
 * onReadyToPost — schedules social posts when a request reaches 'Ready To post'
 * (Coverage via Mark-ready, or Post via the Vetter completing).
 *
 * Per chosen platform: route to its active handler (load-balance only when a
 * platform has multiple handler rows), create a SCHEDULED Post task in the next
 * free 11/14/17 slot for that platform, award the handler post points (if they
 * are a team member), notify them, and denormalize a `posts` summary onto the
 * request. Then set status 'Posted'. Idempotent (skips if posts already exist).
 */

import { onDocumentUpdated } from "firebase-functions/v2/firestore";
import { FieldValue } from "firebase-admin/firestore";

import { db } from "../lib/setup";
import { getPlatforms, getSlots } from "../config";
import { findNextSlot } from "../engine/posting";
import { calendarService } from "../services/calendar";
import { emailService } from "../services/email";
import { appendActivity } from "../lib/log";
import { RequestDoc } from "../types";

function postTaskDoc(
  requestId: string,
  req: RequestDoc,
  platform: string,
  email: string,
  points: number,
  scheduledAt: FirebaseFirestore.Timestamp | null,
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
    reassignTo: "",
    coordinatorEmail: req.coordinatorEmail || "",
    eventName: req.eventName || "",
    eventStart: req.eventStart || null,
    eventEnd: req.eventEnd || null,
    venue: req.venue || "",
    createdAt: FieldValue.serverTimestamp(),
  };
}

export const onReadyToPost = onDocumentUpdated("requests/{id}", async (event) => {
  const before = event.data?.before.data();
  const after = event.data?.after.data();
  if (!before || !after) return;
  if (before.status === "Ready To post" || after.status !== "Ready To post") return;

  const requestId = event.params.id;
  const reqRef = event.data!.after.ref;
  const request = after as RequestDoc;
  const refCode = request.refCode || "";

  // Idempotency: skip if posts were already created.
  const existing = await db.collection("tasks").where("requestId", "==", requestId).get();
  if (existing.docs.some((d) => d.data().task === "Post")) return;

  const platforms = [...new Set((request.platforms || []).filter(Boolean))];
  if (platforms.length === 0) {
    await reqRef.update({ status: "Posted", posts: [] });
    await appendActivity({ event: "posted", requestId, refCode, actor: "engine", detail: "No platforms selected" });
    return;
  }

  const [platformRows, slots] = await Promise.all([getPlatforms(), getSlots()]);
  const nowMs = Date.now();

  // Existing scheduled posts -> taken slots per platform + handler load.
  const scheduledSnap = await db.collection("tasks").where("status", "==", "SCHEDULED").get();
  const takenByPlatform = new Map<string, Set<number>>();
  const loadByEmail = new Map<string, number>();
  for (const d of scheduledSnap.docs) {
    const t = d.data();
    if (t.task !== "Post") continue;
    if (!takenByPlatform.has(t.platform)) takenByPlatform.set(t.platform, new Set());
    if (t.scheduledAt?.toMillis) takenByPlatform.get(t.platform)!.add(t.scheduledAt.toMillis());
    const e = (t.email || "").toLowerCase();
    if (e) loadByEmail.set(e, (loadByEmail.get(e) || 0) + 1);
  }

  const batch = db.batch();
  const posts: Array<{ platform: string; handlerEmail: string; scheduledAt: FirebaseFirestore.Timestamp | null; status: string }> = [];
  const toNotify: Array<ReturnType<typeof postTaskDoc>> = [];
  const handlerEmails = new Set<string>();

  for (const platform of platforms) {
    const handlers = platformRows.filter((r) => r.platform === platform && r.active);

    if (handlers.length === 0) {
      batch.set(db.collection("tasks").doc(), postTaskDoc(requestId, request, platform, "", 0, null, "UNFILLED", "no active handler"));
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
    taken.add(slot.toMillis());
    loadByEmail.set((handler.handlerEmail || "").toLowerCase(), bestLoad + 1);

    const doc = postTaskDoc(requestId, request, platform, handler.handlerEmail, handler.points || 0, slot, "SCHEDULED", "");
    batch.set(db.collection("tasks").doc(), doc);
    posts.push({ platform, handlerEmail: handler.handlerEmail, scheduledAt: slot, status: "SCHEDULED" });
    toNotify.push(doc);
    if (handler.handlerEmail) handlerEmails.add(handler.handlerEmail.toLowerCase());
  }

  // Award post points to handlers who are team members.
  const teamSnaps = await Promise.all([...handlerEmails].map((e) => db.doc(`team/${e}`).get()));
  const teamHandlers = new Set(teamSnaps.filter((s) => s.exists).map((s) => s.id));
  for (const post of posts) {
    if (post.status !== "SCHEDULED" || !post.handlerEmail) continue;
    const e = post.handlerEmail.toLowerCase();
    if (!teamHandlers.has(e)) continue;
    const row = platformRows.find((r) => r.platform === post.platform && r.handlerEmail === post.handlerEmail);
    const pts = row?.points || 0;
    if (pts) batch.update(db.doc(`team/${e}`), { points: FieldValue.increment(pts) });
  }

  batch.update(reqRef, { status: "Posted", posts });
  await batch.commit();

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
      text: `A ${t.platform} post for ${t.eventName} (${refCode}) is scheduled${t.scheduledAt ? ` for ${t.scheduledAt.toDate().toLocaleString()}` : ""}.`,
    });
  }
  await appendActivity({
    event: "posted",
    requestId,
    refCode,
    actor: "engine",
    detail: `${posts.filter((p) => p.status === "SCHEDULED").length} post(s) scheduled`,
  });
});
