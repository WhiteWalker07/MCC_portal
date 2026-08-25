# Handover — Media Committee Portal

_Last updated: 2026-08-22. Read this first when picking up the project in a new
chat. For **what the system does and why**, see [`docs/PRD.md`](docs/PRD.md).
For **who owns what**, see [`docs/PIC.md`](docs/PIC.md)._

## What this is
Workflow portal for the **Media & Communications Committee at IIM Sirmaur**.
Committees/clubs/offices raise **requests** (Coverage or Post); the system
assigns the right people, enforces deadlines, balances workload, schedules
posts, and tracks points/strikes. Full detail: [`docs/PRD.md`](docs/PRD.md).

## Stack (current — free, no card anywhere)
- **Frontend:** vanilla HTML/CSS/ES-modules SPA (`web/`), hash-routed, no
  build step. Hosted on **Vercel** (static).
- **API:** Node/Express + TypeScript (`server/`). Hosted on **Render** (free
  web service — sleeps after ~15 min idle, ~50s cold start).
- **Database:** **MongoDB Atlas** (M0 free cluster).
- **Auth:** Google OAuth (`passport-google-oauth20`) → server issues a
  **signed JWT** the client stores and sends as `Authorization: Bearer`.
  **Not cookie-based** — cookies broke cross-origin sign-in on Safari/iOS
  (third-party cookie blocking), so this is deliberate; see
  `server/src/auth/jwt.ts` and `web/js/api.js`.
- **Email:** Resend (auto-fallback to console logging if `RESEND_API_KEY` unset).
- **Calendar:** Google Calendar API, service account + domain-wide delegation
  (auto-fallback to logging if `CALENDAR_SERVICE_ACCOUNT_JSON` unset).
- **Scheduled job:** hourly deadline sweep via **GitHub Actions cron**
  (`.github/workflows/deadline.yml`) hitting `POST /api/cron/deadline-check`
  — Render's free tier has no cron of its own.

This replaced a Firebase (Cloud Functions + Firestore + Firebase Auth) build
in June 2026 — Cloud Functions require a paid Blaze plan, which the committee
wanted to avoid. The engine *logic* ported over essentially unchanged; only
the data layer, auth, and trigger→route plumbing changed. The old Firebase
code (`functions/`, `firestore.rules`, `firebase.json`) is **no longer used**
and can be deleted once you're confident you won't need to reference it.

## Environment quirks (Windows)
- **Use the PowerShell tool for `node`/`npm`/`tsc`** — the Bash tool's Node is
  too old for this project's tooling. PowerShell's Node is current.
- Parse-check web ES modules from Bash by copying to a temp `.mjs` and running
  `node --check` (syntax-only, works on an older Node).
- Repo **is** a git repo now, remote `WhiteWalker07/MCC_portal`. Working
  branch is `develop`; PRs merge into `main`; Render + Vercel both deploy from
  `main`.

## Key commands
```powershell
# Server (from server/)
npm install
npm run dev              # local API on :8080 (tsx watch)
npm run typecheck        # tsc --noEmit — expect exit 0
npx tsx smoke.ts          # 12-check engine test against an in-memory Mongo (no setup needed)
npm run seed              # sample/demo config+data (local dev only)
npm run load-data          # the REAL committees/team/config — safe to re-run, idempotent
npm run set-roles          # (re)apply admin/POC emails only
npm run db-check           # diagnose "can't reach MongoDB" (Atlas IP allowlist / network block)

# Frontend (from repo root)
npx serve web -l 3000      # local static server; web/js/config.js auto-targets localhost:8080
```
Full local-dev + deploy walkthrough: [`server/README.md`](server/README.md).

## Roles right now
See [`docs/PIC.md`](docs/PIC.md) for the full accountability map. Short version:
**Admin** = `mbatm25010@iimsirmaur.ac.in`, **Secretary/POC** =
`mba25114@iimsirmaur.ac.in`. Both are **data** (`config/settings` in Mongo),
not code — change them via the Admin view or `server/scripts/set-roles.mjs`.

