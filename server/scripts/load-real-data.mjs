/**
 * Loads the real committees + media team into the database (idempotent, safe to
 * re-run). Normalizes the source sheets to the engine's schema:
 *   - campus -> "MBA Campus" / "BMS Campus"
 *   - skills -> canonical task skills (Photography, Photo Editing, Videography,
 *     Video Editing, Content Writing, Graphic design, Coordination, Vetting)
 *   - vertical -> Photography | Videography | Graphic Designs | Content Writing
 *   - designation -> year (Senior Cordinator/POC = 2, Executive = 1) and, for
 *     Senior Coordinators + POC, the Coordination + Vetting skills are added so
 *     events can get a coordinator and posts can be vetted (ADJUST IF WRONG).
 *
 * Upserts preserve engine-managed fields (lastSeq / points / strikes /
 * domainHeadOf / availability) via $setOnInsert. Also sets the admin + POC in
 * config/settings.
 *
 * Run (from server/):       npx tsx ../scripts/load-real-data.mjs
 *   add --clean to also remove the demo seed committees/team:
 *                           npx tsx ../scripts/load-real-data.mjs --clean
 * Needs MONGODB_URI (+ optional MONGODB_DB) in server/.env or the environment.
 */

import "dotenv/config";
import { MongoClient } from "mongodb";

const CLEAN = process.argv.includes("--clean");
const uri = process.env.MONGODB_URI;
const dbName = process.env.MONGODB_DB || "mcc_portal";
if (!uri) {
  console.error("MONGODB_URI is not set (server/.env or the environment).");
  process.exit(1);
}

const ADMIN_EMAILS = ["mbatm25010@iimsirmaur.ac.in"];
const SECRETARY_EMAILS = ["mba25114@iimsirmaur.ac.in"];

// ── Committees (SIGs without a login email are omitted; duplicate emails deduped
//    to the MBA entry: mediacell@, enicell@, campusconnect@) ───────────────────
const committees = [
  ["sapient@iimsirmaur.ac.in", "Sapient", "SPT", "Club", "MBA Campus"],
  ["finserve@iimsirmaur.ac.in", "Finserve", "FIN", "Club", "MBA Campus"],
  ["scope@iimsirmaur.ac.in", "Scope – The Operations Club", "SOC", "Club", "MBA Campus"],
  ["rangmanch@iimsirmaur.ac.in", "RangManch", "RM", "Club", "MBA Campus"],
  ["datonics@iimsirmaur.ac.in", "Datonics", "DATA", "Club", "MBA Campus"],
  ["horizon@iimsirmaur.ac.in", "HORIZON – The HR Club", "HR", "Club", "MBA Campus"],
  ["atithya@iimsirmaur.ac.in", "Atithya (Tourism and Hospitality Club)", "ATH", "Club", "MBA Campus"],
  ["markaizen@iimsirmaur.ac.in", "Markaizen", "MRKZ", "Club", "MBA Campus"],
  ["mosaic@iimsirmaur.ac.in", "Mosaic", "MOSC", "Club", "MBA Campus"],
  ["ebsb@iimsirmaur.ac.in", "EBSB (Ek Bharat Shreshtha Bharat Club)", "EBSB", "Club", "MBA Campus"],
  ["jal@iimsirmaur.ac.in", "JAL", "JAL", "Club", "MBA Campus"],
  ["mediacell@iimsirmaur.ac.in", "Media & Communications Committee", "MCC", "Committee", "MBA Campus"],
  ["sac@iimsirmaur.ac.in", "Student Academic Committee (SAC)", "SAC", "Committee", "MBA Campus"],
  ["campusconnect@iimsirmaur.ac.in", "Admissions Committee", "ADCOM", "Committee", "MBA Campus"],
  ["industrialrelation@iimsirmaur.ac.in", "Industrial Relations and Sponsorship Committee", "IRS", "Committee", "MBA Campus"],
  ["trainingcell@iimsirmaur.ac.in", "Training and Development Committee", "TND", "Committee", "MBA Campus"],
  ["alumni@iimsirmaur.ac.in", "Alumni Relations Committee", "ARC", "Committee", "MBA Campus"],
  ["sportscommittee@iimsirmaur.ac.in", "Sports Committee", "SCOM", "Committee", "MBA Campus"],
  ["sankalp@iimsirmaur.ac.in", "Sankalp – Corporate Social Responsibility Cell", "CSR", "Committee", "MBA Campus"],
  ["enicell@iimsirmaur.ac.in", "Entrepreneurship and Incubation Cell", "ENI", "Committee", "MBA Campus"],
  ["sanskriti@iimsirmaur.ac.in", "Sanskriti – Cultural Committee", "CULCOM", "Committee", "MBA Campus"],
  ["infra-it@iimsirmaur.ac.in", "Infrastructure and IT Committee", "INFRA", "Committee", "MBA Campus"],
  ["messcommittee@iimsirmaur.ac.in", "Naivedyam (Mess Committee)", "MESS", "Committee", "MBA Campus"],
  ["placements@iimsirmaur.ac.in", "Corporate Relations & Placement Committee", "PCOM", "Committee", "MBA Campus"],
  ["xentrixesports@iimsirmaur.ac.in", "Xentrix", "XNTRX", "SIG", "MBA Campus"],
  ["pgpoffice@iimsirmaur.ac.in", "PGP Office", "PGP", "Office", "MBA Campus"],
  ["mdpoffice@iimsirmaur.ac.in", "MDP Office", "MDP", "Office", "MBA Campus"],
  ["bms.infra-it@iimsirmaur.ac.in", "Infra-IT Committee (BMS)", "INFRABMS", "Committee", "BMS Campus"],
  ["sacbms@iimsirmaur.ac.in", "Students' Academic committee (BMS)", "SACBMS", "Committee", "BMS Campus"],
  ["bmspcom@iimsirmaur.ac.in", "Placement Committee (BMS)", "PCOMBMS", "Committee", "BMS Campus"],
  ["bmsmesscommittee@iimsirmaur.ac.in", "Naivedyam (BMS Mess Committee)", "BMSMESS", "Committee", "BMS Campus"],
  ["sportscommittee.bms@iimsirmaur.ac.in", "BMS Sports Committee", "BMSSPORTS", "Committee", "BMS Campus"],
  ["culturalcommittee.bms@iimsirmaur.ac.in", "Kalakriti (BMS Cultural Committee)", "CULCOMBMS", "Committee", "BMS Campus"],
  ["catalyst.x@iimsirmaur.ac.in", "CatalyStX", "CTX", "Club", "BMS Campus"],
  ["econyx@iimsirmaur.ac.in", "Econyx", "ECO", "Club", "BMS Campus"],
  ["hriday@iimsirmaur.ac.in", "HRiday", "HRBMS", "Club", "BMS Campus"],
  ["finexus@iimsirmaur.ac.in", "Finexus", "FINBMS", "Club", "BMS Campus"],
  ["markeista@iimsirmaur.ac.in", "Markeista", "MARBMS", "Club", "BMS Campus"],
  ["synapsys@iimsirmaur.ac.in", "Synapsys", "SNP", "Club", "BMS Campus"],
  ["synex@iimsirmaur.ac.in", "SynEx", "SNX", "Club", "BMS Campus"],
  ["spicmacay@iimsirmaur.ac.in", "Spic Macay", "SM", "Club", "BMS Campus"],
].map(([email, name, acronym, type, campus]) => ({ email, name, acronym, type, campus }));

