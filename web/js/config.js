/**
 * Frontend config (replaces firebase-config.js).
 *
 * The portal now talks to a standalone Express API (on Render) instead of
 * Firebase. The ONLY thing to set for production is PROD_API_BASE below — point
 * it at your Render service URL. Local dev (localhost) auto-targets :8080.
 *
 * Auth, domain restriction, and all data access are enforced server-side; the
 * values here are not security-sensitive.
 */

// Your deployed API origin (Render). In production the frontend does NOT call
// this directly — it uses same-origin "/api" and "/auth" paths that Vercel
// proxies to Render (see the "rewrites" in vercel.json). That keeps the session
// cookie FIRST-PARTY to the Vercel domain, so Safari/iOS (which block cross-site
// cookies) no longer drop you back to the login screen. If you change your
// Render URL, update it here AND in vercel.json.
const PROD_API_BASE = "https://mcc-portal.onrender.com";

const isLocal = ["localhost", "127.0.0.1"].includes(location.hostname);

// "" => relative URLs => same-origin => Vercel proxy => Render.
// localhost talks to the local API directly.
export const API_BASE_URL = isLocal ? "http://localhost:8080" : "";

/** Shown on the sign-in screen (display only — real enforcement is server-side). */
export const ALLOWED_AUTH_DOMAINS = ["iimsirmaur.ac.in"];
