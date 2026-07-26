"""Client Pitch System - CRM API.

Vercel serverless function. Static pages are served by the CDN from public/, so this
module owns /api/* only.

Serverless constraints that shape this file:
  - The filesystem is READ-ONLY except /tmp, so nothing is written at import time.
  - Cold starts are frequent; init_db() is idempotent and cheap after the first call.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Literal, Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator

from . import db, messaging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("client-pitch-system")

app = FastAPI(title="Client Pitch System CRM", docs_url=None, redoc_url=None)

# The CRM is an internal tool; the public lead form is the only cross-origin caller.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

FIELD_KINDS = {
    "text", "number", "select", "multi_select",
    "date", "checkbox", "url", "email", "phone",
}


def _ready() -> None:
    """Ensure schema exists. Raises 503 rather than 500 so the UI can explain itself."""
    try:
        db.init_db()
    except Exception as exc:
        logger.exception("database init failed")
        raise HTTPException(
            status_code=503,
            detail=f"Database unavailable ({db.backend_name()}): {exc}",
        )


def _loads(raw: Any, fallback: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw) if raw else fallback
    except Exception:
        return fallback


def _contact_out(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row["fields"] = _loads(row.get("fields"), {})
    row["value"] = float(row.get("value") or 0)
    return row


def _field_out(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row["options"] = _loads(row.get("options"), [])
    return row


def _require_workspace(workspace_id: str) -> dict[str, Any]:
    """404 on an unknown workspace, so a bad id is never mistaken for an empty pipeline."""
    row = db.query_one("SELECT * FROM workspaces WHERE id = ?", (workspace_id,))
    if not row:
        raise HTTPException(404, "workspace not found")
    return row


def _log(workspace_id: str, contact_id: Optional[str], kind: str, detail: str = "") -> None:
    db.execute(
        "INSERT INTO activities (id, workspace_id, contact_id, kind, detail, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (db.new_id(), workspace_id, contact_id, kind, detail[:500], db.now_iso()),
    )


# ---------------------------------------------------------------- models

class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    color: str = Field(default="#2563eb", max_length=20)


class StageIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    color: str = Field(default="#64748b", max_length=20)
    position: int = 0
    is_won: bool = False
    is_lost: bool = False


class FieldIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    kind: str = "text"
    options: list[dict[str, Any]] = Field(default_factory=list)
    position: int = 0

    @field_validator("kind")
    @classmethod
    def _kind_known(cls, v: str) -> str:
        if v not in FIELD_KINDS:
            raise ValueError(f"kind must be one of: {', '.join(sorted(FIELD_KINDS))}")
        return v


class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=40)
    company: str = Field(default="", max_length=120)
    source: str = Field(default="web", max_length=60)
    notes: str = Field(default="", max_length=4000)
    value: float = 0
    stage_id: Optional[str] = None
    fields: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "email", "phone", "company", "source", "notes")
    @classmethod
    def _strip(cls, v: str) -> str:
        return (v or "").strip()


class ContactPatch(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    email: Optional[str] = Field(default=None, max_length=200)
    phone: Optional[str] = Field(default=None, max_length=40)
    company: Optional[str] = Field(default=None, max_length=120)
    notes: Optional[str] = Field(default=None, max_length=4000)
    value: Optional[float] = None
    stage_id: Optional[str] = None
    fields: Optional[dict[str, Any]] = None


class MessageIn(BaseModel):
    channel: Literal["sms", "email", "note"]
    body: str = Field(min_length=1, max_length=4000)
    subject: str = Field(default="", max_length=200)


class PublicLead(BaseModel):
    """Payload from the public booking form."""
    name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=7, max_length=40)
    email: str = Field(min_length=3, max_length=200)
    date: Optional[str] = Field(default=None, max_length=40)
    notes: str = Field(default="", max_length=2000)
    source: str = Field(default="web", max_length=60)
    workspace: str = Field(default="", max_length=80)

    @field_validator("email")
    @classmethod
    def _email_shape(cls, v: str) -> str:
        v = v.strip()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("must be a valid email address")
        return v


# ---------------------------------------------------------------- meta

@app.get("/api/health")
async def health() -> dict[str, Any]:
    info: dict[str, Any] = {
        "status": "ok",
        "storage": db.backend_name(),
        "durable": db.IS_POSTGRES,
        "messaging": messaging.status_summary(),
    }
    try:
        db.init_db()
        row = db.query_one("SELECT COUNT(*) AS n FROM contacts")
        info["contacts"] = int(row["n"]) if row else 0
    except Exception as exc:
        info["status"] = "degraded"
        info["error"] = str(exc)
    return info


# ---------------------------------------------------------------- workspaces

@app.get("/api/workspaces")
async def list_workspaces() -> dict[str, Any]:
    _ready()
    return {"workspaces": db.query("SELECT * FROM workspaces ORDER BY created_at")}


@app.post("/api/workspaces", status_code=201)
async def create_workspace(payload: WorkspaceIn) -> dict[str, Any]:
    _ready()
    ws_id = db.new_id()
    slug = "".join(c if c.isalnum() else "-" for c in payload.name.lower()).strip("-")[:40]
    if db.query_one("SELECT id FROM workspaces WHERE slug = ?", (slug,)):
        slug = f"{slug}-{ws_id[:4]}"

    statements: list[tuple[str, Any]] = [(
        "INSERT INTO workspaces (id, name, slug, color, created_at) VALUES (?, ?, ?, ?, ?)",
        (ws_id, payload.name, slug, payload.color, db.now_iso()),
    )]
    for name, color, pos, is_won, is_lost in db.DEFAULT_STAGES:
        statements.append((
            "INSERT INTO stages (id, workspace_id, name, color, position, is_won, is_lost)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (db.new_id(), ws_id, name, color, pos, is_won, is_lost),
        ))
    db.execute_many(statements)
    return {"workspace": db.query_one("SELECT * FROM workspaces WHERE id = ?", (ws_id,))}


@app.delete("/api/workspaces/{workspace_id}")
async def delete_workspace(workspace_id: str) -> dict[str, Any]:
    _ready()
    _require_workspace(workspace_id)
    # Children are removed explicitly: SQLite enforces FKs only with the pragma set,
    # and relying on cascade differences between backends would be fragile.
    for table in ("messages", "activities", "contacts", "stages", "field_defs"):
        db.execute(f"DELETE FROM {table} WHERE workspace_id = ?", (workspace_id,))
    db.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
    return {"ok": True}


# ---------------------------------------------------------------- stages

@app.get("/api/workspaces/{workspace_id}/stages")
async def list_stages(workspace_id: str) -> dict[str, Any]:
    _ready()
    _require_workspace(workspace_id)
    return {"stages": db.query(
        "SELECT * FROM stages WHERE workspace_id = ? ORDER BY position, name", (workspace_id,)
    )}


@app.post("/api/workspaces/{workspace_id}/stages", status_code=201)
async def create_stage(workspace_id: str, payload: StageIn) -> dict[str, Any]:
    _ready()
    _require_workspace(workspace_id)
    sid = db.new_id()
    db.execute(
        "INSERT INTO stages (id, workspace_id, name, color, position, is_won, is_lost)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sid, workspace_id, payload.name, payload.color, payload.position,
         int(payload.is_won), int(payload.is_lost)),
    )
    return {"stage": db.query_one("SELECT * FROM stages WHERE id = ?", (sid,))}


@app.delete("/api/stages/{stage_id}")
async def delete_stage(stage_id: str) -> dict[str, Any]:
    _ready()
    if not db.query_one("SELECT id FROM stages WHERE id = ?", (stage_id,)):
        raise HTTPException(404, "stage not found")
    # Contacts outlive their stage - never delete a lead because a column was removed.
    db.execute("UPDATE contacts SET stage_id = NULL WHERE stage_id = ?", (stage_id,))
    db.execute("DELETE FROM stages WHERE id = ?", (stage_id,))
    return {"ok": True}


# ---------------------------------------------------------------- custom fields

@app.get("/api/workspaces/{workspace_id}/fields")
async def list_fields(workspace_id: str) -> dict[str, Any]:
    _ready()
    _require_workspace(workspace_id)
    rows = db.query(
        "SELECT * FROM field_defs WHERE workspace_id = ? ORDER BY position, name", (workspace_id,)
    )
    return {"fields": [_field_out(r) for r in rows]}


@app.post("/api/workspaces/{workspace_id}/fields", status_code=201)
async def create_field(workspace_id: str, payload: FieldIn) -> dict[str, Any]:
    _ready()
    _require_workspace(workspace_id)

    key = "".join(c if c.isalnum() else "_" for c in payload.name.lower()).strip("_")[:40]
    if not key:
        raise HTTPException(422, "field name must contain at least one alphanumeric character")
    if db.query_one("SELECT id FROM field_defs WHERE workspace_id = ? AND key = ?", (workspace_id, key)):
        raise HTTPException(409, f"a field with key '{key}' already exists in this workspace")

    fid = db.new_id()
    db.execute(
        "INSERT INTO field_defs (id, workspace_id, name, key, kind, options, position)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (fid, workspace_id, payload.name, key, payload.kind,
         json.dumps(payload.options), payload.position),
    )
    return {"field": _field_out(db.query_one("SELECT * FROM field_defs WHERE id = ?", (fid,)))}


@app.patch("/api/fields/{field_id}")
async def update_field(field_id: str, payload: FieldIn) -> dict[str, Any]:
    _ready()
    if not db.query_one("SELECT id FROM field_defs WHERE id = ?", (field_id,)):
        raise HTTPException(404, "field not found")
    db.execute(
        "UPDATE field_defs SET name = ?, kind = ?, options = ?, position = ? WHERE id = ?",
        (payload.name, payload.kind, json.dumps(payload.options), payload.position, field_id),
    )
    return {"field": _field_out(db.query_one("SELECT * FROM field_defs WHERE id = ?", (field_id,)))}


@app.delete("/api/fields/{field_id}")
async def delete_field(field_id: str) -> dict[str, Any]:
    _ready()
    if not db.query_one("SELECT id FROM field_defs WHERE id = ?", (field_id,)):
        raise HTTPException(404, "field not found")
    # Stored values in contacts.fields are left alone: deleting a definition hides the
    # column, it does not destroy data the user may still want if they re-add it.
    db.execute("DELETE FROM field_defs WHERE id = ?", (field_id,))
    return {"ok": True}


# ---------------------------------------------------------------- contacts

@app.get("/api/workspaces/{workspace_id}/contacts")
async def list_contacts(workspace_id: str, q: str = "", stage_id: str = "") -> dict[str, Any]:
    _ready()
    _require_workspace(workspace_id)
    sql = "SELECT * FROM contacts WHERE workspace_id = ?"
    params: list[Any] = [workspace_id]
    if stage_id:
        sql += " AND stage_id = ?"
        params.append(stage_id)
    if q:
        sql += " AND (LOWER(name) LIKE ? OR LOWER(email) LIKE ? OR LOWER(company) LIKE ? OR phone LIKE ?)"
        like = f"%{q.lower()}%"
        params.extend([like, like, like, f"%{q}%"])
    sql += " ORDER BY updated_at DESC"
    rows = db.query(sql, params)
    return {"count": len(rows), "contacts": [_contact_out(r) for r in rows]}


@app.post("/api/workspaces/{workspace_id}/contacts", status_code=201)
async def create_contact(workspace_id: str, payload: ContactIn) -> dict[str, Any]:
    _ready()
    _require_workspace(workspace_id)

    stage_id = payload.stage_id
    if not stage_id:
        first = db.query_one(
            "SELECT id FROM stages WHERE workspace_id = ? ORDER BY position LIMIT 1", (workspace_id,)
        )
        stage_id = first["id"] if first else None

    cid = db.new_id()
    ts = db.now_iso()
    db.execute(
        "INSERT INTO contacts (id, workspace_id, stage_id, name, email, phone, company,"
        " source, notes, value, fields, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cid, workspace_id, stage_id, payload.name, payload.email, payload.phone,
         payload.company, payload.source, payload.notes, payload.value,
         json.dumps(payload.fields), ts, ts),
    )
    _log(workspace_id, cid, "contact_created", payload.name)
    return {"contact": _contact_out(db.query_one("SELECT * FROM contacts WHERE id = ?", (cid,)))}


@app.get("/api/contacts/{contact_id}")
async def get_contact(contact_id: str) -> dict[str, Any]:
    _ready()
    row = db.query_one("SELECT * FROM contacts WHERE id = ?", (contact_id,))
    if not row:
        raise HTTPException(404, "contact not found")
    msgs = db.query(
        "SELECT * FROM messages WHERE contact_id = ? ORDER BY created_at", (contact_id,)
    )
    return {"contact": _contact_out(row), "messages": msgs}


@app.patch("/api/contacts/{contact_id}")
async def update_contact(contact_id: str, payload: ContactPatch) -> dict[str, Any]:
    _ready()
    current = db.query_one("SELECT * FROM contacts WHERE id = ?", (contact_id,))
    if not current:
        raise HTTPException(404, "contact not found")

    updates: list[str] = []
    params: list[Any] = []
    for column in ("name", "email", "phone", "company", "notes", "value", "stage_id"):
        value = getattr(payload, column)
        if value is not None:
            updates.append(f"{column} = ?")
            params.append(value)

    if payload.fields is not None:
        # Merge rather than replace, so a partial update cannot wipe other fields.
        merged = {**_loads(current.get("fields"), {}), **payload.fields}
        # An explicit null clears a field.
        merged = {k: v for k, v in merged.items() if v is not None}
        updates.append("fields = ?")
        params.append(json.dumps(merged))

    if not updates:
        return {"contact": _contact_out(current)}

    updates.append("updated_at = ?")
    params.append(db.now_iso())
    params.append(contact_id)
    db.execute(f"UPDATE contacts SET {', '.join(updates)} WHERE id = ?", params)

    if payload.stage_id and payload.stage_id != current.get("stage_id"):
        stage = db.query_one("SELECT name FROM stages WHERE id = ?", (payload.stage_id,))
        _log(current["workspace_id"], contact_id, "stage_changed", stage["name"] if stage else "")

    return {"contact": _contact_out(db.query_one("SELECT * FROM contacts WHERE id = ?", (contact_id,)))}


@app.delete("/api/contacts/{contact_id}")
async def delete_contact(contact_id: str) -> dict[str, Any]:
    _ready()
    if not db.query_one("SELECT id FROM contacts WHERE id = ?", (contact_id,)):
        raise HTTPException(404, "contact not found")
    db.execute("DELETE FROM messages WHERE contact_id = ?", (contact_id,))
    db.execute("DELETE FROM activities WHERE contact_id = ?", (contact_id,))
    db.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
    return {"ok": True}


# ---------------------------------------------------------------- messaging

@app.post("/api/contacts/{contact_id}/messages", status_code=201)
async def send_message(contact_id: str, payload: MessageIn) -> JSONResponse:
    _ready()
    contact = db.query_one("SELECT * FROM contacts WHERE id = ?", (contact_id,))
    if not contact:
        raise HTTPException(404, "contact not found")

    status, provider_id, error = "sent", "", ""
    if payload.channel == "sms":
        status, provider_id, error = await messaging.send_sms(contact["phone"], payload.body)
    elif payload.channel == "email":
        status, provider_id, error = await messaging.send_email(
            contact["email"], payload.subject, payload.body
        )

    mid = db.new_id()
    db.execute(
        "INSERT INTO messages (id, workspace_id, contact_id, channel, direction, subject,"
        " body, status, error, provider_id, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (mid, contact["workspace_id"], contact_id, payload.channel, "out",
         payload.subject, payload.body, status, error, provider_id, db.now_iso()),
    )
    _log(contact["workspace_id"], contact_id, f"{payload.channel}_out", payload.body[:120])

    message = db.query_one("SELECT * FROM messages WHERE id = ?", (mid,))
    # A recorded message is not a delivered one - say which happened.
    http_status = 201 if status in {"sent", "dry_run"} else 502
    return JSONResponse(status_code=http_status, content={
        "ok": status in {"sent", "dry_run"},
        "delivery": status,
        "message": message,
        "note": {
            "dry_run": "Recorded only - MESSAGING_LIVE is not enabled, nothing was sent.",
            "unconfigured": "Recorded only - provider credentials are not set.",
            "failed": "Provider rejected the send. See error.",
            "sent": "Handed off to the provider.",
        }.get(status, ""),
    })


@app.post("/api/webhooks/twilio")
async def twilio_inbound(
    From: str = Form(default=""),
    Body: str = Form(default=""),
    MessageSid: str = Form(default=""),
) -> Response:
    """Inbound SMS from Twilio. Configure this URL as the number's webhook.

    Always returns 200 with empty TwiML: a non-2xx makes Twilio retry, and an
    unmatched sender is not an error worth retrying.
    """
    empty_twiml = Response(content="<Response></Response>", media_type="application/xml")
    try:
        _ready()
        sender = messaging.normalize_phone(From)
        if not sender:
            return empty_twiml

        # Match on the last 10 digits so formatting differences still resolve.
        tail = sender[-10:]
        contact = db.query_one(
            "SELECT * FROM contacts WHERE REPLACE(REPLACE(REPLACE(REPLACE(phone,'-',''),"
            " ' ',''),'(',''),')','') LIKE ? ORDER BY updated_at DESC LIMIT 1",
            (f"%{tail}",),
        )
        if not contact:
            logger.info("inbound SMS from unknown number %s", sender)
            return empty_twiml

        db.execute(
            "INSERT INTO messages (id, workspace_id, contact_id, channel, direction, subject,"
            " body, status, error, provider_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (db.new_id(), contact["workspace_id"], contact["id"], "sms", "in", "",
             Body, "received", "", MessageSid, db.now_iso()),
        )
        _log(contact["workspace_id"], contact["id"], "sms_in", Body[:120])
    except Exception:
        logger.exception("twilio inbound webhook failed")
    return empty_twiml


# ---------------------------------------------------------------- public intake

@app.post("/api/leads", status_code=201)
async def capture_lead(payload: PublicLead) -> JSONResponse:
    """Public booking form intake. Creates a contact in the first pipeline stage."""
    _ready()

    workspace = None
    if payload.workspace:
        workspace = db.query_one(
            "SELECT * FROM workspaces WHERE slug = ? OR id = ?", (payload.workspace, payload.workspace)
        )
    if not workspace:
        workspace = db.query_one("SELECT * FROM workspaces ORDER BY created_at LIMIT 1")
    if not workspace:
        raise HTTPException(503, "no workspace configured")

    first = db.query_one(
        "SELECT id FROM stages WHERE workspace_id = ? ORDER BY position LIMIT 1", (workspace["id"],)
    )
    cid = db.new_id()
    ts = db.now_iso()
    fields = {"follow_up": payload.date} if payload.date else {}

    db.execute(
        "INSERT INTO contacts (id, workspace_id, stage_id, name, email, phone, company,"
        " source, notes, value, fields, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cid, workspace["id"], first["id"] if first else None, payload.name, payload.email,
         payload.phone, "", payload.source, payload.notes, 0, json.dumps(fields), ts, ts),
    )
    _log(workspace["id"], cid, "lead_captured", payload.source)

    return JSONResponse(status_code=201, content={
        "ok": True,
        "storage": db.backend_name(),
        "durable": db.IS_POSTGRES,
        "message": "Lead captured.",
        "contact": _contact_out(db.query_one("SELECT * FROM contacts WHERE id = ?", (cid,))),
    })


@app.get("/api/leads")
async def list_leads() -> dict[str, Any]:
    """Back-compat listing across every workspace."""
    _ready()
    rows = db.query("SELECT * FROM contacts ORDER BY created_at DESC LIMIT 500")
    return {
        "ok": True,
        "storage": db.backend_name(),
        "durable": db.IS_POSTGRES,
        "count": len(rows),
        "leads": [_contact_out(r) for r in rows],
    }
