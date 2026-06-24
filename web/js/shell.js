/**
 * App shell — responsive top bar with the committee tag, role-gated navigation,
 * and a small hash router that supports sub-routes (#/requests/{id}) and tears
 * down each view's live listeners on navigation.
 *
 * Views are registered in VIEWS; routes without a module yet fall back to a
 * per-phase placeholder.
 */

import { renderNewRequest } from "./views/newRequest.js";
import { renderMyRequests } from "./views/myRequests.js";
import { renderMyTasks } from "./views/myTasks.js";
import { renderAssignments } from "./views/assignments.js";
import { renderApprovals } from "./views/approvals.js";
import { renderAdmin } from "./views/admin.js";
import { renderDashboard } from "./views/dashboard.js";

function esc(s) {
  return String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}

// Navigation registry. Each item is gated by a single role; a user sees the
// union of items for all roles they hold. `phase` is shown on the placeholder.
// Each item is gated by a single `role` OR a `when(session)` predicate (for
// multi-role views). A user sees the union of items they're allowed. `short` is
// the label used in the mobile bottom tab bar.
const NAV = [
  // Any signed-in institute user can raise a Post request (Coverage is reserved
  // to committees, enforced in the form + rules), so these show for everyone.
  { id: "new", label: "New Request", short: "New", when: () => true, phase: 4 },
  { id: "requests", label: "My Requests", short: "Requests", when: () => true, phase: 4 },
  { id: "tasks", label: "My Tasks", short: "Tasks", role: "team", phase: 6 },
  {
    id: "assignments",
    label: "Assignments",
    short: "Assign",
    when: (s) =>
      s.isCoordinator || s.isDomainHead || s.isSecondYear || s.isSecretary || s.isAdmin,
    phase: 8,
  },
  { id: "approvals", label: "Approvals", short: "Approve", role: "secretary", phase: 7 },
  {
    id: "dashboard",
    label: "Dashboard",
    short: "Stats",
    when: (s) => s.isAdmin || s.isSecretary,
    phase: 12,
  },
  {
    id: "admin",
    label: "Admin",
    short: "Admin",
    when: (s) => s.isSecretary || s.isAdmin,
    phase: 9,
  },
];

// Inline icons for the mobile tab bar (stroke styled via CSS).
const ICONS = {
  new: `<svg viewBox="0 0 20 20"><path d="M10 4.5v11M4.5 10h11"/></svg>`,
  requests: `<svg viewBox="0 0 20 20"><path d="M5 5h10M5 8.3h10M5 11.6h7"/></svg>`,
  tasks: `<svg viewBox="0 0 20 20"><rect x="4" y="4" width="12" height="12" rx="2.5"/><path d="M7.2 10.2l1.9 1.9 3.7-4.2"/></svg>`,
  assignments: `<svg viewBox="0 0 20 20"><path d="M4.5 7.5h9l-2.4-2.4M15.5 12.5h-9l2.4 2.4"/></svg>`,
  approvals: `<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="6.4"/><path d="M7.2 10.2l1.9 1.9 3.7-4.2"/></svg>`,
  dashboard: `<svg viewBox="0 0 20 20"><path d="M3.5 16.5h13M6 16.5v-4M10 16.5v-8M14 16.5v-6"/></svg>`,
  admin: `<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="2.6"/><path d="M10 3.6v2.2M10 14.2v2.2M3.6 10h2.2M14.2 10h2.2M5.5 5.5l1.5 1.5M13 13l1.5 1.5M14.5 5.5l-1.5 1.5M7 13l-1.5 1.5"/></svg>`,
};

// route id -> view renderer. A renderer may return an unsubscribe function.
const VIEWS = {
  new: renderNewRequest,
  requests: renderMyRequests,
  tasks: renderMyTasks,
  assignments: renderAssignments,
  approvals: renderApprovals,
  dashboard: renderDashboard,
  admin: renderAdmin,
};

const ROLE_LABELS = {
  committee: "Committee",
  team: "Team",
  coordinator: "Coordinator",
  domainHead: "Domain Head",
  secretary: "Secretary",
  admin: "Admin",
};

function availableNav(session) {
  return NAV.filter((item) =>
    typeof item.when === "function"
      ? item.when(session)
      : session.roles.includes(item.role)
  );
}

function identityTag(session) {
  if (session.committee) {
    const c = session.committee;
    return [c.name, c.acronym, c.campus].filter(Boolean).map(esc).join(" · ");
  }
  const parts = [];
  if (session.team) {
    const bits = ["Media Team"];
    if (session.team.campus) bits.push(esc(session.team.campus));
    if (session.domainHeadOf) bits.push(`Head of ${esc(session.domainHeadOf)}`);
    parts.push(bits.join(" · "));
  }
  if (session.isSecretary) parts.push("Secretary (POC)");
  if (session.isAdmin) parts.push("Admin");
  return parts.length ? parts.join(" · ") : "Account not registered";
}

