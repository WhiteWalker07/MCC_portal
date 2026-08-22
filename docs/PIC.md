# Person(s) In Charge — Media & Communications Committee Portal

_Last updated: 2026-08-22 · Owner of this document: Admin (MCC Portal)_

This is the accountability map for the MCC Portal: who owns the system, who leads
each production vertical, who holds the keys to each vendor account, and who to
escalate to when something breaks. It is a companion to
[`HANDOVER.md`](../HANDOVER.md) (how to operate/maintain the system) and
[`docs/PRD.md`](PRD.md) (what the system does and why).

Source of truth for the roster below is
[`server/src/admin/realData.ts`](../server/src/admin/realData.ts) — re-run
`npm run load-data` in `server/` after editing it to push changes to the
database, or use the Admin view once you're signed in.

---

## 1. Platform ownership

| Role | Person | Login email | Responsibility |
|---|---|---|---|
| **Admin** | Agrim Kaundal | `mbatm25010@iimsirmaur.ac.in` | Final authority on the portal: point scheme, vertical heads, committee roster, all secretary powers. Holds/controls the vendor accounts (§3). |
| **Secretary / POC** | Kamalasegaran A | `mba25114@iimsirmaur.ac.in` | Approves/rejects gated requests (events <48h out), team CSV import, vertical-head assignment, committee add/edit. Day-to-day operational owner. |
| **Maintainer / developer** | Agrim Kaundal | `mbatm25010@iimsirmaur.ac.in` | Owns the codebase, deployments, and this documentation set. See [`HANDOVER.md`](../HANDOVER.md) for the technical handover. |

**How admin/secretary rights are granted:** these two emails are written into
`config/settings.adminEmails` / `secretaryEmails` in the database — not hardcoded
in the app. Changing who holds these roles is a data change (Admin view, or
`server/scripts/set-roles.mjs`), not a code change.

## 2. Escalation path

1. **App/workflow question** (how do I request coverage, why wasn't I assigned) → your **vertical lead** (§3) or the **Secretary/POC**.
2. **A request/task is stuck or mis-assigned** → **Secretary/POC**, who can reassign via the Assignments view.
3. **The site is down, sign-in is broken, or data looks wrong** → **Admin/Maintainer**.
4. **Vendor account access needed** (someone else must be able to deploy or manage the database) → **Admin**, who provisions a second account per §3's recommendation.

## 3. Infrastructure & vendor accounts

Whoever holds these logins can take the portal offline or change its data
outright — treat this table as the actual keys to the building. All currently
sit with one person, which is a **named bus-factor risk** (see the action item
below).

| Service | Used for | Account holder | Notes |
|---|---|---|---|
| **GitHub** — `WhiteWalker07/MCC_portal` | Source code, CI (deadline cron), deploy trigger | Agrim Kaundal | Both Render and Vercel deploy from the `main` branch here. |
| **Render** | Hosts the API (`server/`) | Agrim Kaundal | Holds `SESSION_SECRET`, `CRON_SECRET`, DB URI, OAuth secret — the full secret set. |
| **MongoDB Atlas** (M0, free) | The database | Agrim Kaundal | Cluster `cluster0.ri1mfz9`. Network access currently `0.0.0.0/0` (open) — access is by credentials in `MONGODB_URI`, not IP allowlist. |
| **Vercel** | Hosts the frontend (`web/`) | Agrim Kaundal | Static hosting, no secrets stored here. |
| **Google Cloud Console** | OAuth client (sign-in), Calendar API | Agrim Kaundal | Owns the "who can sign in" gate (`ALLOWED_DOMAINS=iimsirmaur.ac.in`) at the code level; Google enforces per-account auth. |
| **Resend** | Outbound email | *Not yet provisioned* | Until an API key is set, the system logs emails instead of sending them (visible in Render logs only — no one outside the dev actually receives them yet). |
| **`iimsirmaur.ac.in` Google Workspace admin** | Domain-wide delegation for Calendar holds | *Not yet provisioned* | Needs a Workspace admin (likely IT, not MCC) to authorize the service account. Optional — the system runs fine without it (calendar checks just no-op). |

**Action item:** add the Secretary/POC (or a designated successor) as a second
owner on GitHub, Render, and Atlas at minimum, so the portal doesn't depend on
one person's account. This is standard handover hygiene for a committee system
that will outlive any one member's term.

## 4. Vertical leadership

The four production verticals and their natural leads, drawn from the "Senior
Coordinator" designation in the team roster. **Note:** being listed here is not
the same as holding the system's `domainHeadOf` flag — that has to be granted
explicitly (Admin view → *Vertical heads*, or the Secretary/POC does it) before
someone can manually assign tasks within their vertical. As of this writing that
grant has **not yet been made** for anyone; this table is the recommended
starting slate.

| Vertical | Recommended lead(s) | Email |
|---|---|---|
| **Photography** | Kamalasegaran A (also POC) | `mba25114@iimsirmaur.ac.in` |
| | G N V Umanand Naik | `mba25109@iimsirmaur.ac.in` |
| **Videography** | Sukriti Saxena | `bms25123@iimsirmaur.ac.in` |
| **Graphic Designs** | Sanjana Jaiswal | `mba25178@iimsirmaur.ac.in` |
| | Aisha Firdouse | `mba25189@iimsirmaur.ac.in` |
| **Content Writing** | Priyal Shende | `mbatm25033@iimsirmaur.ac.in` |
| | Saguna Rishi | `bms24107@iimsirmaur.ac.in` |

## 5. Social platform handlers

Instagram, LinkedIn, and X post-scheduling currently routes to the placeholder
handler `mediacell@iimsirmaur.ac.in` (`config/platforms` in the database) —
**no individual owner is assigned yet.** Whoever actually runs the institute's
social accounts should be named here and set as the `handlerEmail` for their
platform (Admin/Secretary can update `config/platforms` directly, or ask the
maintainer to wire an editor into the Admin view).

| Platform | Handler | Notes |
|---|---|---|
| Instagram | *unassigned* | Currently `mediacell@iimsirmaur.ac.in` |
| LinkedIn | *unassigned* | Currently `mediacell@iimsirmaur.ac.in` |
| X | *unassigned* | Currently `mediacell@iimsirmaur.ac.in` |

## 6. Requesting bodies (committees, clubs, offices)

41 requesting bodies are loaded, each keyed by its own login email (its members
sign in with that shared address to raise requests). Full, editable list:
**Admin view → Committees**, or [`server/src/admin/realData.ts`](../server/src/admin/realData.ts).

| Campus | Clubs | Committees | SIG | Office | Total |
|---|---|---|---|---|---|
| MBA Campus | 11 | 13 | 1 | 2 | 27 |
| BMS Campus | 8 | 6 | 0 | 0 | 14 |
| **Total** | **19** | **19** | **1** | **2** | **41** |

Each committee's own PIC (who checks that inbox / raises requests on its
behalf) is that committee's own internal matter — the portal only needs the
login email to work, and doesn't track a named contact per committee today.

