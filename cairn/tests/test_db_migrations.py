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
        task_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
        task_source_columns = {row["name"] for row in conn.execute("PRAGMA table_info(task_sources)")}
        fact_columns = {row["name"] for row in conn.execute("PRAGMA table_info(facts)")}
        project_defaults = {
            row["name"]: row["dflt_value"]
            for row in conn.execute("PRAGMA table_info(projects)")
        }
        finding_columns = {row["name"] for row in conn.execute("PRAGMA table_info(findings)")}
        ephemeral_columns = {row["name"] for row in conn.execute("PRAGMA table_info(ephemeral_jobs)")}
        settings_columns = {row["name"] for row in conn.execute("PRAGMA table_info(settings)")}
        settings = conn.execute("SELECT * FROM settings WHERE rowid = 1").fetchone()
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    assert "mode" not in project_columns
    assert "bootstrap_enabled" not in project_columns
    assert "session_lock_enabled" not in project_columns
    assert "collection_max_reason_rounds" not in project_columns
    assert {"origin", "project_kind", "auth_mode", "parent_project_id", "parent_snapshot_id", "reason_pending"} <= project_columns
    assert {"id", "type", "description", "creation_time", "completion_time", "to", "worker"} <= task_columns
    assert {"task_id", "project_id", "source_id"} <= task_source_columns
    assert {"id", "type", "description", "creation_time", "from", "from_task", "to", "evidence"} <= fact_columns
    assert project_defaults["project_kind"] == "'vuln'"
    assert {"id", "type", "description", "creation_time", "from", "from_task", "to", "report"} <= finding_columns
    assert "input_json" in ephemeral_columns
    assert {"initial_collection_rounds", "collection_worker_limit"} <= settings_columns
    assert settings["initial_collection_rounds"] == 5
    assert settings["collection_worker_limit"] == 1
    assert {"project_accounts", "project_snapshots", "ephemeral_jobs", "tasks", "task_sources"} <= tables
    assert {"intents", "intent_sources", "finding_reports"}.isdisjoint(tables)


def test_new_database_has_blackboard_schema_without_public_legacy_tables(tmp_path, monkeypatch) -> None:
    path = tmp_path / "blackboard.db"
    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        project_columns = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
        task_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
        task_source_columns = {row["name"] for row in conn.execute("PRAGMA table_info(task_sources)")}
        fact_columns = {row["name"] for row in conn.execute("PRAGMA table_info(facts)")}
        finding_columns = {row["name"] for row in conn.execute("PRAGMA table_info(findings)")}

    assert {"tasks", "task_sources", "facts", "findings"} <= tables
    assert "intents" not in tables
    assert "intent_sources" not in tables
    assert "finding_reports" not in tables
    assert "origin" in project_columns
    assert {"id", "type", "description", "creation_time", "completion_time", "to", "worker"} <= task_columns
    assert {"task_id", "project_id", "source_id"} <= task_source_columns
    assert {"id", "type", "description", "creation_time", "from", "from_task", "to", "evidence"} <= fact_columns
    assert {"id", "type", "description", "creation_time", "from", "from_task", "to", "report"} <= finding_columns


def test_legacy_settings_table_gains_collection_scheduling_columns(tmp_path, monkeypatch) -> None:
    path = tmp_path / "legacy-settings.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE settings (
                intent_timeout INTEGER NOT NULL DEFAULT 15,
                reason_timeout INTEGER NOT NULL DEFAULT 15
            );
            INSERT INTO settings (rowid, intent_timeout, reason_timeout) VALUES (1, 30, 31);
            """
        )

    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM settings WHERE rowid = 1").fetchone()

    assert row["task_timeout"] == 30
    assert row["reason_timeout"] == 31
    assert row["initial_collection_rounds"] == 5
    assert row["collection_worker_limit"] == 1


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
        "idx_tasks_project_open_worker",
        "idx_hints_project_created",
        "idx_findings_project_created",
        "idx_project_accounts_project",
        "idx_project_snapshots_project_created",
        "idx_task_sources_project_task",
        "idx_ephemeral_jobs_queue",
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


def test_configure_failure_does_not_cache_failed_db_path(tmp_path, monkeypatch) -> None:
    bad_path = tmp_path / "standard.db"
    with sqlite3.connect(bad_path) as conn:
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
        db.configure(bad_path)

    assert db._db_path is None

    good_path = tmp_path / "new.db"
    db.configure(good_path)
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM projects").fetchone()["count"] == 0


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


def test_legacy_projects_rebuild_preserves_parent_project_foreign_key(tmp_path, monkeypatch) -> None:
    path = tmp_path / "legacy-parent-fk.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                project_kind TEXT NOT NULL DEFAULT 'vuln',
                auth_mode TEXT NOT NULL DEFAULT 'anonymous',
                parent_project_id TEXT REFERENCES projects(id) ON DELETE RESTRICT,
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
            INSERT INTO projects (id, title, project_kind, auth_mode, created_at)
            VALUES ('proj_001', 'legacy collection max', 'vuln', 'anonymous', '2026-01-01T00:00:00Z');
            """
        )

    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        foreign_keys = conn.execute("PRAGMA foreign_key_list(projects)").fetchall()

    assert any(
        row["from"] == "parent_project_id"
        and row["table"] == "projects"
        and row["on_delete"] == "RESTRICT"
        for row in foreign_keys
    )


