/**
 * Authentication (token-based).
 *
 * Sign-in is a server-side Google OAuth handshake: we redirect to the API's
 * /auth/google, which bounces through Google and redirects back to the frontend
 * with a signed token in the URL (?token=...). app.js stores that token; from
 * then on every API call carries it as a Bearer header (see api.js). No cookies,
 * so it works on Safari/iOS and everywhere else.
 */

import { API_BASE_URL } from "./config.js";
import { apiGet, apiPost, clearToken } from "./api.js";

/** Begin Google sign-in (full-page redirect to the API). */
export function signIn() {
  window.location.href = `${API_BASE_URL}/auth/google`;
}

/** Sign out: drop the token locally (best-effort server ping). */
export function signOut() {
  clearToken();
  return apiPost("/auth/logout").catch(() => {});
}

/**
 * Fetch the current session (roles, committee, team, …) or null if not signed in
 * / token missing or expired. Throws only on network/server errors, not on 401.
 */
export async function getSession() {
  try {
    return await apiGet("/api/me");
  } catch (err) {
    if (err.status === 401) return null;
    throw err;
  }
}
