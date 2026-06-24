/**
 * Read-only config endpoints used by the frontend (all require a signed-in user).
 *   GET /api/options             form options (requestable roles + active platforms)
 *   GET /api/internal-task-types task types staff may add manually
 *   GET /api/config/points       the current scoring scheme
 */

import { Router, Request, Response } from "express";
import { col } from "../db";
import { requireAuth } from "../auth/middleware";
import { asyncHandler } from "../lib/http";
import { getTaskTypes, getPlatforms } from "../config";

export const configRouter = Router();

configRouter.get(
  "/api/options",
  requireAuth,
  asyncHandler(async (_req: Request, res: Response) => {
    const [types, platformRows] = await Promise.all([getTaskTypes(), getPlatforms()]);
    let roles = types.filter((t) => t.requestable === true).map((t) => t.task);
    if (roles.length === 0) {
      // Defensive fallback for config seeded before the `requestable` flag existed.
      roles = types
        .filter((t) => t.atEvent && t.task !== "Event Coordinator" && t.task !== "Content Writer")
        .map((t) => t.task);
    }
    const platforms = [...new Set(platformRows.filter((p) => p.active).map((p) => p.platform))];
    res.json({ roles, platforms });
  })
);

configRouter.get(
  "/api/internal-task-types",
  requireAuth,
  asyncHandler(async (_req: Request, res: Response) => {
    const types = await getTaskTypes();
    res.json({ taskTypes: types.filter((t) => t.internalAssignable === true).map((t) => t.task) });
  })
);

configRouter.get(
  "/api/config/points",
  requireAuth,
  asyncHandler(async (_req: Request, res: Response) => {
    const doc = await col.config().findOne({ _id: "points" as never });
    if (!doc) {
      res.json({});
      return;
    }
    const { _id, ...rest } = doc as Record<string, unknown>;
    res.json(rest);
  })
);
