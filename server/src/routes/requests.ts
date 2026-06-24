/**
 * Request routes. Authorization mirrors the old firestore.rules:
 *   - create: contactEmail is forced to the signed-in user; Post = anyone,
 *     Coverage = committees only; engine-only fields can't be set (allowlist).
 *   - read own / coordinated / (secretary) any; pending queue = secretary.
 *   - approve/reject = secretary, only while pending.
 *
 * The engine runs synchronously: POST /api/requests inserts then awaits
 * processNewRequest so the response already reflects refCode + assignment.
 */

import { Router, Request, Response } from "express";
import { col, oid } from "../db";
import { withId, findRequest } from "../lib/docs";
import { requireAuth, attachRoles, getEmail } from "../auth/middleware";
import { asyncHandler, httpError } from "../lib/http";
import { processNewRequest, rejectRequest } from "../services/workflow";
import { confirmRequest } from "../engine/confirm";

export const requestsRouter = Router();

const CONTENT_FIELDS = [
  "type",
  "eventName",
  "eventStart",
  "eventEnd",
  "venue",
  "requester",
  "rolesNeeded",
  "platforms",
  "contentLinks",
  "notes",
] as const;

function parseDate(v: unknown): Date | null {
  if (!v) return null;
  const d = new Date(v as string);
  return isNaN(d.getTime()) ? null : d;
}

/** Can this caller read this request? (requester / coordinator / secretary-admin) */
function canReadRequest(req: Request, request: { contactEmail?: string; coordinatorEmail?: string }): boolean {
  const me = getEmail(req);
  const roles = req.roles;
  return (
    request.contactEmail === me ||
    (request.coordinatorEmail || "") === me ||
    !!(roles && (roles.isSecretary || roles.isAdmin))
  );
}

// ── create ───────────────────────────────────────────────────────────────────
requestsRouter.post(
  "/api/requests",
  requireAuth,
  asyncHandler(async (req: Request, res: Response) => {
    const me = getEmail(req);
    const body = req.body || {};
    const type = body.type;
    if (type !== "Post" && type !== "Coverage") {
      throw httpError("invalid-argument", "type must be 'Post' or 'Coverage'.");
    }
    if (type === "Coverage") {
      const committee = await col.committees().findOne({ _id: me as never });
      if (!committee) throw httpError("permission-denied", "Coverage requests are reserved to committees.");
    }

    const doc: Record<string, unknown> = {
      contactEmail: me,
      status: "New",
      createdAt: new Date(),
    };
    for (const f of CONTENT_FIELDS) {
      if (body[f] !== undefined) doc[f] = body[f];
    }
    doc.eventStart = parseDate(body.eventStart);
    doc.eventEnd = parseDate(body.eventEnd);

    const ins = await col.requests().insertOne(doc);
    const id = String(ins.insertedId);

    await processNewRequest(id);

    const saved = await findRequest(id);
    res.status(201).json(withId(saved));
  })
);

// ── lists ────────────────────────────────────────────────────────────────────
requestsRouter.get(
  "/api/requests/mine",
  requireAuth,
  asyncHandler(async (req: Request, res: Response) => {
    const me = getEmail(req);
    const docs = await col.requests().find({ contactEmail: me }).sort({ createdAt: -1 }).toArray();
    res.json(docs.map((d) => withId(d)));
  })
);

requestsRouter.get(
  "/api/requests/pending",
  requireAuth,
  attachRoles,
  asyncHandler(async (req: Request, res: Response) => {
    if (!req.roles?.isSecretary) throw httpError("permission-denied", "Secretaries only.");
    const docs = await col
      .requests()
      .find({ status: "Pending for POC approval" })
      .sort({ createdAt: -1 })
      .toArray();
    res.json(docs.map((d) => withId(d)));
  })
);

// ── single + its tasks ───────────────────────────────────────────────────────
requestsRouter.get(
  "/api/requests/:id",
  requireAuth,
  attachRoles,
  asyncHandler(async (req: Request, res: Response) => {
    const request = await findRequest(req.params.id);
    if (!request) throw httpError("not-found", "Request not found.");
    if (!canReadRequest(req, request)) throw httpError("permission-denied", "Not allowed.");
    res.json(withId(request));
  })
);

requestsRouter.get(
  "/api/requests/:id/tasks",
  requireAuth,
  attachRoles,
  asyncHandler(async (req: Request, res: Response) => {
    const request = await findRequest(req.params.id);
    if (!request) throw httpError("not-found", "Request not found.");
    if (!canReadRequest(req, request)) throw httpError("permission-denied", "Not allowed.");
    const tasks = await col.tasks().find({ requestId: req.params.id }).toArray();
    res.json(tasks.map((d) => withId(d)));
  })
);

// ── approve / reject ─────────────────────────────────────────────────────────
requestsRouter.post(
  "/api/requests/:id/approve",
  requireAuth,
  attachRoles,
  asyncHandler(async (req: Request, res: Response) => {
    if (!req.roles?.isSecretary) throw httpError("permission-denied", "Secretaries only.");
    const _id = oid(req.params.id);
    if (!_id) throw httpError("invalid-argument", "Bad id.");
    const request = await col.requests().findOne({ _id });
    if (!request) throw httpError("not-found", "Request not found.");
    if (request.status !== "Pending for POC approval") {
      throw httpError("failed-precondition", "Request is not pending approval.");
    }
    await confirmRequest(req.params.id);
    res.json({ ok: true });
  })
);

requestsRouter.post(
  "/api/requests/:id/reject",
  requireAuth,
  attachRoles,
  asyncHandler(async (req: Request, res: Response) => {
    if (!req.roles?.isSecretary) throw httpError("permission-denied", "Secretaries only.");
    const _id = oid(req.params.id);
    if (!_id) throw httpError("invalid-argument", "Bad id.");
    const request = await col.requests().findOne({ _id });
    if (!request) throw httpError("not-found", "Request not found.");
    if (request.status !== "Pending for POC approval") {
      throw httpError("failed-precondition", "Request is not pending approval.");
    }
    await rejectRequest(req.params.id, String(req.body?.reason || ""), getEmail(req));
    res.json({ ok: true });
  })
);
