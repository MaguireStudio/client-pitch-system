from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
import httpx
import logging
import asyncio
import json
from pathlib import Path

# App setup
app = FastAPI(title="Client Pitch System")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount the public directory so static assets are available if needed
PUBLIC_DIR = Path("public")
if PUBLIC_DIR.exists():
    app.mount("/public", StaticFiles(directory=str(PUBLIC_DIR)), name="public")

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("client-pitch-system")

# Configuration
SHEET_API_URL = os.getenv("SHEET_API_URL", "")
LOCAL_STORE = Path("data")
LOCAL_STORE.mkdir(exist_ok=True)
LOCAL_LEADS_FILE = LOCAL_STORE / "leads.json"

# Ensure leads file exists
if not LOCAL_LEADS_FILE.exists():
    LOCAL_LEADS_FILE.write_text("[]", encoding="utf-8")


@app.on_event("startup")
async def startup_check():
    logger.info("Client Pitch System starting up")
    if SHEET_API_URL:
        logger.info(f"SHEET_API_URL is configured: {SHEET_API_URL[:80]}...")
    else:
        logger.info("SHEET_API_URL not set - running in offline/local persistence mode")


# Serve the landing page
@app.get("/", response_class=HTMLResponse)
async def serve_index():
    try:
        index_path = PUBLIC_DIR / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
        return HTMLResponse("<h1>Index not found</h1>", status_code=404)
    except Exception as e:
        logger.exception("Error serving index.html")
        return HTMLResponse(f"<h1>Error Loading Page</h1><p>{str(e)}</p>", status_code=500)


# Serve the dashboard page
@app.get("/dashboard", response_class=HTMLResponse)
async def serve_dashboard():
    try:
        dashboard_path = PUBLIC_DIR / "dashboard.html"
        if dashboard_path.exists():
            return FileResponse(dashboard_path)
        return HTMLResponse("<h1>Dashboard not found</h1>", status_code=404)
    except Exception as e:
        logger.exception("Error serving dashboard.html")
        return HTMLResponse(f"<h1>Error Loading Dashboard</h1><p>{str(e)}</p>", status_code=500)


# Helpers for local persistence
async def read_local_leads():
    try:
        def _read():
            with open(LOCAL_LEADS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        leads = await asyncio.to_thread(_read)
        return leads
    except Exception as e:
        logger.exception("Failed to read local leads file")
        return []


async def append_local_lead(lead: dict):
    try:
        def _append():
            with open(LOCAL_LEADS_FILE, "r+", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                except Exception:
                    data = []
                data.append(lead)
                f.seek(0)
                json.dump(data, f, indent=2)
                f.truncate()
        await asyncio.to_thread(_append)
        return True
    except Exception as e:
        logger.exception("Failed to append local lead")
        return False


# API: capture a lead
@app.post("/api/leads")
async def capture_lead(request: Request):
    try:
        lead_data = await request.json()
        # Basic normalization
        lead = {
            "name": lead_data.get("name") or lead_data.get("fullName") or "",
            "phone": lead_data.get("phone") or lead_data.get("phoneNumber") or "",
            "email": lead_data.get("email") or "",
            "source": lead_data.get("source") or "web",
            "notes": lead_data.get("notes") or "",
            "received_at": request.headers.get("x-request-start") or asyncio.get_event_loop().time(),
        }

        # If an external SHEET_API_URL is configured, forward the lead there using async HTTP
        if SHEET_API_URL:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(SHEET_API_URL, json=lead)
                    resp.raise_for_status()
                    logger.info("Forwarded lead to SHEET_API_URL")
                    return JSONResponse(status_code=resp.status_code, content={"ok": True, "remote": resp.json()})
            except Exception as e:
                logger.exception("Failed to forward lead to SHEET_API_URL - falling back to local storage")
                await append_local_lead({**lead, "forward_error": str(e)})
                return JSONResponse(status_code=502, content={"ok": False, "message": "Forward failed - saved locally", "error": str(e)})
        else:
            # Save locally
            success = await append_local_lead(lead)
            if success:
                return JSONResponse(status_code=200, content={"ok": True, "message": "Lead saved locally", "data": lead})
            else:
                raise HTTPException(status_code=500, detail="Failed to save lead locally")

    except Exception as e:
        logger.exception("Error in capture_lead")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


# API: fetch leads
@app.get("/api/leads")
async def fetch_leads():
    try:
        if SHEET_API_URL:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(SHEET_API_URL)
                    resp.raise_for_status()
                    data = resp.json()
                    # Normalize different remote payload shapes
                    leads = data if isinstance(data, list) else data.get("leads") if isinstance(data, dict) else []
                    return JSONResponse(status_code=200, content={"ok": True, "leads": leads})
            except Exception as e:
                logger.exception("Failed to fetch leads from SHEET_API_URL - returning local leads")
                local = await read_local_leads()
                return JSONResponse(status_code=200, content={"ok": False, "leads": local, "error": str(e)})
        else:
            local = await read_local_leads()
            return JSONResponse(status_code=200, content={"ok": True, "leads": local})
    except Exception as e:
        logger.exception("Error in fetch_leads")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e), "leads": []})


# Health check
@app.get("/api/health")
async def health_check():
    return {"status": "ok", "sheet_api_configured": bool(SHEET_API_URL)}
