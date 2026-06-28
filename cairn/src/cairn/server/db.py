from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

DEFAULT_DB = Path.home() / ".local" / "share" / "cairn" / "cairn.db"

_db_path: Path | None = None

RETIRED_PROJECT_COLUMNS = {
    "mode",
    "bootstrap_enabled",
    "session_lock_enabled",
    "recon_max_reason_rounds",
    "recon_reason_rounds",
    "recon_explore_rounds",
    "recon_stable_rounds",
    "collection_max_reason_rounds",
}

SCHEMA = """\
CREATE TABLE IF NOT EXISTS settings (
    task_timeout INTEGER NOT NULL DEFAULT 15,
    reason_timeout INTEGER NOT NULL DEFAULT 15,
    initial_collection_rounds INTEGER NOT NULL DEFAULT 5,
    collection_worker_limit INTEGER NOT NULL DEFAULT 1
);

INSERT OR IGNORE INTO settings (rowid, task_timeout, reason_timeout) VALUES (1, 15, 15);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT '',
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
    collection_reason_rounds INTEGER NOT NULL DEFAULT 0,
    collection_explore_rounds INTEGER NOT NULL DEFAULT 0,
    collection_stable_rounds INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    description TEXT NOT NULL,
    creation_time TEXT NOT NULL,
    completion_time TEXT,
    "to" TEXT NOT NULL DEFAULT '[]',
    worker TEXT,
    last_heartbeat_at TEXT,
    auth_scope TEXT,
    PRIMARY KEY (id, project_id)
);

CREATE TABLE IF NOT EXISTS task_sources (
    task_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    PRIMARY KEY (task_id, project_id, source_id),
    FOREIGN KEY (task_id, project_id) REFERENCES tasks(id, project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS facts (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    description TEXT NOT NULL,
    creation_time TEXT NOT NULL,
    "from" TEXT NOT NULL DEFAULT '[]',
    from_task TEXT NOT NULL,
    "to" TEXT NOT NULL DEFAULT '[]',
    evidence TEXT NOT NULL,
    PRIMARY KEY (id, project_id),
    FOREIGN KEY (from_task, project_id) REFERENCES tasks(id, project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS findings (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    type TEXT NOT NULL DEFAULT 'findings',
    description TEXT NOT NULL,
    creation_time TEXT NOT NULL,
    "from" TEXT NOT NULL DEFAULT '[]',
    from_task TEXT NOT NULL,
    "to" TEXT NOT NULL DEFAULT '[]',
    report TEXT,
    PRIMARY KEY (id, project_id),
    FOREIGN KEY (from_task, project_id) REFERENCES tasks(id, project_id) ON DELETE CASCADE
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
    cookies_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (id, project_id)
);

CREATE TABLE IF NOT EXISTS project_snapshots (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    snapshot_type TEXT NOT NULL,
    summary_yaml TEXT NOT NULL,
    selected_fact_ids_json TEXT NOT NULL DEFAULT '[]',
    stats_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    PRIMARY KEY (id, project_id)
);

CREATE TABLE IF NOT EXISTS ephemeral_jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    input_snapshot_yaml TEXT NOT NULL,
    input_json TEXT,
    result_json TEXT,
    error TEXT,
    worker TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_reason_leases (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    task_mode TEXT NOT NULL,
    worker TEXT NOT NULL,
    trigger TEXT NOT NULL,
    started_at TEXT NOT NULL,
    last_heartbeat_at TEXT NOT NULL,
    PRIMARY KEY (project_id, task_mode)
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
    path.parent.mkdir(parents=True, exist_ok=True)
    previous_path = _db_path
    _db_path = path
    try:
        with get_conn() as conn:
            _ensure_no_legacy_standard_projects(conn)
            legacy = _capture_legacy_graph(conn)
            if legacy["has_legacy_graph"]:
                _drop_legacy_graph_tables(conn)
            _ensure_settings_columns(conn)
            conn.executescript(SCHEMA)
            _ensure_settings_columns(conn)
            _ensure_project_columns(conn)
            _ensure_project_accounts_table(conn)
            _ensure_project_snapshots_table(conn)
            _ensure_ephemeral_jobs_table(conn)
            _ensure_project_reason_leases_table(conn)
            _migrate_legacy_graph(conn, legacy)
            _ensure_counters(conn)
            _ensure_indexes(conn)
    except Exception:
        _db_path = previous_path
        raise


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _fetch_table(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    if not _table_exists(conn, table):
        return []
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table}").fetchall()]


def _capture_legacy_graph(conn: sqlite3.Connection) -> dict[str, Any]:
    fact_columns = _columns(conn, "facts")
    has_new_facts = {"type", "creation_time", "from_task", "evidence"} <= fact_columns
    has_legacy_graph = (
        _table_exists(conn, "intents")
        or _table_exists(conn, "intent_sources")
        or _table_exists(conn, "finding_reports")
        or (_table_exists(conn, "facts") and not has_new_facts)
    )
    return {
        "has_legacy_graph": has_legacy_graph,
        "projects": _fetch_table(conn, "projects") if has_legacy_graph else [],
        "facts": _fetch_table(conn, "facts") if has_legacy_graph else [],
        "intents": _fetch_table(conn, "intents") if has_legacy_graph else [],
        "intent_sources": _fetch_table(conn, "intent_sources") if has_legacy_graph else [],
        "findings": _fetch_table(conn, "findings") if has_legacy_graph else [],
        "finding_reports": _fetch_table(conn, "finding_reports") if has_legacy_graph else [],
    }


def _drop_legacy_graph_tables(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys=OFF")
    for table in ("finding_reports", "intent_sources", "intents", "findings", "facts"):
        if _table_exists(conn, table):
            conn.execute(f"DROP TABLE {table}")
    conn.execute("PRAGMA foreign_keys=ON")


def _ensure_no_legacy_standard_projects(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "projects"):
        return
    if "mode" not in _columns(conn, "projects"):
        return
    row = conn.execute("SELECT COUNT(*) AS count FROM projects WHERE mode = 'standard'").fetchone()
    if row and row["count"] > 0:
        raise RuntimeError(
            "Standard workflow has been removed. "
            "Export or delete legacy standard projects before migrating this database."
        )


def _ensure_settings_columns(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "settings"):
        conn.execute(
            """
            CREATE TABLE settings (
                task_timeout INTEGER NOT NULL DEFAULT 15,
                reason_timeout INTEGER NOT NULL DEFAULT 15,
                initial_collection_rounds INTEGER NOT NULL DEFAULT 5,
                collection_worker_limit INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute("INSERT OR IGNORE INTO settings (rowid, task_timeout, reason_timeout) VALUES (1, 15, 15)")
        return
    columns = _columns(conn, "settings")
    if "task_timeout" not in columns:
        conn.execute("ALTER TABLE settings ADD COLUMN task_timeout INTEGER NOT NULL DEFAULT 15")
        if "intent_timeout" in columns:
            conn.execute("UPDATE settings SET task_timeout = intent_timeout WHERE rowid = 1")
    if "initial_collection_rounds" not in columns:
        conn.execute("ALTER TABLE settings ADD COLUMN initial_collection_rounds INTEGER NOT NULL DEFAULT 5")
    if "collection_worker_limit" not in columns:
        conn.execute("ALTER TABLE settings ADD COLUMN collection_worker_limit INTEGER NOT NULL DEFAULT 1")


def _ensure_project_columns(conn: sqlite3.Connection) -> None:
    columns = _columns(conn, "projects")
    additions = {
        "origin": "ALTER TABLE projects ADD COLUMN origin TEXT NOT NULL DEFAULT ''",
        "project_kind": "ALTER TABLE projects ADD COLUMN project_kind TEXT NOT NULL DEFAULT 'vuln'",
        "auth_mode": "ALTER TABLE projects ADD COLUMN auth_mode TEXT NOT NULL DEFAULT 'anonymous'",
        "parent_project_id": "ALTER TABLE projects ADD COLUMN parent_project_id TEXT",
        "parent_snapshot_id": "ALTER TABLE projects ADD COLUMN parent_snapshot_id TEXT",
        "reason_worker": "ALTER TABLE projects ADD COLUMN reason_worker TEXT",
        "reason_trigger": "ALTER TABLE projects ADD COLUMN reason_trigger TEXT",
        "reason_started_at": "ALTER TABLE projects ADD COLUMN reason_started_at TEXT",
        "reason_last_heartbeat_at": "ALTER TABLE projects ADD COLUMN reason_last_heartbeat_at TEXT",
        "reason_pending": "ALTER TABLE projects ADD COLUMN reason_pending INTEGER NOT NULL DEFAULT 0",
        "collection_reason_rounds": "ALTER TABLE projects ADD COLUMN collection_reason_rounds INTEGER NOT NULL DEFAULT 0",
        "collection_explore_rounds": "ALTER TABLE projects ADD COLUMN collection_explore_rounds INTEGER NOT NULL DEFAULT 0",
        "collection_stable_rounds": "ALTER TABLE projects ADD COLUMN collection_stable_rounds INTEGER NOT NULL DEFAULT 0",
    }
    for name, ddl in additions.items():
        if name not in columns:
            conn.execute(ddl)
            columns.add(name)
    if "mode" in columns:
        conn.execute("UPDATE projects SET project_kind = 'vuln' WHERE mode = 'src'")
    if "recon_reason_rounds" in columns:
        conn.execute("UPDATE projects SET collection_reason_rounds = recon_reason_rounds")
    if "recon_explore_rounds" in columns:
        conn.execute("UPDATE projects SET collection_explore_rounds = recon_explore_rounds")
    if "recon_stable_rounds" in columns:
        conn.execute("UPDATE projects SET collection_stable_rounds = recon_stable_rounds")
    conn.execute("UPDATE projects SET auth_mode = 'dual' WHERE project_kind = 'recon'")
    conn.execute("UPDATE projects SET project_kind = 'vuln' WHERE project_kind != 'vuln'")
    if columns & RETIRED_PROJECT_COLUMNS:
        _rebuild_projects_table(conn)


def _rebuild_projects_table(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        """
        CREATE TABLE projects_new (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            origin TEXT NOT NULL DEFAULT '',
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
            collection_reason_rounds INTEGER NOT NULL DEFAULT 0,
            collection_explore_rounds INTEGER NOT NULL DEFAULT 0,
            collection_stable_rounds INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        INSERT INTO projects_new (
            id, title, origin, status, project_kind, auth_mode,
            parent_project_id, parent_snapshot_id, created_at,
            reason_worker, reason_trigger, reason_started_at, reason_last_heartbeat_at,
            reason_pending, collection_reason_rounds, collection_explore_rounds, collection_stable_rounds
        )
        SELECT
            id, title, origin, status, 'vuln', auth_mode,
            parent_project_id, parent_snapshot_id, created_at,
            reason_worker, reason_trigger, reason_started_at, reason_last_heartbeat_at,
            reason_pending, collection_reason_rounds, collection_explore_rounds, collection_stable_rounds
        FROM projects
        """
    )
    conn.execute("DROP TABLE projects")
    conn.execute("ALTER TABLE projects_new RENAME TO projects")
    conn.execute("PRAGMA foreign_keys=ON")


def _ensure_project_accounts_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_accounts (
            id TEXT NOT NULL,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            label TEXT NOT NULL,
            cookies_json TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY (id, project_id)
        )
        """
    )
    columns = _columns(conn, "project_accounts")
    if "cookies_json" in columns and "username" not in columns and "password" not in columns:
        return
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        """
        CREATE TABLE project_accounts_new (
            id TEXT NOT NULL,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            label TEXT NOT NULL,
            cookies_json TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY (id, project_id)
        )
        """
    )
    if "cookies_json" in columns:
        conn.execute(
            """
            INSERT INTO project_accounts_new (id, project_id, label, cookies_json)
            SELECT id, project_id, label, cookies_json FROM project_accounts
            """
        )
    conn.execute("DROP TABLE project_accounts")
    conn.execute("ALTER TABLE project_accounts_new RENAME TO project_accounts")
    conn.execute("PRAGMA foreign_keys=ON")


def _ensure_project_snapshots_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_snapshots (
            id TEXT NOT NULL,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            snapshot_type TEXT NOT NULL,
            summary_yaml TEXT NOT NULL,
            selected_fact_ids_json TEXT NOT NULL DEFAULT '[]',
            stats_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            PRIMARY KEY (id, project_id)
        )
        """
    )