def test_legacy_project_retired_columns_are_removed_without_session_lock(tmp_path, monkeypatch) -> None:
    path = tmp_path / "legacy-retired-columns.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                mode TEXT NOT NULL DEFAULT 'src',
                bootstrap_enabled INTEGER NOT NULL DEFAULT 1,
                recon_max_reason_rounds INTEGER,
                recon_reason_rounds INTEGER NOT NULL DEFAULT 0,
                recon_explore_rounds INTEGER NOT NULL DEFAULT 0,
                recon_stable_rounds INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                reason_worker TEXT,
                reason_trigger TEXT,
                reason_started_at TEXT,
                reason_last_heartbeat_at TEXT
            );
            INSERT INTO projects (
                id,
                title,
                mode,
                bootstrap_enabled,
                recon_max_reason_rounds,
                recon_reason_rounds,
                recon_explore_rounds,
                recon_stable_rounds,
                created_at
            ) VALUES (
                'proj_001',
                'legacy src',
                'src',
                1,
                8,
                3,
                4,
                1,
                '2026-01-01T00:00:00Z'
            );
            """
        )

    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        project_columns = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
        project = conn.execute(
            """
            SELECT project_kind, collection_reason_rounds, collection_explore_rounds, collection_stable_rounds
            FROM projects
            WHERE id = 'proj_001'
            """
        ).fetchone()

    assert {
        "mode",
        "bootstrap_enabled",
        "recon_max_reason_rounds",
        "recon_reason_rounds",
        "recon_explore_rounds",
        "recon_stable_rounds",
    }.isdisjoint(project_columns)
    assert project["project_kind"] == "vuln"
    assert project["collection_reason_rounds"] == 3
    assert project["collection_explore_rounds"] == 4
    assert project["collection_stable_rounds"] == 1


def test_legacy_intent_session_lock_column_is_removed_and_migrated_to_task(tmp_path, monkeypatch) -> None:
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
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
        row = conn.execute("SELECT id, type, description, auth_scope FROM tasks WHERE id = 't1'").fetchone()
    assert "intents" not in tables
    assert "session_lock" not in columns
    assert row["description"] == "old work"
    assert row["type"] == "vulnerability_task"
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


def test_legacy_recon_and_vuln_intents_migrate_to_task_types(tmp_path, monkeypatch) -> None:
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
        projects = {
            row["id"]: row["project_kind"]
            for row in conn.execute("SELECT id, project_kind FROM projects ORDER BY id")
        }
        tasks = {
            row["id"]: row["type"]
            for row in conn.execute("SELECT id, type FROM tasks ORDER BY id")
        }

    assert projects == {"proj_recon": "vuln", "proj_vuln": "vuln"}
    assert tasks == {"ti_recon": "collection_task", "ti_vuln": "vulnerability_task"}


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
        origin = conn.execute("SELECT origin FROM projects WHERE id = 'proj_001'").fetchone()["origin"]
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    assert fact_ids == ["f1"]
    assert origin == "start"
    assert "intent_sources" not in tables


def test_legacy_origin_fact_moves_to_project_origin(tmp_path, monkeypatch) -> None:
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
        project = conn.execute("SELECT origin FROM projects WHERE id = 'proj_001'").fetchone()
        fact_count = conn.execute("SELECT COUNT(*) AS count FROM facts WHERE project_id = 'proj_001'").fetchone()["count"]

    assert {"type", "creation_time", "from", "from_task", "to", "evidence"} <= columns
    assert project["origin"] == "https://target.test"
    assert fact_count == 0
