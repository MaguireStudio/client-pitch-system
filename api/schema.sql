-- Client Pitch System CRM schema.
-- Written to run on both Postgres (production) and SQLite (local dev).
-- Keep it to the common subset: TEXT ids, TEXT timestamps, no SERIAL, no JSONB.

CREATE TABLE IF NOT EXISTS workspaces (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL UNIQUE,
    color       TEXT NOT NULL DEFAULT '#2563eb',
    created_at  TEXT NOT NULL
);

-- Pipeline stages are per-workspace so each business gets its own funnel.
CREATE TABLE IF NOT EXISTS stages (
    id          TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    color       TEXT NOT NULL DEFAULT '#64748b',
    position    INTEGER NOT NULL DEFAULT 0,
    is_won      INTEGER NOT NULL DEFAULT 0,
    is_lost     INTEGER NOT NULL DEFAULT 0
);

-- Notion-style custom field definitions.
-- kind: text | number | select | multi_select | date | checkbox | url | email | phone
-- options: JSON array of {label,color}, used by select / multi_select.
CREATE TABLE IF NOT EXISTS field_defs (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    key          TEXT NOT NULL,
    kind         TEXT NOT NULL,
    options      TEXT NOT NULL DEFAULT '[]',
    position     INTEGER NOT NULL DEFAULT 0,
    UNIQUE (workspace_id, key)
);

CREATE TABLE IF NOT EXISTS contacts (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    stage_id     TEXT REFERENCES stages(id) ON DELETE SET NULL,
    name         TEXT NOT NULL,
    email        TEXT NOT NULL DEFAULT '',
    phone        TEXT NOT NULL DEFAULT '',
    company      TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT 'web',
    notes        TEXT NOT NULL DEFAULT '',
    value        REAL NOT NULL DEFAULT 0,
    fields       TEXT NOT NULL DEFAULT '{}',   -- JSON object of custom field values
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- direction: in | out      channel: sms | email | note
-- status: queued | sent | delivered | failed | received
CREATE TABLE IF NOT EXISTS messages (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    contact_id   TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    channel      TEXT NOT NULL,
    direction    TEXT NOT NULL,
    subject      TEXT NOT NULL DEFAULT '',
    body         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'queued',
    error        TEXT NOT NULL DEFAULT '',
    provider_id  TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activities (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    contact_id   TEXT REFERENCES contacts(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,
    detail       TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_contacts_workspace ON contacts(workspace_id);
CREATE INDEX IF NOT EXISTS idx_contacts_stage ON contacts(stage_id);
CREATE INDEX IF NOT EXISTS idx_messages_contact ON messages(contact_id, created_at);
CREATE INDEX IF NOT EXISTS idx_stages_workspace ON stages(workspace_id, position);
CREATE INDEX IF NOT EXISTS idx_fielddefs_workspace ON field_defs(workspace_id, position);
CREATE INDEX IF NOT EXISTS idx_activities_workspace ON activities(workspace_id, created_at);
