/**
 * importTeamCsv — bulk add/update team members from parsed CSV rows.
 *
 * Gated to secretaries and admins. Upserts team/{email} (keyed by email) with
 * merge, so re-importing updates fields without wiping engine-managed points /
 * strikes; new members get those defaulted. Returns a per-row result.
 *
 * Expected row keys: name, email, vertical, year, domainHeadOf, skills
 * (";"-separated), campus, phone, active.
 */

import { onCall, HttpsError, CallableRequest } from "firebase-functions/v2/https";
import { db } from "../lib/setup";
import { resolveCallerRoles } from "../engine/serverRoles";
import { appendActivity } from "../lib/log";

const VERTICALS = ["Photography", "Videography", "Graphic Designs", "Content Writing"];

/** Require the caller to be a secretary or admin; returns their lowercased email. */
async function requireSecretaryOrAdmin(request: CallableRequest): Promise<string> {
  const caller = request.auth?.token?.email;
  if (!caller) throw new HttpsError("unauthenticated", "Sign in required.");
  const email = String(caller).toLowerCase();
  const roles = await resolveCallerRoles(email);
  if (!roles.isSecretary && !roles.isAdmin) {
    throw new HttpsError("permission-denied", "Secretaries / admins only.");
  }
  return email;
}

interface RowResult {
  email: string;
  status: "created" | "updated" | "error";
  message?: string;
}

function parseBool(v: unknown, dflt: boolean): boolean {
  if (v === undefined || v === null || v === "") return dflt;
  const s = String(v).trim().toLowerCase();
  return s === "true" || s === "1" || s === "yes" || s === "y";
}

export const importTeamCsv = onCall(async (request) => {
  await requireSecretaryOrAdmin(request);

  const rows = request.data?.rows;
  if (!Array.isArray(rows)) {
    throw new HttpsError("invalid-argument", "rows[] required.");
  }

  const results: RowResult[] = [];
  for (const raw of rows) {
    const r = raw || {};
    const email = String(r.email || "").trim().toLowerCase();
    if (!email || !email.includes("@")) {
      results.push({ email: String(r.email || ""), status: "error", message: "invalid email" });
      continue;
    }
    const name = String(r.name || "").trim();
    if (!name) {
      results.push({ email, status: "error", message: "name required" });
      continue;
    }

    const data = {
      name,
      email,
      vertical: String(r.vertical || "").trim(),
      year: Number(r.year) || 1,
      domainHeadOf: String(r.domainHeadOf || "").trim(),
      skills: String(r.skills || "")
        .split(/[;,]/)
        .map((s: string) => s.trim())
        .filter(Boolean),
      campus: String(r.campus || "").trim(),
      phone: String(r.phone || "").trim(),
      active: parseBool(r.active, true),
    };

    try {
      const ref = db.doc(`team/${email}`);
      const snap = await ref.get();
      const payload = snap.exists ? data : { ...data, points: 0, strikes: 0 };
      await ref.set(payload, { merge: true });
      results.push({ email, status: snap.exists ? "updated" : "created" });
    } catch (err) {
      results.push({ email, status: "error", message: (err as Error).message });
    }
  }

  const created = results.filter((r) => r.status === "created").length;
  const updated = results.filter((r) => r.status === "updated").length;
  const errors = results.filter((r) => r.status === "error").length;
  return { results, summary: { created, updated, errors } };
});

/** List team members (secretary/admin) — for the heads picker / admin tooling. */
export const listTeamMembers = onCall(async (request) => {
  await requireSecretaryOrAdmin(request);
  const snap = await db.collection("team").get();
  const members = snap.docs
    .map((d) => {
      const m = d.data();
      return {
        email: d.id,
        name: m.name || d.id,
        vertical: m.vertical || "",
        domainHeadOf: m.domainHeadOf || "",
        campus: m.campus || "",
        year: m.year || null,
        active: m.active !== false,
      };
    })
    .sort((a, b) => String(a.name).localeCompare(String(b.name)));
  return { members, verticals: VERTICALS };
});

/**
 * setDomainHead — make a team member the head of a vertical (or remove them).
 * Secretary/admin only. Enforces one head per vertical (promotes X, demotes any
 * existing head of that vertical) and moves X into the vertical they now lead.
 */
export const setDomainHead = onCall(async (request) => {
  const caller = await requireSecretaryOrAdmin(request);
  const email = String(request.data?.email || "").trim().toLowerCase();
  const vertical = String(request.data?.vertical || "").trim();
  if (!email) throw new HttpsError("invalid-argument", "email required.");

  const ref = db.doc(`team/${email}`);
  const snap = await ref.get();
  if (!snap.exists) throw new HttpsError("not-found", "Not a team member.");
  if (vertical && !VERTICALS.includes(vertical)) {
    throw new HttpsError("failed-precondition", `Unknown vertical: ${vertical}`);
  }

  const batch = db.batch();
  if (vertical) {
    // One head per vertical: demote whoever currently heads it.
    const current = await db
      .collection("team")
      .where("domainHeadOf", "==", vertical)
      .get();
    for (const d of current.docs) {
      if (d.id !== email) batch.update(d.ref, { domainHeadOf: "" });
    }
    batch.update(ref, { domainHeadOf: vertical, vertical });
  } else {
    batch.update(ref, { domainHeadOf: "" });
  }
  await batch.commit();

  await appendActivity({
    event: "domain-head",
    actor: caller,
    member: email,
    detail: vertical ? `Made head of ${vertical}` : "Removed as head",
  });
  return { ok: true };
});

const POINT_KEYS = [
  "coordinatorPoints",
  "domainTaskPoints",
  "vetterPoints",
  "earlyWindowHours",
  "earlyBonusPct",
  "lateThresholdHours",
  "latePenaltyPct",
  "subsequentDelayHours",
  "subsequentPenaltyPct",
];

/** setPointScheme — admins edit the scoring scheme (config/points). */
export const setPointScheme = onCall(async (request) => {
  const caller = request.auth?.token?.email;
  if (!caller) throw new HttpsError("unauthenticated", "Sign in required.");
  const roles = await resolveCallerRoles(String(caller).toLowerCase());
  if (!roles.isAdmin) {
    throw new HttpsError("permission-denied", "Only admins can change the point scheme.");
  }

  const incoming = request.data?.points || {};
  const out: Record<string, number> = {};
  for (const k of POINT_KEYS) {
    const v = Number(incoming[k]);
    if (!Number.isFinite(v) || v < 0) {
      throw new HttpsError("invalid-argument", `Invalid value for ${k}.`);
    }
    out[k] = v;
  }

  await db.doc("config/points").set(out, { merge: true });
  await appendActivity({
    event: "points-scheme",
    actor: String(caller).toLowerCase(),
    detail: "Point scheme updated",
  });
  return { ok: true };
});
