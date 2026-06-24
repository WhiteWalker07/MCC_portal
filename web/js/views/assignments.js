/**
 * Assignments view (Phase 8) — for coordinators, domain heads, 2nd-years,
 * secretaries and admins. Lists the tasks the signed-in user may act on (scoped
 * server-side), and lets them:
 *   - Assign an UNFILLED task (Auto = engine picks, or Manual = choose a member)
 *   - Reassign a filled task (Auto / Manual)
 *   - Add an internal task type to a request (Photo Editor, Video Editor, etc.)
 *
 * All reads/writes go through the server callables (data.js), which enforce the
 * role gating. After each action the list reloads.
 */

import {
  listAssignableTasks,
  getEligibleMembers,
  getInternalTaskTypes,
  requestReassign,
  assignTask,
  markReadyToPost,
} from "../data.js";

function esc(s) {
  return String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}

function statusBadge(status) {
  const s = status || "—";
  return `<span class="status" data-status="${esc(s)}">${esc(s)}</span>`;
}

export function renderAssignments(container, session) {
  container.innerHTML = `
    <section class="card stack">
      <h1>Assignments</h1>
      <p id="asg-error" class="error" hidden></p>
      <div id="asg-list"><p class="muted">Loading…</p></div>
    </section>`;
  load(container, session);
}

async function load(container, session) {
  const listEl = container.querySelector("#asg-list");
  const errEl = container.querySelector("#asg-error");
  errEl.hidden = true;
  try {
    const [{ groups }, internalTypes] = await Promise.all([
      listAssignableTasks(),
      getInternalTaskTypes(),
    ]);
    if (!groups.length) {
      listEl.innerHTML = `<p class="muted">No tasks you can assign right now.</p>`;
      return;
    }
    const canMarkReady =
      session.isCoordinator ||
      session.isSecretary ||
      session.isAdmin ||
      session.isSecondYear;
    listEl.innerHTML = groups
      .map((g) => groupHtml(g, internalTypes, canMarkReady))
      .join("");
    wire(container, session);
  } catch (err) {
    listEl.innerHTML = "";
    errEl.textContent = `Couldn't load: ${err.message || err}`;
    errEl.hidden = false;
  }
}

function groupHtml(g, internalTypes, canMarkReady) {
  const readyBtn =
    canMarkReady && g.type === "Coverage" && g.status === "Event Covered"
      ? `<button type="button" class="btn btn--sm btn--primary mark-ready" data-req="${esc(g.requestId)}">Mark ready to post</button>`
      : "";
  return `
    <div class="asg-group" data-req="${esc(g.requestId)}">
      <div class="view-head">
        <h2 class="h2">${esc(g.refCode || "ID pending")} · ${esc(g.eventName || "(untitled)")}</h2>
        ${statusBadge(g.status)}
      </div>
      <ul class="list">
        ${g.tasks.map((t) => taskRow(g, t)).join("")}
      </ul>
      <div class="row">
        ${addTaskPanel(g, internalTypes)}
        ${readyBtn}
      </div>
    </div>`;
}

function taskRow(g, t) {
  const filled = Boolean(t.email);
  const action = filled ? "Reassign" : "Assign";
  return `
    <li class="list__item">
      <div class="task">
        <div class="list__main">
          <span class="list__title">${esc(t.task)}</span>
          <span class="list__sub">${filled ? esc(t.member || t.email) : "unfilled"}</span>
        </div>
        <div class="task__actions">${statusBadge(t.status)}</div>
      </div>
      <details class="panel"
               data-task="${esc(t.id)}"
               data-req="${esc(g.requestId)}"
               data-type="${esc(t.task)}"
               data-filled="${filled ? "1" : "0"}"
               data-exclude="${esc(t.email || "")}">
        <summary class="btn btn--sm">${action}…</summary>
        ${panelBody()}
      </details>
    </li>`;
}

function addTaskPanel(g, internalTypes) {
  if (!internalTypes.length) return "";
  return `
    <details class="panel addtask" data-req="${esc(g.requestId)}">
      <summary class="btn btn--sm">+ Add task…</summary>
      <div class="panel-body stack">
        <label class="field">
          <span class="field__label">Task type</span>
          <select class="input type-select">
            ${internalTypes.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("")}
          </select>
        </label>
        ${modeBlock()}
        <div class="row">
          <button type="button" class="btn btn--sm btn--primary confirm-btn">Add</button>
        </div>
        <p class="panel-error error" hidden></p>
      </div>
    </details>`;
}

