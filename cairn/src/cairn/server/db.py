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
    project_kind TEXT NOT NULL DEFAULT 'recon',
    auth_mode TEXT NOT NULL DEFAULT 'anonymous',
    parent_project_id TEXT REFERENCES projects(id) ON DELETE RESTRICT,
    parent_snapshot_id TEXT,
    created_at TEXT NOT NULL,
    reason_worker TEXT,
    reason_trigger TEXT,
    reason_started_at TEXT,
    reason_last_heartbeat_at TEXT,
    reason_pending INTEGER NOT NULL DEFAULT 0,
    recon_max_reason_rounds INTEGER,
    recon_reason_rounds INTEGER NOT NULL DEFAULT 0,
    recon_explore_rounds INTEGER NOT NULL DEFAULT 0,
    recon_stable_rounds INTEGER NOT NULL DEFAULT 0,
    judge_status TEXT NOT NULL DEFAULT 'not_judged',
    judged_at TEXT
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
    intent_kind TEXT NOT NULL DEFAULT 'explore',
    finding_id TEXT,
    auth_scope TEXT,
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
    cookies_json TEXT NOT NULL DEFAULT '[]',
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
    research_value TEXT NOT NULL DEFAULT 'unknown',
    next_action TEXT NOT NULL DEFAULT 'triage',
    followup_reason TEXT NOT NULL DEFAULT '',
    followup_intent_description TEXT NOT NULL DEFAULT '',
    followup_intent_id TEXT,
    report_status TEXT NOT NULL DEFAULT 'not_started',
    report_intent_id TEXT,
    triaged_at TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id, project_id),
    FOREIGN KEY (fact_id, project_id) REFERENCES facts(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (intent_id, project_id) REFERENCES intents(id, project_id) ON DELETE CASCADE
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
    result_json TEXT,
    error TEXT,
    worker TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS finding_reports (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    finding_id TEXT NOT NULL,
    intent_id TEXT NOT NULL,
    report_markdown TEXT NOT NULL,
    report_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    PRIMARY KEY (id, project_id),
    FOREIGN KEY (finding_id, project_id) REFERENCES findings(id, project_id) ON DELETE CASCADE,
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
        _ensure_no_legacy_standard_projects(conn)
        conn.executescript(SCHEMA)
        _ensure_src_only_project_columns(conn)
        _migrate_intent_table(conn)
        _ensure_project_accounts_table(conn)
        _ensure_findings_table(conn)
        _ensure_project_snapshots_table(conn)
        _ensure_ephemeral_jobs_table(conn)
        _ensure_finding_reports_table(conn)
        _remove_goal_facts(conn)


def _ensure_no_legacy_standard_projects(conn: sqlite3.Connection) -> None:
    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'projects'"
    ).fetchone()
    if table is None:
        return
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
    if "mode" not in columns:
        return
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM projects WHERE mode = 'standard'"
    ).fetchone()
    if row and row["count"] > 0:
        raise RuntimeError(
            "Standard workflow has been removed. "
            "Export or delete legacy standard projects before migrating this database."
        )


def _ensure_src_only_project_columns(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
    if "project_kind" not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN project_kind TEXT NOT NULL DEFAULT 'vuln'")
        if "mode" in columns:
            conn.execute(
                """
                UPDATE projects
                SET project_kind = CASE
                    WHEN mode = 'src' THEN 'vuln'
                    ELSE project_kind
                END
                """
            )
        columns.add("project_kind")
    if "auth_mode" not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN auth_mode TEXT NOT NULL DEFAULT 'anonymous'")
        columns.add("auth_mode")
    if "parent_project_id" not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN parent_project_id TEXT")
        columns.add("parent_project_id")
    if "parent_snapshot_id" not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN parent_snapshot_id TEXT")
        columns.add("parent_snapshot_id")
    if "recon_max_reason_rounds" not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN recon_max_reason_rounds INTEGER")
        columns.add("recon_max_reason_rounds")
    if "recon_reason_rounds" not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN recon_reason_rounds INTEGER NOT NULL DEFAULT 0")
        columns.add("recon_reason_rounds")
    if "recon_explore_rounds" not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN recon_explore_rounds INTEGER NOT NULL DEFAULT 0")
        columns.add("recon_explore_rounds")
    if "recon_stable_rounds" not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN recon_stable_rounds INTEGER NOT NULL DEFAULT 0")
        columns.add("recon_stable_rounds")
    if "judge_status" not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN judge_status TEXT NOT NULL DEFAULT 'not_judged'")
        columns.add("judge_status")
    if "judged_at" not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN judged_at TEXT")
        columns.add("judged_at")
    if "reason_pending" not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN reason_pending INTEGER NOT NULL DEFAULT 0")
        columns.add("reason_pending")
    conn.execute("UPDATE projects SET auth_mode = 'dual' WHERE project_kind = 'recon'")
    if "session_lock_enabled" not in columns:
        return

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        """
        CREATE TABLE projects_new (
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
            recon_max_reason_rounds INTEGER,
            recon_reason_rounds INTEGER NOT NULL DEFAULT 0,
            recon_explore_rounds INTEGER NOT NULL DEFAULT 0,
            recon_stable_rounds INTEGER NOT NULL DEFAULT 0,
            judge_status TEXT NOT NULL DEFAULT 'not_judged',
            judged_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO projects_new (
            id,
            title,
            status,
            project_kind,
            auth_mode,
            parent_project_id,
            parent_snapshot_id,
            created_at,
            reason_worker,
            reason_trigger,
            reason_started_at,
            reason_last_heartbeat_at,
            reason_pending,
            recon_max_reason_rounds,
            recon_reason_rounds,
            recon_explore_rounds,
            recon_stable_rounds,
            judge_status,
            judged_at
        )
        SELECT
            id,
            title,
            status,
            project_kind,
            auth_mode,
            parent_project_id,
            parent_snapshot_id,
            created_at,
            reason_worker,
            reason_trigger,
            reason_started_at,
            reason_last_heartbeat_at,
            reason_pending,
            recon_max_reason_rounds,
            recon_reason_rounds,
            recon_explore_rounds,
            recon_stable_rounds,
            judge_status,
            judged_at
        FROM projects
        """
    )
    conn.execute("DROP TABLE projects")
    conn.execute("ALTER TABLE projects_new RENAME TO projects")
    conn.execute("PRAGMA foreign_keys=ON")


