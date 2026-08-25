# Media Committee Portal

Workflow portal for the **Media & Communications Committee at IIM Sirmaur**.
Committees, clubs, the MDP office, students, and faculty raise **requests** to
get events covered and/or content posted on the institute's social handles.
The system assigns the right people, enforces deadlines, balances workload,
schedules posts, and tracks accountability.

> **Status:** Live, running on **Django + SQLite**, self-hosted. The stack
> below is the current one — see [Change history](#change-history) if you're
> looking for the earlier Firebase or Express/MongoDB versions.

---

## Where everything is

| For… | Go to |
|---|---|
| **Setting up a dev environment, or deploying it** | [`portal/README.md`](portal/README.md) — the actual, current setup and deployment guide |
| **What the system does and why** | [`docs/PRD.md`](docs/PRD.md) |
| **Who owns what — accounts, vendor logins, escalation** | [`docs/PIC.md`](docs/PIC.md) |
| **Picking the project back up / operating it day to day** | [`HANDOVER.md`](HANDOVER.md) |

`docs/PRD.md`, `docs/PIC.md`, and `HANDOVER.md` were written for the previous
Express/MongoDB architecture and haven't been rewritten for the Django move
yet — the workflow rules and ownership info in them are still accurate, but
anything about the tech stack, hosting, or deploy steps is stale. Trust
`portal/README.md` for anything about the *current* implementation until
those get updated.

## Architecture (current)

| Layer | Choice |
|---|---|
| App | Django 5.2, server-rendered templates — no separate frontend/API split, no JS build step |
| Database | SQLite (WAL mode), one file, on the host machine |
| Auth | Google OAuth (`django-allauth`), restricted to `@iimsirmaur.ac.in` |
| Hosting | Self-hosted on a lab PC, reached over the college network |
| Email / Calendar | Auto-fall back to logging when unconfigured; real providers wired in behind the same interface |

Full detail, including *why* each of these was chosen over the alternatives:
[`portal/README.md`](portal/README.md).

## Repository layout

```
portal/          the live application — see portal/README.md for everything
docs/            PRD.md (what/why) and PIC.md (who owns what)
HANDOVER.md      how to pick this project back up
```

## Change history

- **2026-08** — re-platformed from Express + MongoDB (hosted on Render/Vercel/
  Atlas) onto self-hosted Django + SQLite, to get off the free-tier hosting
  stack and its cold-start/coordination overhead. This is the current version.
- **2026-06** — migrated off the original Firebase build (Cloud Functions +
  Firestore + Firebase Auth) onto Express + MongoDB, since Cloud Functions
  required a paid Blaze plan the committee wanted to avoid.
- **2026-06 (earlier)** — original build: Firebase Cloud Functions, Firestore,
  Firebase Auth, static frontend on Vercel.
