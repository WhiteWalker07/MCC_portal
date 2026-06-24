/**
 * Builds the client-facing session object returned by GET /api/me.
 *
 * This is the server equivalent of the old client-side resolveSession() in
 * web/js/roles.js — but authoritative (roles derived from DB, not trusted from
 * the client). Shape is kept identical so the frontend shell/views are unchanged.
 */

import { col } from "../db";
import { resolveCallerRoles } from "../engine/serverRoles";
import { Committee, TeamMember } from "../types";
import { SessionUser } from "./passport";

export interface ClientSession {
  user: { email: string; displayName: string; photoURL: string };
  committee: Committee | null;
  team: TeamMember | null;
  isSecretary: boolean;
  isAdmin: boolean;
  isCoordinator: boolean;
  isDomainHead: boolean;
  isSecondYear: boolean;
  domainHeadOf: string;
  roles: string[];
}

export async function buildClientSession(user: SessionUser): Promise<ClientSession> {
  const email = (user.email || "").toLowerCase();
  const [roles, committeeDoc, coordinatorTask] = await Promise.all([
    resolveCallerRoles(email),
    col.committees().findOne({ _id: email as never }),
    col.tasks().findOne({ coordinatorEmail: email }),
  ]);

  const committee = committeeDoc
    ? ({ ...(committeeDoc as unknown as Committee), email } as Committee)
    : null;
  const team = roles.member;
  const isCoordinator = !!coordinatorTask;

  const list: string[] = [];
  if (committee) list.push("committee");
  if (team) list.push("team");
  if (isCoordinator) list.push("coordinator");
  if (roles.isDomainHead) list.push("domainHead");
  if (roles.isSecretary) list.push("secretary");
  if (roles.isAdmin) list.push("admin");

  return {
    user: { email: user.email, displayName: user.displayName, photoURL: user.photoURL },
    committee,
    team,
    isSecretary: roles.isSecretary,
    isAdmin: roles.isAdmin,
    isCoordinator,
    isDomainHead: roles.isDomainHead,
    isSecondYear: roles.isSecondYear,
    domainHeadOf: roles.domainHeadOf,
    roles: list,
  };
}
