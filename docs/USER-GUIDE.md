# MCC Portal — Usage Guidelines by Role

_Last updated: 2026-09-16. This is a practical "how do I..." guide for the
people who actually use the portal day to day. For the underlying rules the
system enforces, see [`PRD.md`](PRD.md) §5. For who currently holds which
role, see [`PIC.md`](PIC.md)._

**Where to go**: `https://mcc.iimsirmaur.ac.in` — reachable only on the
**campus WiFi** (the portal runs on a machine in the computer lab, not the
public internet). Sign in with your `@iimsirmaur.ac.in` Google account. If
your browser shows a "connection is not private" warning, that's expected
for now (see the note at the very bottom) — click **Advanced → Proceed** the
first time; it won't ask again on that device.

---

## Everyone (any signed-in @iimsirmaur.ac.in account)

You can always:
- Raise a **Post** request (`New Request` → Post) — a request to have
  something published to the institute's social channels. No approval
  needed unless the Secretary/POC has turned on mandatory approval for
  everyone.
- See the status of requests you've raised (`My Requests`).
- If you're on the media team roster, see and complete tasks assigned to
  you (`My Tasks`) — see the **Team member** section below.

---

## Committees / clubs / offices (shared login, e.g. `sapient@iimsirmaur.ac.in`)

Everything above, plus:

- **Raise a Coverage request** (`New Request` → Coverage) for an event:
  name, start/end time, venue, which roles you need covered (photographer,
  videographer, etc.), and which platforms to post to afterward.
- **If your event starts within 48 hours of when you submit**, the request
  is held for Secretary/POC approval instead of auto-proceeding — a
  short-notice sanity check, not a rejection. You'll be notified either way.
- **Edit the venue after submitting**, if it changes at the last minute —
  open the request from `My Requests` → **Edit venue**. You don't need to
  contact anyone to fix a last-minute room change.
- Every request gets a reference code (e.g. `SAPIENT_7`) — use it when
  following up so people don't have to search by event name.

---

## Media team members

Everything in "Everyone," plus:

- **`My Tasks`** shows everything assigned to you. Mark a task done from
  there once you've delivered.
- **You can't mark an event-bound task done before the event has actually
  started** — the system blocks it on purpose (you can't cover something
  that hasn't happened yet).
- **Completing on time earns bonus points; badly overdue completion loses
  points** — see the point scheme (Admin can show you the exact numbers if
  you want them).
- **If you're going to be unavailable** (exams, travel), tell your vertical
  head or the Secretary/POC so they can mark you "Out of work" — you'll be
  skipped for new auto-assignments until it's turned back off. This doesn't
  affect points or strikes, only future assignments.
- **Strikes**: you get one automatically if a task you're the coordinator on
  goes overdue. You can also be struck manually by a domain head (your own
  vertical only) or the Secretary/POC (anyone) for a reliability issue that
  isn't caught by the deadline system. Too many strikes makes you ineligible
  for new auto-assignments until it's addressed — talk to your vertical head
  or the Secretary/POC if you think a strike was wrong; only they (or Admin)
  can remove one.

### If you're coordinating a request (you raised or were assigned to run one)

- **`Assignments`** shows the tasks on requests you coordinate. You can
  **reassign** any of them — auto-pick a replacement or choose manually —
  and **add** an extra task type that wasn't in the original plan (e.g.
  bringing in a Video Editor after the fact).
- Once every deliverable is in, mark the request **Ready to post** — the
  system schedules the actual posts for you.

### If you're a vertical/domain head

Everything above, but **for any request, not just ones you coordinate** —
you can assign, reassign, and manually strike within your own vertical
specifically (not other verticals). You were appointed this by the
Secretary/POC or Admin (`Portal Admin → Vertical heads`); if you think you
should have this and don't, ask them.

### If you're a second-year member

Second-years can assign/reassign on **any** request, in any vertical — not
scoped to a single vertical the way a domain head is. This is a standing
trust level, not something you request per-task.

---

## Secretary / POC

Everything above, plus:

