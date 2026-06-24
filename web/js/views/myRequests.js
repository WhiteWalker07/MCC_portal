/**
 * My Requests view (Phase 4) — live list + read-only detail.
 *
 * Status is read-only everywhere (engine-owned). The assigned-team roster is read
 * from `request.roster`, which the engine denormalizes onto the request during
 * the confirm routine (Phase 7) — the requester can't read the tasks collection
 * directly, by design.
 *
 * Each render returns an unsubscribe function so the shell can tear down the live
 * listener on navigation.
 */

import { watchMyRequests, watchRequest } from "../data.js";

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
  return d.toLocaleString([], {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function statusBadge(status) {
  const s = status || "New";
  return `<span class="status" data-status="${esc(s)}">${esc(s)}</span>`;
}

// Lifecycle stages [short label, full status].
const STAGES = [
  ["New", "New"],
  ["Pending", "Pending for POC approval"],
  ["Accepted", "Request Accepted"],
  ["Covered", "Event Covered"],
  ["Ready", "Ready To post"],
  ["Posted", "Posted"],
];

function stepperHtml(status) {
  if (status === "Rejected") {
    return `<div class="stepper-rejected">Rejected</div>`;
  }
  const idx = Math.max(
    0,
    STAGES.findIndex((s) => s[1] === status)
  );
  return `<div class="stepper">${STAGES.map(([label], i) => {
    const state = i < idx ? "done" : i === idx ? "current" : "todo";
    const filled = i < idx ? " is-filled" : "";
    return `<div class="stepper__stage${filled}" data-state="${state}"><span class="stepper__dot"></span><span class="stepper__label">${esc(label)}</span></div>`;
  }).join("")}</div>`;
}

function initials(name) {
  return String(name || "")
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0] || "")
    .join("")
    .toUpperCase();
}

function linkify(s) {
  if (!s) return "—";
  return esc(s).replace(
    /(https?:\/\/[^\s]+)/g,
    '<a href="$1" target="_blank" rel="noopener">$1</a>'
  );
}

export function renderMyRequests(container, session, params) {
  const id = params && params[0];
  return id
    ? renderDetail(container, session, id)
    : renderList(container, session);
}

function renderList(container, session) {
  container.innerHTML = `
    <section class="card stack">
      <div class="view-head">
        <h1>My Requests</h1>
        <a class="btn btn--sm btn--primary" href="#/new">New</a>
      </div>
      <div id="req-list"><p class="muted">Loading…</p></div>
    </section>`;
  const listEl = container.querySelector("#req-list");

  return watchMyRequests(
    session.user.email,
    (rows) => {
      if (!rows.length) {
        listEl.innerHTML = `<p class="muted">No requests yet. <a href="#/new">Create one</a>.</p>`;
        return;
      }
      listEl.innerHTML = `<ul class="list">${rows.map(rowHtml).join("")}</ul>`;
    },
    (err) => {
      listEl.innerHTML = `<p class="error">Couldn't load: ${esc(err.message || err)}</p>`;
    }
  );
}

function rowHtml(r) {
  const sub = [
    r.refCode || "ID pending",
    r.type,
    r.type === "Coverage" && r.eventStart ? fmtDate(r.eventStart) : null,
  ]
    .filter(Boolean)
    .map(esc)
    .join(" · ");
  return `
    <li class="list__item">
      <a class="list__link" href="#/requests/${esc(r.id)}">
        <span class="list__main">
          <span class="list__title">${esc(r.eventName || "(untitled)")}</span>
          <span class="list__sub">${sub}</span>
        </span>
        ${statusBadge(r.status)}
      </a>
    </li>`;
}

