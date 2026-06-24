/**
 * Committee management (secretary/admin).
 *   GET  /api/committees   list committees
 *   POST /api/committees   add or update a committee (keyed by login email)
 *
 * Committees are keyed by their login email (_id). Adding one lets that account
 * raise Coverage requests and gives it a refCode prefix (acronym). lastSeq is
 * preserved on update so the request counter never resets.
 */

import { Router, Request, Response } from "express";
import { col } from "../db";
import { requireAuth, attachRoles, requireSecretaryOrAdmin, getEmail } from "../auth/middleware";
import { asyncHandler, httpError } from "../lib/http";
import { appendActivity } from "../lib/log";

export const committeesRouter = Router();

const TYPES = ["Club", "Committee", "SIG", "Office"];
const CAMPUSES = ["MBA Campus", "BMS Campus"];

committeesRouter.get(
  "/api/committees",
  requireAuth,
  attachRoles,
  requireSecretaryOrAdmin,
  asyncHandler(async (_req: Request, res: Response) => {
    const docs = await col.committees().find({}).toArray();
    const committees = docs
      .map((d) => ({
        email: String(d._id),
        name: d.name || "",
        acronym: d.acronym || "",
        type: d.type || "",
        campus: d.campus || "",
      }))
      .sort((a, b) => String(a.name).localeCompare(String(b.name)));
    res.json({ committees, types: TYPES, campuses: CAMPUSES });
  })
);

committeesRouter.post(
  "/api/committees",
  requireAuth,
  attachRoles,
  requireSecretaryOrAdmin,
  asyncHandler(async (req: Request, res: Response) => {
    const b = req.body || {};
    const email = String(b.email || "").trim().toLowerCase();
    const name = String(b.name || "").trim();
    const acronym = String(b.acronym || "").trim().toUpperCase();
    const type = String(b.type || "Committee").trim();
    const campus = String(b.campus || "").trim();

    if (!email || !email.includes("@")) throw httpError("invalid-argument", "A valid login email is required.");
    if (!name) throw httpError("invalid-argument", "Name is required.");
    if (!acronym) throw httpError("invalid-argument", "Acronym is required (used for the request ID prefix).");
    if (campus && !CAMPUSES.includes(campus)) throw httpError("invalid-argument", `Campus must be one of: ${CAMPUSES.join(", ")}`);

    const existing = await col.committees().findOne({ _id: email as never });
    await col.committees().updateOne(
      { _id: email as never },
      {
        $set: { email, name, acronym, type: type || "Committee", campus },
        $setOnInsert: { lastSeq: 0, logo: "" },
      },
      { upsert: true }
    );

    await appendActivity({
      event: "committee",
      actor: getEmail(req),
      detail: `${existing ? "updated" : "added"} committee ${name} (${email})`,
    });
    res.status(existing ? 200 : 201).json({ ok: true, updated: !!existing });
  })
);
