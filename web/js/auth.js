/**
 * Authentication (replaces the Firebase Auth wrapper).
 *
 * Sign-in is a server-side Google OAuth handshake: we redirect the browser to
 * the API's /auth/google, which bounces through Google and back, sets the
 * session cookie, and redirects to the frontend. There is no client SDK and no
 * live auth listener — the app probes the session with GET /api/me on load.
 *
 * Domain restriction (@iimsirmaur.ac.in) is enforced server-side in the OAuth
 * verify callback; the client cannot bypass it.
 */

import { API_BASE_URL } from "./config.js";
import { apiGet, apiPost } from "./api.js";

/** Begin Google sign-in (full-page redirect to the API). */
export function signIn() {
  window.location.href = `${API_BASE_URL}/auth/google`;
}

/** Sign out: destroy the server session. */
export function signOut() {
  return apiPost("/auth/logout");
}

/**
 * Fetch the current session (roles, committee, team, …) or null if not signed in.
 * Throws only on network/server errors, not on 401.
 */
export async function getSession() {
  try {
    return await apiGet("/api/me");
  } catch (err) {
    if (err.status === 401) return null;
    throw err;
  }
}
