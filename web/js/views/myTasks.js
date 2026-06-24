/**
 * My Tasks view (Phase 6) — a team member's own tasks with a Task Completed
 * button. The button shows only for CONFIRMED/LATE tasks (late-but-delivered
 * work can still close); the security rule enforces the same. Marking done sets
 * status DONE, which the engine reacts to (Event Covered / Ready To post).
 */

import { watchMyTasks, markTaskDone } from "../data.js";

function esc(s) {
  return String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}

function fmtDate(ts) {
  if (!ts) return "—";
  const d = ts.toDate ? ts.toDate() : new Date(ts);
  if (isNaN(d)) return "—";
  return d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

// Relative deadline, e.g. "in 22h", "in 2d", "6h ago".
function fmtRel(ts) {
  if (!ts) return "";
  const d = ts.toDate ? ts.toDate() : new Date(ts);
  if (isNaN(d)) return "";
  const diff = d.getTime() - Date.now();
  const past = diff < 0;
  const mins = Math.round(Math.abs(diff) / 60000);
  const hrs = Math.round(Math.abs(diff) / 3600000);
  const days = Math.round(Math.abs(diff) / 86400000);
  const s = mins < 60 ? `${mins}m` : hrs < 48 ? `${hrs}h` : `${days}d`;
  return past ? `${s} ago` : `in ${s}`;
}

function statusBadge(status) {
  const s = status || "PROPOSED";
  return `<span class="status" data-status="${esc(s)}">${esc(s)}</span>`;
}

export function renderMyTasks(container, session) {
  container.innerHTML = `
    <section class="card stack">
      <h1>My Tasks</h1>
      <p id="task-error" class="error" hidden></p>
      <div id="task-list"><p class="muted">Loading…</p></div>
    </section>`;
  const listEl = container.querySelector("#task-list");
  const errEl = container.querySelector("#task-error");

  return watchMyTasks(
    session.user.email,
    (tasks) => {
      if (!tasks.length) {
        listEl.innerHTML = `<p class="muted">No tasks assigned to you yet.</p>`;
        return;
      }
      listEl.innerHTML = `<ul class="tcards">${tasks.map(taskHtml).join("")}</ul>`;
      wireButtons(listEl, errEl);
    },
    (err) => {
      listEl.innerHTML = `<p class="error">Couldn't load: ${esc(err.message || err)}</p>`;
    }
  );
}

function taskHtml(t) {
  const canComplete = t.status === "CONFIRMED" || t.status === "LATE";
  // An event-bound task can't be completed before the event starts.
  const notStarted = t.eventStart && new Date(t.eventStart).getTime() > Date.now();
  const late = t.status === "LATE";
  const sub = [t.refCode, t.eventName].filter(Boolean).map(esc).join(" · ");
  const ptsChip = Number(t.points)
    ? `<span class="chip-pts">+${esc(t.points)} pts</span>`
    : "";
  const dueChip = t.deadline
    ? `<span class="chip-due${late ? " is-late" : ""}">${late ? "overdue " : "due "}${esc(fmtRel(t.deadline))}</span>`
    : "";
  return `
    <li class="tcard${late ? " tcard--late" : ""}">
      <div class="tcard__main">
        <div class="tcard__title">${esc(t.task)}</div>
        <div class="tcard__sub">${sub}</div>
        <div class="tcard__chips">${ptsChip}${statusBadge(t.status)}${dueChip}</div>
      </div>
      ${
        canComplete
          ? notStarted
            ? `<button class="btn btn--sm" type="button" disabled title="Available once the event starts on ${esc(fmtDate(t.eventStart))}">Starts ${esc(fmtRel(t.eventStart))}</button>`
            : `<button class="btn btn--sm btn--primary task-done" data-id="${esc(t.id)}" type="button">Mark done</button>`
          : ""
      }
    </li>`;
}

function wireButtons(root, errEl) {
  root.querySelectorAll(".task-done").forEach((btn) => {
    btn.addEventListener("click", async () => {
      errEl.hidden = true;
      btn.disabled = true;
      btn.textContent = "Saving…";
      try {
        await markTaskDone(btn.dataset.id);
        // onSnapshot re-renders the list with the new status.
      } catch (err) {
        btn.disabled = false;
        btn.textContent = "Mark done";
        errEl.textContent = `Could not complete: ${err.message || err}`;
        errEl.hidden = false;
      }
    });
  });
}
