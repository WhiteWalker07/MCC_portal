/**
 * Admin view (Phase 9) — secretaries / admins only.
 *
 * Bulk add/update team members from CSV. The CSV is parsed in the browser and
 * sent to the importTeamCsv callable, which validates + upserts server-side (the
 * security rules block direct team writes). Results are shown per row.
 */

import {
  importTeamCsv,
  parseCsv,
  listTeamMembers,
  setDomainHead,
  setAvailability,
  getPointScheme,
  setPointScheme,
  listCommittees,
  addCommittee,
} from "../data.js";

const COMMITTEE_TYPES = ["Club", "Committee", "SIG", "Office"];
const COMMITTEE_CAMPUSES = ["MBA Campus", "BMS Campus"];

const SCHEME_FIELDS = [
  ["coordinatorPoints", "Event Coordinator (pts)"],
  ["domainTaskPoints", "Domain task (pts)"],
  ["vetterPoints", "Vetter (pts)"],
  ["earlyWindowHours", "Early window (h)"],
  ["earlyBonusPct", "Early bonus (%)"],
  ["lateThresholdHours", "Late threshold (h)"],
  ["latePenaltyPct", "Late penalty (%)"],
  ["subsequentDelayHours", "Subsequent block (h)"],
  ["subsequentPenaltyPct", "Subsequent penalty (%)"],
];

const TEMPLATE =
  "name,email,vertical,year,domainHeadOf,skills,campus,phone,active\n" +
  "Asha Rao,asha@iimsirmaur.ac.in,Photography,2,Photography,Photography;Photo Editing;Coordination,Permanent,+91-90000-00001,true\n" +
  "Rahul Mehta,rahul@iimsirmaur.ac.in,Photography,1,,Photography;Videography;Coordination,Permanent,+91-90000-00004,true";

function esc(s) {
  return String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}

