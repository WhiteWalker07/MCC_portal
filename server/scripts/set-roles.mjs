/**
 * One-off maintenance script — safe to run against the LIVE database.
 *
 * 1. Sets the admin + secretary (POC) emails in config/settings WITHOUT touching
 *    any other data (unlike `seed --force`, which resets points/strikes).
 * 2. Initializes availability fields on any team member that lacks them, so the
 *    on-work / out-of-work day counting starts from now.
 *
 * Run (from server/):  npx tsx ../scripts/set-roles.mjs
 * Needs MONGODB_URI (+ optional MONGODB_DB) in server/.env or the environment.
 */

import "dotenv/config";
import { MongoClient } from "mongodb";

const uri = process.env.MONGODB_URI;
const dbName = process.env.MONGODB_DB || "mcc_portal";

// 👉 Edit these to the real people.
const ADMIN_EMAILS = ["mbatm25010@iimsirmaur.ac.in"];
const SECRETARY_EMAILS = ["mba25114@iimsirmaur.ac.in"];

if (!uri) {
  console.error("MONGODB_URI is not set (put it in server/.env or the environment).");
  process.exit(1);
}

async function main() {
  const client = new MongoClient(uri);
  await client.connect();
  const db = client.db(dbName);

  const settings = await db.collection("config").updateOne(
    { _id: "settings" },
    { $set: { adminEmails: ADMIN_EMAILS, secretaryEmails: SECRETARY_EMAILS } },
    { upsert: true }
  );
  console.log(`config/settings: admins=${ADMIN_EMAILS.join(", ")} | secretaries=${SECRETARY_EMAILS.join(", ")}`);
  console.log(`  (matched ${settings.matchedCount}, upserted ${settings.upsertedCount})`);

  const avail = await db.collection("team").updateMany(
    { availabilityChangedAt: { $exists: false } },
    { $set: { availability: "available", availabilityChangedAt: new Date() } }
  );
  // Ensure numeric counters exist (separate update so $set above doesn't clobber).
  await db.collection("team").updateMany(
    { onWorkDays: { $exists: false } },
    { $set: { onWorkDays: 0 } }
  );
  await db.collection("team").updateMany(
    { outDays: { $exists: false } },
    { $set: { outDays: 0 } }
  );
  console.log(`team: initialized availability on ${avail.modifiedCount} member(s).`);

  await client.close();
  console.log("Done.");
}

main().catch((err) => {
  console.error("Failed:", err.message || err);
  process.exit(1);
});