// ── Team (skills already normalized; +Coordination/+Vetting on seniors/POC) ────
const SENIOR = { year: 2, lead: true }; // Senior Cordinator / POC
const EXEC = { year: 1, lead: false }; // Executive

const team = [
  ["MBA25178@iimsirmaur.ac.in", "Sanjana Jaiswal", "Graphic Designs", "MBA Campus", SENIOR, ["Graphic design"]],
  ["MBA25189@iimsirmaur.ac.in", "Aisha Firdouse", "Graphic Designs", "MBA Campus", SENIOR, ["Graphic design", "Video Editing", "Photography", "Videography"]],
  ["MBATM25033@iimsirmaur.ac.in", "Priyal Shende", "Content Writing", "MBA Campus", SENIOR, ["Content Writing", "Photography"]],
  ["MBATM25024@iimsirmaur.ac.in", "Navina", "Photography", "MBA Campus", SENIOR, ["Photography"]],
  ["MBA25092@iimsirmaur.ac.in", "Siddarth N", "Photography", "MBA Campus", SENIOR, ["Photography", "Photo Editing"]],
  ["MBATTHM25017@iimsirmaur.ac.in", "Suda Yugandhar", "Photography", "MBA Campus", SENIOR, ["Photography", "Photo Editing", "Videography", "Content Writing", "Video Editing"]],
  ["mba25109@iimsirmaur.ac.in", "G N V Umanand Naik", "Photography", "MBA Campus", SENIOR, ["Photography", "Photo Editing"]],
  ["MBA25114@iimsirmaur.ac.in", "Kamalasegaran A", "Photography", "MBA Campus", SENIOR, ["Photography", "Photo Editing", "Videography", "Content Writing"]],
  ["MBATM25010@iimsirmaur.ac.in", "Agrim Kaundal", "Photography", "MBA Campus", SENIOR, ["Photography", "Photo Editing"]],
  ["MBA25159@iimsirmaur.ac.in", "Ishan Negi", "Photography", "MBA Campus", SENIOR, ["Photography", "Photo Editing", "Videography"]],
  ["bms25021@iimsirmaur.ac.in", "Arya Paliwal", "Photography", "BMS Campus", EXEC, ["Photography", "Videography"]],
  ["bms25123@iimsirmaur.ac.in", "Sukriti Saxena", "Videography", "BMS Campus", EXEC, ["Videography", "Graphic design"]],
  ["bms25088@iimsirmaur.ac.in", "Nitya Jaiswal", "Photography", "BMS Campus", EXEC, ["Photography", "Content Writing"]],
  ["bms24006@iimsirmaur.ac.in", "Aditi Shukla", "", "BMS Campus", EXEC, ["Photography", "Graphic design", "Content Writing"]],
  ["bms24107@iimsirmaur.ac.in", "Saguna Rishi", "Content Writing", "BMS Campus", EXEC, ["Content Writing", "Graphic design"]],
  ["bms24002@iimsirmaur.ac.in", "Aarushi Dubey", "", "BMS Campus", EXEC, []],
  ["bms24092@iimsirmaur.ac.in", "Prashant Kumar", "", "BMS Campus", EXEC, []],
  ["bms25077@iimsirmaur.ac.in", "Laasya Nekkanti", "", "BMS Campus", EXEC, []],
  ["Bms24007@iimsirmaur.ac.in", "Aditya Kalyankar", "", "BMS Campus", EXEC, []],
  ["bms24141@iimsirmaur.ac.in", "Ujjwala Naudiyal", "", "BMS Campus", EXEC, []],
  ["bms24104@iimsirmaur.ac.in", "Rishabh Garg", "", "BMS Campus", EXEC, []],
  ["bms24150@iimsirmaur.ac.in", "Yash Tripathi", "", "BMS Campus", EXEC, []],
  ["bms24036@iimsirmaur.ac.in", "Deepshikha Das", "", "BMS Campus", EXEC, []],
].map(([email, name, vertical, campus, role, skills]) => {
  const s = new Set(skills);
  if (role.lead) {
    s.add("Coordination");
    s.add("Vetting");
  }
  return { email: email.toLowerCase(), name, vertical, campus, year: role.year, skills: [...s] };
});

