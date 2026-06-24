/**
 * Session middleware — express-session backed by MongoDB (connect-mongo).
 *
 * Cross-origin note: the frontend (Vercel) and API (Render) are on different
 * origins, so the session cookie must be `SameSite=None; Secure` to be sent on
 * cross-site fetches. That requires HTTPS on both ends (Render + Vercel provide
 * it) and `trust proxy` on the app (set in index.ts) so Express knows the
 * connection is secure behind Render's proxy.
 *
 * For LOCAL dev over http://localhost the cookie is downgraded to
 * `SameSite=Lax; Secure=false` (set COOKIE_INSECURE=1) so it works without TLS.
 */

import session from "express-session";
import MongoStore from "connect-mongo";

export function sessionMiddleware() {
  const insecure = process.env.COOKIE_INSECURE === "1";
  return session({
    name: "mcc.sid",
    secret: process.env.SESSION_SECRET || "dev-insecure-secret",
    resave: false,
    saveUninitialized: false,
    rolling: true,
    store: MongoStore.create({
      mongoUrl: process.env.MONGODB_URI as string,
      dbName: process.env.MONGODB_DB || "mcc_portal",
      collectionName: "sessions",
      ttl: 14 * 24 * 60 * 60, // 14 days
    }),
    cookie: {
      httpOnly: true,
      maxAge: 14 * 24 * 60 * 60 * 1000,
      sameSite: insecure ? "lax" : "none",
      secure: !insecure,
    },
  });
}