export function renderAdmin(container, session) {
  if (!session.isSecretary && !session.isAdmin) {
    container.innerHTML = `<section class="card stack"><h1>Admin</h1>
      <p class="muted">Restricted to secretaries and admins.</p></section>`;
    return;
  }

  container.innerHTML = `
    <section class="card stack">
      <h1>Committees</h1>
      <p class="muted">Add a club / committee / office. It signs in with the login email
        and can raise Coverage requests; the acronym is the request-ID prefix
        (e.g. <code>MKTG_1</code>). Re-adding the same email updates it (the request
        counter is preserved).</p>
      <p id="comm-error" class="error" hidden></p>
      <div class="grid2">
        <label class="field"><span class="field__label">Name</span>
          <input class="input" id="comm-name" autocomplete="off" /></label>
        <label class="field"><span class="field__label">Login email</span>
          <input class="input" id="comm-email" type="email" autocomplete="off" placeholder="club@iimsirmaur.ac.in" /></label>
        <label class="field"><span class="field__label">Acronym</span>
          <input class="input" id="comm-acronym" autocomplete="off" placeholder="MKTG" /></label>
        <label class="field"><span class="field__label">Type</span>
          <select class="input" id="comm-type">${COMMITTEE_TYPES.map((t) => `<option>${t}</option>`).join("")}</select></label>
        <label class="field"><span class="field__label">Campus</span>
          <select class="input" id="comm-campus">${COMMITTEE_CAMPUSES.map((c) => `<option>${c}</option>`).join("")}</select></label>
      </div>
      <div class="row">
        <button type="button" class="btn btn--primary" id="comm-add">Add committee</button>
        <span id="comm-ok" class="muted" hidden>Saved ✓</span>
      </div>
      <div id="comm-list"><p class="muted">Loading…</p></div>
    </section>

    <section class="card stack">
      <h1>Vertical heads</h1>
      <p class="muted">Make a team member the head of a vertical. Heads can manually
        assign tasks within their vertical. One head per vertical — assigning a new
        one replaces the previous.</p>
      <p id="heads-error" class="error" hidden></p>
      <div id="heads-body"><p class="muted">Loading…</p></div>
    </section>

    <section class="card stack">
      <h1>Availability</h1>
      <p class="muted">Mark a member <strong>Out of work</strong> (on break) so the engine
        stops assigning them tasks, or bring them back <strong>On work</strong>. Days on /
        out of work are tallied from each change.</p>
      <p id="avail-error" class="error" hidden></p>
      <div id="avail-body"><p class="muted">Loading…</p></div>
    </section>

    <section class="card stack">
      <h1>Team admin — CSV import</h1>
      <p class="muted">Columns: <code>name, email, vertical, year, domainHeadOf, skills, campus, phone, active</code>.
        Use <code>;</code> between multiple skills. Re-importing updates existing members (points/strikes preserved).</p>

      <label class="field">
        <span class="field__label">Paste CSV</span>
        <textarea class="input" id="csv-text" rows="8" placeholder="${esc(TEMPLATE)}"></textarea>
      </label>
      <div class="row">
        <input type="file" id="csv-file" accept=".csv,text/csv" />
        <button type="button" class="btn btn--sm" id="csv-template">Load template</button>
        <button type="button" class="btn btn--primary" id="csv-import">Import</button>
      </div>
      <p id="admin-error" class="error" hidden></p>
      <div id="csv-results"></div>
    </section>

    <section class="card stack">
      <h1>Point scheme</h1>
      <p class="muted">Base points per role + completion-timing modifiers (turnaround
        from event end for coverage, from request creation for posts).${session.isAdmin ? "" : " Read-only — admins can edit."}</p>
      <p id="scheme-error" class="error" hidden></p>
      <div id="scheme-grid" class="scheme-grid"><p class="muted">Loading…</p></div>
      ${session.isAdmin ? `<div class="row"><button type="button" class="btn btn--primary" id="scheme-save">Save scheme</button><span id="scheme-ok" class="muted" hidden>Saved ✓</span></div>` : ""}
    </section>`;

  // ---- Committees ---------------------------------------------------------
  const commErr = container.querySelector("#comm-error");
  const commOk = container.querySelector("#comm-ok");
  const commList = container.querySelector("#comm-list");

  async function loadCommittees() {
    try {
      const { committees } = await listCommittees();
      commList.innerHTML = committeesHtml(committees);
    } catch (err) {
      commList.innerHTML = `<p class="error">${esc(err.message || err)}</p>`;
    }
  }

  container.querySelector("#comm-add").addEventListener("click", async () => {
    commErr.hidden = true;
    commOk.hidden = true;
    const nameEl = container.querySelector("#comm-name");
    const emailEl = container.querySelector("#comm-email");
    const acrEl = container.querySelector("#comm-acronym");
    const payload = {
      name: nameEl.value.trim(),
      email: emailEl.value.trim(),
      acronym: acrEl.value.trim(),
      type: container.querySelector("#comm-type").value,
      campus: container.querySelector("#comm-campus").value,
    };
    if (!payload.name || !payload.email || !payload.acronym) {
      commErr.textContent = "Name, login email, and acronym are required.";
      commErr.hidden = false;
      return;
    }
    const btn = container.querySelector("#comm-add");
    btn.disabled = true;
    btn.textContent = "Saving…";
    try {
      await addCommittee(payload);
      commOk.hidden = false;
      nameEl.value = "";
      emailEl.value = "";
      acrEl.value = "";
      await loadCommittees();
    } catch (err) {
      commErr.textContent = err.message || String(err);
      commErr.hidden = false;
    } finally {
      btn.disabled = false;
      btn.textContent = "Add committee";
    }
  });

  loadCommittees();

  const textEl = container.querySelector("#csv-text");
  const fileEl = container.querySelector("#csv-file");
  const errEl = container.querySelector("#admin-error");
  const resultsEl = container.querySelector("#csv-results");

  container.querySelector("#csv-template").addEventListener("click", () => {
    textEl.value = TEMPLATE;
  });

  fileEl.addEventListener("change", async () => {
    const f = fileEl.files && fileEl.files[0];
    if (f) textEl.value = await f.text();
  });

  container.querySelector("#csv-import").addEventListener("click", async () => {
    errEl.hidden = true;
    resultsEl.innerHTML = "";
    const rows = parseCsv(textEl.value);
    if (!rows.length) {
      errEl.textContent = "Nothing to import — paste CSV with a header row first.";
      errEl.hidden = false;
      return;
    }
    const btn = container.querySelector("#csv-import");
    btn.disabled = true;
    btn.textContent = "Importing…";
    try {
      const { results, summary } = await importTeamCsv(rows);
      resultsEl.innerHTML = resultsHtml(results, summary);
    } catch (err) {
      errEl.textContent = err.message || String(err);
      errEl.hidden = false;
    } finally {
      btn.disabled = false;
      btn.textContent = "Import";
    }
  });

  // ---- Vertical heads -----------------------------------------------------
  const headsBody = container.querySelector("#heads-body");
  const headsErr = container.querySelector("#heads-error");

  async function loadHeads() {
    headsErr.hidden = true;
    try {
      const { members, verticals } = await listTeamMembers();
      headsBody.innerHTML = headsHtml(members, verticals);
      wireHeads();
    } catch (err) {
      headsBody.innerHTML = "";
      headsErr.textContent = err.message || String(err);
      headsErr.hidden = false;
    }
  }

  function wireHeads() {
    headsBody.querySelectorAll(".head-set").forEach((b) => {
      b.addEventListener("click", async () => {
        const sel = headsBody.querySelector(`.head-sel[data-v="${cssEsc(b.dataset.v)}"]`);
        const email = sel && sel.value;
        if (!email) {
          headsErr.textContent = "Pick a member to set as head.";
          headsErr.hidden = false;
          return;
        }
        await applyHead(b, email, b.dataset.v);
      });
    });
    headsBody.querySelectorAll(".head-remove").forEach((b) => {
      b.addEventListener("click", () => applyHead(b, b.dataset.email, ""));
    });
  }

  async function applyHead(btn, email, vertical) {
    headsErr.hidden = true;
    btn.disabled = true;
    try {
      await setDomainHead(email, vertical);
      await loadHeads();
    } catch (err) {
      btn.disabled = false;
      headsErr.textContent = err.message || String(err);
      headsErr.hidden = false;
    }
  }

  loadHeads();

  // ---- Availability -------------------------------------------------------
  const availBody = container.querySelector("#avail-body");
  const availErr = container.querySelector("#avail-error");

  async function loadAvailability() {
    availErr.hidden = true;
    try {
      const { members } = await listTeamMembers();
      availBody.innerHTML = availabilityHtml(members);
      wireAvailability();
    } catch (err) {
      availBody.innerHTML = "";
      availErr.textContent = err.message || String(err);
      availErr.hidden = false;
    }
  }

  function wireAvailability() {
    availBody.querySelectorAll(".avail-toggle").forEach((b) => {
      b.addEventListener("click", async () => {
        availErr.hidden = true;
        b.disabled = true;
        try {
          await setAvailability(b.dataset.email, b.dataset.next);
          await loadAvailability();
        } catch (err) {
          b.disabled = false;
          availErr.textContent = err.message || String(err);
          availErr.hidden = false;
        }
      });
    });
  }

  loadAvailability();

  // ---- Point scheme -------------------------------------------------------
  const schemeGrid = container.querySelector("#scheme-grid");
  const schemeErr = container.querySelector("#scheme-error");
  const schemeSave = container.querySelector("#scheme-save");
  const schemeOk = container.querySelector("#scheme-ok");

  getPointScheme()
    .then((vals) => {
      schemeGrid.innerHTML = SCHEME_FIELDS.map(
        ([k, label]) =>
          `<label class="field"><span class="field__label">${esc(label)}</span><input class="input scheme-inp" data-k="${k}" type="number" min="0" value="${esc(vals[k] ?? "")}" ${session.isAdmin ? "" : "disabled"} /></label>`
      ).join("");
    })
    .catch((err) => {
      schemeGrid.innerHTML = "";
      schemeErr.textContent = err.message || String(err);
      schemeErr.hidden = false;
    });

  if (schemeSave) {
    schemeSave.addEventListener("click", async () => {
      schemeErr.hidden = true;
      schemeOk.hidden = true;
      const points = {};
      schemeGrid.querySelectorAll(".scheme-inp").forEach((inp) => {
        points[inp.dataset.k] = Number(inp.value);
      });
      schemeSave.disabled = true;
      schemeSave.textContent = "Saving…";
      try {
        await setPointScheme(points);
        schemeOk.hidden = false;
      } catch (err) {
        schemeErr.textContent = err.message || String(err);
        schemeErr.hidden = false;
      } finally {
        schemeSave.disabled = false;
        schemeSave.textContent = "Save scheme";
      }
    });
  }
}

