# Media Committee Portal

Workflow portal for the **Media Committee at IIM Sirmaur**. Committees, clubs, the
MDP office, students, and faculty raise **requests** to get events covered and/or
content posted on the institute's social handles. The system assigns the right
people, enforces deadlines, balances workload, schedules posts, and tracks
accountability.

> **Status:** Feature-complete in the emulator (Phases 1–13 implemented). Email
> (Resend) and Calendar (Google) are wired behind env-based auto-fallback — they
> stay logging-stubs until you provide credentials. Remaining: configure
> production credentials + deploy. See [Build phases](#build-phases).

---

## Architecture (decided)

| Layer | Choice |
|---|---|
| Frontend | Mobile-first **HTML + CSS + vanilla JS (ES modules)**, Firebase Web SDK v10+. No SPA framework — plain HTML so a non-coder successor can maintain it. |
| Hosting (frontend) | **Vercel** (static, serves `/web`) |
| Auth | **Firebase Auth**, Google provider, restricted to `@iimsirmaur.ac.in` |
| Database | **Cloud Firestore** + security rules (rules enforce all role-based access) |
| Engine | **Firebase Cloud Functions** (Node 20 / TypeScript) — Firestore triggers + one scheduled function |
| Email | **Resend** (behind a swappable interface) |
| Calendar | Google Calendar API via service account w/ domain-wide delegation — **stubbed until Phase 11** |
| Local dev | **Firebase Emulator Suite** (Auth, Firestore, Functions) |

---

## Repository layout

```
/web                 # responsive HTML/CSS/JS frontend (deployed to Vercel)
  /assets/css        # styles
  /js                # firebase init, auth, data access, per-view modules
  index.html
/functions           # Cloud Functions engine — TypeScript
  /src               # index.ts (entry) + triggers (added per phase)
/scripts             # idempotent seed script (config + sample data)
firestore.rules      # security rules (full ruleset lands in Phase 3)
firestore.indexes.json
firebase.json        # Firestore + Functions + Emulator config
vercel.json          # static hosting config for /web
.env.example         # documented config/secret placeholders (never real values)
```

---

## Prerequisites

- **Node.js 20 LTS or newer.**
  > ⚠️ The Firebase Emulator Suite, `firebase-tools`, and Cloud Functions v2 do
  > **not** run on Node 15/16/18-EOL. If `node --version` is below 20, install
  > Node 20 LTS (e.g. via [nvm-windows](https://github.com/coreybutler/nvm-windows)
  > or the official installer) before continuing.
- **Firebase CLI** — `npm install -g firebase-tools`
- **Java JDK 11+** — required by the Firestore emulator.
- **Vercel CLI** (for frontend deploys) — `npm install -g vercel`
- A Firebase project on the **Blaze** plan (Cloud Functions requires it). Cost at
  ~1500 users is single-digit dollars/month.

---

## First-time setup

1. **Install dependencies**

   ```bash
   # Functions deps
   npm --prefix functions install
   ```

2. **Configure the frontend** — edit `web/js/firebase-config.js` and replace the
   `REPLACE_*` placeholders with your Firebase **web app** config
   (Firebase Console → Project settings → Your apps → Web app). These values are
   public and safe to commit; access is enforced by security rules, not secrecy.

3. **Configure the functions** — copy the templates and fill them in:

   ```bash
   cp functions/.env.example functions/.env          # non-secret runtime params
   # then create functions/.secret.local for the emulator:
   #   RESEND_API_KEY=re_xxxxxxxxxxxx
   ```

   `functions/.env` and `functions/.secret.local` are git-ignored. See the root
   [`.env.example`](.env.example) for a full explanation of every value.

---

## Run locally (Firebase Emulator Suite)

```bash
# Build the functions once (or use the watcher in a second terminal)
npm run build:functions
npm run watch:functions        # optional: rebuild on change

# Start emulators (Auth + Firestore + Functions + UI)
npm run emulators
#   Emulator UI:      http://127.0.0.1:4000
#   Functions health: http://127.0.0.1:5001/<project-id>/asia-south1/healthCheck

# Persist emulator data between runs (handy once seeding exists):
npm run emulators:persist
```

Serve the static frontend against the emulators (any static server works):

```bash
# from the repo root, e.g.
npx serve web        # or: python -m http.server --directory web 5500
```

The frontend auto-detects `localhost`/`127.0.0.1` and will connect to the
emulators (wired up in Phase 2).

---

## Seed data

The seed script uses the Admin SDK, so install root deps once:

```bash
npm install                # root deps (firebase-admin) for the seed script
```

Then, with the emulator running:

```bash
npm run seed               # idempotent: create-if-absent (leaves existing docs alone)
npm run seed:force         # overwrite seeded docs back to canonical values (resets
                           # engine-managed fields like lastSeq / points / strikes — dev only)
```

Seeds `config` (taskTypes, slots, platforms, settings) plus sample `committees`
and `team` rows with **placeholder** `@iimsirmaur.ac.in` emails you can replace.
Targets the emulator by default; pass `-- --prod` (with ADC /
`GOOGLE_APPLICATION_CREDENTIALS` + a real project) to seed a live project.

### Sample logins after seeding

| Sign in as | Role you'll see |
|---|---|
| `marketing@iimsirmaur.ac.in` | Committee — Marketing Club · MKTG · Permanent |
| `asha@iimsirmaur.ac.in` | Team member |
| `poc@iimsirmaur.ac.in` | Secretary (POC) |

> Emails are the document IDs for `committees`/`team` and must be **lowercase**
> (Google returns lowercased emails). Sign in via the Auth emulator's *Add new
> account* using one of the addresses above.

---

## Data model (Firestore)

| Collection | Key (doc id) | Notes |
|---|---|---|
| `committees/{email}` | login email | requesting bodies; `lastSeq` is engine-managed |
| `team/{email}` | member email | media team; `skills[]`, `points`, `strikes`, `active`, `campus` |
| `config/{taskTypes,slots,platforms,settings}` | fixed ids | pipeline + options + engine toggles + `secretaryEmails` |
| `requests/{auto}` | auto id | `refCode`, `status`, `campus`, `coordinatorEmail` are **engine-only** |
| `tasks/{auto}` | auto id | one per assigned/derived task; `coordinatorEmail` denormalized for coordinator reads |
| `activityLog/{auto}` | auto id | append-only audit; secretary-readable |

### Security rules (who can do what)

- **committees / team** — read your **own** doc (or any, if secretary); writes engine-only.
- **config** — readable by any signed-in user; writes engine-only.
- **requests** — requester reads **own**; coordinator reads events they coordinate; secretary reads all. Committee users **create** at status `New` (engine-only fields are blocked by a key allowlist). Secretary approve/reject writes only a `decision` field — the engine sets the real `status`.
- **tasks** — assignee reads **own**; coordinator reads their event's tasks; secretary all. Only two client writes: assignee marks **own** task `DONE` (from `CONFIRMED`/`LATE`), or coordinator/secretary sets `reassignTo`. Everything else (points, status, assignment) is engine-only.
- **activityLog** — secretary read; writes engine-only.

The Admin SDK (Cloud Functions, seed) bypasses rules, so the engine owns all
the protected fields.

### Composite indexes (`firestore.indexes.json`)

| Query (phase) | Index |
|---|---|
| My Requests (4) | `requests`: contactEmail ASC, createdAt DESC |
| Approvals (7) | `requests`: status ASC, createdAt DESC |
| My Tasks (6) | `tasks`: email ASC, deadline ASC |
| Coordinator (8) | `tasks`: coordinatorEmail ASC, deadline ASC |
| Deadline check (10) | `tasks`: status ASC, deadline ASC |

The emulator auto-creates indexes; deploy them to prod with
`npm run deploy:rules`.

---

## Deploy

### Frontend → Vercel

```bash
npm run deploy:web        # vercel deploy --prod  (serves /web as static)
```

Or connect the repo in the Vercel dashboard with **Output Directory = `web`** and
no build command (`vercel.json` already sets this).

### Functions + Rules → Firebase

```bash
firebase login
firebase use <your-project-id>

npm run deploy:rules      # firestore rules + indexes
npm run deploy:functions  # cloud functions (builds TS first)
```

**Email (Resend):** the email service uses Resend when `RESEND_API_KEY` is set,
otherwise it logs. For production set `RESEND_API_KEY` + `EMAIL_FROM` in
`functions/.env`, or bind a Secret Manager secret to the functions:

```bash
firebase functions:secrets:set RESEND_API_KEY
```

**Calendar (Google):** the calendar service uses the real Google Calendar API when
`CALENDAR_SERVICE_ACCOUNT_JSON` is set (free/busy + invites, impersonating each
member via domain-wide delegation), otherwise it logs. See the next section to
enable it. Both providers **auto-fall-back to logging stubs** when unset, so the
emulator and any unconfigured environment run identically with no code change.

---

## Google Calendar — domain-wide delegation

> Optional. Until `CALENDAR_SERVICE_ACCOUNT_JSON` is set the calendar layer logs
> instead of calling Google (assignment treats everyone as free). Do this to
> enable real free/busy checks and invites.

1. Create a **service account** in Google Cloud (no key needed for delegation if
   using workload identity, but a JSON key is simplest here) and note its
   **client ID**.
2. Enable the **Google Calendar API** for the project.
3. In the **Google Workspace Admin console** → Security → Access and data control
   → **API controls** → **Domain-wide delegation**, add the service account's
   client ID with these scopes:
   - `https://www.googleapis.com/auth/calendar`
   - `https://www.googleapis.com/auth/calendar.events`
   - `https://www.googleapis.com/auth/calendar.freebusy` *(free/busy checks)*
4. Set `GOOGLE_CALENDAR_DELEGATED_SUBJECT` to a real mailbox in the institute
   domain that the service account impersonates (e.g. an ops/automation user).

---

## Build phases

All feature phases are implemented and emulator-tested. Production deploy is the
remaining step.

- ✅ **Scaffold** — repo layout, configs, functions project, emulator, env.
- ✅ **Auth + shell** — Google sign-in, domain restriction, role resolution, committee tag, role-gated nav.
- ✅ **Data + rules + seed** — Firestore models, security rules, idempotent seed.
- ✅ **Requests** — New Request form + My Requests (list/detail), status read-only.
- ✅ **Engine core** — `onRequestCreated`: refCode (`ACRONYM_n`), campus, pipeline, assignment, <48h gate, confirm.
- ✅ **My Tasks** — My Tasks + `onTaskCompleted` → Event Covered / Ready To post.
- ✅ **Approvals** — secretary Approve/Reject (`onRequestDecided`) + Resend email (auto-fallback).
- ✅ **Reassign + manual assignment** — `onTaskReassign` + auto/manual assign callables + Assignments view.
- ✅ **Team admin** — `importTeamCsv` (secretary/admin) + Admin view.
- ✅ **Posting** — Mark-ready gate + `onReadyToPost` slot scheduler → Posted.
- ✅ **Deadlines** — `scheduledDeadlineCheck` + strikes.
- ✅ **Calendar (real)** — Google Calendar free/busy + invites (env auto-fallback).
- ✅ **Polish** — responsive pass (sticky app-bar). (activityLog dashboard intentionally skipped for v1.)
- ⏳ **Deploy** — Vercel (frontend) + Firebase Blaze (functions/rules) with production credentials.

---

## Conventions / guardrails

- Clients **never** write `requests.status` or `tasks.points`/`status` directly —
  the engine (Admin SDK) owns those. Clients act only through dedicated,
  rule-checked transitions.
- Role checks live in **security rules**, not just the UI.
- The institute domain, secretary list, and committee data are **config/data**,
  never hardcoded in logic.
