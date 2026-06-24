/**
 * Auth/authorization middleware + helpers.
 *
 * `requireAuth` gates any /api route on a valid session. `getEmail` returns the
 * signed-in, lowercased email. `requireSecretaryOrAdmin` and `attachRoles`
 * resolve authoritative roles from the database (serverRoles).
 */

import { Request, Response, NextFunction } from "express";
import { SessionUser } from "./passport";
import { verifyToken } from "./jwt";
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

/**
 * Authenticate via the Bearer token (stateless). Sets req.user from the token so
 * the rest of the stack (getEmail / attachRoles / buildClientSession) is
 * unchanged. No cookie is involved, so it works cross-origin on every browser.
 */
export function requireAuth(req: Request, res: Response, next: NextFunction): void {
  const header = req.get("authorization") || "";
  const match = /^Bearer\s+(.+)$/i.exec(header);
  const payload = match ? verifyToken(match[1]) : null;
  if (!payload?.email) {
    res.status(401).json({ error: "Sign in required." });
    return;
  }
  req.user = { email: payload.email, displayName: payload.name, photoURL: payload.photo };
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