function renderDetail(container, session, id) {
  container.innerHTML = `
    <section class="card stack">
      <a class="back" href="#/requests">← My Requests</a>
      <div id="req-detail"><p class="muted">Loading…</p></div>
    </section>`;
  const el = container.querySelector("#req-detail");

  return watchRequest(
    id,
    (r) => {
      el.innerHTML = r
        ? detailHtml(r)
        : `<p class="error">Request not found, or you don't have access.</p>`;
    },
    (err) => {
      el.innerHTML = `<p class="error">Couldn't load: ${esc(err.message || err)}</p>`;
    }
  );
}

function detailHtml(r) {
  const accepted = [
    "Request Accepted",
    "Event Covered",
    "Ready To post",
    "Posted",
  ].includes(r.status);
  const roster = Array.isArray(r.roster) ? r.roster : [];

  const coverageRows =
    r.type === "Coverage"
      ? `
        <div><dt>Start</dt><dd>${esc(fmtDate(r.eventStart))}</dd></div>
        <div><dt>End</dt><dd>${esc(fmtDate(r.eventEnd))}</dd></div>
        <div><dt>Venue</dt><dd>${esc(r.venue || "—")}</dd></div>
        <div><dt>Roles</dt><dd>${esc((r.rolesNeeded || []).join(", ") || "—")}</dd></div>`
      : "";

  return `
    <div class="view-head">
      <h1>${esc(r.eventName || "(untitled)")}</h1>
      ${statusBadge(r.status)}
    </div>
    ${stepperHtml(r.status)}
    <dl class="dl">
      <div><dt>Request ID</dt><dd>${esc(r.refCode || "pending")}</dd></div>
      <div><dt>Type</dt><dd>${esc(r.type)}</dd></div>
      ${r.campus ? `<div><dt>Campus</dt><dd>${esc(r.campus)}</dd></div>` : ""}
      ${coverageRows}
      <div><dt>Platforms</dt><dd>${esc((r.platforms || []).join(", ") || "—")}</dd></div>
      <div><dt>Content links</dt><dd>${linkify(r.contentLinks)}</dd></div>
      <div><dt>Requester</dt><dd>${esc(r.requester || "—")}</dd></div>
      <div><dt>Notes</dt><dd>${esc(r.notes || "—")}</dd></div>
      <div><dt>Created</dt><dd>${esc(fmtDate(r.createdAt))}</dd></div>
    </dl>

    <h2 class="h2">Assigned team</h2>
    ${rosterHtml(accepted, roster)}
    ${postsHtml(r)}
  `;
}

function postsHtml(r) {
  const posts = Array.isArray(r.posts) ? r.posts : [];
  if (!posts.length) return "";
  return `
    <h2 class="h2">Scheduled posts</h2>
    <table class="roster">
      <thead><tr><th>Platform</th><th>Scheduled</th><th>Handler</th></tr></thead>
      <tbody>
        ${posts
          .map(
            (p) =>
              `<tr><td>${esc(p.platform)}</td><td>${esc(p.scheduledAt ? fmtDate(p.scheduledAt) : "—")}</td><td>${esc(p.handlerEmail || "—")}</td></tr>`
          )
          .join("")}
      </tbody>
    </table>`;
}

function rosterHtml(accepted, roster) {
  if (!accepted) {
    return `<p class="muted">The assigned team and their contact details appear
      here once the request is accepted.</p>`;
  }
  if (!roster.length) {
    return `<p class="muted">Team assigned — contact details will appear shortly.</p>`;
  }
  return `
    <div class="roster-cards">
      ${roster
        .map((m) => {
          const meta = [m.email, m.phone].filter(Boolean).map(esc).join(" · ");
          const call = m.phone
            ? `<a class="contact__call" href="tel:${esc(m.phone)}">Call</a>`
            : "";
          return `
        <div class="contact">
          <div class="avatar">${esc(initials(m.name))}</div>
          <div class="contact__main">
            <div class="contact__name">${esc(m.name)} · ${esc(m.role)}</div>
            <div class="contact__meta mono">${meta || "—"}</div>
          </div>
          ${call}
        </div>`;
        })
        .join("")}
    </div>`;
}