function roleBadges(session) {
  return session.roles
    .map((r) => `<span class="badge">${esc(ROLE_LABELS[r] || r)}</span>`)
    .join("");
}

// Committee profile pic / logo. `logo` is a filename in /assets/logos/ or a URL.
function committeeLogo(session) {
  const logo = session.committee && session.committee.logo;
  if (!logo) return "";
  const src = /^https?:\/\//.test(logo) ? logo : `/assets/logos/${logo}`;
  return `<img class="committee-logo" src="${esc(src)}" alt="" />`;
}

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [id, ...params] = raw.split("/").filter(Boolean);
  return { id: id || "", params };
}

// Single active hashchange handler + current view cleanup so re-mounting and
// navigation don't leak listeners.
let activeRouteHandler = null;
let currentCleanup = null;

function teardownCurrentView() {
  if (currentCleanup) {
    try {
      currentCleanup();
    } catch {
      /* ignore */
    }
    currentCleanup = null;
  }
}

export function unmountShell() {
  teardownCurrentView();
  if (activeRouteHandler) {
    window.removeEventListener("hashchange", activeRouteHandler);
    activeRouteHandler = null;
  }
}

export function mountShell(root, session, { onSignOut }) {
  root.innerHTML = `
    <div class="appbar">
    <header class="topbar">
      <div class="topbar__left">
        <span class="topbar__brand">Media Portal</span>
        <span class="topbar__tag">${identityTag(session)}</span>
      </div>
      <div class="topbar__right">
        ${committeeLogo(session)}
        <span class="topbar__user" title="${esc(session.user.email)}">${esc(
          session.user.displayName || session.user.email
        )}</span>
        <button class="btn btn--sm" id="signout-btn" type="button">Sign out</button>
      </div>
    </header>
    <nav class="nav" id="main-nav"></nav>
    </div>
    <main class="container" id="content"></main>
  `;

  root.querySelector("#signout-btn").addEventListener("click", () => onSignOut());

  const items = availableNav(session);
  const nav = root.querySelector("#main-nav");
  if (items.length) {
    nav.innerHTML = items
      .map(
        (it) =>
          `<a class="nav__link" data-id="${it.id}" href="#/${it.id}">` +
          `<span class="nav__icon">${ICONS[it.id] || ""}</span>` +
          `<span class="nav__full">${esc(it.label)}</span>` +
          `<span class="nav__short">${esc(it.short || it.label)}</span>` +
          `</a>`
      )
      .join("");
  } else {
    nav.hidden = true;
  }

  const content = root.querySelector("#content");

  function route() {
    const { id, params } = parseHash();
    const current = items.find((i) => i.id === id) || items[0] || null;
    nav.querySelectorAll(".nav__link").forEach((a) =>
      a.classList.toggle(
        "nav__link--active",
        Boolean(current) && a.dataset.id === current.id
      )
    );
    renderView(content, current, session, params);
  }

  unmountShell(); // clear any handler/listener from a previous mount
  activeRouteHandler = route;
  window.addEventListener("hashchange", route);
  route();
}

function renderView(content, item, session, params) {
  teardownCurrentView();

  if (!item) {
    const hasRole = session.roles.length > 0;
    content.innerHTML = hasRole
      ? `
      <section class="card stack center">
        <h1>You're signed in 👋</h1>
        <p class="muted">${esc(session.user.email)}</p>
        <div class="badges center">${roleBadges(session)}</div>
        <p>Your role's view isn't built yet — it arrives in a later phase.</p>
      </section>`
      : `
      <section class="card stack center">
        <h1>You're signed in 👋</h1>
        <p class="muted">${esc(session.user.email)}</p>
        <p>Your account isn't registered to a committee or the media team yet,
           and you're not listed as a secretary.</p>
        <p class="muted">Ask the Media Committee admin to add you, then sign in again.</p>
      </section>`;
    return;
  }

  const view = VIEWS[item.id];
  if (view) {
    const cleanup = view(content, session, params);
    if (typeof cleanup === "function") currentCleanup = cleanup;
    return;
  }

  // Not-yet-built views (tasks / coordinator / approvals).
  content.innerHTML = `
    <section class="card stack">
      <div class="view-head">
        <h1>${esc(item.label)}</h1>
        <div class="badges">${roleBadges(session)}</div>
      </div>
      <p class="muted">This view arrives in <strong>Phase ${item.phase}</strong>.</p>
    </section>`;
}
