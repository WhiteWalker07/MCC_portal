/**
 * Data access layer — every backend call the views use goes through here.
 *
 * Previously a thin wrapper over the Firebase SDK (Firestore + callables); now a
 * thin wrapper over the Express API (api.js). Function names + signatures are
 * preserved so the views are unchanged.
 *
 * Live Firestore listeners (onSnapshot) are replaced by POLLING: each `watch*`
 * fetches immediately, then re-fetches on an interval, and returns an
 * unsubscribe function (so the shell's per-view teardown still works). Mutations
 * resolve a promise; views re-fetch / navigate on success as before.
 */

import { apiGet, apiPost } from "./api.js";

const POLL_MS = 20000;

/**
 * Poll `fetchFn` immediately and then every POLL_MS. Calls onData with each
 * result and onError on failure. Returns an unsubscribe function.
 */
function poll(fetchFn, onData, onError, intervalMs = POLL_MS) {
  let stopped = false;
  let timer = null;

  async function tick() {
    if (stopped) return;
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    try {
      const data = await fetchFn();
      if (!stopped) onData(data);
    } catch (err) {
      if (!stopped && onError) onError(err);
    }
    if (!stopped) timer = setTimeout(tick, intervalMs);
  }

  // A successful mutation anywhere triggers an immediate refresh (replaces the
  // instant feedback the old Firestore onSnapshot listeners gave).
  const onMutated = () => {
    if (!stopped) tick();
  };
  window.addEventListener("mcc:mutated", onMutated);

  tick();
  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    window.removeEventListener("mcc:mutated", onMutated);
  };
}

/** POST a mutation, then signal the pollers to refresh immediately. */
async function mutate(path, body) {
  const res = await apiPost(path, body);
  window.dispatchEvent(new Event("mcc:mutated"));
  return res;
}

/** Convert a <input type="datetime-local"> value to an ISO string for the API. */
export function tsFromLocal(value) {
  return new Date(value).toISOString();
}

// ---- Config-driven form options -------------------------------------------

export async function getRequestOptions() {
  return apiGet("/api/options"); // { roles, platforms }
}

/** Internal-only task types staff may add manually. */
export async function getInternalTaskTypes() {
  const { taskTypes } = await apiGet("/api/internal-task-types");
  return taskTypes;
}

// ---- Requests --------------------------------------------------------------

/**
 * Create a request. The client supplies only content fields; the server sets
 * contactEmail (from the session), the initial status, createdAt, and runs the
 * engine (refCode / pipeline / assignment) synchronously before responding.
 */
export async function createRequest(_session, payload) {
  const saved = await mutate("/api/requests", payload);
  return saved?.id;
}

/** Live list of the signed-in requester's own requests, newest first. */
export function watchMyRequests(_email, onData, onError) {
  return poll(() => apiGet("/api/requests/mine"), onData, onError);
}

/** Live single request (detail view). Calls back with null if not found/allowed. */
export function watchRequest(id, onData, onError) {
  return poll(
    async () => {
      try {
        return await apiGet(`/api/requests/${id}`);
      } catch (err) {
        if (err.status === 404 || err.status === 403) return null;
        throw err;
      }
    },
    onData,
    onError
  );
}

/** Live list of requests awaiting POC approval (secretary). */
export function watchPendingRequests(onData, onError) {
  return poll(() => apiGet("/api/requests/pending"), onData, onError);
}

/** Live tasks for a request (used to show the proposed team in Approvals). */
export function watchRequestTasks(requestId, onData, onError) {
  return poll(() => apiGet(`/api/requests/${requestId}/tasks`), onData, onError);
}

export function approveRequest(id, _by) {
  return mutate(`/api/requests/${id}/approve`);
}

export function rejectRequest(id, _by, reason) {
  return mutate(`/api/requests/${id}/reject`, { reason: reason || "" });
}

// ---- Tasks -----------------------------------------------------------------

/** Live list of the signed-in team member's tasks, soonest deadline first. */
export function watchMyTasks(_email, onData, onError) {
  return poll(() => apiGet("/api/tasks/mine"), onData, onError);
}

/** Mark a task done (assignee closing their own CONFIRMED/LATE task). */
export function markTaskDone(taskId) {
  return mutate(`/api/tasks/${taskId}/done`);
}

// ---- Assignments -----------------------------------------------------------

export const listAssignableTasks = () => apiGet("/api/assignable-tasks");

export const getEligibleMembers = (data) => apiPost("/api/eligible-members", data);

export const requestReassign = (data) => mutate("/api/reassign", data);

export const assignTask = (data) => mutate("/api/assign", data);

export const markReadyToPost = (data) => mutate("/api/mark-ready", data);

// ---- Dashboard -------------------------------------------------------------

export const getDashboardStats = (filters) => apiPost("/api/dashboard", { filters: filters || {} });

// ---- Team admin ------------------------------------------------------------

export const importTeamCsv = (rows) => mutate("/api/team/import", { rows });

export const listTeamMembers = () => apiGet("/api/team");

// ---- Committees ------------------------------------------------------------

export const listCommittees = () => apiGet("/api/committees");

export const addCommittee = (payload) => mutate("/api/committees", payload);

export const setDomainHead = (email, vertical) => mutate("/api/team/head", { email, vertical });

/** Flip a member between "available" (on work) and "out" (out of work / break). */
export const setAvailability = (email, availability) =>
  mutate("/api/team/availability", { email, availability });

export const setPointScheme = (points) => mutate("/api/config/points", { points });

/** Read the current scoring scheme. */
export async function getPointScheme() {
  return apiGet("/api/config/points");
}

// ---- CSV helper (unchanged) ------------------------------------------------

/**
 * Minimal CSV parser: first row is the header, fields comma-separated, multi-value
 * fields (skills) use ';'. Good enough for the team template; not RFC-4180.
 */
export function parseCsv(text) {
  const lines = String(text)
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
  if (lines.length < 2) return [];
  const headers = lines[0].split(",").map((h) => h.trim());
  return lines.slice(1).map((line) => {
    const cells = line.split(",");
    const row = {};
    headers.forEach((h, i) => {
      row[h] = (cells[i] || "").trim();
    });
    return row;
  });
}
