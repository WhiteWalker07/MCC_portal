/**
 * Connection diagnostic — verifies server/.env can actually reach MongoDB.
 * Run (from server/):  npm run db-check
 */

import "dotenv/config";
import { MongoClient } from "mongodb";

const uri = process.env.MONGODB_URI;
const dbName = process.env.MONGODB_DB || "mcc_portal";

if (!uri) {
  console.error("✗ MONGODB_URI is not set. Put it in server/.env");
  process.exit(1);
}

const masked = uri.replace(/\/\/([^:@/]+):([^@]+)@/, "//$1:****@");
const host = (uri.match(/@([^/?]+)/) || [])[1] || "(unknown)";
console.log("URI :", masked);
console.log("host:", host);
console.log("db  :", dbName);

if (/xxxxx|USER:PASS|REPLACE|<password>/i.test(uri)) {
  console.warn("\n⚠ This looks like the PLACEHOLDER from .env.example — replace it with your real Atlas connection string (Atlas → Connect → Drivers).");
}
if (/\s/.test(dbName)) {
  console.warn("\n⚠ MONGODB_DB contains a space — use e.g. mcc_portal.");
}

const client = new MongoClient(uri, { serverSelectionTimeoutMS: 8000 });
try {
  await client.connect();
  await client.db(dbName).admin().ping();
  const counts = {
    committees: await client.db(dbName).collection("committees").countDocuments(),
    team: await client.db(dbName).collection("team").countDocuments(),
  };
  console.log("\n✓ Connected OK. Existing docs:", counts);
} catch (e) {
  console.error("\n✗ Could not connect:", e.message);
  console.error(
    "\nMost common causes (in order):\n" +
      "  1. Atlas Network Access doesn't include your current IP.\n" +
      "     Atlas → Network Access → Add IP Address → Allow from anywhere (0.0.0.0/0).\n" +
      "  2. This network blocks outbound port 27017 (common on campus/office wifi).\n" +
      "     Try a mobile hotspot, then re-run.\n" +
      "  3. Wrong password, or special characters not URL-encoded, or a placeholder URI.\n"
  );
} finally {
  await client.close().catch(() => {});
  process.exit(0);
}
