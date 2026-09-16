# Handover — Media Committee Portal

_Last updated: 2026-09-16. Read this first when picking up the project in a
new chat. For **what the system does and why**, see
[`docs/PRD.md`](docs/PRD.md) — **note: that doc and `docs/PIC.md` still
describe the old Express/Mongo/Vercel/Render stack and have not been updated
since the Django re-platform below; treat their architecture/ops sections as
stale, the workflow-rules sections as still accurate.**_

## What this is
Workflow portal for the **Media & Communications Committee at IIM Sirmaur**.
Committees/clubs/offices raise **requests** (Coverage or Post); the system
assigns the right people, enforces deadlines, balances workload, schedules
posts, and tracks points/strikes. Full workflow detail:
[`docs/PRD.md`](docs/PRD.md) §5.

## Stack (current, since 2026-09 — re-platformed off Express/Mongo/Vercel/Render)
- **Framework:** Django 5.2 LTS, server-rendered templates (`portal/ui/`) —
  no separate frontend build, no JS framework.
- **Database:** **SQLite**, one file (`portal/data/mcc.sqlite3`), WAL journal
  mode + `IMMEDIATE` transactions (needs Django 5.1+; see
  `portal/mccportal/settings.py`).
- **Auth:** `django-allauth` Google OAuth → Django session cookie. Institute
  domain gate enforced **server-side** in
  `portal/accounts/adapters.py::PortalSocialAccountAdapter.pre_social_login`
  (Google's `hd` param is only a UI convenience, not a real gate).
- **Hosting:** **self-hosted on one PC in the IIM Sirmaur computer lab**,
  reachable over the campus WiFi at `https://mcc.iimsirmaur.ac.in`. No cloud
  vendor, no card, no free-tier cold starts — and no access from off-campus
  (see Open items).
- **OS: Windows Server** (this flipped once already from an earlier "your
  choice of OS" answer that started as Ubuntu — if it ever comes up again,
  confirm before assuming; both `portal/deploy/setup.sh` (Linux) and
  `portal/deploy/setup.ps1` (Windows) are kept in the repo for this reason).
- **Process model on the lab PC:** Waitress (WSGI server — gunicorn doesn't
  run on Windows, it needs `os.fork()`) behind Caddy (TLS + reverse proxy),
  both wrapped as Windows services via NSSM. WhiteNoise serves static files
  from inside Django, so Caddy is purely TLS+proxy.
- **Email:** Django SMTP backend (auto-fallback to console logging if
  unconfigured).
- **Calendar:** `google-api-python-client`, **per-member OAuth consent, not a
  service account** — IT declined Workspace domain-wide delegation, so each
  team member instead grants Calendar access themselves via the same Google
  sign-in (`CALENDAR_ENABLED=1` adds the scope to the login flow; allauth
  stores each member's token; `services/calendar.py` looks it up by email
  when another user — coordinator, deadline cron — needs to check/book that
  member's calendar). Auto-fallback to logging when `CALENDAR_ENABLED` is
  off, and fails open per-member if someone hasn't (re)consented yet or their
  token won't refresh. `docs/PIC.md` §3 still describes the old
  service-account plan — stale, superseded by this.
- **Scheduled work:** Windows Scheduled Tasks (not GitHub Actions cron
  anymore — the machine has its own scheduler now) — hourly deadline sweep,
  nightly backup. See `portal/deploy/README-windows.md` §3.

This replaced the Node/Express + MongoDB Atlas build (itself a June-2026
migration off Firebase). `server/` and `web/` (the old codebases) have been
**deleted** — fully gone from this repo, not just unused. The workflow
*engine* logic (`portal/engine/`) ported over essentially unchanged from
`server/src/engine/`; what changed is the data layer, auth, and
request→view plumbing.

## Environment quirks (Windows)
- **Use the PowerShell tool for `python`/`manage.py`/`git`/`pip`** — same
  reasoning as before (Bash tool versions are older/less reliable for this
  project's tooling on this machine).
- `setup.ps1` needs a UTF-8 BOM and pure-ASCII string literals — Windows
  PowerShell 5.1 without a BOM reads the file via the system ANSI codepage
  and silently garbles non-ASCII characters (even inside comments-adjacent
  string literals) into parse errors that point at the wrong line. If you
  edit it, re-verify with
  `[System.Management.Automation.Language.Parser]::ParseFile()` and
  PSScriptAnalyzer before trusting it.
- Repo **is** a git repo, remote `WhiteWalker07/MCC_portal`. Working branch
  `develop`, PRs merge into `main`. `.github/workflows/auto-pr.yml`
  auto-opens/updates a develop→main PR on every push and best-effort
  auto-merges once CI (`.github/workflows/ci.yml`) passes — auto-merge needs
  a one-time repo setting; see [`portal/deploy/README.md`](portal/deploy/README.md).

## Key commands
```powershell
# From portal/, local dev
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py seed_real_data   # idempotent -- never resets points/strikes/availability
.venv\Scripts\python manage.py runserver        # http://localhost:8000 -- Google OAuth accepts localhost directly

# Tests
.venv\Scripts\python manage.py test engine      # the 12 ported smoke checks -- the real proof of behavior parity
.venv\Scripts\python manage.py test             # full suite (engine + ui, 46+ integration tests via real templates)
.venv\Scripts\python manage.py check --deploy   # expect no warnings once PORTAL_HTTPS=1

# Useful management commands
.venv\Scripts\python manage.py reset_points     # zero everyone's points, confirmation-prompted (--yes to skip)
.venv\Scripts\python manage.py backup_db        # sqlite3 hot backup, WAL-safe
.venv\Scripts\python manage.py deadline_check   # the hourly sweep, runnable manually
```
Full lab-PC deployment: [`portal/deploy/README.md`](portal/deploy/README.md)
(shared step) → [`portal/deploy/README-windows.md`](portal/deploy/README-windows.md)
(Windows-specific: services, HTTPS, day-to-day ops, backups, troubleshooting).

## Roles right now
See [`docs/PIC.md`](docs/PIC.md) for the full accountability map (stale on
infra/ops details, current on people). Admin/Secretary emails are **data**,
not code — `PortalSettings.admin_emails`/`secretary_emails`
(`portal/core/models.py`), editable via Django admin at `/admin/`. On login
these mirror onto Django's real `is_staff`/`is_superuser` automatically
(`accounts/adapters.py::_sync_staff_flags`) — never demotes the break-glass
superuser.

**Break-glass account**: one local Django superuser exists for `/admin/`
access if Google or the internet is down. It was deliberately created with a
distinct email (not the real admin's Google account) — allauth refuses to
auto-connect a Google sign-in to an existing account with a name collision,
as an anti-takeover safeguard. Don't rename it to match anyone's real email.

## ⚠️ Open items
1. **No real, publicly-trusted HTTPS certificate yet — this is the live
   blocker, see full state below.**
2. **`docs/PRD.md` and `docs/PIC.md` need updating** to reflect the Django
   re-platform (architecture tables, vendor-account rows → lab-PC
   ownership/DNS/cert/backup ownership). Deliberately deferred throughout the
   re-platform work; not yet requested.
3. **Campus-WiFi-only access is a known, accepted regression** from the old
   Vercel/Render setup — committees can't raise requests from off-campus
   anymore. Inherent to "self-hosted on one lab PC," not a bug. Mitigation
   would be an institute VPN reaching the lab LAN (an IT decision).
4. Same open items carried over from before the re-platform (still
   unresolved, `docs/PRD.md` §10): points timing-reference basis unconfirmed;
   social platform handlers are placeholders; no vertical heads formally
   appointed yet.

### HTTPS/DNS state as of 2026-09-16 (read this before touching Caddy/DNS again)
- **`mcc.iimsirmaur.ac.in` now has a real public A record** →
  `10.10.8.48` (confirmed via `nslookup -type=A ... 8.8.8.8`, a private LAN
  IP but publicly *resolvable*). This is new as of this session and is why
  the site suddenly started opening on every device, including Mac/Android
  that previously got `ERR_NAME_NOT_RESOLVED` (they weren't using the
  internal AD DNS server, so they got NXDOMAIN until this public record
  existed).
- **Caddy is still on `tls internal`** (self-signed local CA) — every device
  still gets the browser "not private" warning
  (`NET::ERR_CERT_AUTHORITY_INVALID`) unless it clicks through or has
  Caddy's root CA manually imported into its trust store. That's a
  per-device workaround, not a fix (steps for Windows/Mac/Android in this
  session's transcript if needed again).
- **The real fix (DNS-01 via CNAME delegation) is still blocked**: the
  domain's authoritative public DNS is confirmed to be **Google Cloud DNS**
  (`ns-cloud-e1.googledomains.com`), IT denied direct GCP service-account
  access, so the fallback plan is delegating just the ACME challenge:
  `_acme-challenge.mcc.iimsirmaur.ac.in CNAME <a zone you control>`. **As of
  this session, that CNAME does NOT exist** —
  `nslookup -type=CNAME _acme-challenge.mcc.iimsirmaur.ac.in 8.8.8.8` returns
  `Non-existent domain`, confirmed non-cached (real NXDOMAIN, not a stale
  answer). The user asked IT for *a* DNS change and got the main A record
  added instead/first — **unconfirmed whether IT was ever specifically asked
  for the `_acme-challenge` CNAME, or only for the main hostname record.**
  Next step: confirm that distinction with IT, get the delegation CNAME
  created, re-check with the same `nslookup` command, then follow
  [`portal/deploy/README-windows.md`](portal/deploy/README-windows.md) §1
  (build Caddy with `xcaddy` + the DNS provider plugin matching wherever the
  delegated zone lives, once known).
- **Confirmed dead end, don't re-litigate**: HTTP-01/TLS-ALPN-01
  (port-forwarding) can never work here regardless of what IT allows, because
  `10.10.8.48` is a private, non-routable address — Let's Encrypt's
  validators physically cannot reach it from the public internet. DNS-01 is
  the only viable path to a real cert.
- **Live Caddyfile** (`C:\mcc-portal-tools\Caddyfile` on the lab PC):
  ```
  mcc.iimsirmaur.ac.in {
  	tls internal
  	reverse_proxy 127.0.0.1:8000
  }
  ```
  Port 443 is confirmed `LISTENING`; Caddy's log shows a locally-issued cert
  obtained successfully. The one cosmetic Caddy error, "failed to install
  root certificate," is expected (Caddy runs as a non-interactive Windows
  service and can't auto-install into the OS trust store) and does not block
  anything remote.

## Architecture cheat-sheet
- **Engine owns the workflow, not views**: `portal/engine/` (points,
  pipeline, posting, assign, refcode, assignment, confirm, workflow) is pure
  logic ported ~1:1 from the old `server/src/engine/`, no ORM calls inside
  it — that's what keeps `portal/engine/tests/test_smoke.py` fast and
  meaningful (12/12, the direct descendant of the old `server/smoke.ts`).
- **Roles are resolved per-request**, not stored as a flag:
  `portal/core/roles.py` (`resolve_roles`, `can_assign`, `can_edit_venue`,
  `can_strike` — vertical-scoped for domain heads).
- **Lifecycle** (unchanged from the old system): New → (Pending for POC
  approval, if gated) → Request Accepted → Event Covered → Ready To post →
  Posted. (Rejected is terminal.)
- **Views/forms/URLs**: `portal/ui/views.py`, `ui/forms.py`, `ui/urls.py`.
  Includes newer additions this round: `request_edit_venue`, `issue_strike`,
  `remove_strike`, `remove_from_team` (deactivate, not hard-delete — explicit
  choice, keeps history).
- **Models**: `portal/core/models.py` — `PortalSettings`, `PointsScheme`,
  `TaskType`, `PostSlot`, `Platform`, `Committee`, `TeamMember`, `Request`,
  `Task`, `ActivityLog`.
- **Cross-vertical manual reassignment**: any member can be manually assigned
  from any vertical (auto-assignment still skill-matches) —
  `engine/assign.py`'s `eligible_members(require_skill=False)`.

## Verification habit
- After engine/core changes: PowerShell, from `portal/` →
  `.venv\Scripts\python manage.py test engine` (expect 12/12).
- After any change: `.venv\Scripts\python manage.py test` (expect all green)
  and `.venv\Scripts\python manage.py check`.
- Both were green as of this handover.

## Pointers
- **What the system does:** [`docs/PRD.md`](docs/PRD.md) (workflow rules
  current; architecture/ops sections stale, see top of this file)
- **Who owns what:** [`docs/PIC.md`](docs/PIC.md) (stale on infra, see above)
- **Stakeholder usage guide** (what each role can actually do, in plain
  language — for committees, team members, vertical heads, Secretary/POC,
  Admin): [`docs/USER-GUIDE.md`](docs/USER-GUIDE.md), current as of the
  Django re-platform
- **Deploy steps:** [`portal/deploy/README.md`](portal/deploy/README.md) →
  [`README-windows.md`](portal/deploy/README-windows.md) /
  [`README-linux.md`](portal/deploy/README-linux.md)
- Auto-memory index: `C:\Users\Agrim Kaundal\.claude\projects\F--MCC-Portal\memory\MEMORY.md`
