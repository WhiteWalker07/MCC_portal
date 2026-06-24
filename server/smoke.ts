/**
 * Integration smoke test for the ported engine — runs against an in-memory
 * MongoDB (no external services). Exercises the hardest part of the migration:
 * refCode allocation, pipeline build, assignment, confirm, task completion,
 * post scheduling, and the <48h approval gate.
 *
 * Run (from server/):  npx tsx smoke.ts
 */

import { MongoMemoryServer } from "mongodb-memory-server";

let passed = 0;
let failed = 0;
function check(label: string, cond: boolean, extra?: unknown) {
  if (cond) {
    passed++;
    console.log(`  ✓ ${label}`);
  } else {
    failed++;
    console.error(`  ✗ ${label}${extra !== undefined ? `  (got: ${JSON.stringify(extra)})` : ""}`);
  }
}

const taskTypes = {
  types: [
    { task: "Photographer", requiredSkill: "Photography", points: 5, slaHours: 0, atEvent: true, requestable: true, internalAssignable: true, vertical: "Photography" },
    { task: "Photo Editor", requiredSkill: "Photo Editing", points: 3, slaHours: 24, atEvent: false, requestable: false, internalAssignable: true, vertical: "Photography" },
    { task: "Vetter", requiredSkill: "Vetting", points: 2, slaHours: 24, atEvent: false, requestable: false, internalAssignable: true, vertical: "" },
    { task: "Event Coordinator", requiredSkill: "Coordination", points: 4, slaHours: 0, atEvent: true, requestable: false, internalAssignable: true, vertical: "" },
  ],
};
const settings = {
  slaHours: 48, strikeLimit: 3, campusStrict: true, requireApprovalAlways: false,
  strikeAssigneeToo: false, secretaryEmails: ["poc@iimsirmaur.ac.in"], adminEmails: ["admin@iimsirmaur.ac.in"],
  allowedDomains: ["iimsirmaur.ac.in"], defaultAcronym: "MEDIA", generalSeq: 0,
};
const points = {
  coordinatorPoints: 20, domainTaskPoints: 10, vetterPoints: 10, earlyWindowHours: 24,
  earlyBonusPct: 30, lateThresholdHours: 48, latePenaltyPct: 30, subsequentDelayHours: 6, subsequentPenaltyPct: 10,
};
const platforms = { platforms: [{ platform: "Instagram", handlerEmail: "asha@iimsirmaur.ac.in", points: 2, active: true }] };
const slots = { slots: ["11:00", "14:00", "17:00"] };

const team = [
  { _id: "asha@iimsirmaur.ac.in", name: "Asha", email: "asha@iimsirmaur.ac.in", campus: "Permanent", year: 2, domainHeadOf: "Photography", skills: ["Photography", "Photo Editing", "Coordination"], points: 0, strikes: 0, active: true },
  { _id: "neha@iimsirmaur.ac.in", name: "Neha", email: "neha@iimsirmaur.ac.in", campus: "Permanent", year: 2, domainHeadOf: "", skills: ["Vetting", "Coordination"], points: 0, strikes: 0, active: true },
];

