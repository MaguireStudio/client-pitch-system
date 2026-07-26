Vercel Runbook: Troubleshooting FUNCTION_INVOCATION_FAILED and Deploy Steps

Purpose
-------
This document explains step-by-step what to do in the Vercel dashboard when a serverless function returns:

500: INTERNAL_SERVER_ERROR
Code: FUNCTION_INVOCATION_FAILED
ID: <invocation-id>

It covers how to locate detailed logs, common root causes, recommended configuration changes, how to redeploy, and how to provide a reproducible bundle to an on-call person with Vercel access.

Location in repo
----------------
File: docs/VERCEL_RUNBOOK.md

Prerequisites
-------------
- A Vercel account with Owner/Access to the project.
- The project linked to this GitHub repository (Client Pitch System).
- A Vercel project that uses the repo's vercel.json (if present).
- Basic familiarity with the Vercel dashboard and deployments page.

Quick facts (what to look for first)
-----------------------------------
- The Invocation ID in the error message (e.g. sfo1::6flks-1785086348752-5a4fbe61631d). Save it.
- Deployment timestamp and the Commit SHA that was deployed.
- Which function file was invoked (main.py in this repo).

Step-by-step: Inspect the failing invocation in Vercel
-----------------------------------------------------
1. Open Vercel and select the project (Client Pitch System).
2. In the left-hand menu, click "Deployments".
3. Find the failing deployment (match by time or commit SHA). Click it.
4. On the deployment page, open the "Functions" tab (or "Logs" tab if Functions tab missing).
5. Locate the function that corresponds to your Python entry (for single-file Python projects, the function is usually the filename or "/" route mapped to main.py).
6. Use the invocation ID (or timestamp) to filter or search logs. If there's a search box, paste the ID (or parts of it).
7. Read the stack trace and logs for the exact exception and file/line. Important details to capture:
   - Full stack trace
   - Python exception type and message
   - Request headers (X-Forwarded-For, etc.) if present
   - Any inner cause (timeout, connection refused, missing env var)
8. In the Deployment page, also check the Build Logs (the "Build" tab) to confirm requirements were installed and that the build succeeded.

Common immediate root-cause checks
---------------------------------
- Missing environment variables (SHEET_API_URL) -> functions trying to call an empty URL.
- Blocking synchronous network calls in async handlers (requests instead of httpx) -> can cause invocation failures/timeouts.
- The external bridge (Google Apps Script / SHEET_API_URL) is down or returning non-JSON or slow responses.
- Build did not install dependencies: check Build logs to ensure httpx, fastapi etc. installed.
- File not found when serving static HTML: check that the repository contains public/index.html and deployment includes it.

Configuration changes you can make from the Vercel Dashboard
------------------------------------------------------------
(These changes are project-level and take effect on next deployment.)

A. Set required environment variables
   1. Project Settings → Environment Variables
   2. Add key: SHEET_API_URL
      - Value: https://<your-bridge>/exec (the real Google Apps Script URL or API endpoint)
      - Environment: set for "Production" and/or "Preview" as needed
   3. Save and re-deploy

B. Increase serverless function timeout and memory (if your function needs > default)
   - Edit vercel.json in the repo (recommended) with function config (example below). Commit and push to trigger a redeploy.

   Example vercel.json snippet to configure function runtime (add or merge with existing file):

   {
     "version": 2,
     "builds": [
       { "src": "main.py", "use": "@vercel/python" }
     ],
     "routes": [
       { "src": "/(.*)", "dest": "main.py" }
     ],
     "functions": {
       "main.py": {
         "memory": 1024,
         "maxDuration": 30
       }
     }
   }

   Notes:
   - memory is in MB. 1024 is a common choice.
   - maxDuration is in seconds and extends the function timeout; choose values consistent with your plan and SLAs.
   - If you need much longer jobs, use background workers or a dedicated server (Vercel serverless has limits).

C. Confirm Build & Output
   - Project Settings → General → Framework Preset (if applicable) — leave as "Other" for single-file Python.
   - Ensure requirements.txt is present (the build step uses it to install dependencies). If dependencies are missing, add them and re-deploy.