## Data status
The real committee roster (41) and media team (23) are loaded via
`server/src/admin/realData.ts`, run with `npm run load-data` (locally, if your
network can reach Atlas) or `POST /api/admin/load-data` on Render (guarded by
`CRON_SECRET` — works from anywhere since it's a normal HTTPS call, useful
when a campus network blocks MongoDB's port directly). **If platforms are
missing from the Post form or the admin/POC accounts don't have their
privileges, this load hasn't completed against production yet** — run it, then
`GET /api/admin/status` (same secret) to verify. Details:
[`server/README.md`](server/README.md).

## Recently added (this round, 2026-08)
1. **Event-start completion guard:** a task tied to an event can't be marked
   done before that event starts (`server/src/routes/tasks.ts` +
   `web/js/views/myTasks.js`).
2. **Availability / "out of work" tracking:** secretary/admin can toggle a
   member out of work (excluded from auto-assignment) and back; cumulative
   on-work/out days are tracked live (`server/src/routes/team.ts` §
   `/api/team/availability`, Admin view).
3. **Real data loaded:** 41 committees + 23 team members
   (`server/src/admin/realData.ts`), replacing the demo seed. Also seeds the
   engine config (`taskTypes`/`slots`/`platforms`/`points`/`settings`) if
   absent, without clobbering admin-tuned values on re-run.
4. **Committee management UI:** add/update a committee from the Admin view
   (`GET/POST /api/committees`) — no script needed for day-to-day use.
5. **Admin status endpoint:** `GET /api/admin/status` (secret-guarded) — a
   quick read of what's actually in the database, for verifying a data load
   without DB credentials.
6. **Token-based auth (Safari/iOS fix):** replaced the session cookie with a
   signed JWT the client stores and sends as a Bearer header. Cookie sessions
   couldn't survive the cross-site Vercel↔Render hop on Safari/iOS (blocks
   third-party cookies); a same-origin Vercel-proxy attempt was tried first
   and reverted as fragile in favor of this. See `server/src/auth/jwt.ts`,
   `web/js/api.js`, `web/js/auth.js`.
7. **Real logo favicon** — `web/favicon.svg` is the actual MCC logo, not a
   placeholder.

## ⚠️ Open items (see `docs/PRD.md` §10 for full detail)
1. **Points timing-reference is unconfirmed** — currently measured from
   event-end/request-creation to completion; may need to be relative to each
   task's own deadline instead. One-line change in
   `server/src/services/workflow.ts` (`completeTask`) once confirmed.
2. **Social platform handlers are placeholders** (`mediacell@…` for all of
   Instagram/LinkedIn/X) — need real owners.
3. **No vertical heads formally appointed** in the database yet (Admin view →
   Vertical heads) — until then, no one has the manual-assign privilege that
   comes with it.
4. **All vendor accounts (GitHub/Render/Atlas/Vercel/Google Cloud) sit with
   one person** — add a second owner; see `docs/PIC.md` §3.

## Architecture cheat-sheet
- **Clients never own** `requests.status`, `tasks.points/status`, `refCode`,
  `campus`, `coordinatorEmail`, or points/strikes — the engine (server-side)
  owns those; enforced in Express routes + `engine/serverRoles.ts`'s
  `canAssign`, not in the client.
- **Lifecycle:** New → (Pending for POC approval, if gated) → Request
  Accepted → Event Covered → Ready To post → Posted. (Rejected is terminal.)
- **Former Firestore triggers**, now plain functions in
  `server/src/services/workflow.ts` called directly from routes:
  `processNewRequest`, `confirmRequest` (approve), `rejectRequest`,
  `completeTask`, `schedulePosts`, `runDeadlineCheck`.
- **Routes** (`server/src/routes/`): `auth`, `config`, `requests`, `tasks`,
  `assignments`, `team`, `committees`, `dashboard`, `cron`, `admin`.
- **Frontend views** (`web/js/views/`): newRequest, myRequests, myTasks,
  assignments, approvals, dashboard, admin. Shell/routing in `web/js/shell.js`
  (hash router); auth in `app.js`/`auth.js`; data layer in `data.js` (polls
  every ~20s, refreshes instantly on any mutation via a `mcc:mutated` event).

## Verification habit
- After server changes: PowerShell → `cd server; npm run typecheck` (expect
  exit 0) and `npx tsx smoke.ts` (expect 12/12).
- After web changes: Bash → copy the changed file to a temp `.mjs`,
  `node --check` it.
- Both were green as of this handover.

## Pointers
- **What the system does:** [`docs/PRD.md`](docs/PRD.md)
- **Who owns what:** [`docs/PIC.md`](docs/PIC.md)
- **Deploy + local-run steps:** [`server/README.md`](server/README.md)
- Auto-memory index: `C:\Users\Agrim Kaundal\.claude\projects\F--MCC-Portal\memory\MEMORY.md`
