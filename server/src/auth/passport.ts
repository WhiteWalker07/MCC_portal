/**
 * Passport Google OAuth 2.0 strategy.
 *
 * Replaces Firebase Auth. The verify callback enforces the institute-domain
 * restriction (the rules' implicit trust + the client `isAllowedDomain` check are
 * now both consolidated here, server-side and authoritative). Only minimal
 * profile data is stored in the session; roles are resolved per request from the
 * database (see /api/me + middleware), never trusted from the cookie.
 */

import passport from "passport";
import { Strategy as GoogleStrategy, Profile } from "passport-google-oauth20";

export interface SessionUser {
  email: string;
  displayName: string;
  photoURL: string;
}

function allowedDomains(): string[] {
  return (process.env.ALLOWED_DOMAINS || "iimsirmaur.ac.in")
    .split(",")
    .map((d) => d.trim().toLowerCase())
    .filter(Boolean);
}

export function isAllowedDomain(email: string): boolean {
  if (!email || !email.includes("@")) return false;
  return allowedDomains().includes(email.split("@")[1].toLowerCase());
}

export function configurePassport(): void {
  passport.use(
    new GoogleStrategy(
      {
        clientID: process.env.GOOGLE_CLIENT_ID as string,
        clientSecret: process.env.GOOGLE_CLIENT_SECRET as string,
        callbackURL: process.env.OAUTH_CALLBACK_URL as string,
        scope: ["email", "profile"],
      },
      (_accessToken, _refreshToken, profile: Profile, done) => {
        const email = (profile.emails?.[0]?.value || "").toLowerCase();
        if (!isAllowedDomain(email)) {
          // Surface a friendly message the callback route can show.
          return done(null, false, { message: "domain-not-allowed" });
        }
        const user: SessionUser = {
          email,
          displayName: profile.displayName || email,
          photoURL: profile.photos?.[0]?.value || "",
        };
        return done(null, user);
      }
    )
  );

  // The whole minimal user object is the session payload.
  passport.serializeUser((user, done) => done(null, user as SessionUser));
  passport.deserializeUser((obj, done) => done(null, obj as SessionUser));
}

export { passport };
