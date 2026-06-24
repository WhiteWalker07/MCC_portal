# Handover — Media Committee Portal

_Last updated: 2026-06-24. Read this first when picking up the project in a new chat._

## 🟢 ARCHITECTURE CHANGE (2026-06-24): migrated off Firebase → Express + MongoDB
To run **fully free with no credit card** (Cloud Functions require the paid Blaze plan), the
backend was rebuilt as a standalone **Node/Express + MongoDB** server in **`server/`**, meant
for **Render (free) + MongoDB Atlas M0 (free)**, with **Passport `google-oauth20`** sign-in
(session in Mongo) replacing Firebase Auth. Resend + googleapis are reused unchanged. The
Firestore **triggers are now synchronous functions** in `server/src/services/workflow.ts`,
called from the routes; the hourly job is `POST /api/cron/deadline-check` driven by a GitHub
Actions cron (`.github/workflows/deadline.yml`). The frontend (`web/`, still on Vercel) keeps
all views/CSS; only its data/auth layer was swapped (`web/js/api.js` + `config.js` replace
`firebase.js`/`firebase-config.js`; `roles.js` deleted; `data.js` `watch*` now poll every 20s
and refresh instantly after a mutation via a `mcc:mutated` event).
- **Deploy + local-run guide:** [`server/README.md`](server/README.md).
- **Verified:** `cd server; npx tsc --noEmit` (exit 0) and `npx tsx smoke.ts` (12/12 engine
  checks against in-memory Mongo — refcode, pipeline, assign, confirm, complete, schedule
  posts, <48h gate, points).
- The old **Firebase stack below (`functions/`, `firebase.json`, `firestore.rules`, the
  Firebase parts of this doc) is SUPERSEDED but kept** until the Render deploy is validated,
  then it can be removed. Sections below describe that legacy stack.

## What this is
Workflow portal for the **Media Committee at IIM Sirmaur**. Committees/clubs/MDP/students
raise **requests** (Coverage or Post); the system assigns the right people, enforces
deadlines, balances workload, schedules posts, and tracks points/strikes.

**Stack:** vanilla HTML/CSS/ES-modules frontend (no framework, Firebase Web SDK from CDN
pinned in `web/js/firebase.js`) on Vercel · Firebase Auth (Google, domain
`@iimsirmaur.ac.in`) · Firestore (+ rules) · Cloud Functions v2 (TypeScript, region
`asia-south1`). Email = Resend, Calendar = Google — both behind **env auto-fallback** to
logging stubs when keys absent.

## ⚠️ Environment quirk (important)
- The **Bash tool's `node` is v15** (too old — can't run firebase-admin/tsc/emulator).
  **PowerShell's `node` is v24** — use the **PowerShell tool** for `node`/`npm`/`tsc`/seed/emulator.
- Typecheck functions: PowerShell → `cd "F:\MCC Portal\functions"; npx tsc --noEmit`
- Syntax-check web ES modules in Bash by copying to a temp `.mjs` and `node --check` (parse-only works on v15).
- Repo is **not a git repo**. Platform: Windows 11, primary dir `F:\MCC Portal`.

## Status: feature-complete in the emulator. Only production deploy remains.
All phases 1–13 built + emulator-tested, plus several rounds of user-requested additions.

## Key commands (run in PowerShell)
```
npm run build:functions     # compile TS (do after any functions/ change)
npm run seed:force          # (re)seed config + sample data into the emulator
npm run emulators           # start Firebase emulators
npx serve web               # serve the frontend (separate terminal)
cd functions; npx tsc --noEmit     # typecheck
npm --prefix functions run shell   # invoke scheduled/callable fns manually (e.g. scheduledDeadlineCheck())
```
Seed accounts: `admin@iimsirmaur.ac.in` (admin), `poc@iimsirmaur.ac.in` (secretary),
committee logins like `marketing@iimsirmaur.ac.in`, plus a sample team. Any
`@iimsirmaur.ac.in` account can sign in (add via Auth emulator).

## Architecture cheat-sheet
- **Clients never write** `requests.status`, `tasks.points/status`, refCode, campus,
  coordinatorEmail, roster, or `team/*`. The engine (Admin SDK) owns those. Clients act
  only through gated callables or narrow rule-checked field writes.
- **Lifecycle:** New → (Pending for POC approval, if <48h Coverage / approval-always) →
  Request Accepted → Event Covered → Ready To post → Posted. (Rejected is terminal.)
- **Triggers** (`functions/src/triggers/`): `onRequestCreated` (refCode, pipeline, assign,
  gate, confirm), `onRequestDecided` (approve/reject), `onTaskCompleted` (advance +
  points-timing modifier), `onTaskReassign`, `onReadyToPost` (slot scheduler), `scheduledDeadlineCheck` (hourly LATE+strikes).
- **Callables** (`functions/src/callable/`): `assignments.ts` (getEligibleMembers,
  listAssignableTasks, requestReassign, assignTask, markReadyToPost), `team.ts`
  (importTeamCsv, listTeamMembers, setDomainHead, setPointScheme), `dashboard.ts`
  (getDashboardStats). All exported in `functions/src/index.ts`.
