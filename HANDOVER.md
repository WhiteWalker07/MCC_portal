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
# Fresh start: delete ALL requests/tasks/sub-events, restart every committee's ID counter
# (SPT_1 ...), zero points AND strikes. Previews by default; --apply backs up first (file
# mcc-pre-reset-<time>.sqlite3, never pruned) and asks you to type RESET. Keeps roster,
# committees, settings, point scheme, meetings, leave requests and the activity log.
.venv\Scripts\python manage.py reset_event_data            # preview only
.venv\Scripts\python manage.py reset_event_data --apply    # do it
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
  `can_strike`, `can_change_event_time`, `can_edit_subevents`).
- **Lifecycle**: New → (Pending for POC approval, if gated — every Post,
  short-notice Coverage) → Request Accepted → Event Covered → Ready To post →
  Posted. (Rejected is terminal.)
- **Who does what (since 2026-09-29)** — enforced in `engine/assign.py`'s
  `is_base_eligible`, the single source of truth:
  - **2nd-years only supervise.** They are eligible for the **Task Supervisor**
    role and nothing else; every hands-on role, *including Event Coordinator*
    (no skill needed), goes to 1st-years. A 2nd-year has no blanket reassign
    power; heads keep theirs.
  - **Task Supervisor**: one per *Coverage* request (not Post), auto-picked
    (fewest open supervisions) in `process_new_request`, changeable **only by
    POC/Admin** (`can_assign(..., task_name=...)`). No points, no deadline,
    no Mark-done; it closes itself when the Event Coordinator marks done
    (`workflow._close_supervision`). Receives the `[Late]` emails.
    `Request.supervisor_email` mirrors `coordinator_email`.
  - **Post pipeline** = Content Writer + Graphic Designer (no Vetter).
    Scheduled automatically when both are done. The Graphic Designs head is
    emailed on approval (`confirm._notify_graphic_heads`) and can change the
    auto-pick. A Post accepted *before* this change still has its Vetter and
    finishes the old way.
  - **Two verticals** per member (`vertical` = primary, `secondary_vertical`).
    Auto-assignment orders primary → secondary → anyone with the skill
    (`assign._vertical_tier`); a head's scope is primary *or* secondary.
    **The seed command re-applies verticals/skills from `core/seed_data.py` on
    every deploy** — Admin-page edits to verticals last only until then.
  - **Strikes are yellow/red, manual only** (`yellow_strikes`/`red_strikes`).
    Heads give yellow only; POC/Admin either. The deadline sweep no longer
    strikes (tasks still go LATE + emailed) and strikes never affect
    assignment.
  - **Clubs can amend a Coverage request**: a single-day event's *time* (never
    date) until 24h before it starts (`can_change_event_time`); **sub-events**
    each have their own cutoff, **48h before that sub-event** (`SUBEVENT_CUTOFF_HOURS`,
    `roles.subevent_is_open`, enforced in `SubEventForm(enforce_lead=True)` and the
    add/edit/delete views — the new start is checked too, so one can't be dragged
    into the last 48h). See `engine/event_changes.py`. Existing calendar holds
    can't be moved (no event id is stored), so a time change adds new holds and
    tells people the old entry is stale.
  - **Single-day vs multi-day** (`Request.is_multiday`, migration `0007`): the
    request form has a toggle. Single-day = one venue + one start/end window,
    no sub-events at creation (they can be added later). Multi-day = only a first
    and last *date*: `RequestForm._clean_multiday` turns them into 00:00 on the
    first day to `MULTIDAY_END_TIME` (23:59) on the last, clears the venue, and its
    sub-events (the page's "+" button, plain JS cloning the formset's `empty_form`)
    must fall inside the dates (`SubEventForm(bounds=...)`). Multi-day events have
    **no time change, no venue edit, no calendar holds** and no calendar busy-check
    when staffing (`assign.py`, `assignment.py`, `notify.py` all skip on
    `is_multiday`; members still get the deadline reminder). Deadlines count from
    the end of the last day. An old client that posts no `event_kind` is treated as
    single-day.
