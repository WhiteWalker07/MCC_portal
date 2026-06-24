/**
 * Auth routes — Google OAuth handshake, then a stateless token.
 *
 *   GET  /auth/google           -> redirect to Google
 *   GET  /auth/google/callback  -> issue a token, redirect to the frontend with it
 *   POST /auth/logout           -> no-op (token is cleared client-side)
 *   GET  /api/me                -> the client session (401 without a valid token)
 *
 * After the OAuth handshake (which is all first-party, top-level navigation to
 * this server, so its cookie isn't blocked), we hand the browser a signed JWT in
 * the redirect URL. The SPA stores it and sends it as a Bearer header — no
 * cross-site cookie, so it works on Safari/iOS and every other browser.
 */

import { Router, Request, Response } from "express";
import { passport, SessionUser } from "../auth/passport";
import { requireAuth, getEmail } from "../auth/middleware";
import { buildClientSession } from "../auth/clientSession";
import { signToken } from "../auth/jwt";

export const authRouter = Router();

const clientOrigin = () => process.env.CLIENT_ORIGIN || "http://localhost:3000";

authRouter.get(
  "/auth/google",
  passport.authenticate("google", { scope: ["email", "profile"], prompt: "select_account", session: false })
);

authRouter.get("/auth/google/callback", (req, res, next) => {
  passport.authenticate("google", { session: false }, (err: unknown, user: SessionUser | false, info: { message?: string }) => {
    if (err) return next(err);
    if (!user) {
      const reason = info?.message === "domain-not-allowed" ? "domain" : "failed";
      return res.redirect(`${clientOrigin()}/?authError=${reason}`);
    }
    const token = signToken(user);
    return res.redirect(`${clientOrigin()}/?token=${encodeURIComponent(token)}`);
  })(req, res, next);
});

authRouter.post("/auth/logout", (_req: Request, res: Response) => {
  // Stateless: the client discards its token. Nothing to clean up server-side.
  res.json({ ok: true });
});

authRouter.get("/api/me", requireAuth, async (req: Request, res: Response, next) => {
  try {
    const session = await buildClientSession(req.user as SessionUser);
    res.json(session);
  } catch (err) {
    next(err);
  }
});

// Tiny helper endpoint the client uses to confirm reachability.
authRouter.get("/api/whoami", (req: Request, res: Response) => {
  res.json({ email: getEmail(req) || null });
});
