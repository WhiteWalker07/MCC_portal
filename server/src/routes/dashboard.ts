/**
 * Dashboard stats — fairness & usage aggregates for admins / secretaries.
 * Ported from functions/src/callable/dashboard.ts. Reads team + tasks + requests
 * and returns pre-computed stats, optionally sliced by filters (period / campus /
 * year / vertical). Dates are native `Date` now (was Firestore Timestamp).
 */

import { Router, Request, Response } from "express";
import { col } from "../db";
import { requireAuth, attachRoles } from "../auth/middleware";
import { asyncHandler, httpError } from "../lib/http";
import { TeamMember } from "../types";

export const dashboardRouter = Router();

const HOUR_MS = 3_600_000;
const DAY_MS = 86_400_000;

function ms(v: unknown): number | null {
  if (!v) return null;
  const d = new Date(v as string | Date);
  return isNaN(d.getTime()) ? null : d.getTime();
}

function periodCutoff(period: string): number | null {
  const now = Date.now();
  switch (period) {
    case "7d":
      return now - 7 * DAY_MS;
    case "30d":
      return now - 30 * DAY_MS;
    case "90d":
      return now - 90 * DAY_MS;
    case "month": {
      const d = new Date();
      return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1);
    }
    default:
      return null; // all time
  }
}

dashboardRouter.post(
  "/api/dashboard",
  requireAuth,
  attachRoles,
  asyncHandler(async (req: Request, res: Response) => {
    if (!req.roles?.isAdmin && !req.roles?.isSecretary) {
      throw httpError("permission-denied", "Admins / secretaries only.");
    }

    const f = req.body?.filters || {};
    const period: string = f.period || "all";
    const campus: string = f.campus || "all";
    const year: string = String(f.year || "all");
    const vertical: string = f.vertical || "all";
    const cutoff = periodCutoff(period);
    const memberFiltered = campus !== "all" || year !== "all" || vertical !== "all";

    const [teamDocs, taskDocs, reqDocs] = await Promise.all([
      col.team().find({}).toArray(),
      col.tasks().find({}).toArray(),
      col.requests().find({}).toArray(),
    ]);

    const members = teamDocs.map((d) => ({ ...(d as unknown as TeamMember), email: String(d._id) }));
    const memberByEmail: Record<string, TeamMember> = {};
    for (const m of members) memberByEmail[(m.email || "").toLowerCase()] = m;

    function memberMatches(m: TeamMember | undefined): boolean {
      if (!m) return false;
      if (campus !== "all" && (m.campus || "") !== campus) return false;
      if (year !== "all" && String(m.year || "") !== year) return false;
      if (vertical !== "all" && (m.vertical || "") !== vertical) return false;
      return true;
    }

    const filteredMembers = members.filter(memberMatches);

    const doneByEmail: Record<string, number> = {};
    const onTimeByEmail: Record<string, number> = {};
    let completed = 0;
    let onTime = 0;
    let openLate = 0;
    let turnaroundSum = 0;
    let turnaroundN = 0;

    for (const t of taskDocs) {
      const email = (t.email || "").toLowerCase();
      if (memberFiltered && !memberMatches(memberByEmail[email])) continue;

      if (t.status === "LATE") openLate++;
      if (t.status !== "DONE") continue;

      const ca = ms(t.completedAt);
      if (cutoff && (ca == null || ca < cutoff)) continue; // outside the window

      completed++;
      if (email) doneByEmail[email] = (doneByEmail[email] || 0) + 1;

      const dl = ms(t.deadline);
      const cr = ms(t.createdAt);
      const punctual = dl && ca ? ca <= dl : true;
      if (punctual) {
        onTime++;
        if (email) onTimeByEmail[email] = (onTimeByEmail[email] || 0) + 1;
      }
      if (cr && ca && ca >= cr) {
        turnaroundSum += ca - cr;
        turnaroundN++;
      }
    }

    const leaderboard = filteredMembers
      .map((m) => {
        const e = (m.email || "").toLowerCase();
        const done = doneByEmail[e] || 0;
        const ot = onTimeByEmail[e] || 0;
        return {
          name: m.name || e,
          email: m.email,
          vertical: m.vertical || "",
          campus: m.campus || "",
          points: m.points || 0,
          strikes: m.strikes || 0,
          active: m.active !== false,
          done,
          onTimePct: done ? Math.round((ot / done) * 100) : null,
        };
      })
      .sort((a, b) => b.points - a.points || b.done - a.done);

    const byVerticalMap: Record<string, { points: number; members: number; done: number }> = {};
    for (const m of filteredMembers) {
      const v = m.vertical || "—";
      byVerticalMap[v] = byVerticalMap[v] || { points: 0, members: 0, done: 0 };
      byVerticalMap[v].points += m.points || 0;
      byVerticalMap[v].members += 1;
    }
    for (const [e, n] of Object.entries(doneByEmail)) {
      const v = (memberByEmail[e] && memberByEmail[e].vertical) || "—";
      if (byVerticalMap[v]) byVerticalMap[v].done += n;
    }
    const byVertical = Object.entries(byVerticalMap)
      .map(([v, x]) => ({ vertical: v, ...x }))
      .sort((a, b) => b.points - a.points);

    const requestsByStatus: Record<string, number> = {};
    let totalRequests = 0;
    for (const r of reqDocs) {
      if (campus !== "all" && (r.campus || "") !== campus) continue;
      const cr = ms(r.createdAt);
      if (cutoff && (cr == null || cr < cutoff)) continue;
      const s = r.status || "New";
      requestsByStatus[s] = (requestsByStatus[s] || 0) + 1;
      totalRequests++;
    }

    res.json({
      filters: { period, campus, year, vertical },
      totals: {
        activeMembers: filteredMembers.filter((m) => m.active !== false).length,
        totalMembers: filteredMembers.length,
        totalPoints: filteredMembers.reduce((s, m) => s + (m.points || 0), 0),
        tasksCompleted: completed,
        onTimeRate: completed ? Math.round((onTime / completed) * 100) : null,
        avgTurnaroundHours: turnaroundN ? Math.round((turnaroundSum / turnaroundN / HOUR_MS) * 10) / 10 : null,
        openLate,
        totalRequests,
      },
      leaderboard,
      byVertical,
      requestsByStatus,
    });
  })
);
