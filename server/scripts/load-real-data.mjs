/**
 * Loads the real committees + media team + engine config into the database
 * (idempotent, safe to re-run). Thin wrapper over the shared loader in
 * src/admin/realData.ts so the script and the POST /api/admin/load-data endpoint
 * never diverge.
 *
 * Run (from server/):  npm run load-data        |  npm run load-data -- --clean
 * Needs MONGODB_URI (+ optional MONGODB_DB) in server/.env or the environment.
 *
 * Can't reach Atlas from your network? Run it on the server instead:
 *   curl -X POST -H "x-cron-secret: $CRON_SECRET" \
 *     "https://<your-service>.onrender.com/api/admin/load-data?clean=1"
 */

import "dotenv/config";
import { connect, close } from "../src/db";
import { loadRealData } from "../src/admin/realData";

const clean = process.argv.includes("--clean");

async function main() {
  if (!process.env.MONGODB_URI) {
    console.error("MONGODB_URI is not set (server/.env or the environment).");
    process.exit(1);
  }
  await connect();
  const r = await loadRealData({ clean });
  if (clean) console.log(`--clean: removed ${r.cleaned.committees} demo committee(s), ${r.cleaned.team} demo member(s)`);
  console.log(`committees upserted: ${r.committees}`);
  console.log(`team upserted:       ${r.team}`);
  console.log("config ensured:      taskTypes, slots, platforms, points, settings");
  console.log("admin:     mbatm25010@iimsirmaur.ac.in");
  console.log("secretary: mba25114@iimsirmaur.ac.in");
  console.log("Done.");
  await close();
}

main().catch((err) => {
  console.error("Failed:", err.message || err);
  process.exit(1);
});
