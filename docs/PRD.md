# Product Requirements Document — MCC Portal

_Last updated: 2026-08-22 · Status: **Live** (core workflow shipped; see §10 for
open items) · Document owner: Admin (MCC Portal) · Related: [`PIC.md`](PIC.md),
[`HANDOVER.md`](../HANDOVER.md)_

## 1. Overview

The **Media & Communications Committee (MCC) Portal** is the workflow system
for how every other committee, club, and office at IIM Sirmaur gets its events
covered (photo/video) and its content posted to the institute's social
channels. Before this system, coverage and posting requests were coordinated
ad hoc — over chat, by memory, with no consistent record of who was assigned,
whether they delivered on time, or how workload was distributed across the
media team.

The portal turns that into a tracked pipeline: a requesting body submits a
request, the system assigns qualified, available team members automatically,
tracks the work to completion against a deadline, schedules the resulting
posts, and tallies points and strikes so contribution and reliability are
visible rather than anecdotal.

## 2. Goals

- **Every request gets a qualified, available person assigned automatically** — no manual routing for the common case.
- **Nothing silently slips.** Deadlines are enforced; a missed one is visible (task marked `LATE`, coordinator strikes).
- **Workload and reliability are measurable**, not word-of-mouth — points for delivered work, strikes for missed deadlines, per-member and per-vertical rollups.
- **Zero recurring cost.** The whole stack runs on free tiers (see §7) — no committee budget line for hosting.
- **Committees self-serve.** A club raising a Post request needs no back-and-forth with a human coordinator to get it scheduled.

## 3. Non-goals

- **Not a general content-creation tool.** The portal schedules and tracks work; it doesn't host, edit, or store media assets (`contentLinks` is a link out to Drive/wherever the content actually lives).
- **Not real-time collaboration.** State updates via polling (~20s) and refresh-on-action, not live sockets — a deliberate cost/complexity trade-off (see §8).
- **Not a general HR/attendance system.** The "out of work" toggle (§5.9) exists to keep the assignment engine accurate, not as a formal leave-management feature.
- **No native mobile app.** The web frontend is responsive and works on phones, but there's no App Store/Play Store presence.

## 4. Users & roles

| Role | Who | What they can do |
|---|---|---|
| **Requester** | Any `@iimsirmaur.ac.in` account | Raise a **Post** request. See their own requests' status. |
| **Committee** | A committee's shared login (e.g. `sapient@iimsirmaur.ac.in`) | Everything a Requester can, plus raise **Coverage** requests (events). |
| **Team member** | Media team roster | See and complete their own assigned tasks. |
| **Domain / vertical head** | A team member granted `domainHeadOf` | Everything a Team member can, plus manually assign/reassign tasks within their vertical. |
| **Secretary / POC** | `config/settings.secretaryEmails` | Approve/reject gated requests, import team CSV, set vertical heads, add committees, everything below admin. |
| **Admin** | `config/settings.adminEmails` | Everything, plus edit the point scheme. |

