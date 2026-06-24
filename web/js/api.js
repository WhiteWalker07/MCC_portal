/**
 * Tiny fetch wrapper around the backend API.
 *
 * Auth is a Bearer token kept in localStorage (set after Google sign-in). It's
 * sent on every request as `Authorization: Bearer <token>` — no cookies — so the
 * cross-origin call to the API works on every browser, including Safari/iOS.
 */

import { API_BASE_URL } from "./config.js";

const TOKEN_KEY = "mcc_token";

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}
export function setToken(t) {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t);
  } catch {
    /* ignore storage errors */
  }
}
export function clearToken() {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

export async function apiFetch(path, { method = "GET", body, headers } = {}) {
  const opts = { method, headers: { ...(headers || {}) } };
  const token = getToken();
  if (token) opts.headers["Authorization"] = `Bearer ${token}`;
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }

  let res;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, opts);
  } catch (networkErr) {
    const err = new Error("Cannot reach the server. Check your connection.");
    err.status = 0;
    err.cause = networkErr;
    throw err;
  }

  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }

  if (!res.ok) {
    const err = new Error((data && data.error) || res.statusText || "Request failed");
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

export const apiGet = (path) => apiFetch(path);
export const apiPost = (path, body) => apiFetch(path, { method: "POST", body });
