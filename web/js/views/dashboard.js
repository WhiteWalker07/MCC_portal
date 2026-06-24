/**
 * Dashboard view — fairness & usage stats for admins / secretaries.
 *
 * All numbers come from the getDashboardStats callable (server-aggregated), so
 * the client needs no broad read access.
 */

import { getDashboardStats } from "../data.js";

function esc(s) {
  return String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}

const FILTERS = [
  {
    name: "period",
    options: [
      ["all", "All time"],
      ["7d", "Last 7 days"],
      ["30d", "Last 30 days"],
      ["90d", "Last 90 days"],
      ["month", "This month"],
    ],
  },
  {
    name: "campus",
    options: [
      ["all", "All campuses"],
      ["Permanent", "Permanent"],
      ["BMS", "BMS"],
    ],
  },
  {
    name: "year",
    options: [
      ["all", "All years"],
      ["1", "1st year"],
      ["2", "2nd year"],
    ],
  },
  {
    name: "vertical",
    options: [
      ["all", "All verticals"],
      ["Photography", "Photography"],
      ["Videography", "Videography"],
      ["Graphic Designs", "Graphic Designs"],
      ["Content Writing", "Content Writing"],
    ],
  },
];

function filterBarHtml(state) {
  return `<div class="filters">${FILTERS.map(
    (f) =>
      `<select class="input filter__sel" data-name="${f.name}" aria-label="${f.name}">${f.options
        .map(
          ([v, l]) =>
            `<option value="${esc(v)}"${v === state[f.name] ? " selected" : ""}>${esc(l)}</option>`
        )
        .join("")}</select>`
  ).join("")}</div>`;
}

export function renderDashboard(container) {
  const state = { period: "all", campus: "all", year: "all", vertical: "all" };

  container.innerHTML = `
    <section class="card stack">
      <h1>Dashboard</h1>
      ${filterBarHtml(state)}
      <p id="dash-error" class="error" hidden></p>
      <div id="dash-body"><p class="muted">Loading…</p></div>
    </section>`;
  const body = container.querySelector("#dash-body");
  const errEl = container.querySelector("#dash-error");

  function load() {
    errEl.hidden = true;
    body.innerHTML = `<p class="muted">Loading…</p>`;
    getDashboardStats(state)
      .then((stats) => {
        body.innerHTML = bodyHtml(stats);
      })
      .catch((err) => {
        body.innerHTML = "";
        errEl.textContent = err.message || String(err);
        errEl.hidden = false;
      });
  }

  container.querySelectorAll(".filter__sel").forEach((sel) => {
    sel.addEventListener("change", () => {
      state[sel.dataset.name] = sel.value;
      load();
    });
  });

  load();
}

function statCard(label, value, sub) {
  return `
    <div class="stat">
      <div class="stat__label">${esc(label)}</div>
      <div class="stat__value">${esc(value)}</div>
      ${sub ? `<div class="stat__sub">${esc(sub)}</div>` : ""}
    </div>`;
}

function bodyHtml(stats) {
  const t = stats.totals || {};
  const cards = [
    statCard("Active members", t.activeMembers ?? 0, `${t.totalMembers ?? 0} total`),
    statCard("Points awarded", t.totalPoints ?? 0, "all-time"),
    statCard("Tasks completed", t.tasksCompleted ?? 0, ""),
    statCard("On-time rate", t.onTimeRate == null ? "—" : `${t.onTimeRate}%`, "of completed"),
    statCard("Avg turnaround", t.avgTurnaroundHours == null ? "—" : `${t.avgTurnaroundHours}h`, "assign → done"),
    statCard("Open / late", t.openLate ?? 0, "overdue now"),
  ].join("");

  return `
    <div class="stat-grid">${cards}</div>

    <h2 class="h2">Points spread (fairness)</h2>
    ${leaderboardHtml(stats.leaderboard || [])}

    <h2 class="h2">By vertical</h2>
    ${verticalHtml(stats.byVertical || [])}

    <h2 class="h2">Requests by status</h2>
    ${statusHtml(stats.requestsByStatus || {})}
  `;
}

function leaderboardHtml(rows) {
  const active = rows.filter((r) => r.active);
  if (!active.length) return `<p class="muted">No active members.</p>`;
  const max = Math.max(1, ...active.map((r) => r.points));
  return `<div class="bars">${active
    .map((r) => {
      const pct = Math.round((r.points / max) * 100);
      const meta = [
        `${r.done} done`,
        r.onTimePct == null ? null : `${r.onTimePct}% on-time`,
        r.strikes ? `${r.strikes} strike${r.strikes > 1 ? "s" : ""}` : null,
      ]
        .filter(Boolean)
        .join(" · ");
      return `
        <div class="bar-row">
          <div class="bar-row__head">
            <span class="bar-row__name">${esc(r.name)}${r.vertical ? ` <span class="muted">· ${esc(r.vertical)}</span>` : ""}</span>
            <span class="bar-row__val">${esc(r.points)} pts</span>
          </div>
          <div class="bar"><div class="bar__fill${r.strikes ? " bar__fill--warn" : ""}" style="width:${pct}%"></div></div>
          <div class="bar-row__meta">${esc(meta)}</div>
        </div>`;
    })
    .join("")}</div>`;
}

function verticalHtml(rows) {
  if (!rows.length) return `<p class="muted">—</p>`;
  return `
    <table class="roster">
      <thead><tr><th>Vertical</th><th>Members</th><th>Points</th><th>Done</th></tr></thead>
      <tbody>
        ${rows
          .map(
            (v) =>
              `<tr><td>${esc(v.vertical)}</td><td>${esc(v.members)}</td><td>${esc(v.points)}</td><td>${esc(v.done)}</td></tr>`
          )
          .join("")}
      </tbody>
    </table>`;
}

function statusHtml(map) {
  const entries = Object.entries(map);
  if (!entries.length) return `<p class="muted">No requests yet.</p>`;
  return `<div class="badges">${entries
    .map(
      ([status, n]) =>
        `<span class="status" data-status="${esc(status)}">${esc(status)} · ${esc(n)}</span>`
    )
    .join("")}</div>`;
}