- **`Approvals`** — decide on gated requests (short-notice Coverage,
  or anything caught by "require approval always" if that's turned on).
  Approving confirms the roster and sends notifications; rejecting needs a
  reason, which gets emailed to the requester.
- **Strikes, team-wide**: issue or remove a strike for **any** member (not
  vertical-scoped, unlike a domain head).
- **Deactivate ("kick") a team member** (`Portal Admin` → Remove from team)
  — this is a **deactivation, not a delete**: their point/strike/task
  history is kept, and it's reversible. Use this for someone leaving the
  team or a serious reliability problem, not as a substitute for a strike.
- **Team roster (CSV import)** (`Portal Admin` → Team import) — bulk
  add/update members: name, email, vertical, year, skills, campus, phone,
  active. Re-importing updates existing people without resetting anyone's
  points or strikes, so it's safe to re-run after every roster change (add
  the new people, don't try to hand-edit the whole file).
- **Committees** (`Portal Admin` → Committees) — add or update a requesting
  body (name, login email, acronym, type, campus) without needing anyone
  to touch code or the database directly.
- **Vertical heads** (`Portal Admin` → Vertical heads) — appoint one head
  per vertical; appointing someone new automatically replaces whoever held
  it before.
- **Availability** (`Portal Admin` → Availability) — mark anyone out of
  work / back on work; see §Media team members above for what this does.
- **`Dashboard`** — fairness/usage view: active members, total points,
  on-time completion rate, average turnaround, a leaderboard, a by-vertical
  breakdown, requests-by-status. Filterable by time window, campus, year,
  vertical. Use this at the end of a term to see whether workload is
  actually spread evenly, not just assumed to be.

---

## Admin

Everything above, plus:

- **Point scheme** (`Portal Admin` → Points) — the only role that can
  change the point values themselves (base points per task type, the
  timing bonus/penalty curve, the strike limit). Changing this affects all
  *future* completions; it doesn't retroactively re-score past work.
- **Reset everyone's points to zero** — a full-team reset (e.g. start of a
  new term). This does **not** touch strikes, availability history, or task
  records — it only zeroes the points counter. There is no UI button for
  this; it's a one-off run by whoever has access to the lab PC (see
  [`HANDOVER.md`](../HANDOVER.md) → `manage.py reset_points`). If you need
  this done, ask whoever currently holds lab-PC/deploy access
  ([`PIC.md`](PIC.md)).
- **Django admin** (`/admin/`, a separate, more technical screen) — direct
  read/write access to every record in the system, including the raw
  activity log. Reserved for troubleshooting; the `Portal Admin` screens
  above cover every routine operation, so you shouldn't need `/admin/` for
  day-to-day work.
- **The one non-Google login** ("break-glass" account) exists purely so
  someone can reach `/admin/` if Google sign-in or the internet is ever
  down. Its password is held by whoever currently owns lab-PC access
  ([`PIC.md`](PIC.md)) — it is not, and should never become, a normal
  day-to-day login.

---

## Quick reference — "who do I ask"

| I need to... | Ask |
|---|---|
| Fix a wrong venue on my request | Nobody — edit it yourself from `My Requests` |
| Get marked out of work / back on work | My vertical head, or the Secretary/POC |
| Dispute a strike | My vertical head (my own vertical) or the Secretary/POC (any) |
| Add/remove a committee, or update the team roster | Secretary/POC |
| Get appointed a vertical head | Secretary/POC or Admin |
| Change the point values themselves | Admin |
| Reset everyone's points (new term) | Whoever holds lab-PC access — see `PIC.md` |
| The site won't load at all | Check you're on campus WiFi first; if it still fails, see `PIC.md` for who owns the lab PC |

---

## A note on the browser warning

The portal currently uses a self-signed certificate while the institute's
DNS team finishes setting up a properly trusted one, so your browser may
show a "your connection is not private" page the first time you visit on a
given device. This is expected, not a sign anything is wrong or insecure on
this network — click through once (**Advanced → Proceed**) and it won't
reappear on that device. This will go away once the real certificate is in
place; no action needed from end users in the meantime.
