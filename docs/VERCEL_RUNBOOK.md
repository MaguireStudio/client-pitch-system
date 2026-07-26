# Vercel Runbook

## The 500 that took the site down (resolved)

`https://client-pitch-system.vercel.app/` returned `500 INTERNAL_SERVER_ERROR /
FUNCTION_INVOCATION_FAILED` on **every** route, including `/api/health`.

**Root cause:** `main.py` wrote to disk at *import* time:

```python
LOCAL_STORE = Path("data")
LOCAL_STORE.mkdir(exist_ok=True)              # <-- PermissionError on Vercel
LOCAL_LEADS_FILE.write_text("[]", ...)        # <-- same
```

Vercel's serverless filesystem is **read-only except `/tmp`**. The module raised
`PermissionError: [Errno 13] Permission denied: 'data'` before FastAPI ever loaded, so
the handler did not exist and every request failed. The in-code `try/except` blocks
never helped, because the crash happened at import, above all of them.

Reproduce the original failure:

```bash
mkdir ro && cp main.py ro/ && chmod -R a-w ro && cd ro
python3 -c "import main"   # PermissionError
```

**Fix:** all writes moved to `tempfile.gettempdir()` and are performed lazily inside
request handlers, never at import.

An earlier revision of this runbook blamed an unset `SHEET_API_URL`. That was wrong —
the code already defaulted it to `""` and handled the empty case. Setting that variable
would not have fixed the outage.

## Architecture

Vercel zero-config layout — do not reintroduce a legacy `builds` block in `vercel.json`,
as it disables this and stops `public/` from being served:

| Path | Served by | Notes |
|---|---|---|
| `/`, `/dashboard.html` | Vercel CDN, straight from `public/` | static, no function invoked |
| `/api/*` | `api/index.py` serverless function | FastAPI ASGI app |

`vercel.json` rewrites `/api/(.*)` to `/api/index` so FastAPI's own router handles the paths.

## Storage: read this before a client demo

| `SHEET_API_URL` | Behavior | Durable? |
|---|---|---|
| unset | Leads buffered in `/tmp` | **No** |
| set | Leads POSTed/GETed from that endpoint | Yes |

**`/tmp` is per-instance and ephemeral.** Vercel may recycle the instance at any time,
and concurrent invocations get *different* `/tmp` directories. Leads submitted without
`SHEET_API_URL` can disappear, and the dashboard may show a partial list. This is fine
for a demo; it is not fine for real client leads. The dashboard shows an amber banner
whenever it is in this mode.

To make storage durable, set `SHEET_API_URL` in **Project Settings → Environment
Variables** (Production + Preview), then redeploy. It should accept `POST` of a single
lead object and return leads on `GET` (either a bare array or `{"leads": [...]}`).

## API

| Method | Path | Success | Notes |
|---|---|---|---|
| `GET` | `/api/health` | 200 | reports which storage mode is active |
| `POST` | `/api/leads` | 201 | requires `name`, `phone`, `email`; 422 on invalid input |
| `GET` | `/api/leads` | 200 | `{ok, storage, count, leads}` |

If the sheet is unreachable, `POST` returns **502** and says the lead may not persist.
It does not report success for a lead it could not durably store.

## Local development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python dev.py     # http://127.0.0.1:8000
```

`dev.py` mounts `public/` so one local server mimics the split CDN/function setup.
It is a dev convenience only and is not used by Vercel.

Smoke test:

```bash
curl -s localhost:8000/api/health
curl -s -X POST localhost:8000/api/leads -H 'Content-Type: application/json' \
  -d '{"name":"Test","phone":"5551234567","email":"t@example.com"}'
curl -s localhost:8000/api/leads
```

## If a function 500s again

1. Vercel dashboard → **Deployments** → failing deployment → **Functions** / **Logs**.
2. Search the **Invocation ID** from the error page for the stack trace.
3. Check the top frame. If it is at *module scope* rather than inside a handler, it is an
   import-time crash — the whole function is dead and no route will respond.
4. Import-time crashes are almost always: a filesystem write outside `/tmp`, a missing
   dependency in `requirements.txt`, or a required env var read with `os.environ[...]`
   (use `os.getenv(name, default)` instead).
5. Confirm **Build logs** show `fastapi`, `httpx`, and `pydantic` installing.

When escalating, include the Invocation ID, deployment timestamp, commit SHA, and the
full stack trace.
