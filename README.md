# Client Pitch System

All-in-one prospect and client relations tool: a public booking page that feeds a
multi-business CRM with a Kanban pipeline, Notion-style custom fields, and SMS/email
threads per contact.

| Page | What it is |
|---|---|
| `/` | Public booking form. Submissions become CRM contacts. |
| `/crm.html` | The CRM: board + table views, custom fields, message threads. |
| `/api/*` | FastAPI serverless function. |

## ⚠️ There is no login

Anyone with the URL can read, edit, and delete every contact across every business,
and `GET /api/leads` returns all contacts in one call. This is fine for a demo with the
seeded sample businesses. **Do not enter real client data until authentication exists.**

## Storage

| `DATABASE_URL` set? | Backend | Durable? |
|---|---|---|
| yes | Postgres | Yes |
| no | SQLite in `/tmp` | **No** — wiped whenever the instance recycles |

On Vercel, leaving `DATABASE_URL` unset keeps the deploy a self-resetting demo, which is
the safer default while the app is unauthenticated. To make it durable: Vercel dashboard →
**Storage** → create a Postgres (Neon) store → connect it to the project. Vercel injects
`DATABASE_URL` automatically. Redeploy, and the schema self-creates on first request.

The CRM shows an amber banner whenever storage is not durable.

## Messaging

Sending is **off** unless `MESSAGING_LIVE=true`, even when credentials are present. This
makes it impossible to text a real client while still setting things up. Messages are
always recorded in the thread; the status says what actually happened
(`sent`, `dry_run`, `unconfigured`, `failed`).

| Variable | For |
|---|---|
| `MESSAGING_LIVE` | Master switch. Omit or set `false` to keep everything dry-run. |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` | Outbound SMS |
| `RESEND_API_KEY`, `RESEND_FROM_EMAIL` | Outbound email |

**Inbound SMS:** point your Twilio number's webhook at
`https://<your-domain>/api/webhooks/twilio` (HTTP POST). Replies are matched to a contact
by the last 10 digits of the sender's number and appear in that contact's thread.

Note: the webhook does not verify Twilio's request signature, so anyone who knows the URL
could post a fake inbound message. Worth adding before this handles real conversations.

## Custom fields

Per business, so Waves can track roof type while DK tracks furnace age. Types: text,
number, select, multi-select, date, checkbox, URL, email, phone. Select fields carry
colored options that render as chips on the board. **Customize fields** in the top bar.

Removing a field definition hides the column but keeps values already saved on contacts.

## Booking page routing

`public/index.html` has a `WORKSPACE_SLUG` constant near the top of its `<script>`. Set it
to a workspace slug (`waves`, `dk-hvac`, `az-detailing`, ...) to file that page's leads
under a specific business. Empty means the first workspace.

## Local development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python dev.py     # http://127.0.0.1:8000
```

Runs on SQLite with no setup. `dev.py` also serves `public/` so one process mimics
Vercel's split of CDN + function; Vercel never runs it.

## Architecture

Vercel zero-config. **Do not add a legacy `builds` block to `vercel.json`** — it disables
this and stops `public/` from being served.

```
public/          static, served by the CDN
api/index.py     FastAPI app (routes)
api/db.py        storage layer, Postgres + SQLite behind one interface
api/messaging.py Twilio / Resend, dry-run by default
api/schema.sql   schema, portable across both backends
```

See [docs/VERCEL_RUNBOOK.md](docs/VERCEL_RUNBOOK.md) for deployment and troubleshooting.
