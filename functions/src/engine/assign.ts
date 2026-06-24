/**
 * Assignment eligibility + selection.
 *
 * Eligible = active, strikes < strikeLimit, has the required skill, and (if
 * campusStrict and the request has a campus) same campus — a member with a blank
 * campus stays eligible during rollout. For at-event tasks, exclude anyone busy
 * on the calendar during the event window (the stub reports everyone free until
 * Phase 11).
 *
 *   chooseMember     — auto-pick (prefers not-already-assigned; lowest points,
 *                      tie-break fewer strikes, then name).
 *   eligibleMembers  — the full sorted eligible pool (for the manual picker /
 *                      validation), with an optional exclude set.
 */

import { CalendarService } from "../services/calendar";
import { PipelineTask, RequestDoc, Settings, TeamMember } from "../types";

export interface ChoiceResult {
  member: TeamMember | null;
  reason: string;
}

export function campusOk(
  m: TeamMember,
  request: RequestDoc,
  settings: Settings
): boolean {
  if (!settings.campusStrict) return true;
  if (!request.campus) return true;
  if (!m.campus) return true; // blank campus stays eligible during rollout
  return m.campus === request.campus;
}

/** Base (non-calendar) eligibility for a member against a required skill. */
export function isBaseEligible(
  m: TeamMember,
  requiredSkill: string,
  request: RequestDoc,
  settings: Settings
): boolean {
  return (
    m.active === true &&
    (m.strikes || 0) < settings.strikeLimit &&
    Array.isArray(m.skills) &&
    m.skills.includes(requiredSkill) &&
    campusOk(m, request, settings)
  );
}

function byPointsStrikesName(a: TeamMember, b: TeamMember): number {
  return (
    (a.points || 0) - (b.points || 0) ||
    (a.strikes || 0) - (b.strikes || 0) ||
    String(a.name).localeCompare(String(b.name))
  );
}

/**
 * Full sorted eligible pool for a required skill, with calendar exclusion for
 * at-event tasks and an optional set of emails to exclude (e.g. the current
 * assignee when reassigning).
 */
export async function eligibleMembers(
  requiredSkill: string,
  atEvent: boolean,
  request: RequestDoc,
  settings: Settings,
  team: TeamMember[],
  calendar: CalendarService,
  exclude: Set<string> = new Set()
): Promise<TeamMember[]> {
  let pool = team.filter(
    (m) =>
      isBaseEligible(m, requiredSkill, request, settings) &&
      !exclude.has(m.email)
  );

  if (atEvent && request.eventStart && request.eventEnd) {
    const free: TeamMember[] = [];
    for (const m of pool) {
      if (await calendar.isFree(m.email, request.eventStart, request.eventEnd)) {
        free.push(m);
      }
    }
    pool = free;
  }

  return pool.sort(byPointsStrikesName);
}

export async function chooseMember(
  pt: PipelineTask,
  request: RequestDoc,
  settings: Settings,
  team: TeamMember[],
  alreadyAssigned: Set<string>,
  calendar: CalendarService
): Promise<ChoiceResult> {
  const pool = await eligibleMembers(
    pt.requiredSkill,
    pt.atEvent,
    request,
    settings,
    team,
    calendar
  );

  if (pool.length === 0) {
    return {
      member: null,
      reason: `no active member with skill "${pt.requiredSkill}"${
        settings.campusStrict && request.campus
          ? ` on ${request.campus} campus`
          : ""
      }`,
    };
  }

  // Role exclusivity: prefer people not already on this request.
  const fresh = pool.filter((m) => !alreadyAssigned.has(m.email));
  const candidates = fresh.length > 0 ? fresh : pool;
  return { member: candidates[0], reason: "" };
}