function panelBody() {
  return `<div class="panel-body stack">${modeBlock()}
      <div class="row">
        <button type="button" class="btn btn--sm btn--primary confirm-btn">Confirm</button>
      </div>
      <p class="panel-error error" hidden></p>
    </div>`;
}

function modeBlock() {
  return `
    <fieldset class="seg">
      <label class="seg__opt"><input type="radio" name="m" value="auto" checked> Auto (engine picks)</label>
      <label class="seg__opt"><input type="radio" name="m" value="manual"> Manual</label>
    </fieldset>
    <label class="field member-wrap" hidden>
      <span class="field__label">Member</span>
      <select class="input member-select"><option value="">Loading…</option></select>
    </label>`;
}

function wire(container, session) {
  const pageErr = container.querySelector("#asg-error");

  container.querySelectorAll(".mark-ready").forEach((btn) => {
    btn.addEventListener("click", async () => {
      pageErr.hidden = true;
      btn.disabled = true;
      btn.textContent = "Working…";
      try {
        await markReadyToPost({ requestId: btn.dataset.req });
        await load(container, session);
      } catch (err) {
        btn.disabled = false;
        btn.textContent = "Mark ready to post";
        pageErr.textContent = err.message || String(err);
        pageErr.hidden = false;
      }
    });
  });

  container.querySelectorAll("details.panel").forEach((panel) => {
    const modeRadios = panel.querySelectorAll('input[name="m"]');
    const memberWrap = panel.querySelector(".member-wrap");
    const memberSelect = panel.querySelector(".member-select");
    const typeSelect = panel.querySelector(".type-select"); // only on add panels
    const confirmBtn = panel.querySelector(".confirm-btn");
    const errEl = panel.querySelector(".panel-error");

    const isAdd = panel.classList.contains("addtask");
    const requestId = panel.dataset.req;

    const currentMode = () =>
      panel.querySelector('input[name="m"]:checked').value;
    const currentType = () =>
      isAdd ? typeSelect.value : panel.dataset.type;

    async function loadMembers() {
      memberWrap.hidden = false;
      memberSelect.innerHTML = `<option value="">Loading…</option>`;
      try {
        const { members } = await getEligibleMembers({
          requestId,
          taskType: currentType(),
          excludeEmail: isAdd ? "" : panel.dataset.exclude,
        });
        memberSelect.innerHTML = members.length
          ? members
              .map(
                (m) =>
                  `<option value="${esc(m.email)}">${esc(m.name)} — ${esc(m.points)} pts${m.campus ? ` · ${esc(m.campus)}` : ""}</option>`
              )
              .join("")
          : `<option value="">No eligible members</option>`;
      } catch (err) {
        memberSelect.innerHTML = `<option value="">Error</option>`;
        showErr(errEl, err);
      }
    }

    modeRadios.forEach((r) =>
      r.addEventListener("change", () => {
        if (currentMode() === "manual") loadMembers();
        else memberWrap.hidden = true;
      })
    );
    if (typeSelect) {
      typeSelect.addEventListener("change", () => {
        if (currentMode() === "manual") loadMembers();
      });
    }

    confirmBtn.addEventListener("click", async () => {
      errEl.hidden = true;
      const mode = currentMode();
      const memberEmail = memberSelect.value;
      if (mode === "manual" && !memberEmail) {
        showErr(errEl, new Error("Pick a member."));
        return;
      }
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Saving…";
      try {
        if (isAdd) {
          await assignTask({ requestId, taskType: currentType(), mode, memberEmail });
        } else if (panel.dataset.filled === "1") {
          await requestReassign({ taskId: panel.dataset.task, mode, memberEmail });
        } else {
          await assignTask({ taskId: panel.dataset.task, mode, memberEmail });
        }
        await load(container, session); // reload list
      } catch (err) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = isAdd ? "Add" : "Confirm";
        showErr(errEl, err);
      }
    });
  });
}

function showErr(el, err) {
  el.textContent = err.message || String(err);
  el.hidden = false;
}