function committeesHtml(committees) {
  if (!committees.length) return `<p class="muted">No committees yet.</p>`;
  return `
    <p class="muted">${committees.length} committee(s)</p>
    <table class="roster">
      <thead><tr><th>Name</th><th>Acronym</th><th>Type</th><th>Campus</th><th>Login email</th></tr></thead>
      <tbody>
        ${committees
          .map(
            (c) =>
              `<tr><td>${esc(c.name)}</td><td>${esc(c.acronym)}</td><td>${esc(c.type)}</td><td>${esc(c.campus)}</td><td>${esc(c.email)}</td></tr>`
          )
          .join("")}
      </tbody>
    </table>`;
}

function headsHtml(members, verticals) {
  return `<div class="heads">${verticals
    .map((v) => {
      const head = members.find((m) => m.domainHeadOf === v);
      const opts = members
        .map(
          (m) =>
            `<option value="${esc(m.email)}"${head && head.email === m.email ? " selected" : ""}>${esc(m.name)}${m.vertical ? ` · ${esc(m.vertical)}` : ""}</option>`
        )
        .join("");
      return `
        <div class="head-row">
          <div class="head-row__v">${esc(v)}</div>
          <div class="head-row__cur">${head ? esc(head.name) : `<span class="muted">No head</span>`}</div>
          <select class="input head-sel" data-v="${esc(v)}" aria-label="Head of ${esc(v)}">
            <option value="">— choose member —</option>${opts}
          </select>
          <button type="button" class="btn btn--sm btn--primary head-set" data-v="${esc(v)}">Set</button>
          ${head ? `<button type="button" class="btn btn--sm head-remove" data-v="${esc(v)}" data-email="${esc(head.email)}">Remove</button>` : ""}
        </div>`;
    })
    .join("")}</div>`;
}

