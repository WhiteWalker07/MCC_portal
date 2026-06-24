/**
 * Admin ops endpoint — run the real-data loader server-side (handy when your
 * local network can't reach Atlas but Render can).
 *
 *   POST /api/admin/load-data        (header x-cron-secret: $CRON_SECRET)
 *   POST /api/admin/load-data?clean=1  also removes the demo rows
 *
 * Guarded by the shared CRON_SECRET (not the user session) so it can be invoked
 * with a single curl from anywhere. Idempotent — safe to call more than once.
 */

import { Router, Request, Response } from "express";
import { col } from "../db";
import { asyncHandler } from "../lib/http";
import { loadRealData } from "../admin/realData";

export const adminRouter = Router();

function authorized(req: Request): boolean {
  const expected = process.env.CRON_SECRET || "";
  if (!expected) return false;
  const got = req.get("x-cron-secret") || (req.query.secret as string) || "";
  return got === expected;
}

/**
 * GET /api/admin/status — quick read of what's actually in the DB (counts,
 * active platforms, admin/secretary emails). Secret-guarded so you can curl it
 * to verify a data load without DB access. Answers "are platforms seeded?" and
 * "is X an admin?".
 */
adminRouter.get(
  "/api/admin/status",
  asyncHandler(async (req: Request, res: Response) => {
    if (!authorized(req)) {
      res.status(401).json({ error: "Bad admin secret." });
      return;
    }
    const settings = (await col.config().findOne({ _id: "settings" as never })) as Record<string, unknown> | null;
    const platformsDoc = (await col.config().findOne({ _id: "platforms" as never })) as { platforms?: Array<{ platform: string; active: boolean }> } | null;
    res.json({
      committees: await col.committees().countDocuments(),
      team: await col.team().countDocuments(),
      configDocs: await col.config().countDocuments(),
      platformsActive: (platformsDoc?.platforms || []).filter((p) => p.active).map((p) => p.platform),
      adminEmails: settings?.adminEmails || [],
      secretaryEmails: settings?.secretaryEmails || [],
    });
  })
);

adminRouter.post(
  "/api/admin/load-data",
  asyncHandler(async (req: Request, res: Response) => {
    if (!authorized(req)) {
      res.status(401).json({ error: "Bad admin secret." });
      return;
    }
    const clean = req.query.clean === "1" || req.body?.clean === true;
    const result = await loadRealData({ clean });
    res.json({ ok: true, ...result });
  })
);
