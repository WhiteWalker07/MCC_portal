/**
 * Server-side role resolution + assignment authorization.
 *
 * Ported from functions/src/engine/serverRoles.ts. This is the authoritative
 * role source (the client cannot be trusted). Data reads go to Mongo instead of
 * Firestore; the logic is unchanged.
 */

import { col } from "../db";
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
  const [settingsDoc, memberDoc] = await Promise.all([
    col.config().findOne({ _id: "settings" as never }),
    col.team().findOne({ _id: e as never }),
  ]);
  const settings = (settingsDoc as unknown as Settings) || ({} as Settings);
  const member = memberDoc
    ? ({ ...(memberDoc as unknown as TeamMember), id: e, email: e } as TeamMember)
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
 *   secretary / admin -> anything; 2nd-year -> anything; domain head -> own
 *   vertical; event coordinator -> events they coordinate.
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