def _ensure_ephemeral_jobs_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ephemeral_jobs (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            job_type TEXT NOT NULL,
            status TEXT NOT NULL,
            input_snapshot_yaml TEXT NOT NULL,
            input_json TEXT,
            result_json TEXT,
            error TEXT,
            worker TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            expires_at TEXT NOT NULL
        )
        """
    )
    columns = _columns(conn, "ephemeral_jobs")
    if "input_json" not in columns:
        conn.execute("ALTER TABLE ephemeral_jobs ADD COLUMN input_json TEXT")
    if "worker" not in columns:
        conn.execute("ALTER TABLE ephemeral_jobs ADD COLUMN worker TEXT")


def _ensure_project_reason_leases_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_reason_leases (
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            task_mode TEXT NOT NULL,
            worker TEXT NOT NULL,
            trigger TEXT NOT NULL,
            started_at TEXT NOT NULL,
            last_heartbeat_at TEXT NOT NULL,
            PRIMARY KEY (project_id, task_mode)
        )
        """
    )
    if not {"reason_worker", "reason_trigger", "reason_started_at", "reason_last_heartbeat_at"} <= _columns(conn, "projects"):
        return
    conn.execute(
        """
        INSERT OR IGNORE INTO project_reason_leases (
            project_id, task_mode, worker, trigger, started_at, last_heartbeat_at
        )
        SELECT id, 'collection', reason_worker, reason_trigger, reason_started_at, reason_last_heartbeat_at
        FROM projects
        WHERE reason_worker IS NOT NULL
          AND reason_trigger IS NOT NULL
          AND reason_started_at IS NOT NULL
          AND reason_last_heartbeat_at IS NOT NULL
        """
    )