async function main() {
  const mem = await MongoMemoryServer.create();
  process.env.MONGODB_URI = mem.getUri();
  process.env.MONGODB_DB = "smoke";

  // Import AFTER env is set so db/config read the right place.
  const { connect, col, close } = await import("./src/db");
  const { processNewRequest, completeTask } = await import("./src/services/workflow");
  const { confirmRequest } = await import("./src/engine/confirm");

  await connect();
  await col.config().insertMany([
    { _id: "taskTypes" as never, ...taskTypes },
    { _id: "settings" as never, ...settings },
    { _id: "points" as never, ...points },
    { _id: "platforms" as never, ...platforms },
    { _id: "slots" as never, ...slots },
  ]);
  await col.committees().insertOne({ _id: "marketing@iimsirmaur.ac.in" as never, email: "marketing@iimsirmaur.ac.in", name: "Marketing", type: "Club", campus: "Permanent", acronym: "MKTG", lastSeq: 0 });
  await col.team().insertMany(team as never[]);

  // ── 1. Post flow (committee) ────────────────────────────────────────────────
  console.log("\nPost flow:");
  const p = await col.requests().insertOne({
    type: "Post", eventName: "Launch Post", contactEmail: "marketing@iimsirmaur.ac.in",
    platforms: ["Instagram"], contentLinks: "http://x", status: "New", createdAt: new Date(),
    eventStart: null, eventEnd: null,
  } as never);
  const pid = String(p.insertedId);
  await processNewRequest(pid);
  let r = await col.requests().findOne({ _id: p.insertedId });
  check("refCode = MKTG_1", r?.refCode === "MKTG_1", r?.refCode);
  check("Post auto-accepted (not gated)", r?.status === "Request Accepted", r?.status);
  const vetter = await col.tasks().findOne({ requestId: pid, task: "Vetter" });
  check("Vetter assigned + CONFIRMED", vetter?.status === "CONFIRMED" && !!vetter?.email, vetter?.status);

  await col.tasks().updateOne({ _id: vetter!._id }, { $set: { status: "DONE", completedAt: new Date() } });
  await completeTask(String(vetter!._id));
  r = await col.requests().findOne({ _id: p.insertedId });
  check("Post -> Posted after Vetter done", r?.status === "Posted", r?.status);
  const postTasks = await col.tasks().find({ requestId: pid, task: "Post" }).toArray();
  check("1 scheduled Post task created", postTasks.length === 1 && postTasks[0].status === "SCHEDULED", postTasks.map((t) => t.status));

  // ── 2. Coverage flow, event far out (not gated) ─────────────────────────────
  console.log("\nCoverage flow (far event):");
  const farStart = new Date(Date.now() + 5 * 24 * 3600 * 1000);
  const c = await col.requests().insertOne({
    type: "Coverage", eventName: "Fest", contactEmail: "marketing@iimsirmaur.ac.in", venue: "Aud",
    rolesNeeded: ["Photographer"], platforms: ["Instagram"], status: "New", createdAt: new Date(),
    eventStart: farStart, eventEnd: new Date(farStart.getTime() + 2 * 3600 * 1000),
  } as never);
  const cid = String(c.insertedId);
  await processNewRequest(cid);
  const cr = await col.requests().findOne({ _id: c.insertedId });
  check("refCode = MKTG_2", cr?.refCode === "MKTG_2", cr?.refCode);
  check("Coverage far -> Request Accepted", cr?.status === "Request Accepted", cr?.status);
  check("coordinatorEmail stamped", !!cr?.coordinatorEmail, cr?.coordinatorEmail);
  const ctasks = await col.tasks().find({ requestId: cid }).toArray();
  const names = ctasks.map((t) => t.task).sort();
  check("pipeline = Coordinator + Photographer + derived Photo Editor",
    JSON.stringify(names) === JSON.stringify(["Event Coordinator", "Photo Editor", "Photographer"]), names);

  // ── 3. Coverage flow, event <48h (gated -> pending -> approve) ───────────────
  console.log("\nCoverage flow (soon event, gated):");
  const soon = new Date(Date.now() + 3 * 3600 * 1000);
  const g = await col.requests().insertOne({
    type: "Coverage", eventName: "Flash", contactEmail: "marketing@iimsirmaur.ac.in", venue: "Lawn",
    rolesNeeded: ["Photographer"], platforms: [], status: "New", createdAt: new Date(),
    eventStart: soon, eventEnd: new Date(soon.getTime() + 3600 * 1000),
  } as never);
  const gid = String(g.insertedId);
  await processNewRequest(gid);
  let gr = await col.requests().findOne({ _id: g.insertedId });
  check("Coverage <48h -> Pending for POC approval", gr?.status === "Pending for POC approval", gr?.status);
  await confirmRequest(gid);
  gr = await col.requests().findOne({ _id: g.insertedId });
  check("approve -> Request Accepted", gr?.status === "Request Accepted", gr?.status);

  // ── 4. Points awarded to assignees ──────────────────────────────────────────
  console.log("\nPoints:");
  const asha = await col.team().findOne({ _id: "asha@iimsirmaur.ac.in" as never });
  check("Asha accrued points", (asha?.points || 0) > 0, asha?.points);

  await close();
  await mem.stop();

  console.log(`\n${"─".repeat(40)}\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
}

main().catch((err) => {
  console.error("Smoke test crashed:", err);
  process.exit(1);
});
