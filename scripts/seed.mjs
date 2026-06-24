/**
 * Seed script (idempotent) — Phase 3.
 *
 * Seeds `config` (taskTypes / slots / platforms / settings) plus a few sample
 * `committees` and `team` rows so the app and engine are testable immediately.
 * Uses placeholder @iimsirmaur.ac.in emails — replace them with real values.
 *
 * Identity collections are keyed BY EMAIL (committees/{email}, team/{email}) so
 * the security rules can do O(1) membership checks and the app can read a single
 * doc by id.
 *
 * Behaviour:
 *   - Targets the Firestore EMULATOR by default (sets FIRESTORE_EMULATOR_HOST if
 *     unset). Pass `--prod` to target a real project (needs ADC /
 *     GOOGLE_APPLICATION_CREDENTIALS + a real project id).
 *   - Idempotent: existing docs are LEFT ALONE (so engine-managed fields like
 *     committees.lastSeq and team.points/strikes survive re-runs).
 *   - Pass `--force` to OVERWRITE seeded docs back to these canonical values
 *     (this resets lastSeq / points / strikes — use only in dev).
 *
 * Run:  npm run seed        (emulator must be running)
 *       npm run seed -- --force
 */

import { initializeApp } from "firebase-admin/app";
import { getFirestore } from "firebase-admin/firestore";

const FORCE = process.argv.includes("--force");
const PROD = process.argv.includes("--prod");

if (!PROD && !process.env.FIRESTORE_EMULATOR_HOST) {
  process.env.FIRESTORE_EMULATOR_HOST = "127.0.0.1:8080";
}

const projectId =
  process.env.GCLOUD_PROJECT ||
  process.env.FIREBASE_PROJECT_ID ||
  "demo-mcc-portal";

initializeApp(PROD ? {} : { projectId });
const db = getFirestore();

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------

const taskTypes = {
  // Pipeline definition. The engine reads this in one go.
  //   requestable:        shown on the New Request form (committees can ask for it)
  //   internalAssignable: staff (admin / team) can add it manually — internal only,
  //                       never shown to requesters
  //   vertical:           team vertical for domain-head scope; "" = unscoped
  types: [
    { task: "Photographer",     requiredSkill: "Photography",     points: 5, slaHours: 0,  atEvent: true,  requestable: true,  internalAssignable: true,  vertical: "Photography" },
    { task: "Videographer",     requiredSkill: "Videography",     points: 8, slaHours: 0,  atEvent: true,  requestable: true,  internalAssignable: true,  vertical: "Videography" },
    { task: "Content Writer",   requiredSkill: "Content Writing", points: 3, slaHours: 12, atEvent: true,  requestable: false, internalAssignable: true,  vertical: "Content Writing" },
    { task: "Photo Editor",     requiredSkill: "Photo Editing",   points: 3, slaHours: 24, atEvent: false, requestable: false, internalAssignable: true,  vertical: "Photography" },
    { task: "Video Editor",     requiredSkill: "Video Editing",   points: 5, slaHours: 48, atEvent: false, requestable: false, internalAssignable: true,  vertical: "Videography" },
    { task: "Vetter",           requiredSkill: "Vetting",         points: 2, slaHours: 24, atEvent: false, requestable: false, internalAssignable: true,  vertical: "" },
    { task: "Event Coordinator",requiredSkill: "Coordination",    points: 4, slaHours: 0,  atEvent: true,  requestable: false, internalAssignable: true,  vertical: "" },
  ],
};

const slots = { slots: ["11:00", "14:00", "17:00"] };

const platforms = {
  // Two rows for one platform => the engine load-balances between handlers.
  platforms: [
    { platform: "Instagram", handlerEmail: "ig.handler@iimsirmaur.ac.in", points: 2, active: true },
    { platform: "LinkedIn",  handlerEmail: "li.handler@iimsirmaur.ac.in", points: 2, active: true },
    { platform: "X",         handlerEmail: "x.handler@iimsirmaur.ac.in",  points: 2, active: true },
  ],
};

const settings = {
  slaHours: 48,
  strikeLimit: 3,
  campusStrict: true,
  requireApprovalAlways: false,
  strikeAssigneeToo: false,
  secretaryEmails: ["poc@iimsirmaur.ac.in"],
  adminEmails: ["admin@iimsirmaur.ac.in"],
  headEmail: "media.head@iimsirmaur.ac.in",
  committeeName: "Media Committee",
  allowedDomains: ["iimsirmaur.ac.in"],
  // Prefix + counter for Post requests from non-committee institute users.
  defaultAcronym: "MEDIA",
  generalSeq: 0,
};

// config/points — the scoring scheme (admin-editable). Base points by role plus
// completion-timing modifiers (turnaround measured from event end for coverage,
// from request creation for posts/no-event).
const points = {
  coordinatorPoints: 20, // Event Coordinator, per event
  domainTaskPoints: 10, // each domain/deliverable task (photographer, editor, writer…)
  vetterPoints: 10, // Vetter
  earlyWindowHours: 24, // done within this → bonus
  earlyBonusPct: 30, // +30%
  lateThresholdHours: 48, // beyond this → penalty
  latePenaltyPct: 30, // -30% at the threshold
  subsequentDelayHours: 6, // each further block of this many hours …
  subsequentPenaltyPct: 10, // … subtracts another 10%
};

