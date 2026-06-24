/**
 * Approvals view (Phase 7) — secretaries only.
 *
 * Lists requests parked at 'Pending for POC approval', shows the engine's
 * proposed team (read from tasks), and lets the secretary Approve or Reject
 * (with a reason). Approve/Reject write only a `decision` field; the engine
 * (onRequestDecided) runs the confirm/reject routine.
 */

import {
  watchPendingRequests,
  watchRequestTasks,
  approveRequest,
  rejectRequest,
} from "../data.js";

function esc(s) {
  return String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}

export function renderApprovals(container, session) {
  container.innerHTML = `
    <section class="card stack">
      <h1>Approvals</h1>
      <p id="apr-error" class="error" hidden></p>
      <div id="apr-list"><p class="muted">Loading…</p></div>
    </section>`;
  const listEl = container.querySelector("#apr-list");
  const errEl = container.querySelector("#apr-error");

  const taskUnsubs = [];
  const cleanupTasks = () => {
    while (taskUnsubs.length) taskUnsubs.pop()();
  };

  const unsub = watchPendingRequests(
    (rows) => {
      cleanupTasks();
      if (!rows.length) {
        listEl.innerHTML = `<p class="muted">No requests awaiting approval.</p>`;
        return;
      }
      listEl.innerHTML = rows.map(cardHtml).join("");
      rows.forEach((r) => wireCard(listEl, r, session, errEl, taskUnsubs));
    },
    (err) => {
      listEl.innerHTML = `<p class="error">Couldn't load: ${esc(err.message || err)}</p>`;
    }
  );

  return () => {
    cleanupTasks();
    unsub();
  };
}

function cardHtml(r) {
  return `
    <div class="asg-group" data-req="${esc(r.id)}">
      <div class="view-head">
        <h2 class="h2">${esc(r.refCode || "ID pending")} · ${esc(r.eventName || "(untitled)")}</h2>
        <span class="status" data-status="${esc(r.status)}">${esc(r.status)}</span>
      </div>
      <p class="muted">${esc(r.type)}${r.campus ? " · " + esc(r.campus) : ""} · ${esc(r.contactEmail || "")}</p>
      <div class="proposed muted">Loading proposed team…</div>
      <div class="row">
        <button type="button" class="btn btn--sm btn--primary approve-btn">Approve</button>
        <details class="panel reject">
          <summary class="btn btn--sm">Reject…</summary>
          <div class="panel-body stack">
            <label class="field">
              <span class="field__label">Reason</span>
              <input class="input reject-reason" autocomplete="off" />
            </label>
            <div class="row"><button type="button" class="btn btn--sm reject-confirm">Confirm reject</button></div>
          </div>
        </details>
      </div>
      <p class="card-error error" hidden></p>
    </div>`;
}

function wireCard(root, r, session, pageErr, taskUnsubs) {
  const card = root.querySelector(`.asg-group[data-req="${cssEscape(r.id)}"]`);
  if (!card) return;
  const proposedEl = card.querySelector(".proposed");
  const cardErr = card.querySelector(".card-error");
  const approveBtn = card.querySelector(".approve-btn");
  const rejectConfirm = card.querySelector(".reject-confirm");
  const reasonInput = card.querySelector(".reject-reason");

  // Live proposed team.
  const u = watchRequestTasks(
    r.id,
    (tasks) => {
      const rows = tasks
        .filter((t) => t.task !== "Post")
        .map(
          (t) =>
            `<tr><td>${esc(t.task)}</td><td>${esc(t.member || (t.status === "UNFILLED" ? "UNFILLED" : "—"))}</td><td>${esc(t.email || "")}</td></tr>`
        )
        .join("");
      proposedEl.innerHTML = rows
        ? `<table class="roster"><thead><tr><th>Role</th><th>Member</th><th>Email</th></tr></thead><tbody>${rows}</tbody></table>`
        : `<span class="muted">No tasks.</span>`;
    },
    () => {
      proposedEl.innerHTML = `<span class="muted">Couldn't load tasks.</span>`;
    }
  );
  taskUnsubs.push(u);

  approveBtn.addEventListener("click", async () => {
    cardErr.hidden = true;
    approveBtn.disabled = true;
    approveBtn.textContent = "Approving…";
    try {
      await approveRequest(r.id, session.user.email);
      // onSnapshot drops it from the pending list once the engine confirms.
    } catch (err) {
      approveBtn.disabled = false;
      approveBtn.textContent = "Approve";
      cardErr.textContent = err.message || String(err);
      cardErr.hidden = false;
    }
  });

  rejectConfirm.addEventListener("click", async () => {
    cardErr.hidden = true;
    rejectConfirm.disabled = true;
    try {
      await rejectRequest(r.id, session.user.email, reasonInput.value.trim());
    } catch (err) {
      rejectConfirm.disabled = false;
      cardErr.textContent = err.message || String(err);
      cardErr.hidden = false;
    }
  });
}

function cssEscape(s) {
  return String(s).replace(/["\\]/g, "\\$&");
}