- **Views/forms/URLs**: `portal/ui/views.py`, `ui/forms.py`, `ui/urls.py`.
  Includes newer additions this round: `request_edit_venue`, `issue_strike`,
  `remove_strike`, `remove_from_team` (deactivate, not hard-delete — explicit
  choice, keeps history).
- **Models**: `portal/core/models.py` — `PortalSettings`, `PointsScheme`,
  `TaskType`, `PostSlot`, `Platform`, `Committee`, `TeamMember`, `Request`,
  `SubEvent`, `Task`, `Meeting`, `MeetingInvite`, `LeaveRequest`, `ActivityLog`.
- **Cross-vertical manual reassignment**: any first-year can be manually
  assigned from any vertical (auto-assignment still skill-matches) —
  `engine/assign.py`'s `eligible_members(require_skill=False)`.
- **Shooters edit their own work (since 2026-09-29, second batch)**: the
  Photo/Video Editor is given to the same person as the Photographer/Videographer
  (skill ignored for the pairing). `Task.paired_task` (editor → shooter) records
  it; reassigning the shooter moves the editor too unless the editor is DONE or
  was deliberately given to someone else (`assignment._editors_following`, used by
  `perform_swap` and `override_proposed_assignee`). "Add a task" for an extra
  Photographer/Videographer also creates that person's editor
  (`ui/views.py::assignment_add`). If the shooter is UNFILLED the editor is
  auto-picked as before.
- **The Event Coordinator is timed**: due `COORDINATOR_GRACE_HOURS` (12) after the
  latest deadline of the request's other tasks (event end + 12h if none) —
  `pipeline.coordinator_deadline` / `refresh_coordinator_deadline` (re-run when a
  task is added and on a time change). The hourly sweep now marks it LATE. Points
  stay a flat 20 if on time (no early bonus); after the deadline they follow the
  late curve from that deadline (`points.overdue_multiplier`: −30%, then −10% per
  6h, floor 0). Coordinators already in flight keep their old event-end deadline.
- **Team meetings** (`Meeting`, `MeetingInvite`; `engine/meetings.py`,
  `ui/meeting_views.py`, `/meetings/`): the POC, Admin or any vertical head calls
  one for the whole team, chosen verticals (primary or secondary) or chosen
  people. Anyone "Out of work" or inactive can't be called. Invitees get one
  threaded `[Meeting]` email plus a Calendar hold (fail-open). Optional
  minutes-taker + venue booker: auto-picked first-year invitee with the fewest
  points, the caller can change it; a responsibility, no points. The caller or
  POC/Admin can edit/cancel **only before it starts** (edit/cancel emails say old
  calendar entries are stale) and mark attendance **only after it starts**
  (Present/Late/Absent/Excused). **Absent gives a yellow strike automatically**;
  changing it away from Absent takes that strike back
  (`MeetingInvite.strike_given` makes it idempotent and never below zero).

## Deploying the 2026-09-29 workflow update (lab PC)
Migrations `0002` (schema — **drops `TeamMember.strikes`**, resetting every
strike to 0 by design) and `0003` (data — blanks Event Coordinator's skill,
adds the Task Supervisor task type, stops Vetters being added by hand). In order:
1. `manage.py backup_db` — the strike column can't be recovered afterwards.
2. `git pull`, `pip install -r requirements.txt`, `manage.py migrate`.
3. `manage.py seed_real_data` — adds the 14 new first-years and the Graphic
   Designer / Task Supervisor task types. **Deploy the migration and the seed
   together**: with only 2nd-years on the MBA campus, MBA Coverage requests
   cannot be staffed until the first-years exist.
4. `Restart-Service MCCPortal`.
This was rehearsed on a copy of the dev database (migrate, then seed twice —
idempotent). Requests in flight are untouched; older Coverage requests simply
have no Task Supervisor (POC/Admin can add one from Assignments).

