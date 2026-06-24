# MCC Portal — Server (Express + MongoDB)

Standalone backend that replaces the Firebase Cloud Functions / Firestore / Firebase
Auth stack so the whole portal can run **free, with no credit card**: Express on
**Render** (free web service), data in **MongoDB Atlas M0** (free), Google sign-in via
**Passport** (`google-oauth20`), email via **Resend**, calendar via **googleapis**. The
former Firestore triggers are now plain functions called synchronously from the routes
(`src/services/workflow.ts`); the hourly scheduled job is an HTTP endpoint driven by a
free external cron.

## Layout
```
src/
  index.ts            app bootstrap (cors, session, passport, routers)
  db.ts               Mongo client + col.* accessors
  config.ts           config reads (settings/taskTypes/platforms/slots/points)
  auth/               passport strategy, session (connect-mongo), middleware, /api/me builder
  engine/             ported workflow logic (points, pipeline, assign, refcode, confirm, assignment, serverRoles)
  services/           email (Resend), calendar (googleapis), workflow (the former triggers)
  routes/             auth, config, requests, tasks, assignments, team, dashboard, cron
  lib/                http helpers, activity log, doc helpers
smoke.ts              in-memory integration test (npx tsx smoke.ts)
```

## Local development
1. `npm install`
2. Copy `.env.example` → `.env` and fill it in. For local dev set `COOKIE_INSECURE=1`
   (so the session cookie works over plain http on localhost) and
   `CLIENT_ORIGIN=http://localhost:3000`.
   - Mongo: a local `mongod` (`mongodb://127.0.0.1:27017`) or your Atlas URI.
3. `npm run seed` (or `npm run seed:force`) — seeds config/committees/team into Mongo.
4. `npm run dev` — server on `http://localhost:8080`.
5. Serve the frontend from `../web` separately (e.g. `npx serve ../web -l 3000`) with
   `web/js/config.js` auto-targeting `http://localhost:8080` on localhost.

Verify: `npm run typecheck` (tsc, exit 0) and `npx tsx smoke.ts` (12 engine checks).

## Deployment (free, no card)

### 1. MongoDB Atlas
Create a free **M0** cluster → Database Access (user/password) → Network Access
`0.0.0.0/0` → copy the `MONGODB_URI`.

### 2. Google OAuth
Google Cloud Console → **OAuth consent screen** (Internal, the `iimsirmaur.ac.in`
Workspace) → **Credentials → Create OAuth client ID → Web application**:
- Authorized JavaScript origins: your Vercel URL (e.g. `https://mcc-portal.vercel.app`)
- Authorized redirect URI: `https://<render-service>.onrender.com/auth/google/callback`

Copy the client id + secret.

### 3. Render (API)
New **Web Service** from the GitHub repo (the repo must be pushed to GitHub first):
- Root directory: `server`
- Build command: `npm install && npm run build`
- Start command: `npm start`
- Environment variables:
  | var | value |
  |---|---|
  | `MONGODB_URI` | from Atlas |
  | `MONGODB_DB` | `mcc_portal` |
  | `SESSION_SECRET` | long random string |
  | `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | from step 2 |
  | `OAUTH_CALLBACK_URL` | `https://<render-service>.onrender.com/auth/google/callback` |
  | `CLIENT_ORIGIN` | your Vercel URL |
  | `ALLOWED_DOMAINS` | `iimsirmaur.ac.in` |
  | `CRON_SECRET` | long random string |
  | `RESEND_API_KEY`, `EMAIL_FROM` | optional (else email logs to stdout) |
  | `CALENDAR_SERVICE_ACCOUNT_JSON` | optional (else calendar logs) |

  Do **not** set `COOKIE_INSECURE` in production (cookies must be `SameSite=None; Secure`).
  Free instances sleep after ~15 min idle (~50s cold start).

### 4. Seed / load data
With the Atlas `MONGODB_URI` in `server/.env` (or exported), from `server/`:
- `npm run seed` — sample config/committees/team (edit `server/scripts/seed-mongo.mjs` first), or
- `npm run load-data` — the real committees + media team (`server/scripts/load-real-data.mjs`;
  add `-- --clean` to also remove the demo rows). Also sets the admin + POC.
- `npm run set-roles` — only (re)apply the admin/POC emails + init availability fields.
- `npm run db-check` — diagnose whether `server/.env` can reach MongoDB.

Can't reach Atlas from your network (campus firewall etc.)? Run the loader on the
server instead — same loader, guarded by `CRON_SECRET`:
```
curl -X POST -H "x-cron-secret: <CRON_SECRET>" \
  "https://<your-service>.onrender.com/api/admin/load-data?clean=1"
```

### 5. Vercel (frontend)
Set `web/js/config.js` → `PROD_API_BASE` to the Render URL, then redeploy `web/`
(static, no build — `vercel.json` already serves it).

### 6. Cron
`.github/workflows/deadline.yml` pings `/api/cron/deadline-check` hourly. Add repo
secrets `API_BASE_URL` (Render URL) and `CRON_SECRET` (same as the server's).

## Cross-origin checklist (the usual failure point)
- Server: `app.set('trust proxy', 1)`, `cors({ origin: CLIENT_ORIGIN, credentials: true })`,
  cookie `SameSite=None; Secure` (prod).
- Client: every request uses `credentials: 'include'` (handled in `web/js/api.js`).
- `CLIENT_ORIGIN` must match the Vercel origin exactly (scheme + host, no trailing slash).
