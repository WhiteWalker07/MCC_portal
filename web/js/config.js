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

// 👉 Your deployed API origin (Render). The frontend calls it directly; auth uses
// a Bearer token (not a cookie), so cross-origin works on every browser.
const PROD_API_BASE = "https://mcc-portal.onrender.com";

const isLocal = ["localhost", "127.0.0.1"].includes(location.hostname);

export const API_BASE_URL = isLocal ? "http://localhost:8080" : PROD_API_BASE;

/** Shown on the sign-in screen (display only — real enforcement is server-side). */
export const ALLOWED_AUTH_DOMAINS = ["iimsirmaur.ac.in"];