### Tenth batch (+ and Delete under each sub-event)
No migration. Two pages changed; both group by sub-event and put a **+** and **Delete** on it.
- **Assignments** (`views.assignment_detail`, `assignment_detail.html`): the table is split into
  `groups` (one per sub-event in time order, then "Whole event"; a request with no sub-events is
  one untitled group). Each group has a `<details>` "+ Add a task" with the per-type add forms,
  carrying a hidden `sub_event` (blank = whole event); `assignment_add` is unchanged. The
  context's `add_forms` is gone (now `groups[i]["add_forms"]`); the forms are hidden once the
  request is closed. **Delete** (`assignment_remove`) now works on any task of a Coverage request
  except the Event Coordinator and Task Supervisor, until it is DONE: `engine.assignment.remove_task`
  (was `remove_shooter`). A shooter takes its paired editing; an editor goes alone.
- **Team page** (`ui/allocation.py`, `request_allocate.html`, `_allocate_row.html`): every
  sub-event (and "Whole event") has a **+** that adds a row under it (JS, pre-selecting the
  sub-event; numbering still `extra-<i>-*`). Each proposed task has **Delete**, a plain submit
  (`name="drop"`), which re-renders the page; deleted keys are carried as hidden `dropped`
  inputs and shown struck through with **Restore** (`name="restore"`). `build_pipeline(...,
  dropped=)` leaves them out (a dropped shooter drops its editor, EC/Supervisor never) and the
  coordinator's deadline follows what is left; `propose_team` / `process_new_request` take the
  same `dropped`. Only the POC/Admin back-entry flow reads these fields.