D. Redeploy / Rollback
   - To redeploy the fixed code, push a commit to the repository (or click "Redeploy" on a deployment page).
   - To rollback to a previous working deployment: Deployments → find the older deployment → click "Promote" or open and select to make it production.

Local reproduction checklist (what to run locally before redeploying)
-------------------------------------------------------------------
1. Clone the repo and checkout the target branch (main).
2. Create and activate a virtualenv.
3. Install dependencies:

   pip install -r requirements.txt

4. Set the SHEET_API_URL environment variable locally (or leave blank for offline mode):

   export SHEET_API_URL="https://script.google.com/macros/s/.../exec"

5. Run the app locally using Uvicorn:

   uvicorn main:app --reload --port 8000

6. Test endpoints:

   # Health
   curl -s http://localhost:8000/api/health | jq

   # GET leads (should return local leads if SHEET_API_URL not configured)
   curl -s http://localhost:8000/api/leads | jq

   # POST lead
   curl -s -X POST http://localhost:8000/api/leads -H "Content-Type: application/json" -d '{"name":"Alice","phone":"555-1212","email":"a@example.com"}' | jq

What to capture and share with the on-call / Vercel support
-----------------------------------------------------------
When escalating or asking another engineer to look, provide the following:
- Project name and repo URL
- Deployment URL and Commit SHA
- Exact Invocation ID from the error (e.g. sfo1::6flks-...)
- Full timestamp (timezone) of the failing request
- Relevant log excerpts (stack trace and Build logs) — copy full stack trace
- A short description of what you were doing and the request payload (if applicable)

If you need to contact Vercel Support, include the Invocation ID and deployment details — Vercel uses Invocation IDs to find server logs faster.

Specific recommendations for this repo (Client Pitch System)
----------------------------------------------------------
1. Ensure requirements.txt includes httpx and fastapi (we added them). Verify Build logs show these installed.
2. Ensure SHEET_API_URL is set in Production env (Project Settings → Environment Variables). Without it the app will run in offline mode and save leads locally (data/leads.json).
3. Keep async httpx usage in endpoints (already implemented). Do not use blocking requests in async handlers.
4. If the sheet bridge is slow, consider:
   - Adding retries with exponential backoff
   - Increasing function maxDuration in vercel.json (example above)
   - Offloading long tasks to a background worker or a queue
5. Add a health endpoint (present at /api/health) and configure Vercel or an external monitor to hit it regularly for uptime alerts.

Example: How to patch vercel.json and redeploy quickly
----------------------------------------------------
1. Edit the repo locally and add the "functions" block shown earlier.
2. Commit and push to main.
3. In Vercel, the push will trigger a new deployment. Monitor the build logs and the new Deployment → Functions logs.

Extra: Suggested vercel.json (full file)
---------------------------------------
```json
{
  "version": 2,
  "builds": [
    { "src": "main.py", "use": "@vercel/python" }
  ],
  "routes": [
    { "src": "/(.*)", "dest": "main.py" }
  ],
  "functions": {
    "main.py": {
      "memory": 1024,
      "maxDuration": 30
    }
  }
}
```

Postmortem checklist (if the incident continues)
------------------------------------------------
- Confirm the external bridge (SHEET_API_URL) is reachable from outside Vercel (curl the URL from your machine or use a third-party ping tool).
- Temporarily disable forwarding to the remote bridge (unset SHEET_API_URL in Vercel) to stop failing calls and allow the site to operate in local/offline mode.
- Rollback to a known-good commit if the new commit introduced a regression.
- If the failure is intermittent and difficult to reproduce, gather several failing invocation IDs and timestamps and contact Vercel Support with that list.

Contact & next steps for the on-call engineer
--------------------------------------------
1. Gather: Invocation ID, Deployment URL, Commit SHA, Build logs, Function logs, Full stack trace.
2. If the stack trace shows the external request timed out, consider increasing maxDuration and/or implementing retries.
3. If the stack trace shows a missing file or import error, fix code and push a patch, then redeploy.
4. If you want me to apply changes (update vercel.json, add retries or logging), I can prepare a PR or push a commit — tell me which and I will do it.

---

End of runbook.