def _migrate_legacy_graph(conn: sqlite3.Connection, legacy: dict[str, Any]) -> None:
    if not legacy["has_legacy_graph"]:
        return
    project_rows = conn.execute("SELECT id, title, created_at, origin FROM projects").fetchall()
    project_created = {row["id"]: row["created_at"] for row in project_rows}
    project_titles = {row["id"]: row["title"] for row in project_rows}
    legacy_project_kind = {
        project["id"]: project.get("project_kind") or project.get("mode")
        for project in legacy.get("projects", [])
    }

    origin_by_project: dict[str, str] = {}
    for fact in legacy["facts"]:
        if fact.get("id") == "origin":
            origin_by_project[fact["project_id"]] = fact.get("description") or project_titles.get(fact["project_id"], "")
    for project_id, title in project_titles.items():
        origin = origin_by_project.get(project_id, title)
        conn.execute("UPDATE projects SET origin = ? WHERE id = ? AND origin = ''", (origin, project_id))

    intent_to_task: dict[tuple[str, str], str] = {}
    for intent in legacy["intents"]:
        project_id = intent["project_id"]
        old_id = intent["id"]
        task_id = _legacy_prefixed_id(old_id, "i", "t")
        task_mode = intent.get("task_mode")
        if task_mode is None:
            task_mode = "collection" if legacy_project_kind.get(project_id) == "recon" else "vulnerability"
        task_type = "collection_task" if task_mode == "collection" else "vulnerability_task"
        to_ids = [intent["to_fact_id"]] if intent.get("to_fact_id") else []
        conn.execute(
            """
            INSERT OR IGNORE INTO tasks (
                id, project_id, type, description, creation_time, completion_time,
                "to", worker, last_heartbeat_at, auth_scope
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                project_id,
                task_type,
                intent.get("description") or "Migrated legacy task",
                intent.get("created_at") or project_created.get(project_id, ""),
                intent.get("concluded_at"),
                json.dumps(to_ids, ensure_ascii=False),
                intent.get("worker"),
                intent.get("last_heartbeat_at"),
                intent.get("auth_scope") or ("anonymous" if task_type != "report" else None),
            ),
        )
        intent_to_task[(project_id, old_id)] = task_id

    for source in legacy["intent_sources"]:
        task_id = intent_to_task.get((source["project_id"], source["intent_id"]))
        if task_id is None:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO task_sources (task_id, project_id, source_id) VALUES (?, ?, ?)",
            (task_id, source["project_id"], source["fact_id"]),
        )

    fact_to_task: dict[tuple[str, str], str] = {}
    for intent in legacy["intents"]:
        if intent.get("to_fact_id"):
            task_id = intent_to_task.get((intent["project_id"], intent["id"]))
            if task_id:
                fact_to_task[(intent["project_id"], intent["to_fact_id"])] = task_id

    for fact in legacy["facts"]:
        if fact.get("id") in ("origin", "goal"):
            continue
        project_id = fact["project_id"]
        task_id = fact_to_task.get((project_id, fact["id"]))
        if task_id is None:
            task_id = _ensure_migration_task(conn, project_id, project_created.get(project_id, ""))
        sources = _task_sources(conn, project_id, task_id) or ["origin"]
        task_row = conn.execute(
            "SELECT type, creation_time FROM tasks WHERE id = ? AND project_id = ?",
            (task_id, project_id),
        ).fetchone()
        fact_type = "collection_fact" if task_row and task_row["type"] == "collection_task" else "vulnerability_fact"
        conn.execute(
            """
            INSERT OR IGNORE INTO facts (
                id, project_id, type, description, creation_time, "from", from_task, "to", evidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '[]', ?)
            """,
            (
                _legacy_prefixed_id(fact["id"], "f", "f"),
                project_id,
                fact_type,
                fact.get("description") or "Migrated legacy fact",
                task_row["creation_time"] if task_row else project_created.get(project_id, ""),
                json.dumps(sources, ensure_ascii=False),
                task_id,
                "legacy:migrated without reproducible evidence",
            ),
        )

    for finding in legacy["findings"]:
        project_id = finding["project_id"]
        task_id = intent_to_task.get((project_id, finding.get("intent_id"))) or _ensure_migration_task(
            conn, project_id, project_created.get(project_id, "")
        )
        sources = [finding.get("fact_id") or "origin"]
        description = finding.get("title") or finding.get("description") or "Migrated legacy finding"
        conn.execute(
            """
            INSERT OR IGNORE INTO findings (
                id, project_id, type, description, creation_time, "from", from_task, "to", report
            ) VALUES (?, ?, 'findings', ?, ?, ?, ?, '[]', NULL)
            """,
            (
                _legacy_prefixed_id(finding["id"], "v", "F"),
                project_id,
                description,
                finding.get("created_at") or project_created.get(project_id, ""),
                json.dumps(sources, ensure_ascii=False),
                task_id,
            ),
        )


def _legacy_prefixed_id(value: str, old_prefix: str, new_prefix: str) -> str:
    suffix = value[len(old_prefix) :] if value.startswith(old_prefix) else value
    suffix = suffix.lstrip("0") or "1"
    return f"{new_prefix}{suffix}" if suffix.isdigit() else f"{new_prefix}{value}"


def _ensure_migration_task(conn: sqlite3.Connection, project_id: str, created_at: str) -> str:
    task_id = "t_migration"
    conn.execute(
        """
        INSERT OR IGNORE INTO tasks (
            id, project_id, type, description, creation_time, completion_time, "to"
        ) VALUES (?, ?, 'collection_task', 'Migrated legacy nodes without task provenance', ?, ?, '[]')
        """,
        (task_id, project_id, created_at, created_at),
    )
    conn.execute(
        "INSERT OR IGNORE INTO task_sources (task_id, project_id, source_id) VALUES (?, ?, 'origin')",
        (task_id, project_id),
    )
    return task_id


def _task_sources(conn: sqlite3.Connection, project_id: str, task_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT source_id FROM task_sources WHERE project_id = ? AND task_id = ? ORDER BY rowid",
        (project_id, task_id),
    ).fetchall()
    return [row["source_id"] for row in rows]


def _ensure_counters(conn: sqlite3.Connection) -> None:
    for row in conn.execute("SELECT id FROM projects").fetchall():
        project_id = row["id"]
        for kind, table, prefix in (
            ("task", "tasks", "t"),
            ("fact", "facts", "f"),
            ("finding", "findings", "F"),
            ("hint", "hints", "h"),
            ("account", "project_accounts", "a"),
            ("snapshot", "project_snapshots", "snap_"),
        ):
            value = _max_numeric_suffix(conn, table, project_id, prefix)
            conn.execute(
                """
                INSERT INTO scoped_counters (project_id, kind, value)
                VALUES (?, ?, ?)
                ON CONFLICT(project_id, kind) DO UPDATE SET value = MAX(value, excluded.value)
                """,
                (project_id, kind, value),
            )


def _max_numeric_suffix(conn: sqlite3.Connection, table: str, project_id: str, prefix: str) -> int:
    if not _table_exists(conn, table):
        return 0
    rows = conn.execute(f"SELECT id FROM {table} WHERE project_id = ? AND id GLOB ?", (project_id, f"{prefix}*")).fetchall()
    max_value = 0
    for row in rows:
        value = row["id"]
        suffix = value[len(prefix) :]
        if suffix.isdigit():
            max_value = max(max_value, int(suffix))
    return max_value


def _ensure_indexes(conn: sqlite3.Connection) -> None:
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_tasks_project_open_worker ON tasks(project_id, completion_time, worker, creation_time, id)",
        "CREATE INDEX IF NOT EXISTS idx_task_sources_project_task ON task_sources(project_id, task_id)",
        "CREATE INDEX IF NOT EXISTS idx_facts_project ON facts(project_id, id)",
        "CREATE INDEX IF NOT EXISTS idx_findings_project_created ON findings(project_id, creation_time, id)",
        "CREATE INDEX IF NOT EXISTS idx_hints_project_created ON hints(project_id, created_at, id)",
        "CREATE INDEX IF NOT EXISTS idx_project_accounts_project ON project_accounts(project_id, id)",
        "CREATE INDEX IF NOT EXISTS idx_project_snapshots_project_created ON project_snapshots(project_id, created_at, id)",
        "CREATE INDEX IF NOT EXISTS idx_ephemeral_jobs_queue ON ephemeral_jobs(status, job_type, created_at, id)",
        "CREATE INDEX IF NOT EXISTS idx_project_reason_leases_project ON project_reason_leases(project_id, task_mode)",
    ):
        conn.execute(ddl)


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