## 7. Full media team roster (23 members)

| Name | Vertical | Year | Campus | Email |
|---|---|---|---|---|
| Kamalasegaran A | Photography | 2 (Sr.) | MBA | `mba25114@iimsirmaur.ac.in` |
| Agrim Kaundal | Photography | 2 (Sr.) | MBA | `mbatm25010@iimsirmaur.ac.in` |
| Navina | Photography | 2 (Sr.) | MBA | `mbatm25024@iimsirmaur.ac.in` |
| Siddarth N | Photography | 2 (Sr.) | MBA | `mba25092@iimsirmaur.ac.in` |
| Suda Yugandhar | Photography | 2 (Sr.) | MBA | `mbatthm25017@iimsirmaur.ac.in` |
| G N V Umanand Naik | Photography | 2 (Sr.) | MBA | `mba25109@iimsirmaur.ac.in` |
| Ishan Negi | Photography | 2 (Sr.) | MBA | `mba25159@iimsirmaur.ac.in` |
| Arya Paliwal | Photography | 1 | BMS | `bms25021@iimsirmaur.ac.in` |
| Nitya Jaiswal | Photography | 1 | BMS | `bms25088@iimsirmaur.ac.in` |
| Sanjana Jaiswal | Graphic Designs | 2 (Sr.) | MBA | `mba25178@iimsirmaur.ac.in` |
| Aisha Firdouse | Graphic Designs | 2 (Sr.) | MBA | `mba25189@iimsirmaur.ac.in` |
| Priyal Shende | Content Writing | 2 (Sr.) | MBA | `mbatm25033@iimsirmaur.ac.in` |
| Saguna Rishi | Content Writing | 1 | BMS | `bms24107@iimsirmaur.ac.in` |
| Sukriti Saxena | Videography | 1 | BMS | `bms25123@iimsirmaur.ac.in` |
| Aditi Shukla | *(unset)* | 1 | BMS | `bms24006@iimsirmaur.ac.in` |
| Aarushi Dubey | *(unset)* | 1 | BMS | `bms24002@iimsirmaur.ac.in` |
| Prashant Kumar | *(unset)* | 1 | BMS | `bms24092@iimsirmaur.ac.in` |
| Laasya Nekkanti | *(unset)* | 1 | BMS | `bms25077@iimsirmaur.ac.in` |
| Aditya Kalyankar | *(unset)* | 1 | BMS | `bms24007@iimsirmaur.ac.in` |
| Ujjwala Naudiyal | *(unset)* | 1 | BMS | `bms24141@iimsirmaur.ac.in` |
| Rishabh Garg | *(unset)* | 1 | BMS | `bms24104@iimsirmaur.ac.in` |
| Yash Tripathi | *(unset)* | 1 | BMS | `bms24150@iimsirmaur.ac.in` |
| Deepshikha Das | *(unset)* | 1 | BMS | `bms24036@iimsirmaur.ac.in` |

*"Sr." = Senior Coordinator (year 2 — can manually assign tasks once a
vertical-head grant is made, see §4). Members with an unset vertical were not
assigned one in the source sheet and won't be auto-assigned tasks in that area
until they're given a vertical + skills (Admin view → CSV import, or ask the
maintainer).*

## 8. Review cadence

Re-confirm this document **once per academic term**, or immediately when: the
Secretary/POC changes, a new admin is designated, a vertical head is formally
appointed in-app, or a vendor-account owner changes. Stale PICs are worse than
no PIC — update this file (and the Admin view data it mirrors) together.