const DEMO_COMMITTEES = ["marketing", "consult", "cultural", "mdp", "bms.cultural"].map((p) => `${p}@iimsirmaur.ac.in`);
const DEMO_TEAM = ["asha", "vikram", "neha", "rahul", "priya", "karan", "sara"].map((p) => `${p}@iimsirmaur.ac.in`);

async function main() {
  const client = new MongoClient(uri);
  await client.connect();
  const db = client.db(dbName);

  if (CLEAN) {
    const dc = await db.collection("committees").deleteMany({ _id: { $in: DEMO_COMMITTEES } });
    const dt = await db.collection("team").deleteMany({ _id: { $in: DEMO_TEAM } });
    console.log(`--clean: removed ${dc.deletedCount} demo committee(s), ${dt.deletedCount} demo member(s)`);
  }

  let cN = 0;
  for (const c of committees) {
    await db.collection("committees").updateOne(
      { _id: c.email },
      {
        $set: { email: c.email, name: c.name, type: c.type, campus: c.campus, acronym: c.acronym },
        $setOnInsert: { lastSeq: 0, logo: "" },
      },
      { upsert: true }
    );
    cN++;
  }

  let tN = 0;
  for (const t of team) {
    await db.collection("team").updateOne(
      { _id: t.email },
      {
        $set: { email: t.email, name: t.name, skills: t.skills, vertical: t.vertical, year: t.year, campus: t.campus, active: true },
        $setOnInsert: {
          phone: "",
          points: 0,
          strikes: 0,
          domainHeadOf: "",
          availability: "available",
          availabilityChangedAt: new Date(),
          onWorkDays: 0,
          outDays: 0,
        },
      },
      { upsert: true }
    );
    tN++;
  }

  await db.collection("config").updateOne(
    { _id: "settings" },
    { $set: { adminEmails: ADMIN_EMAILS, secretaryEmails: SECRETARY_EMAILS } },
    { upsert: true }
  );

  console.log(`committees upserted: ${cN}`);
  console.log(`team upserted:       ${tN}`);
  console.log(`admin:     ${ADMIN_EMAILS.join(", ")}`);
  console.log(`secretary: ${SECRETARY_EMAILS.join(", ")}`);
  console.log("Done.");
  await client.close();
}

main().catch((err) => {
  console.error("Failed:", err.message || err);
  process.exit(1);
});
