from __future__ import annotations

import sqlite3

from cairn.server import db


def test_configure_adds_auth_mode_and_drops_legacy_session_lock_project_column(tmp_path, monkeypatch) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                session_lock_enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                reason_worker TEXT,
                reason_trigger TEXT,
                reason_started_at TEXT,
                reason_last_heartbeat_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO projects (id, title, created_at) VALUES ('proj_001', 'legacy', '2026-01-01T00:00:00Z')"
        )

    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
        row = conn.execute("SELECT bootstrap_enabled, auth_mode FROM projects WHERE id = 'proj_001'").fetchone()
        account_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'project_accounts'"
        ).fetchone()
    assert row["bootstrap_enabled"] == 1
    assert row["auth_mode"] == "anonymous"
    assert "session_lock_enabled" not in columns
    assert account_table is not None


def test_configure_maps_disabled_bootstrap_mode_to_false(tmp_path, monkeypatch) -> None:
    path = tmp_path / "intermediate.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                bootstrap_mode TEXT NOT NULL DEFAULT 'auto',
                created_at TEXT NOT NULL,
                reason_worker TEXT,
                reason_trigger TEXT,
                reason_started_at TEXT,
                reason_last_heartbeat_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO projects (id, title, bootstrap_mode, created_at) VALUES ('proj_001', 'disabled', 'disabled', '2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO projects (id, title, bootstrap_mode, created_at) VALUES ('proj_002', 'enabled', 'enabled', '2026-01-01T00:00:00Z')"
        )

    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, bootstrap_enabled FROM projects ORDER BY id").fetchall()
    assert [(row["id"], row["bootstrap_enabled"]) for row in rows] == [
        ("proj_001", 0),
        ("proj_002", 1),
    ]


def test_configure_drops_legacy_session_lock_intent_column(tmp_path, monkeypatch) -> None:
    path = tmp_path / "legacy-intents.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                reason_worker TEXT,
                reason_trigger TEXT,
                reason_started_at TEXT,
                reason_last_heartbeat_at TEXT
            );
            CREATE TABLE intents (
                id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                to_fact_id TEXT,
                description TEXT NOT NULL,
                creator TEXT NOT NULL,
                worker TEXT,
                last_heartbeat_at TEXT,
                session_lock INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                concluded_at TEXT,
                PRIMARY KEY (id, project_id)
            );
            """
        )
        conn.execute(
            "INSERT INTO projects (id, title, created_at) VALUES ('proj_001', 'legacy', '2026-01-01T00:00:00Z')"
        )
        conn.execute(
            """
            INSERT INTO intents (
                id,
                project_id,
                description,
                creator,
                created_at
            ) VALUES ('i001', 'proj_001', 'old work', 'reasoner', '2026-01-01T00:00:01Z')
            """
        )

    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(intents)")}
        row = conn.execute("SELECT id, description FROM intents WHERE id = 'i001'").fetchone()
    assert "session_lock" not in columns
    assert row["description"] == "old work"
