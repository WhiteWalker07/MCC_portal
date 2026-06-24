/**
 * Seed script (idempotent) — MongoDB version of scripts/seed.mjs.
 *
 * Seeds `config` (taskTypes / slots / platforms / settings / points) plus sample
 * `committees` and `team` rows. Identity collections are keyed BY EMAIL
 * (committees._id / team._id) so the server can read a single doc by id and the
 * O(1) membership checks the old rules relied on still hold.
 *
 * Behaviour:
 *   - Connects to MONGODB_URI (loaded from server/.env via dotenv, or the env).
 *   - Idempotent: existing docs are LEFT ALONE (so engine-managed fields like
 *     committees.lastSeq and team.points/strikes survive re-runs).
 *   - Pass `--force` to OVERWRITE seeded docs back to these canonical values
 *     (resets lastSeq / points / strikes — dev only).
 *
 * Run (from server/):  npm run seed        |  npm run seed:force
 */

import "dotenv/config";
import { MongoClient } from "mongodb";

const FORCE = process.argv.includes("--force");
const uri = process.env.MONGODB_URI;
const dbName = process.env.MONGODB_DB || "mcc_portal";

if (!uri) {
  console.error("MONGODB_URI is not set (put it in server/.env or the environment).");
  process.exit(1);
}

// ---------------------------------------------------------------------------
// Data (identical to scripts/seed.mjs)
// ---------------------------------------------------------------------------

const taskTypes = {
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
  defaultAcronym: "MEDIA",
  generalSeq: 0,
};

const points = {
  coordinatorPoints: 20,
  domainTaskPoints: 10,
  vetterPoints: 10,
  earlyWindowHours: 24,
  earlyBonusPct: 30,
  lateThresholdHours: 48,
  latePenaltyPct: 30,
  subsequentDelayHours: 6,
  subsequentPenaltyPct: 10,
};

const committees = [
  { email: "marketing@iimsirmaur.ac.in",     name: "Marketing Club",         type: "Club",       campus: "Permanent", acronym: "MKTG", lastSeq: 0, logo: "" },
  { email: "consult@iimsirmaur.ac.in",       name: "Consulting Club",        type: "Club",       campus: "Permanent", acronym: "CONS", lastSeq: 0, logo: "" },
  { email: "cultural@iimsirmaur.ac.in",      name: "Cultural Committee",     type: "Committee",  campus: "Permanent", acronym: "CULT", lastSeq: 0, logo: "" },
  { email: "mdp@iimsirmaur.ac.in",           name: "MDP Office",             type: "MDP Office", campus: "Permanent", acronym: "MDP",  lastSeq: 0, logo: "" },
  { email: "bms.cultural@iimsirmaur.ac.in",  name: "BMS Cultural Committee", type: "Committee",  campus: "BMS",       acronym: "BCUL", lastSeq: 0, logo: "" },
];

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

let written = 0;
let skipped = 0;

async function put(db, coll, id, data, label) {
  const existing = await db.collection(coll).findOne({ _id: id });
  if (existing && !FORCE) {
    skipped++;
    console.log(`  · skip   ${label} (exists)`);
    return;
  }
  await db.collection(coll).replaceOne({ _id: id }, { _id: id, ...data }, { upsert: true });
  written++;
  console.log(`  ✓ ${existing ? "force " : "write "} ${label}`);
}

async function main() {
  const client = new MongoClient(uri);
  await client.connect();
  const db = client.db(dbName);

  console.log("──────────────────────────────────────────────");
  console.log(` MCC Portal Mongo seed${FORCE ? " (--force)" : ""}`);
  console.log(` Target: ${dbName}`);
  console.log("──────────────────────────────────────────────");

  console.log("config:");
  await put(db, "config", "taskTypes", taskTypes, "config/taskTypes");
  await put(db, "config", "slots", slots, "config/slots");
  await put(db, "config", "platforms", platforms, "config/platforms");
  await put(db, "config", "settings", settings, "config/settings");
  await put(db, "config", "points", points, "config/points");

  console.log("committees:");
  for (const c of committees) {
    await put(db, "committees", c.email, c, `committees/${c.email}`);
  }

  console.log("team:");
  for (const t of team) {
    const doc = { strikes: 0, points: 0, active: true, ...t };
    await put(db, "team", t.email, doc, `team/${t.email}`);
  }

  console.log("──────────────────────────────────────────────");
  console.log(` Done. ${written} written, ${skipped} skipped.`);
  if (skipped && !FORCE) console.log(" (Re-run with --force to overwrite existing docs.)");
  console.log("──────────────────────────────────────────────");

  await client.close();
}

main().catch((err) => {
  console.error("\nSeed failed:", err.message || err);
  process.exit(1);
});