### Ninth batch addendum (additional coordinator during back entry)
The team page has **+ Add an additional Event Coordinator** (Coverage only, under "Whole event"):
a submit button `add_co`, carried as a hidden `co_coordinator=1`; its row's Delete (`drop=Event
Coordinator+1`) turns it off. `build_pipeline(..., co_coordinator=True)` adds `Event Coordinator+1`
(`PipelineTask.additional`), staffed like any task so it can't equal the main coordinator
(`already_assigned`; a hand-picked duplicate is refused in `allocation.validate_picks`).
`process_new_request(..., co_coordinator=)` saves it with `Task.additional` and sets
`Request.co_coordinator_email`; if nobody is eligible it is skipped. After saving, the POC/Admin
manage it from Assignments as before.

### Ninth batch (additional Event Coordinator)
Migration `0010` is schema-only (`Request.co_coordinator_email`, `Task.additional`). Same deploy
order; no seed step. Existing requests are untouched (no co-coordinator).
- A Coverage request can have **one extra** Event Coordinator, added by POC/Admin only from
  Assignments (`views.assignment_add_coordinator` / `assignment_remove_coordinator`, engine
  `assignment.add_additional_coordinator` / `remove_additional_coordinator`, `CoordinatorError`).
  The first coordinator stays "main" (`Request.coordinator_email`); the extra is
  `Request.co_coordinator_email` and has their own task with `Task.additional=True`.
- Powers: everything the main one can. `Request.coordinator_emails` is the pair; `roles.resolve_roles`
  (`is_coordinator`), `can_assign`, `can_read_request`, `can_edit_venue`, the Assignments scope, Home,
  `[Late]` recipients and sub-event notices all use it. The two are different people (reassign
  dropdowns exclude both).
- Scoring: each is scored on their own task (same deadline, 12h after the last other task).
  Whoever shares the drive link and marks covered closes the other's task as DONE with 0 points
  (`workflow._close_other_coordinator`, logs `coordinator-closed`).
- Reassigning the additional coordinator changes only `co_coordinator_email`; the main one keeps the
  old behaviour (`assignment._commit_swap`). Tests: `ui/tests/test_co_coordinator.py`.

### Eighth batch (per-sub-event teams, extra tasks, remove, landing page)
Migration `0009` is schema-only (`Task.sub_event`). Same deploy order; no seed step.
Requests already in the database are untouched (their tasks have no sub-event).
- **Per-sub-event teams**: for a *multi-day* Coverage request that has sub-events,
  `pipeline.build_pipeline(..., sub_events=ordered_sub_events(...))` creates each ticked
  shoot role once **per sub-event**, each with its own paired editor, on that
  sub-event's own window (`PipelineTask.window`, deadline from `compute_deadline(...,
  window_end=sub.end)`, the Task's `event_start/end/venue` are the sub-event's). One
  Event Coordinator and Task Supervisor per request; the coordinator is due 12h after
  the *last* of these. A multi-day event with no sub-events, and every single-day event,
  keep one of each (sub-events there are information only, as before). `Task.sub_event`
  and `Task.label` ("Photographer — Opening"). Calendar: a sub-event task gets a hold
  for its window and is checked free for it; whole-event tasks of a multi-day event still
  get neither (`notify.py`, `assign.eligible_members(window=...)`, `validate_member(window=)`).
- **Task keys**: every pipeline task has an `ident` (`Photographer`, `Photographer@s1`
  = second sub-event, `Photographer+1` = first extra, `Photographer+1@s1`), used for the
  team page's `pick:<ident>` fields and `preferred`. Sub-events are numbered by
  `ordered_sub_events` so the preview and the real save agree. Tasks are now created one by
  one (`workflow.create_task_row`), not `bulk_create`; pairing is set at creation.
- **Team page** (`ui/allocation.py`, `request_allocate.html`): grouped by sub-event, plus
  "Additional tasks" (`extra-TOTAL`, `extra-<i>-task|sub|who`; any assignable type except
  the one-per-request EC/Supervisor; `process_new_request(..., extras=[(task, sub_index)])`).
- **After the fact** (`engine/event_changes.py`): a sub-event *added* to an accepted multi-day
  request is staffed at once (`staff_new_sub_event`: confirmed, emailed, added to the club's
  `roster`, club told); *edited* → its team's windows/deadlines move and they are emailed
  (`retime_sub_event`); *deleted* → its team is released and told (`release_sub_event`).
- **Assignments**: "Add a task" now offers repeatable types again (it used to hide a type
  already on the request, so a second Photographer could not be added from the page) and
  asks which sub-event; new **Remove** (`engine.assignment.remove_shooter`, view
  `assignment_remove`) for a Photographer/Videographer that isn't done, taking their
  paired editing with it, emailing them and the club, and recalculating the coordinator's
  deadline. Same authority as reassigning (`can_assign`).
  *Superseded in the tenth batch below.*
- **Landing page**: `ui/home_views.py` / `home.html`, `/`, first in the menu, sections by role.

### Seventh batch (choose the team before saving a back-entered request)
No migration. For `request_new?for=<club>` (POC/Admin back entry) the form now has a
second step, `ui/templates/request_allocate.html`: **Next** shows the team the engine
would pick for every task with a dropdown each, and nothing is saved or emailed until
**Save and send emails** (`step=confirm`; `step=edit` is Back). The form is carried
between steps as hidden fields (`allocation.carried_fields`) and re-validated each time;
hand picks are `pick:<Task Name>` fields, checked with `validate_member(require_skill=False)`
and applied through `process_new_request(..., preferred={task: TeamMember})`. The engine
side is `workflow.staff_pipeline` (extracted from `process_new_request`, read-only, shared
by the preview `propose_team` and the real save) — a hand pick counts as already on the
request so the automatic picks steer around it; an editor with no explicit pick follows its
shoot. The preview never allocates a reference code or books the calendar, and asks each
person's calendar once per window (`allocation._RememberingCalendar`). A club's own
request is unchanged (no second step, `pick:*` fields ignored). Back-entry tests must post
`step=confirm`.

### Sixth batch (random last-resort tie-break, reset_event_data)
No migration. **Auto-assignment ties are broken at random**, never alphabetically:
rank = (vertical tier, then fewest points); everyone still tied for first is equally
good and `engine.assign.pick_best` chooses one at random. Used by `choose_member`,
`choose_supervisor`, the hand-assign screens' "auto-pick" and the meeting
minutes-taker. Dropdown lists stay name-sorted (reading order only). It is switched
by `settings.PORTAL_RANDOM_TIE_BREAK` (on, except under `manage.py test`, so the
suite stays deterministic; `test_random_tiebreak.py` turns it on to test it). A new
test that needs a particular pick should set up distinct points/verticals rather
than rely on name order — and one that wants the random path must override the setting.
`reset_event_data` is documented under the commands list above.

### Fifth batch (request entered for a club, submit pop-up, points card)
Migration `0008` is schema-only (`Request.created_on_behalf_by`). Same deploy order; no seed step.
- **Manual creation**: `request_new` takes `?for=<committee id>` (POC/Admin only, 403
  otherwise; the id is read from the query string, never the POST body). The form
  then behaves as that committee's (`committee_name` locks the requester),
  `contact_email` is the club's login, `created_on_behalf_by` is the staff member,
  and `process_new_request(..., skip_approval=True)` accepts it at once, so the
  club gets the usual `[Accepted]` mail (with an "entered on your behalf" line).
- **Submit pop-up**: a successful submit is Post/Redirect/Get back to a blank
  `/requests/new/` (keeping `?for=`); the result rides in the session
  (`request_submitted`) and renders a native `<dialog>` once. Any existing test that
  expected a redirect to the request page now expects `request-new`.
- **Point scheme card**: labels/help/validation live in `PointSchemeForm` (not on the
  model, so no migration); the "what it pays" tables come from
  `engine.points.scheme_examples`, which calls the same functions that award points.
  The save logs a diff (`Early bonus (%): 30 -> 40`). The per-task `TaskType.points`
  number is **not used by the engine any more** (everything comes from the scheme);
  it is still shown in Django admin and editing it does nothing.

### Fourth batch (single-day / multi-day events)
Migration `0007` is schema-only (`Request.is_multiday`, default False, so every
existing request stays single-day). Same deploy order; no seed step. The sub-event
cutoff changed from "24h before the main event" to "48h before each sub-event" for
**all** Coverage requests, not just multi-day ones.

### Third batch (Out-of-work requests, tabbed Admin, MBA 1st-year meeting option)
Migration `0005` is schema-only (`LeaveRequest`). Same deploy order; no seed step.
- **Out of work** (`engine/leave.py`, `ui/leave_views.py`): a team member asks from
  their Profile (reason + from/to dates); the POC/Admin approves or declines on
  the Approvals page (email to the POC on request, to the member on decision). It
  changes nothing until approved *and* the start date arrives; open tasks are
  never moved (the POC sees them and reassigns by hand). `run_leave_sweep` starts
  and ends leaves by date and runs from the hourly `run_deadline_check` (no new
  scheduled task); the last day is inclusive, so they return the day after.
  Members can withdraw a pending/not-yet-started request or press "I'm back". The
  Admin roster switch still marks anyone out directly. All availability changes go
  through `switch_availability` (day banking + audit log).
- **Admin page** is tabs (Team | Committees | Setup); `ADMIN_TABS` in `ui/views.py`.
  The current tab is kept in the session so existing redirects return to it. Tests
  that read the page must request `?tab=...`. The Team tab's campus / year /
  vertical filters (GET params) use `dashboard.member_matches_filters`, the same
  rule as the Dashboard; the roster-row forms post the current URL as `next` and
  `_back_to_roster` returns there (only `/portal-admin/...` paths are honoured).
- **Meetings**: extra invite mode "MBA 1st year" = active, on-work members with
  `year == 1` and `campus == CAMPUS_MBA`.

### Second batch (meetings, timed coordinator, paired editors)
Migration `0004` is schema-only (adds `Task.paired_task`, `Meeting`,
`MeetingInvite`) — no data step, so nothing changes for requests in flight:
existing tasks have no pairing, and existing coordinators keep their event-end
deadline. Same order as above (`backup_db`, `git pull`, `migrate`, restart);
`seed_real_data` need not be re-run for it. Rehearsed on a scratch copy of the dev
database (migrate, then seed twice).

## Testing safely on a machine with live email
A dev machine whose `.env` has real SMTP credentials *and* a database holding the
real roster will email real people the moment you run workflow code against it
(creating or approving a request, changing a time…). Run such scripts and the
dev server with `$env:EMAIL_HOST=""` (an empty environment value beats `.env`
and falls back to the console backend), or use the test suite, which never sends.

## Verification habit
- After engine/core changes: PowerShell, from `portal/` →
  `.venv\Scripts\python manage.py test engine` (the ported smoke checks live
  in `test_smoke.py`; several were deliberately updated for the current
  workflow, so they no longer match `server/smoke.ts` byte for byte).
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
