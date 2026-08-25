# MCC Portal — Django (server-rendered, SQLite, self-hosted)

This is the re-platformed portal: **Django + SQLite**, server-rendered
templates (no SPA, no JS build), meant to run on **one PC in the computer lab**
and be reached over the **college WiFi**. It replaces the Express/MongoDB/
Vercel stack (and, before that, the original Firebase build) — see the root
[`README.md`](../README.md)'s change history for the full lineage.

Full context: [`../docs/PRD.md`](../docs/PRD.md) (what the system does),
[`../docs/PIC.md`](../docs/PIC.md) (who owns what),
[`../HANDOVER.md`](../HANDOVER.md) (operating this system generally).

## Why this shape

- **Django templates, not the old SPA.** Frontend and backend are now one
  process on one origin, which is also why the old JWT-in-localStorage
  workaround for Safari/iOS cookie blocking is gone — a plain session cookie
  works everywhere once there's no cross-origin hop.
- **SQLite, not MongoDB Atlas.** One file (`data/mcc.sqlite3`), configured for
  WAL mode + `IMMEDIATE` transactions (see `mccportal/settings.py`) so
  concurrent submissions can't collide or corrupt a reference-code counter.
- **One machine, not five vendor accounts.** No Render, Vercel, Atlas, or
  Resend — see `../docs/PIC.md` §3 for what that used to require.

## Layout

```
mccportal/        settings.py, urls.py, wsgi.py
core/              models, roles, config accessors, activity log, seed data,
                   management commands (seed_real_data, deadline_check, backup_db)
engine/            ported workflow logic (points, pipeline, assign, refcode,
                   confirm, assignment, workflow) + engine/tests (the ported smoke checks)
accounts/          Google sign-in domain gate + role sync (allauth adapters)
services/          email.py, calendar.py — auto-fallback to logging when unconfigured
ui/                views, forms, dashboard aggregates, templates
static/            styles.css + favicon.svg (copied from web/assets, unchanged)
data/              mcc.sqlite3, secret_key.txt, portal.log, backups/ — gitignored
```

## Local development

1. `python -m venv .venv` then activate it (PowerShell:
   `.\.venv\Scripts\Activate.ps1`) — **use the PowerShell tool for
   python/manage.py commands**, per this repo's usual Windows quirk
   (`../HANDOVER.md`'s environment note).
2. `pip install -r requirements.txt` (add `-r requirements-calendar.txt`
   instead if you're wiring up Google Calendar).
3. Copy `.env.example` → `.env` and fill in `GOOGLE_CLIENT_ID` /
   `GOOGLE_CLIENT_SECRET` if you want to test real sign-in locally — Google
   **does** accept `http://localhost:8000/accounts/google/login/callback/` as
   a redirect URI, so this works without IT provisioning anything.
4. `python manage.py migrate`
5. `python manage.py seed_real_data` — loads the 41 committees + 23 team
   members from `core/seed_data.py` (safe to re-run; never resets points,
   strikes, or availability history).
6. `python manage.py createsuperuser` — a break-glass account for `/admin/`,
   independent of Google. **Give it an email that no real Google account will
   ever use** (e.g. `breakglass-admin@mcc-portal.local`) — using a real
   person's institute address here will collide with their actual Google
   sign-in later: allauth finds a local user already on that email with no
   verified link to the Google account, refuses to auto-connect them (a
   deliberate anti-account-takeover safeguard), and dumps them on a manual
   "complete signup" page instead of just logging them in. Keep exactly one
   break-glass account; document who holds its password in `../docs/PIC.md`.
7. `python manage.py runserver` — the portal on `http://localhost:8000`.

Verify: `python manage.py test` (expect the 12 ported engine checks + the view
integration tests, all green) and `python manage.py check --deploy`.

## Deploying on the lab PC

The lab PC is a **blank Ubuntu Server 24.04 LTS** machine (chosen over Windows
specifically because it's a dedicated box with no other use — native
`systemd` for service supervision and scheduled jobs beats fighting Windows
Update reboots and NSSM on an unattended server).

**[`deploy/setup.sh`](deploy/setup.sh) is the actual setup** — one script that
checks and installs every dependency (Python, Caddy, `ufw`, …), sets the app
up as a `systemd` service with `gunicorn`, wires the hourly deadline sweep and
nightly backup as `systemd` timers, configures the firewall, and disables
sleep. Safe to re-run.

```bash
git clone https://github.com/WhiteWalker07/MCC_portal.git
cd MCC_portal/portal
cp .env.example .env && nano .env    # GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET at minimum
sudo bash deploy/setup.sh
```

**[`deploy/README.md`](deploy/README.md)** covers everything that script
can't do for you — the DNS-01 HTTPS setup (needs IT to name a DNS provider
first), registering the real Google OAuth redirect URI, day-to-day operations
(deploying an update, checking logs), and testing a backup restore. Whoever
runs `setup.sh` and picks the DNS-01 provider becomes that certificate's named
owner in `../docs/PIC.md` §3 — a lapsed cert silently breaks sign-in.

## Roles are still data, not code

Admin and secretary rights live in `PortalSettings.admin_emails` /
`secretary_emails` (Django admin, or re-run `seed_real_data` after editing
`core/seed_data.py`) — never hard-coded, per `../docs/PRD.md` §4. Signing in
mirrors those lists onto `is_staff` / `is_superuser` automatically
(`accounts/adapters.py`), so Django admin access follows the same source of
truth without a second list to maintain.
