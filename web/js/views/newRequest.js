/**
 * New Request view (Phase 4).
 *
 * One form, two modes (Coverage / Post). `status` is NEVER an editable field —
 * it's set to 'New' by the data layer and then owned by the engine. Role and
 * platform options are config-driven. Only rule-allowed keys are submitted.
 */

import { getRequestOptions, createRequest, tsFromLocal } from "../data.js";

function esc(s) {
  return String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}

export function renderNewRequest(container, session) {
  container.innerHTML = `<section class="card stack"><h1>New Request</h1><p class="muted">Loading options…</p></section>`;

  getRequestOptions()
    .then(({ roles, platforms }) => {
      container.innerHTML = formHtml(session, roles, platforms);
      wireForm(container, session);
    })
    .catch((err) => {
      container.innerHTML = `
        <section class="card stack">
          <h1>New Request</h1>
          <p class="error">Couldn't load options: ${esc(err.message || err)}</p>
        </section>`;
    });
}

function formHtml(session, roles, platforms) {
  const c = session.committee;
  const isCommittee = Boolean(c);
  const subtitle = isCommittee
    ? `${esc(c.name)} · ${esc(c.acronym)} · ${esc(c.campus)} · ${esc(session.user.email)}`
    : `${esc(session.user.email)} · Post request`;
  const typeToggle = isCommittee
    ? `<fieldset class="seg">
        <legend class="field__label">Request type</legend>
        <label class="seg__opt"><input type="radio" name="type" value="Coverage" checked> Coverage</label>
        <label class="seg__opt"><input type="radio" name="type" value="Post"> Post</label>
      </fieldset>`
    : `<fieldset class="seg">
        <legend class="field__label">Request type</legend>
        <label class="seg__opt"><input type="radio" name="type" value="Post" checked> Post</label>
      </fieldset>
      <p class="note">// coverage requests are reserved to committees</p>`;
  return `
  <section class="card stack">
    <h1>New Request</h1>
    <p class="muted">${subtitle}</p>

    <form id="req-form" class="stack" novalidate>
      ${typeToggle}

      <label class="field">
        <span class="field__label" id="eventName-label">Event name</span>
        <input class="input" name="eventName" autocomplete="off" />
      </label>

      <div id="coverage-fields" class="stack">
        <div class="grid2">
          <label class="field">
            <span class="field__label">Event start</span>
            <input class="input" type="datetime-local" name="eventStart" />
          </label>
          <label class="field">
            <span class="field__label">Event end</span>
            <input class="input" type="datetime-local" name="eventEnd" />
          </label>
        </div>
        <label class="field">
          <span class="field__label">Venue</span>
          <input class="input" name="venue" autocomplete="off" />
        </label>
        <fieldset class="field">
          <legend class="field__label">Roles needed</legend>
          <div class="checks">
            ${roles
              .map(
                (r) =>
                  `<label class="check"><input type="checkbox" name="rolesNeeded" value="${esc(r)}"> ${esc(r)}</label>`
              )
              .join("")}
          </div>
        </fieldset>
      </div>

      <fieldset class="field">
        <legend class="field__label">Platforms <span class="muted" id="platforms-hint">(where to post)</span></legend>
        <div class="checks">
          ${platforms
            .map(
              (p) =>
                `<label class="check"><input type="checkbox" name="platforms" value="${esc(p)}"> ${esc(p)}</label>`
            )
            .join("")}
        </div>
      </fieldset>

      <label class="field">
        <span class="field__label">Content links <span class="muted" id="contentLinks-hint">(optional)</span></span>
        <textarea class="input" name="contentLinks" rows="2" placeholder="Drive / links to assets…"></textarea>
      </label>

      <label class="field">
        <span class="field__label">Requester</span>
        <input class="input" name="requester" value="${esc(isCommittee ? c.name : session.user.displayName || session.user.email)}" autocomplete="off" />
      </label>

      <label class="field">
        <span class="field__label">Notes</span>
        <textarea class="input" name="notes" rows="2"></textarea>
      </label>

      <p class="muted">The Request ID and status are assigned automatically.</p>
      <p id="form-error" class="error" hidden></p>
      <div class="row">
        <button type="submit" class="btn btn--primary" id="submit-btn">Submit request</button>
        <a class="btn btn--sm" href="#/requests">Cancel</a>
      </div>
    </form>
  </section>`;
}

function wireForm(container, session) {
  const form = container.querySelector("#req-form");
  const coverage = container.querySelector("#coverage-fields");
  const errEl = container.querySelector("#form-error");
  const submitBtn = container.querySelector("#submit-btn");
  const enLabel = container.querySelector("#eventName-label");
  const clHint = container.querySelector("#contentLinks-hint");
  const pfHint = container.querySelector("#platforms-hint");

  const currentType = () =>
    form.querySelector('input[name="type"]:checked').value;

  function applyType() {
    const isCoverage = currentType() === "Coverage";
    coverage.hidden = !isCoverage;
    enLabel.textContent = isCoverage ? "Event name" : "Title / subject";
    clHint.textContent = isCoverage ? "(optional)" : "(required)";
    pfHint.textContent = isCoverage ? "(where to post)" : "(required)";
  }
  form
    .querySelectorAll('input[name="type"]')
    .forEach((r) => r.addEventListener("change", applyType));
  applyType();

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errEl.hidden = true;

    const type = currentType();
    const fd = new FormData(form);
    const eventName = (fd.get("eventName") || "").trim();
    const venue = (fd.get("venue") || "").trim();
    const requester = (fd.get("requester") || "").trim();
    const contentLinks = (fd.get("contentLinks") || "").trim();
    const notes = (fd.get("notes") || "").trim();
    const platforms = fd.getAll("platforms");
    const rolesNeeded = fd.getAll("rolesNeeded");
    const eventStart = fd.get("eventStart");
    const eventEnd = fd.get("eventEnd");

    const errs = [];
    if (!eventName) errs.push("Event name is required.");
    if (type === "Coverage") {
      if (!eventStart) errs.push("Event start is required.");
      if (!eventEnd) errs.push("Event end is required.");
      if (eventStart && eventEnd && new Date(eventEnd) < new Date(eventStart)) {
        errs.push("Event end must be after the start.");
      }
      if (rolesNeeded.length === 0) errs.push("Select at least one role.");
    } else {
      if (platforms.length === 0) errs.push("Select at least one platform.");
      if (!contentLinks) errs.push("Content links are required for a Post.");
    }
    if (errs.length) {
      errEl.textContent = errs.join(" ");
      errEl.hidden = false;
      return;
    }

    // Only rule-allowed keys; engine sets refCode/campus/coordinatorEmail.
    const payload = { type, eventName, requester, notes, platforms, contentLinks };
    if (type === "Coverage") {
      payload.eventStart = tsFromLocal(eventStart);
      payload.eventEnd = tsFromLocal(eventEnd);
      payload.venue = venue;
      payload.rolesNeeded = rolesNeeded;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = "Submitting…";
    try {
      await createRequest(session, payload);
      location.hash = "#/requests";
    } catch (err) {
      errEl.textContent = `Could not submit: ${err.message || err}`;
      errEl.hidden = false;
      submitBtn.disabled = false;
      submitBtn.textContent = "Submit request";
    }
  });
}