- **Config docs** (`config/*`, client-readable, engine-written): `taskTypes`, `slots`,
  `platforms`, `settings`, `points`. Seeded by `scripts/seed.mjs`.
- **Frontend views** (`web/js/views/`): newRequest, myRequests, myTasks, assignments,
  approvals, dashboard, admin. Routing/nav/shell in `web/js/shell.js` (hash router); auth
  in `app.js`/`auth.js`; roles in `roles.js`; data layer in `data.js`.

## Recently added (beyond the original phase plan)
1. **UI redesign** to the user's wireframes (`Media Portal Wireframes.dc.html`, Approach A):
   warm `#e8e7e3` canvas, Hanken Grotesk + Space Mono, indigo `#5b61d6` primary; lifecycle
   stepper, flat task cards, contact-card roster, status chips. All in `web/assets/css/styles.css`.
2. **Device-adaptive shell:** desktop = top pill nav; **phones = fixed bottom tab bar** with
   icons (in `shell.js` + a `@media (max-width:767px)` block).
3. **Critical bug fixed:** `.splash`/`.signin-view` set `display:flex`, which overrode the
   `hidden` attribute, so loading/sign-in never hid and all views stacked into one
   scrollable page ("login doesn't disappear"). Fixed with `[hidden]{display:none!important}`
   at the top of `styles.css`. **This was the root cause of the "single pager" complaint.**
4. **Dashboard** (`dashboard.ts` callable + `dashboard.js`): admin/secretary fairness+usage
   stats (active members, points, on-time rate, turnaround, leaderboard bars, by-vertical,
   requests-by-status) with **filters**: Time / Campus / Year / Vertical.
5. **Vertical heads:** admins/POCs appoint domain heads from the Admin view
   (`setDomainHead` / `listTeamMembers` callables). One head per vertical (new demotes old).
6. **Access change:** any `@iimsirmaur.ac.in` user can create a **Post** request; **Coverage
   reserved to committees** (rule in `firestore.rules` + form in `newRequest.js`).
   Non-committee requesters get a fallback refCode `MEDIA_n` (`settings.defaultAcronym` +
   `settings.generalSeq`, in `engine/refcode.ts`).
7. **Committee logos:** `web/assets/logos/` folder + `committee.logo` field shown in top bar.
8. **Configurable point scheme** (`config/points`, `engine/points.ts`, admin editor in
   `admin.js` via `setPointScheme`): base points (Coordinator 20, domain task 10, Vetter 10)
   + completion-timing modifier — `≤24h → +30%`, `>48h → −30%`, each further 6h → −10%.
   Applied in `onTaskCompleted` once per task; logs `points-adjust`.

## ⚠️ OPEN QUESTION the user has not yet answered
The points **timing reference** is currently assumed to be **turnaround from the event end
(Coverage) / request creation (Post)** to completion (see `onTaskCompleted.ts` line ~52). The
user's spec ("post under 24h / delayed >48h") didn't say *measured from when*. If they meant
**relative to each task's deadline**, it's a one-line change of `refTs` in `onTaskCompleted.ts`.
**Confirm this with the user.**

## Email & Calendar (both wired, auto-fallback)
- **Email** (`services/email.ts`): real Resend when `RESEND_API_KEY` set, else logs. Used by
  `onRequestCreated` (secretary approval email) + `onRequestDecided` (rejection email).
- **Calendar** (`services/calendar.ts`): real Google Calendar when
  `CALENDAR_SERVICE_ACCOUNT_JSON` set (service account + domain-wide delegation), else
  treats everyone free / logs invites. Used by `chooseMember` (free/busy for atEvent tasks)
  + holds/reminders. README has the Workspace delegation setup steps.
- In the emulator both run as stubs (logged). **To test real:** set the env var(s) in
  `functions/.env` (or Secret Manager for prod) and restart functions.

## What's NOT done / next steps
- **Production deploy** (the only remaining roadmap item):
  - Frontend → Vercel: put real config in `web/js/firebase-config.js`, add the Auth
    authorized domain, `npm run deploy:web`.
  - Backend → Firebase Blaze: `firebase use <project>`, `npm run deploy:rules`,
    `firebase functions:secrets:set RESEND_API_KEY`, `npm run deploy:functions`,
    `npm run seed -- --prod` with real committees/team/settings.
- **After any of the recent additions, re-run `npm run seed:force`** so `config/points`,
  `settings.defaultAcronym`/`generalSeq`, and `committee.logo` exist in the emulator.
- Confirm the points timing-reference question above.
- Optional/未requested: real two-pane desktop layouts, literal multi-HTML-file routing
  (deliberately not done — would re-run auth per page; current SPA gives clean per-view
  pages with working back button).

## Verification habit
After functions changes: PowerShell `cd functions; npx tsc --noEmit` (expect exit 0).
After web changes: Bash copy-to-`.mjs` + `node --check`. Both were green at handover.

## Pointers
- Full feature/phase history + deploy steps: `README.md`.
- Auto-memory index: `C:\Users\Agrim Kaundal\.claude\projects\F--MCC-Portal\memory\MEMORY.md`
  (project overview + the Node/PowerShell quirk).
