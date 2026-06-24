/**
 * Tiny fetch wrapper around the backend API (replaces firebase.js).
 *
 * Every call sends the session cookie (`credentials: "include"`) so the
 * cross-origin Passport session works. JSON in / JSON out; non-2xx responses
 * throw an Error carrying `.status` and `.data` so callers can branch on 401/404.
 */

import { API_BASE_URL } from "./config.js";

export async function apiFetch(path, { method = "GET", body, headers } = {}) {
  const opts = {
    method,
    credentials: "include",
    headers: { ...(headers || {}) },
  };
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
