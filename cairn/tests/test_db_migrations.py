from __future__ import annotations

import sqlite3

import pytest

from cairn.server import db


def test_new_database_has_src_only_schema(tmp_path, monkeypatch) -> None:
    path = tmp_path / "new.db"
    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        project_columns = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
        fact_columns = {row["name"] for row in conn.execute("PRAGMA table_info(facts)")}
        intent_columns = {row["name"] for row in conn.execute("PRAGMA table_info(intents)")}
        finding_columns = {row["name"] for row in conn.execute("PRAGMA table_info(findings)")}
        ephemeral_columns = {row["name"] for row in conn.execute("PRAGMA table_info(ephemeral_jobs)")}
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    assert "mode" not in project_columns
    assert "bootstrap_enabled" not in project_columns
    assert "session_lock_enabled" not in project_columns
    assert {"project_kind", "auth_mode", "parent_project_id", "parent_snapshot_id", "reason_pending"} <= project_columns
    assert {"fact_type", "title", "summary", "details_json"} <= fact_columns
    assert {"intent_kind", "finding_id", "auth_scope"} <= intent_columns
    assert "session_lock" not in intent_columns
    assert {"research_value", "next_action", "report_status", "report_intent_id"} <= finding_columns
    assert "input_json" in ephemeral_columns
    assert {"project_accounts", "project_snapshots", "ephemeral_jobs", "finding_reports"} <= tables


def test_legacy_standard_project_blocks_startup(tmp_path, monkeypatch) -> None:
    path = tmp_path / "standard.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                mode TEXT NOT NULL DEFAULT 'standard',
                created_at TEXT NOT NULL
            );
            INSERT INTO projects (id, title, mode, created_at)
            VALUES ('proj_001', 'legacy standard', 'standard', '2026-01-01T00:00:00Z');
            """
        )

    monkeypatch.setattr(db, "_db_path", None)
    with pytest.raises(RuntimeError, match="Standard workflow has been removed"):
        db.configure(path)


def test_legacy_src_project_migrates_to_parentless_vuln_and_drops_password_accounts(tmp_path, monkeypatch) -> None:
    path = tmp_path / "src.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                mode TEXT NOT NULL DEFAULT 'src',
                auth_mode TEXT NOT NULL DEFAULT 'authenticated',
                session_lock_enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                reason_worker TEXT,
                reason_trigger TEXT,
                reason_started_at TEXT,
                reason_last_heartbeat_at TEXT
            );
            CREATE TABLE project_accounts (
                id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                label TEXT NOT NULL,
                username TEXT NOT NULL,
                password TEXT NOT NULL,
                PRIMARY KEY (id, project_id)
            );
            INSERT INTO projects (id, title, mode, auth_mode, created_at)
            VALUES ('proj_001', 'legacy src', 'src', 'authenticated', '2026-01-01T00:00:00Z');
            INSERT INTO project_accounts (id, project_id, label, username, password)
            VALUES ('a001', 'proj_001', 'alice', 'alice@example.test', 'secret');
            """
        )

    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        project_columns = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
        project = conn.execute(
            "SELECT project_kind, auth_mode, parent_project_id, parent_snapshot_id, reason_pending FROM projects WHERE id = 'proj_001'"
        ).fetchone()
        account = conn.execute(
            "SELECT label, cookies_json FROM project_accounts WHERE project_id = 'proj_001'"
        ).fetchone()
        account_columns = {row["name"] for row in conn.execute("PRAGMA table_info(project_accounts)")}

    assert project["project_kind"] == "vuln"
    assert project["auth_mode"] == "authenticated"
    assert project["parent_project_id"] is None
    assert project["parent_snapshot_id"] is None
    assert project["reason_pending"] == 0
    assert account is None
    assert "cookies_json" in account_columns
    assert "username" not in account_columns
    assert "password" not in account_columns
    assert "session_lock_enabled" not in project_columns