// committees keyed by login email. `logo` = filename in web/assets/logos/ (or a
// URL); blank by default — drop an image in that folder and set this to show it.
const committees = [
  { email: "marketing@iimsirmaur.ac.in",     name: "Marketing Club",         type: "Club",       campus: "Permanent", acronym: "MKTG", lastSeq: 0, logo: "" },
  { email: "consult@iimsirmaur.ac.in",       name: "Consulting Club",        type: "Club",       campus: "Permanent", acronym: "CONS", lastSeq: 0, logo: "" },
  { email: "cultural@iimsirmaur.ac.in",      name: "Cultural Committee",     type: "Committee",  campus: "Permanent", acronym: "CULT", lastSeq: 0, logo: "" },
  { email: "mdp@iimsirmaur.ac.in",           name: "MDP Office",             type: "MDP Office", campus: "Permanent", acronym: "MDP",  lastSeq: 0, logo: "" },
  { email: "bms.cultural@iimsirmaur.ac.in",  name: "BMS Cultural Committee", type: "Committee",  campus: "BMS",       acronym: "BCUL", lastSeq: 0, logo: "" },
];

// team keyed by member email; defaults (strikes/points/active) applied below.
// vertical: one of Photography | Videography | Graphic Designs | Content Writing
// year: 1 or 2 (2nd-years may manually assign); domainHeadOf: vertical or "".
const team = [
  { name: "Asha Rao",       email: "asha@iimsirmaur.ac.in",   campus: "Permanent", phone: "+91-90000-00001", vertical: "Photography",      year: 2, domainHeadOf: "Photography",      skills: ["Photography", "Photo Editing", "Coordination"] },
  { name: "Vikram Singh",   email: "vikram@iimsirmaur.ac.in", campus: "Permanent", phone: "+91-90000-00002", vertical: "Videography",      year: 2, domainHeadOf: "Videography",      skills: ["Videography", "Video Editing"] },
  { name: "Neha Gupta",     email: "neha@iimsirmaur.ac.in",   campus: "Permanent", phone: "+91-90000-00003", vertical: "Content Writing",  year: 2, domainHeadOf: "Content Writing",  skills: ["Content Writing", "Vetting", "Coordination"] },
  { name: "Rahul Mehta",    email: "rahul@iimsirmaur.ac.in",  campus: "Permanent", phone: "+91-90000-00004", vertical: "Photography",      year: 1, domainHeadOf: "",                 skills: ["Photography", "Videography", "Coordination"] },
  { name: "Priya Nair",     email: "priya@iimsirmaur.ac.in",  campus: "Permanent", phone: "+91-90000-00005", vertical: "Graphic Designs",  year: 2, domainHeadOf: "Graphic Designs",  skills: ["Graphic design", "Photo Editing", "Vetting"] },
  { name: "Karan Malhotra", email: "karan@iimsirmaur.ac.in",  campus: "BMS",       phone: "+91-90000-00006", vertical: "Videography",      year: 2, domainHeadOf: "",                 skills: ["Photography", "Videography", "Photo Editing", "Video Editing", "Content Writing", "Vetting", "Coordination"] },
  { name: "Sara Khan",      email: "sara@iimsirmaur.ac.in",   campus: "BMS",       phone: "+91-90000-00007", vertical: "Content Writing",  year: 1, domainHeadOf: "",                 skills: ["Content Writing", "Coordination"] },
];

// ---------------------------------------------------------------------------
// Upsert helper: create-if-absent (idempotent); overwrite only with --force.
// ---------------------------------------------------------------------------

let written = 0;
let skipped = 0;

async function put(ref, data, label) {
  const snap = await ref.get();
  if (snap.exists && !FORCE) {
    skipped++;
    console.log(`  · skip   ${label} (exists)`);
    return;
  }
  await ref.set(data);
  written++;
  console.log(`  ✓ ${snap.exists ? "force " : "write "} ${label}`);
}

async function main() {
  console.log("──────────────────────────────────────────────");
  console.log(` MCC Portal seed${FORCE ? " (--force)" : ""}`);
  console.log(` Target: ${PROD ? `PROJECT ${projectId}` : `EMULATOR ${process.env.FIRESTORE_EMULATOR_HOST} (${projectId})`}`);
  console.log("──────────────────────────────────────────────");

  console.log("config:");
  await put(db.doc("config/taskTypes"), taskTypes, "config/taskTypes");
  await put(db.doc("config/slots"), slots, "config/slots");
  await put(db.doc("config/platforms"), platforms, "config/platforms");
  await put(db.doc("config/settings"), settings, "config/settings");
  await put(db.doc("config/points"), points, "config/points");

  console.log("committees:");
  for (const c of committees) {
    await put(db.doc(`committees/${c.email}`), c, `committees/${c.email}`);
  }

  console.log("team:");
  for (const t of team) {
    const doc = { strikes: 0, points: 0, active: true, ...t };
    await put(db.doc(`team/${t.email}`), doc, `team/${t.email}`);
  }

  console.log("──────────────────────────────────────────────");
  console.log(` Done. ${written} written, ${skipped} skipped.`);
  if (skipped && !FORCE) {
    console.log(" (Re-run with --force to overwrite existing docs.)");
  }
  console.log("──────────────────────────────────────────────");
}

main().catch((err) => {
  console.error("\nSeed failed:", err.message || err);
  if (!PROD) {
    console.error(
      "Is the Firestore emulator running? Start it with:  npm run emulators"
    );
  }
  process.exit(1);
});
