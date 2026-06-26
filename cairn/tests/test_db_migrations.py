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
        intent_column_rows = conn.execute("PRAGMA table_info(intents)").fetchall()
        intent_columns = {row["name"] for row in intent_column_rows}
        intent_defaults = {row["name"]: row["dflt_value"] for row in intent_column_rows}
        project_defaults = {
            row["name"]: row["dflt_value"]
            for row in conn.execute("PRAGMA table_info(projects)")
        }
        finding_columns = {row["name"] for row in conn.execute("PRAGMA table_info(findings)")}
        ephemeral_columns = {row["name"] for row in conn.execute("PRAGMA table_info(ephemeral_jobs)")}
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    assert "mode" not in project_columns
    assert "bootstrap_enabled" not in project_columns
    assert "session_lock_enabled" not in project_columns
    assert "collection_max_reason_rounds" not in project_columns
    assert {"project_kind", "auth_mode", "parent_project_id", "parent_snapshot_id", "reason_pending"} <= project_columns
    assert {"fact_type", "title", "summary", "details_json"} <= fact_columns
    assert {"intent_kind", "finding_id", "auth_scope", "task_mode"} <= intent_columns
    assert intent_defaults["task_mode"] == "'validation'"
    assert project_defaults["project_kind"] == "'vuln'"
    assert "session_lock" not in intent_columns
    assert {"research_value", "next_action", "report_status", "report_intent_id"} <= finding_columns
    assert "input_json" in ephemeral_columns
    assert {"project_accounts", "project_snapshots", "ephemeral_jobs", "finding_reports"} <= tables


def test_new_database_has_query_indexes(tmp_path, monkeypatch) -> None:
    path = tmp_path / "indexed.db"
    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        indexes = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }

    assert {
        "idx_facts_project",
        "idx_intents_project_open_worker",
        "idx_hints_project_created",
        "idx_findings_project_created",
        "idx_project_accounts_project",
        "idx_project_snapshots_project_created",
        "idx_intent_sources_project_intent",
        "idx_ephemeral_jobs_queue",
        "idx_finding_reports_project_created",
    } <= indexes


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


def test_legacy_recon_project_kind_migrates_to_vuln(tmp_path, monkeypatch) -> None:
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

    assert project["project_kind"] == "vuln"
    assert project["auth_mode"] == "dual"


def test_legacy_collection_max_reason_rounds_column_is_removed(tmp_path, monkeypatch) -> None:
    path = tmp_path / "legacy-collection-max.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                project_kind TEXT NOT NULL DEFAULT 'vuln',
                auth_mode TEXT NOT NULL DEFAULT 'anonymous',
                parent_project_id TEXT,
                parent_snapshot_id TEXT,
                created_at TEXT NOT NULL,
                reason_worker TEXT,
                reason_trigger TEXT,
                reason_started_at TEXT,
                reason_last_heartbeat_at TEXT,
                reason_pending INTEGER NOT NULL DEFAULT 0,
                collection_max_reason_rounds INTEGER,
                collection_reason_rounds INTEGER NOT NULL DEFAULT 0,
                collection_explore_rounds INTEGER NOT NULL DEFAULT 0,
                collection_stable_rounds INTEGER NOT NULL DEFAULT 0,
                judge_status TEXT NOT NULL DEFAULT 'not_judged',
                judged_at TEXT
            );
            INSERT INTO projects (
                id,
                title,
                project_kind,
                auth_mode,
                created_at,
                collection_max_reason_rounds,
                collection_reason_rounds,
                collection_explore_rounds,
                collection_stable_rounds
            ) VALUES (
                'proj_001',
                'legacy collection max',
                'vuln',
                'anonymous',
                '2026-01-01T00:00:00Z',
                8,
                4,
                5,
                1
            );
            """
        )

    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        project_columns = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
        project = conn.execute(
            """
            SELECT collection_reason_rounds, collection_explore_rounds, collection_stable_rounds
            FROM projects
            WHERE id = 'proj_001'
            """
        ).fetchone()

    assert "collection_max_reason_rounds" not in project_columns
    assert project["collection_reason_rounds"] == 4
    assert project["collection_explore_rounds"] == 5
    assert project["collection_stable_rounds"] == 1


def test_legacy_recon_and_vuln_intents_backfill_task_mode(tmp_path, monkeypatch) -> None:
    path = tmp_path / "legacy-task-mode.db"
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
            CREATE TABLE intents (
                id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                to_fact_id TEXT,
                description TEXT NOT NULL,
                creator TEXT NOT NULL,
                worker TEXT,
                last_heartbeat_at TEXT,
                created_at TEXT NOT NULL,
                concluded_at TEXT,
                intent_kind TEXT NOT NULL DEFAULT 'explore',
                finding_id TEXT,
                auth_scope TEXT,
                PRIMARY KEY (id, project_id)
            );
            INSERT INTO projects (id, title, project_kind, auth_mode, created_at)
            VALUES
                ('proj_recon', 'legacy recon', 'recon', 'dual', '2026-01-01T00:00:00Z'),
                ('proj_vuln', 'legacy vuln', 'vuln', 'anonymous', '2026-01-01T00:00:00Z');
            INSERT INTO intents (id, project_id, description, creator, created_at)
            VALUES
                ('i_recon', 'proj_recon', 'map app', 'reasoner', '2026-01-01T00:00:01Z'),
                ('i_vuln', 'proj_vuln', 'verify bug', 'reasoner', '2026-01-01T00:00:01Z');
            """
        )

    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(intents)")}
        projects = {
            row["id"]: row["project_kind"]
            for row in conn.execute("SELECT id, project_kind FROM projects ORDER BY id")
        }
        intents = {
            row["id"]: row["task_mode"]
            for row in conn.execute("SELECT id, task_mode FROM intents ORDER BY id")
        }

    assert "task_mode" in columns
    assert projects == {"proj_recon": "vuln", "proj_vuln": "vuln"}
    assert intents == {"i_recon": "collection", "i_vuln": "validation"}


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
