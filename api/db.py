"""Storage layer with two interchangeable backends.

Postgres when DATABASE_URL (or POSTGRES_URL) is set - this is production on Vercel.
SQLite otherwise, so the CRM is fully runnable and testable locally.

Both speak the same SQL subset (see schema.sql). Queries here are written with '?'
placeholders and rewritten to '%s' for Postgres, so callers never branch on backend.

All values are passed as bound parameters - never string-formatted into SQL.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

DATABASE_URL = (
    os.getenv("DATABASE_URL")
    or os.getenv("POSTGRES_URL")
    or os.getenv("POSTGRES_URL_NON_POOLING")
    or ""
).strip()

# Local fallback lives in the only writable dir on a serverless host.
SQLITE_PATH = os.getenv("SQLITE_PATH") or str(Path(os.getenv("TMPDIR", "/tmp")) / "client-pitch-crm.sqlite3")

IS_POSTGRES = bool(DATABASE_URL)

_init_lock = threading.Lock()
_initialized = False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def _adapt(sql: str) -> str:
    """Rewrite '?' placeholders to '%s' for psycopg. Naive but safe here: the SQL in
    this codebase contains no literal '?' characters outside placeholders."""
    return sql.replace("?", "%s") if IS_POSTGRES else sql


@contextmanager
def connect():
    if IS_POSTGRES:
        import psycopg
        from psycopg.rows import dict_row

        conn = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(SQLITE_PATH, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def query(sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        cur = conn.execute(_adapt(sql), tuple(params))
        rows = cur.fetchall()
        return [dict(r) for r in rows]


def query_one(sql: str, params: Iterable[Any] = ()) -> Optional[dict[str, Any]]:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Iterable[Any] = ()) -> None:
    with connect() as conn:
        conn.execute(_adapt(sql), tuple(params))


def execute_many(statements: list[tuple[str, Iterable[Any]]]) -> None:
    """Run several statements in one transaction."""
    with connect() as conn:
        for sql, params in statements:
            conn.execute(_adapt(sql), tuple(params))


def init_db() -> None:
    """Create tables and seed defaults. Idempotent; safe to call on every cold start."""
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        ddl = _SCHEMA_PATH.read_text(encoding="utf-8")
        with connect() as conn:
            if IS_POSTGRES:
                conn.execute(ddl)
            else:
                conn.executescript(ddl)
        _seed_defaults()
        _initialized = True


DEFAULT_STAGES = [
    ("New Lead", "#3b82f6", 0, 0, 0),
    ("Contacted", "#8b5cf6", 1, 0, 0),
    ("Quoted", "#f59e0b", 2, 0, 0),
    ("Won", "#10b981", 3, 1, 0),
    ("Lost", "#ef4444", 4, 0, 1),
]

# Seeded from the businesses already in flight, so the tool is useful on first load.
DEFAULT_WORKSPACES = [
    ("Maguire Studio", "maguire-studio", "#2563eb"),
    ("Waves Window Washing", "waves", "#0ea5e9"),
    ("DK Heating & Cooling", "dk-hvac", "#ef4444"),
    ("A-Z Mobile Detailing", "az-detailing", "#7c2d43"),
]

DEFAULT_FIELDS = [
    ("Service", "service", "select", [
        {"label": "Window Cleaning", "color": "#0ea5e9"},
        {"label": "HVAC Repair", "color": "#ef4444"},
        {"label": "Detailing", "color": "#7c2d43"},
        {"label": "Website", "color": "#2563eb"},
    ]),
    ("Priority", "priority", "select", [
        {"label": "Low", "color": "#64748b"},
        {"label": "Medium", "color": "#f59e0b"},
        {"label": "High", "color": "#ef4444"},
    ]),
    ("Follow-up date", "follow_up", "date", []),
    ("Quoted", "quoted", "checkbox", []),
]


def _seed_defaults() -> None:
    existing = query_one("SELECT COUNT(*) AS n FROM workspaces")
    if existing and int(existing["n"]) > 0:
        return

    statements: list[tuple[str, Iterable[Any]]] = []
    ts = now_iso()
    for name, slug, color in DEFAULT_WORKSPACES:
        ws_id = new_id()
        statements.append((
            "INSERT INTO workspaces (id, name, slug, color, created_at) VALUES (?, ?, ?, ?, ?)",
            (ws_id, name, slug, color, ts),
        ))
        for st_name, st_color, pos, is_won, is_lost in DEFAULT_STAGES:
            statements.append((
                "INSERT INTO stages (id, workspace_id, name, color, position, is_won, is_lost)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (new_id(), ws_id, st_name, st_color, pos, is_won, is_lost),
            ))
        for pos, (f_name, f_key, f_kind, f_opts) in enumerate(DEFAULT_FIELDS):
            statements.append((
                "INSERT INTO field_defs (id, workspace_id, name, key, kind, options, position)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (new_id(), ws_id, f_name, f_key, f_kind, json.dumps(f_opts), pos),
            ))
    execute_many(statements)


def backend_name() -> str:
    return "postgres" if IS_POSTGRES else "sqlite"
