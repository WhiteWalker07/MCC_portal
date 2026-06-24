/**
 * Team / admin routes. Ported from functions/src/callable/team.ts.
 *   GET  /api/team           listTeamMembers (secretary/admin)
 *   POST /api/team/import    importTeamCsv (secretary/admin, upsert)
 *   POST /api/team/head      setDomainHead (secretary/admin)
 *   POST /api/config/points  setPointScheme (admin only)
 */

import { Router, Request, Response } from "express";
import { col } from "../db";
import { requireAuth, attachRoles, requireSecretaryOrAdmin, getEmail } from "../auth/middleware";
import { asyncHandler, httpError } from "../lib/http";
import { appendActivity } from "../lib/log";

export const teamRouter = Router();

const VERTICALS = ["Photography", "Videography", "Graphic Designs", "Content Writing"];

interface RowResult {
  email: string;
  status: "created" | "updated" | "error";
  message?: string;
}

function parseBool(v: unknown, dflt: boolean): boolean {
  if (v === undefined || v === null || v === "") return dflt;
  const s = String(v).trim().toLowerCase();
  return s === "true" || s === "1" || s === "yes" || s === "y";
}

const DAY_MS = 86_400_000;
const round1 = (n: number) => Math.round(n * 10) / 10;

/**
 * Current availability + day totals for a member, including the in-progress
 * segment since `availabilityChangedAt` (so counts are live, not just banked).
 */
function availabilitySnapshot(m: Record<string, unknown>) {
  const status = m.availability === "out" ? "out" : "available";
  const changedAt = m.availabilityChangedAt ? new Date(m.availabilityChangedAt as string).getTime() : null;
  const seg = changedAt ? Math.max(0, (Date.now() - changedAt) / DAY_MS) : 0;
  const onWorkDays = (Number(m.onWorkDays) || 0) + (status === "available" ? seg : 0);
  const outDays = (Number(m.outDays) || 0) + (status === "out" ? seg : 0);
  return { availability: status, onWorkDays: round1(onWorkDays), outDays: round1(outDays) };
}

// ── listTeamMembers ──────────────────────────────────────────────────────────
teamRouter.get(
  "/api/team",
  requireAuth,
  attachRoles,
  requireSecretaryOrAdmin,
  asyncHandler(async (_req: Request, res: Response) => {
    const docs = await col.team().find({}).toArray();
    const members = docs
      .map((d) => ({
        email: String(d._id),
        name: d.name || String(d._id),
        vertical: d.vertical || "",
        domainHeadOf: d.domainHeadOf || "",
        campus: d.campus || "",
        year: d.year || null,
        active: d.active !== false,
        ...availabilitySnapshot(d),
      }))
      .sort((a, b) => String(a.name).localeCompare(String(b.name)));
    res.json({ members, verticals: VERTICALS });
  })
);

// ── importTeamCsv ────────────────────────────────────────────────────────────
teamRouter.post(
  "/api/team/import",
  requireAuth,
  attachRoles,
  requireSecretaryOrAdmin,
  asyncHandler(async (req: Request, res: Response) => {
    const rows = req.body?.rows;
    if (!Array.isArray(rows)) throw httpError("invalid-argument", "rows[] required.");

    const results: RowResult[] = [];
    for (const raw of rows) {
      const r = raw || {};
      const email = String(r.email || "").trim().toLowerCase();
      if (!email || !email.includes("@")) {
        results.push({ email: String(r.email || ""), status: "error", message: "invalid email" });
        continue;
      }
      const name = String(r.name || "").trim();
      if (!name) {
        results.push({ email, status: "error", message: "name required" });
        continue;
      }

      const data = {
        name,
        email,
        vertical: String(r.vertical || "").trim(),
        year: Number(r.year) || 1,
        domainHeadOf: String(r.domainHeadOf || "").trim(),
        skills: String(r.skills || "")
          .split(/[;,]/)
          .map((s: string) => s.trim())
          .filter(Boolean),
        campus: String(r.campus || "").trim(),
        phone: String(r.phone || "").trim(),
        active: parseBool(r.active, true),
      };

      try {
        const existing = await col.team().findOne({ _id: email as never });
        const payload = existing ? data : { ...data, points: 0, strikes: 0 };
        await col.team().updateOne({ _id: email as never }, { $set: payload }, { upsert: true });
        results.push({ email, status: existing ? "updated" : "created" });
      } catch (err) {
        results.push({ email, status: "error", message: (err as Error).message });
      }
    }

    const created = results.filter((r) => r.status === "created").length;
    const updated = results.filter((r) => r.status === "updated").length;
    const errors = results.filter((r) => r.status === "error").length;
    res.json({ results, summary: { created, updated, errors } });
  })
);

