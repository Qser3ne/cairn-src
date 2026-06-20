from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

DEFAULT_DB = Path.home() / ".local" / "share" / "cairn" / "cairn.db"

_db_path: Path | None = None

SCHEMA = """\
CREATE TABLE IF NOT EXISTS settings (
    intent_timeout INTEGER NOT NULL DEFAULT 15,
    reason_timeout INTEGER NOT NULL DEFAULT 15
);

INSERT OR IGNORE INTO settings (rowid, intent_timeout, reason_timeout) VALUES (1, 15, 15);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    mode TEXT NOT NULL DEFAULT 'standard',
    auth_mode TEXT NOT NULL DEFAULT 'anonymous',
    bootstrap_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    reason_worker TEXT,
    reason_trigger TEXT,
    reason_started_at TEXT,
    reason_last_heartbeat_at TEXT
);

CREATE TABLE IF NOT EXISTS facts (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    PRIMARY KEY (id, project_id)
);

CREATE TABLE IF NOT EXISTS intents (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    to_fact_id TEXT,
    description TEXT NOT NULL,
    creator TEXT NOT NULL,
    worker TEXT,
    last_heartbeat_at TEXT,
    created_at TEXT NOT NULL,
    concluded_at TEXT,
    PRIMARY KEY (id, project_id)
);

CREATE TABLE IF NOT EXISTS intent_sources (
    intent_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    fact_id TEXT NOT NULL,
    PRIMARY KEY (intent_id, project_id, fact_id),
    FOREIGN KEY (intent_id, project_id) REFERENCES intents(id, project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS hints (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    creator TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id, project_id)
);

CREATE TABLE IF NOT EXISTS project_accounts (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    username TEXT NOT NULL,
    password TEXT NOT NULL,
    PRIMARY KEY (id, project_id)
);

CREATE TABLE IF NOT EXISTS findings (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    fact_id TEXT NOT NULL,
    intent_id TEXT NOT NULL,
    title TEXT NOT NULL,
    vulnerability_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    target TEXT NOT NULL,
    location TEXT NOT NULL,
    impact TEXT NOT NULL,
    evidence TEXT NOT NULL,
    reproduction TEXT NOT NULL,
    remediation TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id, project_id),
    FOREIGN KEY (fact_id, project_id) REFERENCES facts(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (intent_id, project_id) REFERENCES intents(id, project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS counters (
    name TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);

INSERT OR IGNORE INTO counters (name, value) VALUES ('project', 0);

CREATE TABLE IF NOT EXISTS scoped_counters (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    value INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (project_id, kind)
);
"""


def configure(path: Path) -> None:
    global _db_path
    if _db_path is not None:
        return
    _db_path = path
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate_project_table(conn)
        _migrate_intent_table(conn)
        _ensure_project_accounts_table(conn)
        _ensure_findings_table(conn)


def _migrate_project_table(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
    if "mode" not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN mode TEXT NOT NULL DEFAULT 'standard'")
        columns.add("mode")
    if "bootstrap_enabled" not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN bootstrap_enabled INTEGER NOT NULL DEFAULT 1")
        if "bootstrap_mode" in columns:
            conn.execute(
                "UPDATE projects SET bootstrap_enabled = CASE WHEN bootstrap_mode = 'disabled' THEN 0 ELSE 1 END"
            )
        columns.add("bootstrap_enabled")
    if "auth_mode" not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN auth_mode TEXT NOT NULL DEFAULT 'anonymous'")
        columns.add("auth_mode")
    if "session_lock_enabled" not in columns:
        return

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        """
        CREATE TABLE projects_new (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            mode TEXT NOT NULL DEFAULT 'standard',
            auth_mode TEXT NOT NULL DEFAULT 'anonymous',
            bootstrap_enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            reason_worker TEXT,
            reason_trigger TEXT,
            reason_started_at TEXT,
            reason_last_heartbeat_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO projects_new (
            id,
            title,
            status,
            mode,
            auth_mode,
            bootstrap_enabled,
            created_at,
            reason_worker,
            reason_trigger,
            reason_started_at,
            reason_last_heartbeat_at
        )
        SELECT
            id,
            title,
            status,
            mode,
            auth_mode,
            bootstrap_enabled,
            created_at,
            reason_worker,
            reason_trigger,
            reason_started_at,
            reason_last_heartbeat_at
        FROM projects
        """
    )
    conn.execute("DROP TABLE projects")
    conn.execute("ALTER TABLE projects_new RENAME TO projects")
    conn.execute("PRAGMA foreign_keys=ON")


def _migrate_intent_table(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(intents)")}
    if "session_lock" not in columns:
        return

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        """
        CREATE TABLE intents_new (
            id TEXT NOT NULL,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            to_fact_id TEXT,
            description TEXT NOT NULL,
            creator TEXT NOT NULL,
            worker TEXT,
            last_heartbeat_at TEXT,
            created_at TEXT NOT NULL,
            concluded_at TEXT,
            PRIMARY KEY (id, project_id)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO intents_new (
            id,
            project_id,
            to_fact_id,
            description,
            creator,
            worker,
            last_heartbeat_at,
            created_at,
            concluded_at
        )
        SELECT
            id,
            project_id,
            to_fact_id,
            description,
            creator,
            worker,
            last_heartbeat_at,
            created_at,
            concluded_at
        FROM intents
        """
    )
    conn.execute("DROP TABLE intents")
    conn.execute("ALTER TABLE intents_new RENAME TO intents")
    conn.execute("PRAGMA foreign_keys=ON")


def _ensure_project_accounts_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_accounts (
            id TEXT NOT NULL,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            label TEXT NOT NULL,
            username TEXT NOT NULL,
            password TEXT NOT NULL,
            PRIMARY KEY (id, project_id)
        )
        """
    )


def _ensure_findings_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS findings (
            id TEXT NOT NULL,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            fact_id TEXT NOT NULL,
            intent_id TEXT NOT NULL,
            title TEXT NOT NULL,
            vulnerability_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            target TEXT NOT NULL,
            location TEXT NOT NULL,
            impact TEXT NOT NULL,
            evidence TEXT NOT NULL,
            reproduction TEXT NOT NULL,
            remediation TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (id, project_id),
            FOREIGN KEY (fact_id, project_id) REFERENCES facts(id, project_id) ON DELETE CASCADE,
            FOREIGN KEY (intent_id, project_id) REFERENCES intents(id, project_id) ON DELETE CASCADE
        )
        """
    )


@contextmanager
def get_conn() -> Generator[sqlite3.Connection, None, None]:
    assert _db_path is not None
    conn = sqlite3.connect(str(_db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