Current role holders are in [`PIC.md`](PIC.md#1-platform-ownership). Roles are
**data, not code** — resolved server-side from the database on every request
(`server/src/engine/serverRoles.ts`), so granting/revoking access never
requires a deploy.

## 5. Functional requirements

### 5.1 Request creation
- A **Post** request (any signed-in user): title, target platform(s), content
  links, notes. Auto-accepted (no approval gate) unless
  `settings.requireApprovalAlways` is on.
- A **Coverage** request (committees only): event name, start/end time, venue,
  roles needed (from the `requestable` task types), platforms to post to
  afterward. If the event starts **within 48 hours** of submission
  (`settings.slaHours`), it's held for Secretary/POC approval instead of
  auto-proceeding — short-notice events get a human sanity check.
- Every request gets a human-readable **reference code**: `ACRONYM_n` for a
  committee (its own counter), or `MEDIA_n` for a non-committee Post request
  (a shared counter). Both allocated atomically — never collide, never skip
  under concurrent submissions.

### 5.2 Automatic assignment
- On creation, the system builds the task **pipeline** for the request (e.g. a
  Coverage request needing a Photographer also gets a derived Photo Editor
  task; every request gets a Vetter before it can post).
- For each task, it picks a member who: has the required skill, isn't over
  the strike limit, matches the campus (`settings.campusStrict`), is marked
  **available** (not "out of work," §5.9), and — for at-event tasks — is free
  on their calendar at that time (if Calendar integration is configured).
- Selection balances load: among eligible members, it doesn't just pick the
  first match every time (see `engine/assign.ts`).

### 5.3 Approval gate
- Gated requests (short-notice Coverage, or `requireApprovalAlways`) sit at
  **"Pending for POC approval"**. The Secretary/POC approves (confirms the
  roster, sends notifications) or rejects (with a reason, emailed to the
  requester).

### 5.4 Task lifecycle
- States: `PROPOSED → CONFIRMED → DONE`, or `CONFIRMED → LATE` if the deadline
  passes first (still completable from `LATE`).
- **An event-bound task can't be marked done before its event starts** — you
  can't "cover" an event that hasn't happened yet. (Added 2026-08-22.)
- Completing a task applies a **timing modifier** to its points (§5.7) and
  advances the parent request: last deliverable done → **Event Covered**; for
  a Post request, the Vetter finishing moves it straight to scheduling.

### 5.5 Reassignment & manual assignment
- A coordinator, vertical head, secretary, or admin can **reassign** a task
  (auto-pick a replacement, or choose manually) or **add** an internal-only
  task type to a request (e.g. bring in a Video Editor that wasn't in the
  original pipeline). Points and notifications move with the reassignment.

### 5.6 Post scheduling
- Once a request is marked **Ready To post**, the system schedules one task
  per requested platform into the next available slot
  (`config/slots`, e.g. 11:00/14:00/17:00), load-balancing across each
  platform's handler(s), and creates a calendar hold + notification.

### 5.7 Points & strikes
- Base points per task type (Coordinator, domain task, Vetter — configurable,
  admin-only) get a **completion-timing modifier**: delivered well before the
  deadline earns a bonus, badly overdue takes a penalty, escalating in steps
  the further past the threshold. Full formula: `engine/points.ts`.
- Missing a deadline marks the task `LATE` and **strikes the coordinator**
  (optionally the assignee too — `settings.strikeAssigneeToo`). Too many
  strikes (`settings.strikeLimit`) makes a member ineligible for new
  assignments until it's addressed.

### 5.8 Deadline enforcement
- An hourly check (driven by GitHub Actions cron, since the free hosting tier
  has none of its own) finds overdue `CONFIRMED` tasks, marks them `LATE`,
  applies strikes, and notifies.

### 5.9 Availability (out of work / on break)
- Secretary/admin can mark a member **"Out of work"** (exams, travel,
  personal). While out, they're excluded from auto-assignment. The system
  tracks **cumulative days on-work vs. out** per member, live (not just at
  toggle time) — visible in the Admin view. (Added 2026-08-22.)

### 5.10 Admin operations
- **Team roster**: bulk CSV import/update (name, email, vertical, year,
  skills, campus, phone, active) — re-importing updates existing members
  without resetting their points/strikes.
- **Committees**: add or update a requesting body (name, login email,
  acronym, type, campus) from the Admin view — no script needed.
- **Vertical heads**: appoint one head per vertical; appointing a new one
  automatically replaces the previous.
- **Point scheme**: admin-only editor for every constant in §5.7.

### 5.11 Dashboard
- Fairness/usage aggregates for admin/secretary: active members, total
  points, on-time completion rate, average turnaround, a leaderboard, a
  by-vertical rollup, and requests-by-status — filterable by time window,
  campus, year, and vertical.

### 5.12 Notifications
- **Email** (Resend) on: assignment, approval-needed, accepted, rejected,
  scheduled post, deadline missed.
- **Calendar** (Google, optional): a hold on the assignee's calendar for
  at-event tasks and scheduled posts, plus a reminder for non-event
  deadlines. Both integrations **auto-fall back to logging** when their API
  keys aren't configured, so the system runs identically in dev and
  production regardless of whether email/calendar are wired up yet.

## 6. Key workflows (at a glance)

```
Coverage request  ─▶ (gate if <48h) ─▶ auto-assign roster ─▶ event happens
                                                              ─▶ deliverables done ─▶ Event Covered
                                                              ─▶ Vetter done ─▶ Ready To post
                                                              ─▶ scheduled per platform ─▶ Posted

Post request      ─▶ auto-accept ─▶ Vetter assigned ─▶ Vetter done
                                                       ─▶ scheduled per platform ─▶ Posted
```

## 7. System architecture (summary)

| Layer | Choice | Why |
|---|---|---|
| Frontend | Vanilla HTML/CSS/ES-modules, hash-routed SPA shell | No framework build step; a non-specialist successor can read and edit it directly. |
| Hosting (frontend) | Vercel (static) | Free, zero-config for a static site. |
| API | Node/Express (TypeScript), on Render | Free web-service tier; no card required. |
| Database | MongoDB Atlas (M0, free) | Document model fits the domain; free tier needs no card. |
| Auth | Google OAuth (`passport-google-oauth20`) + a signed JWT the client holds | Domain-restricted sign-in; token-based (not cookie-based) so it works cross-origin on every browser, including Safari/iOS. |
| Email | Resend | Generous free tier; simple API. |
| Calendar | Google Calendar API (service account, domain-wide delegation) | Institute already runs Google Workspace. |
| Scheduled jobs | GitHub Actions cron → an HTTP endpoint | Render's free tier has no built-in cron. |

Full technical detail: [`HANDOVER.md`](../HANDOVER.md) and
[`server/README.md`](../server/README.md).

## 8. Non-functional requirements

- **Cost:** $0/month on current usage — every piece of the stack (Render,
  Vercel, Atlas M0, Resend free tier, GitHub Actions) is free-tier, no card on
  file anywhere.
- **Access control:** enforced server-side on every route (never trust the
  client) — see `requireAuth` / `attachRoles` / `canAssign` in `server/src/auth/`
  and `server/src/engine/serverRoles.ts`.
- **Availability:** Render's free tier sleeps after ~15 minutes idle; a cold
  request takes ~50s to wake it. Acceptable for this audience's traffic
  pattern; a paid tier removes this if it becomes a problem.
- **Freshness:** views poll every ~20s and refresh immediately after any
  action the user takes — not real-time, but never stale for more than the
  poll interval.
- **Browser support:** must work on Safari/iOS (a large share of student
  devices) — this drove the switch from cookie-based to token-based sessions
  (2026-08-22), since Safari blocks third-party cookies outright.

## 9. Data model (summary)

| Collection | Keyed by | Holds |
|---|---|---|
| `committees` | login email | Requesting bodies — name, acronym, type, campus, request-counter. |
| `team` | member email | Roster — skills, vertical, points, strikes, availability. |
| `requests` | generated id | One per Coverage/Post submission — status, roster, timestamps. |
| `tasks` | generated id | One per assigned (or derived) unit of work on a request. |
| `config` | fixed keys | `settings`, `taskTypes`, `slots`, `platforms`, `points` — every tunable. |
| `activityLog` | generated id | Append-only audit trail. |

Full field-level detail: `server/src/types.ts`.

## 10. Open questions / known gaps

1. **Points timing reference is unconfirmed.** The completion-timing bonus/
   penalty (§5.7) is currently measured from event-end (Coverage) or
   request-creation (Post) to completion. If the intent was *relative to each
   task's own deadline* instead, it's a one-line change
   (`server/src/services/workflow.ts`, `completeTask`). **Needs a decision
   from the Secretary/POC or Admin.**
2. **Social platform handlers are placeholders** — see
   [`PIC.md §5`](PIC.md#5-social-platform-handlers). Needs real owners named.
3. **No vertical heads formally appointed yet** — the assignment/manual-assign
   privilege that comes with it isn't granted to anyone. See
   [`PIC.md §4`](PIC.md#4-vertical-leadership).
4. **Single account holds every vendor login** — see
   [`PIC.md §3`](PIC.md#3-infrastructure--vendor-accounts). Recommended fix:
   add a second owner before the current admin's term ends.
5. **Real-data load must be (re-)run after any roster/committee edit** to
   `server/src/admin/realData.ts` — it's not automatic on deploy.

## 11. Success metrics

- **Coverage:** % of events with a confirmed roster before they start (target: ~100% for requests submitted with normal notice).
- **Reliability:** % of tasks completed before their deadline (visible on the Dashboard's on-time rate).
- **Turnaround:** average time from assignment to completion, trending down.
- **Adoption:** number of distinct committees actively submitting requests each term (of the 41 loaded).
- **Fairness:** points/workload spread across the team not concentrated in a handful of members (Dashboard leaderboard + by-vertical view).

## 12. Change log (high-level)

- **2026-06:** Migrated the backend off Firebase (which required a paid plan
  for Cloud Functions) to a fully free Express + MongoDB stack on Render,
  keeping the existing frontend and all workflow logic intact.
- **2026-08:** Loaded the real committee (41) and media-team (23) rosters;
  added committee management and an availability/"out of work" tracker to the
  Admin view; added the event-hasn't-started completion guard; switched
  session auth from cookies to a client-held token to fix Safari/iOS sign-in.
