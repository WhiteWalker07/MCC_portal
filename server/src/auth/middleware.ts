/**
 * Auth/authorization middleware + helpers.
 *
 * `requireAuth` gates any /api route on a valid session. `getEmail` returns the
 * signed-in, lowercased email. `requireSecretaryOrAdmin` and `attachRoles`
 * resolve authoritative roles from the database (serverRoles).
 */

import { Request, Response, NextFunction } from "express";
import { SessionUser } from "./passport";
import { resolveCallerRoles, CallerRoles } from "../engine/serverRoles";

// Augment Express request with resolved roles (set by attachRoles).
declare global {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace Express {
    interface Request {
      roles?: CallerRoles;
    }
  }
}

export function getEmail(req: Request): string {
  const user = req.user as SessionUser | undefined;
  return (user?.email || "").toLowerCase();
}

export function requireAuth(req: Request, res: Response, next: NextFunction): void {
  if (!req.isAuthenticated || !req.isAuthenticated() || !getEmail(req)) {
    res.status(401).json({ error: "Sign in required." });
    return;
  }
  next();
}

/** Resolve roles once and attach to req.roles (use after requireAuth). */
export async function attachRoles(
  req: Request,
  res: Response,
  next: NextFunction
): Promise<void> {
  try {
    req.roles = await resolveCallerRoles(getEmail(req));
    next();
  } catch (err) {
    next(err);
  }
}

export function requireSecretaryOrAdmin(
  req: Request,
  res: Response,
  next: NextFunction
): void {
  const roles = req.roles;
  if (!roles || (!roles.isSecretary && !roles.isAdmin)) {
    res.status(403).json({ error: "Secretaries / admins only." });
    return;
  }
  next();
}