function cssEsc(s) {
  return String(s).replace(/["\\]/g, "\\$&");
}

function availabilityHtml(members) {
  if (!members.length) return `<p class="muted">No team members yet.</p>`;
  const rows = members
    .map((m) => {
      const out = m.availability === "out";
      const next = out ? "available" : "out";
      const statusChip = `<span class="status" data-status="${out ? "LATE" : "CONFIRMED"}">${out ? "Out of work" : "On work"}</span>`;
      return `
        <tr>
          <td>${esc(m.name)}${m.vertical ? `<br><span class="muted">${esc(m.vertical)}</span>` : ""}</td>
          <td>${statusChip}</td>
          <td>${esc(m.onWorkDays ?? 0)} d</td>
          <td>${esc(m.outDays ?? 0)} d</td>
          <td><button type="button" class="btn btn--sm${out ? " btn--primary" : ""} avail-toggle"
                data-email="${esc(m.email)}" data-next="${next}">
                ${out ? "Mark on work" : "Mark out of work"}</button></td>
        </tr>`;
    })
    .join("");
  return `
    <table class="roster">
      <thead><tr><th>Member</th><th>Status</th><th>On work</th><th>Out</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function resultsHtml(results, summary) {
  return `
    <p class="muted">${summary.created} created · ${summary.updated} updated · ${summary.errors} error(s)</p>
    <table class="roster">
      <thead><tr><th>Email</th><th>Result</th></tr></thead>
      <tbody>
        ${results
          .map(
            (r) =>
              `<tr><td>${esc(r.email)}</td><td>${esc(r.status)}${r.message ? " — " + esc(r.message) : ""}</td></tr>`
          )
          .join("")}
      </tbody>
    </table>`;
}