def test_legacy_intent_session_lock_column_is_removed_and_new_columns_added(tmp_path, monkeypatch) -> None:
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
            INSERT INTO projects (id, title, created_at)
            VALUES ('proj_001', 'legacy', '2026-01-01T00:00:00Z');
            INSERT INTO intents (id, project_id, description, creator, created_at)
            VALUES ('i001', 'proj_001', 'old work', 'reasoner', '2026-01-01T00:00:01Z');
            """
        )

    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(intents)")}
        row = conn.execute("SELECT id, description, intent_kind, finding_id, auth_scope FROM intents WHERE id = 'i001'").fetchone()
    assert "session_lock" not in columns
    assert row["description"] == "old work"
    assert row["intent_kind"] == "explore"
    assert row["finding_id"] is None
    assert row["auth_scope"] == "anonymous"


def test_legacy_recon_project_migrates_to_dual_auth_mode(tmp_path, monkeypatch) -> None:
    path = tmp_path / "legacy-recon.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                project_kind TEXT NOT NULL DEFAULT 'recon',
                auth_mode TEXT NOT NULL DEFAULT 'anonymous',
                created_at TEXT NOT NULL
            );
            INSERT INTO projects (id, title, project_kind, auth_mode, created_at)
            VALUES ('proj_001', 'legacy recon', 'recon', 'anonymous', '2026-01-01T00:00:00Z');
            """
        )

    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        project = conn.execute("SELECT project_kind, auth_mode FROM projects WHERE id = 'proj_001'").fetchone()

    assert project["project_kind"] == "recon"
    assert project["auth_mode"] == "dual"


def test_legacy_goal_facts_are_removed_on_startup(tmp_path, monkeypatch) -> None:
    path = tmp_path / "goal-facts.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL
            );
            CREATE TABLE facts (
                id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                description TEXT NOT NULL,
                PRIMARY KEY (id, project_id)
            );
            CREATE TABLE intent_sources (
                intent_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                fact_id TEXT NOT NULL,
                PRIMARY KEY (intent_id, project_id, fact_id)
            );
            INSERT INTO projects (id, title, created_at)
            VALUES ('proj_001', 'legacy', '2026-01-01T00:00:00Z');
            INSERT INTO facts (id, project_id, description)
            VALUES
                ('origin', 'proj_001', 'start'),
                ('goal', 'proj_001', 'finish'),
                ('f001', 'proj_001', 'known');
            INSERT INTO intent_sources (intent_id, project_id, fact_id)
            VALUES ('i001', 'proj_001', 'goal');
            """
        )

    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        fact_ids = [
            row["id"]
            for row in conn.execute("SELECT id FROM facts WHERE project_id = 'proj_001' ORDER BY id")
        ]
        source_ids = [
            row["fact_id"]
            for row in conn.execute("SELECT fact_id FROM intent_sources WHERE project_id = 'proj_001'")
        ]

    assert fact_ids == ["f001", "origin"]
    assert source_ids == []


def test_legacy_facts_gain_structured_fact_columns(tmp_path, monkeypatch) -> None:
    path = tmp_path / "legacy-facts.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL
            );
            CREATE TABLE facts (
                id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                description TEXT NOT NULL,
                PRIMARY KEY (id, project_id)
            );
            INSERT INTO projects (id, title, created_at)
            VALUES ('proj_001', 'legacy', '2026-01-01T00:00:00Z');
            INSERT INTO facts (id, project_id, description)
            VALUES ('origin', 'proj_001', 'https://target.test');
            """
        )

    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(facts)")}
        fact = conn.execute(
            "SELECT fact_type, title, summary, details_json FROM facts WHERE id = 'origin'"
        ).fetchone()

    assert {"fact_type", "title", "summary", "details_json"} <= columns
    assert fact["fact_type"] == "observation"
    assert fact["title"] is None
    assert fact["summary"] is None
    assert fact["details_json"] == "{}"