// ── setDomainHead ────────────────────────────────────────────────────────────
teamRouter.post(
  "/api/team/head",
  requireAuth,
  attachRoles,
  requireSecretaryOrAdmin,
  asyncHandler(async (req: Request, res: Response) => {
    const email = String(req.body?.email || "").trim().toLowerCase();
    const vertical = String(req.body?.vertical || "").trim();
    if (!email) throw httpError("invalid-argument", "email required.");

    const member = await col.team().findOne({ _id: email as never });
    if (!member) throw httpError("not-found", "Not a team member.");
    if (vertical && !VERTICALS.includes(vertical)) {
      throw httpError("failed-precondition", `Unknown vertical: ${vertical}`);
    }

    if (vertical) {
      // One head per vertical: demote whoever currently heads it.
      await col.team().updateMany(
        { domainHeadOf: vertical, _id: { $ne: email as never } },
        { $set: { domainHeadOf: "" } }
      );
      await col.team().updateOne({ _id: email as never }, { $set: { domainHeadOf: vertical, vertical } });
    } else {
      await col.team().updateOne({ _id: email as never }, { $set: { domainHeadOf: "" } });
    }

    await appendActivity({
      event: "domain-head",
      actor: getEmail(req),
      member: email,
      detail: vertical ? `Made head of ${vertical}` : "Removed as head",
    });
    res.json({ ok: true });
  })
);

// ── setAvailability (out of work / on break) ─────────────────────────────────
// Secretary/admin flips a member between "available" and "out". The completed
// segment in the previous state is banked into onWorkDays / outDays.
teamRouter.post(
  "/api/team/availability",
  requireAuth,
  attachRoles,
  requireSecretaryOrAdmin,
  asyncHandler(async (req: Request, res: Response) => {
    const email = String(req.body?.email || "").trim().toLowerCase();
    const next = String(req.body?.availability || "").trim();
    if (!email) throw httpError("invalid-argument", "email required.");
    if (next !== "available" && next !== "out") {
      throw httpError("invalid-argument", "availability must be 'available' or 'out'.");
    }

    const member = await col.team().findOne({ _id: email as never });
    if (!member) throw httpError("not-found", "Not a team member.");

    const now = new Date();
    const prev = member.availability === "out" ? "out" : "available";
    const changedAt = member.availabilityChangedAt ? new Date(member.availabilityChangedAt).getTime() : now.getTime();
    const seg = Math.max(0, (now.getTime() - changedAt) / DAY_MS);
    const incField = prev === "out" ? "outDays" : "onWorkDays";

    await col.team().updateOne(
      { _id: email as never },
      {
        $inc: { [incField]: seg },
        $set: { availability: next, availabilityChangedAt: now },
      }
    );

    await appendActivity({
      event: "availability",
      actor: getEmail(req),
      member: email,
      detail: `${prev} -> ${next}`,
    });
    res.json({ ok: true, availability: next });
  })
);

// ── setPointScheme (admin only) ──────────────────────────────────────────────
const POINT_KEYS = [
  "coordinatorPoints",
  "domainTaskPoints",
  "vetterPoints",
  "earlyWindowHours",
  "earlyBonusPct",
  "lateThresholdHours",
  "latePenaltyPct",
  "subsequentDelayHours",
  "subsequentPenaltyPct",
];

teamRouter.post(
  "/api/config/points",
  requireAuth,
  attachRoles,
  asyncHandler(async (req: Request, res: Response) => {
    if (!req.roles?.isAdmin) throw httpError("permission-denied", "Only admins can change the point scheme.");

    const incoming = req.body?.points || {};
    const out: Record<string, number> = {};
    for (const k of POINT_KEYS) {
      const v = Number(incoming[k]);
      if (!Number.isFinite(v) || v < 0) throw httpError("invalid-argument", `Invalid value for ${k}.`);
      out[k] = v;
    }

    await col.config().updateOne({ _id: "points" as never }, { $set: out }, { upsert: true });
    await appendActivity({ event: "points-scheme", actor: getEmail(req), detail: "Point scheme updated" });
    res.json({ ok: true });
  })
);
