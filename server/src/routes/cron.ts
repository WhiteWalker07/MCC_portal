/**
 * Cron route — the hourly deadline sweep, triggered by an external scheduler
 * (GitHub Actions / cron-job.org) since Render's free tier has no cron.
 *
 * Protected by a shared secret in the `x-cron-secret` header (or `?secret=`),
 * compared against CRON_SECRET. Replaces the Firebase scheduledDeadlineCheck.
 */

import { Router, Request, Response } from "express";
import { asyncHandler } from "../lib/http";
import { runDeadlineCheck } from "../services/workflow";

export const cronRouter = Router();

function authorized(req: Request): boolean {
  const expected = process.env.CRON_SECRET || "";
  if (!expected) return false;
  const got = req.get("x-cron-secret") || (req.query.secret as string) || "";
  return got === expected;
}

cronRouter.post(
  "/api/cron/deadline-check",
  asyncHandler(async (req: Request, res: Response) => {
    if (!authorized(req)) {
      res.status(401).json({ error: "Bad cron secret." });
      return;
    }
    const result = await runDeadlineCheck();
    res.json({ ok: true, ...result });
  })
);