def _migrate_intent_table(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(intents)")}
    if "intent_kind" not in columns:
        conn.execute("ALTER TABLE intents ADD COLUMN intent_kind TEXT NOT NULL DEFAULT 'explore'")
        columns.add("intent_kind")
    if "finding_id" not in columns:
        conn.execute("ALTER TABLE intents ADD COLUMN finding_id TEXT")
        columns.add("finding_id")
    if "auth_scope" not in columns:
        conn.execute("ALTER TABLE intents ADD COLUMN auth_scope TEXT")
        columns.add("auth_scope")
    conn.execute(
        """
        UPDATE intents
        SET auth_scope = CASE
            WHEN intent_kind = 'report' THEN NULL
            WHEN project_id IN (
                SELECT id FROM projects WHERE auth_mode = 'authenticated'
            ) THEN 'authenticated'
            ELSE 'anonymous'
        END
        WHERE auth_scope IS NULL AND intent_kind != 'report'
        """
    )
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
            intent_kind TEXT NOT NULL DEFAULT 'explore',
            finding_id TEXT,
            auth_scope TEXT,
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
            concluded_at,
            intent_kind,
            finding_id,
            auth_scope
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
            concluded_at,
            intent_kind,
            finding_id,
            auth_scope
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
            cookies_json TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY (id, project_id)
        )
        """
    )
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(project_accounts)")}
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
            SELECT id, project_id, label, cookies_json
            FROM project_accounts
            """
        )
    conn.execute("DROP TABLE project_accounts")
    conn.execute("ALTER TABLE project_accounts_new RENAME TO project_accounts")
    conn.execute("PRAGMA foreign_keys=ON")


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
            research_value TEXT NOT NULL DEFAULT 'unknown',
            next_action TEXT NOT NULL DEFAULT 'triage',
            followup_reason TEXT NOT NULL DEFAULT '',
            followup_intent_description TEXT NOT NULL DEFAULT '',
            followup_intent_id TEXT,
            report_status TEXT NOT NULL DEFAULT 'not_started',
            report_intent_id TEXT,
            triaged_at TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (id, project_id),
            FOREIGN KEY (fact_id, project_id) REFERENCES facts(id, project_id) ON DELETE CASCADE,
            FOREIGN KEY (intent_id, project_id) REFERENCES intents(id, project_id) ON DELETE CASCADE
        )
        """
    )
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(findings)")}
    for name, ddl in {
        "research_value": "ALTER TABLE findings ADD COLUMN research_value TEXT NOT NULL DEFAULT 'unknown'",
        "next_action": "ALTER TABLE findings ADD COLUMN next_action TEXT NOT NULL DEFAULT 'triage'",
        "followup_reason": "ALTER TABLE findings ADD COLUMN followup_reason TEXT NOT NULL DEFAULT ''",
        "followup_intent_description": "ALTER TABLE findings ADD COLUMN followup_intent_description TEXT NOT NULL DEFAULT ''",
        "followup_intent_id": "ALTER TABLE findings ADD COLUMN followup_intent_id TEXT",
        "report_status": "ALTER TABLE findings ADD COLUMN report_status TEXT NOT NULL DEFAULT 'not_started'",
        "report_intent_id": "ALTER TABLE findings ADD COLUMN report_intent_id TEXT",
        "triaged_at": "ALTER TABLE findings ADD COLUMN triaged_at TEXT",
    }.items():
        if name not in columns:
            conn.execute(ddl)


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
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(ephemeral_jobs)")}
    if "worker" not in columns:
        conn.execute("ALTER TABLE ephemeral_jobs ADD COLUMN worker TEXT")


def _ensure_finding_reports_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS finding_reports (
            id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            finding_id TEXT NOT NULL,
            intent_id TEXT NOT NULL,
            report_markdown TEXT NOT NULL,
            report_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            PRIMARY KEY (id, project_id),
            FOREIGN KEY (finding_id, project_id) REFERENCES findings(id, project_id) ON DELETE CASCADE,
            FOREIGN KEY (intent_id, project_id) REFERENCES intents(id, project_id) ON DELETE CASCADE
        )
        """
    )


def _remove_goal_facts(conn: sqlite3.Connection) -> None:
    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'facts'"
    ).fetchone()
    if table is None:
        return
    sources_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'intent_sources'"
    ).fetchone()
    if sources_table is not None:
        conn.execute("DELETE FROM intent_sources WHERE fact_id = 'goal'")
    conn.execute("DELETE FROM facts WHERE id = 'goal'")


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
