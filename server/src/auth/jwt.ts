/**
 * Stateless auth tokens (JWT).
 *
 * After the Google OAuth handshake the server issues a signed token that the
 * browser stores and sends as `Authorization: Bearer <token>`. This replaces the
 * session cookie for API requests, so auth works cross-origin on every browser
 * (Safari/iOS block cross-site cookies; the Authorization header is always sent).
 *
 * Signed with SESSION_SECRET (already a strong random value), 14-day expiry.
 */

import jwt from "jsonwebtoken";
import { SessionUser } from "./passport";

const SECRET = process.env.SESSION_SECRET || "dev-insecure-secret";
const TTL = "14d";

export interface TokenPayload {
  email: string;
  name: string;
  photo: string;
}

export function signToken(user: SessionUser): string {
  return jwt.sign(
    { email: user.email, name: user.displayName, photo: user.photoURL },
    SECRET,
    { expiresIn: TTL }
  );
}

export function verifyToken(token: string): TokenPayload | null {
  try {
    const decoded = jwt.verify(token, SECRET) as TokenPayload & { exp: number };
    if (!decoded?.email) return null;
    return { email: decoded.email, name: decoded.name || "", photo: decoded.photo || "" };
  } catch {
    return null;
  }
}
