/**
 * Task routes.
 *   GET  /api/tasks/mine       — the signed-in member's tasks, soonest deadline first.
 *   POST /api/tasks/:id/done   — assignee closes their own CONFIRMED/LATE task; the
 *                                engine (completeTask) then advances the request.
 *
 * Mirrors the old tasks rule: only the assignee may mark their own task DONE, and
 * only from CONFIRMED or LATE.
 */

import { Router, Request, Response } from "express";
import { col } from "../db";
import { withId, findTask } from "../lib/docs";
import { requireAuth, getEmail } from "../auth/middleware";
import { asyncHandler, httpError } from "../lib/http";
import { completeTask } from "../services/workflow";

export const tasksRouter = Router();

tasksRouter.get(
  "/api/tasks/mine",
  requireAuth,
  asyncHandler(async (req: Request, res: Response) => {
    const me = getEmail(req);
    const docs = await col.tasks().find({ email: me }).sort({ deadline: 1 }).toArray();
    res.json(docs.map((d) => withId(d)));
  })
);

tasksRouter.post(
  "/api/tasks/:id/done",
  requireAuth,
  asyncHandler(async (req: Request, res: Response) => {
    const me = getEmail(req);
    const task = await findTask(req.params.id);
    if (!task) throw httpError("not-found", "Task not found.");
    if ((task.email || "").toLowerCase() !== me) {
      throw httpError("permission-denied", "Not your task.");
    }
    if (!["CONFIRMED", "LATE"].includes(task.status)) {
      throw httpError("failed-precondition", "Task is not open for completion.");
    }
    await col.tasks().updateOne({ _id: task._id }, { $set: { status: "DONE", completedAt: new Date() } });
    await completeTask(req.params.id);
    res.json({ ok: true });
  })
);
