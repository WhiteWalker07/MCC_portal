/**
 * Auth routes — Google OAuth handshake, logout, and the session probe (/api/me).
 *
 *   GET  /auth/google           -> redirect to Google
 *   GET  /auth/google/callback  -> finish sign-in, redirect back to the frontend
 *   POST /auth/logout           -> destroy the session
 *   GET  /api/me                -> the client session (401 if not signed in)
 */

import { Router, Request, Response } from "express";
import { passport } from "../auth/passport";
import { requireAuth, getEmail } from "../auth/middleware";
import { buildClientSession } from "../auth/clientSession";
import { SessionUser } from "../auth/passport";

export const authRouter = Router();

const clientOrigin = () => process.env.CLIENT_ORIGIN || "http://localhost:3000";

authRouter.get(
  "/auth/google",
  passport.authenticate("google", { scope: ["email", "profile"], prompt: "select_account" })
);

authRouter.get("/auth/google/callback", (req, res, next) => {
  passport.authenticate("google", (err: unknown, user: SessionUser | false, info: { message?: string }) => {
    if (err) return next(err);
    if (!user) {
      const reason = info?.message === "domain-not-allowed" ? "domain" : "failed";
      return res.redirect(`${clientOrigin()}/#/signin?error=${reason}`);
    }
    req.logIn(user, (loginErr) => {
      if (loginErr) return next(loginErr);
      return res.redirect(`${clientOrigin()}/#/`);
    });
  })(req, res, next);
});

authRouter.post("/auth/logout", (req: Request, res: Response) => {
  req.logout(() => {
    req.session.destroy(() => {
      res.clearCookie("mcc.sid");
      res.json({ ok: true });
    });
  });
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
