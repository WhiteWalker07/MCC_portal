/**
 * Server-side role resolution + assignment authorization for callables.
 *
 * Mirrors the client `roles.js` but is authoritative (the client cannot be
 * trusted). Used to gate who may assign/reassign tasks.
 */

import { db } from "../lib/setup";
import { Settings, TeamMember } from "../types";

export interface CallerRoles {
  email: string;
  isSecretary: boolean;
  isAdmin: boolean;
  isTeam: boolean;
  isDomainHead: boolean;
  domainHeadOf: string;
  isSecondYear: boolean;
  member: TeamMember | null;
}

function lower(arr?: string[]): string[] {
  return (arr || []).map((x) => String(x).toLowerCase());
}

export async function resolveCallerRoles(email: string): Promise<CallerRoles> {
  const e = (email || "").toLowerCase();
  const [settingsSnap, memberSnap] = await Promise.all([
    db.doc("config/settings").get(),
    db.doc(`team/${e}`).get(),
  ]);
  const settings = (settingsSnap.data() as Settings) || ({} as Settings);
  const member = memberSnap.exists
    ? ({ id: memberSnap.id, ...(memberSnap.data() as TeamMember) } as TeamMember)
    : null;

  return {
    email: e,
    isSecretary: lower(settings.secretaryEmails).includes(e),
    isAdmin: lower(settings.adminEmails).includes(e),
    isTeam: !!member,
    isDomainHead: !!(member && member.domainHeadOf),
    domainHeadOf: member?.domainHeadOf || "",
    isSecondYear: !!(member && Number(member.year) === 2),
    member,
  };
}

/**
 * May this caller assign/modify a task of `taskVertical` on a request coordinated
 * by `coordinatorEmail`?
 *   secretary / admin      -> anything
 *   2nd-year               -> anything (senior member)
 *   domain head            -> only tasks in their own vertical
 *   event coordinator      -> only tasks on events they coordinate
 */
export function canAssign(
  roles: CallerRoles,
  taskVertical: string,
  coordinatorEmail: string
): boolean {
  if (roles.isSecretary || roles.isAdmin) return true;
  if (roles.isSecondYear) return true;
  if (roles.isDomainHead && taskVertical && roles.domainHeadOf === taskVertical) {
    return true;
  }
  if (coordinatorEmail && roles.email === String(coordinatorEmail).toLowerCase()) {
    return true;
  }
  return false;
}
