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
import { asyncHandler } from "../lib/http";
import { loadRealData } from "../admin/realData";

export const adminRouter = Router();

function authorized(req: Request): boolean {
  const expected = process.env.CRON_SECRET || "";
  if (!expected) return false;
  const got = req.get("x-cron-secret") || (req.query.secret as string) || "";
  return got === expected;
}

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
