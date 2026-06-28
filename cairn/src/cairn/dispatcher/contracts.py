from __future__ import annotations

from typing import Any

import yaml

from cairn.dispatcher.output_parser import extract_json_object


def parse_json_output(stdout: str) -> dict[str, Any]:
    return extract_json_object(stdout)


def _unwrap_wrapped_payload(payload: dict[str, Any]) -> tuple[bool | None, dict[str, Any] | None]:
    accepted = payload.get("accepted")
    if accepted is False:
        return False, None
    if accepted is True:
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("data must be an object")
        return True, data
    return None, None


def _is_dict(value: Any) -> bool:
    return isinstance(value, dict)


def _looks_like_reason_data(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    keys = set(payload)
    if keys == {"tasks"} or keys == {"decision", "tasks"} or keys == {"decision", "coverage", "tasks"}:
        return isinstance(payload["tasks"], list)
    if keys == {"task"}:
        task = payload["task"]
        return isinstance(task, dict) and "from" in task and "description" in task
    return False


REASON_TASK_MODES = {"collection", "vulnerability"}
VALIDATION_FOCUS_TERMS = (
    "validate",
    "validation",
    "vulnerability",
    "vuln",
    "idor",
    "xss",
    "csrf",
    "ssrf",
    "rce",
    "injection",
    "authz",
    "authorization",
    "bypass",
    "exploit",
    "finding",
    "reproduce",
    "impact",
)


def _has_validation_focus(description: Any) -> bool:
    if not isinstance(description, str):
        return False
    text = description.lower()
    return any(term in text for term in VALIDATION_FOCUS_TERMS)


def _validate_reason_task(
    task: Any,
    index: int,
    *,
    task_mode: str,
    require_auth_scope: bool = False,
) -> dict[str, Any]:
    if not isinstance(task, dict) or "from" not in task or "description" not in task:
        raise ValueError(f"invalid task at index {index}")
    if "type" in task:
        task_type = task["type"]
        if task_type not in ("collection_task", "vulnerability_task"):
            raise ValueError(f"unknown task type at index {index}")
        inferred_mode = "collection" if task_type == "collection_task" else "vulnerability"
    else:
        inferred_mode = task_mode
    if task_mode == "vulnerability" and inferred_mode != "vulnerability":
        raise ValueError(f"vulnerability reason task must use vulnerability_task at index {index}")
    if task_mode == "collection" and inferred_mode == "vulnerability" and "type" not in task:
        raise ValueError(f"vulnerability seed task must explicitly set type at index {index}")
    if task_mode == "collection" and inferred_mode == "vulnerability" and not _has_validation_focus(task.get("description")):
        raise ValueError(f"vulnerability seed task must be vulnerability-focused at index {index}")
    auth_scope = task.get("auth_scope")
    if require_auth_scope and inferred_mode == "collection" and auth_scope not in ("anonymous", "authenticated"):
        raise ValueError(f"auth_scope is required at index {index}")
    if auth_scope is not None and auth_scope not in ("anonymous", "authenticated"):
        raise ValueError(f"invalid auth_scope at index {index}")
    normalized = dict(task)
    normalized["type"] = "collection_task" if inferred_mode == "collection" else "vulnerability_task"
    return normalized


EXPLORE_ALLOWED_KEYS = {
    "description",
    "evidence",
    "findings",
}

JUDGE_RECOMMENDED_ACTIONS = {
    "create_vuln_project",
    "continue_anonymous_collection",
    "continue_authenticated_collection",
    "clarify_scope",
    "fix_account_access",
    "stop_or_archive",
}

JUDGE_CHECKLIST_KEYS = (
    "scope_clarity",
    "feature_coverage",
    "feature_api_mapping_quality",
    "auth_boundary_coverage",
    "candidate_surface_quality",
)

FINDING_REQUIRED_TEXT_FIELDS = (
    "description",
)
FINDING_RESEARCH_VALUES = {"unknown", "high", "medium", "low", "none"}
FINDING_NEXT_ACTIONS = {"triage", "follow_up", "report", "close"}


def _looks_like_explore_data(payload: dict[str, Any]) -> bool:
    return isinstance(payload, dict) and "description" in payload and set(payload) <= EXPLORE_ALLOWED_KEYS


def validate_reason_payload(
    payload: dict[str, Any],
    open_tasks_empty: bool,
    max_tasks: int,
    *,
    task_mode: str,
    require_auth_scope: bool = False,
) -> tuple[str, dict[str, Any] | list[dict[str, Any]] | None]:
    if task_mode not in REASON_TASK_MODES:
        raise ValueError("unknown task_mode")
    accepted, data = _unwrap_wrapped_payload(payload)
    if accepted is False:
        return "rejected", None
    if accepted is None:
        if not _looks_like_reason_data(payload):
            raise ValueError("accepted must be true or false")
        data = payload
    if not isinstance(data, dict):
        raise ValueError("accepted must be true or false")
    if data.get("complete") is not None:
        raise ValueError("complete payload is not supported in SRC-only mode")
    decision = data.get("decision")
    if "intents" in data or "intent" in data:
        raise ValueError("intents output is no longer supported; use tasks")
    tasks = data.get("tasks")
    if tasks is None:
        singular = data.get("task")
        if isinstance(singular, dict):
            tasks = [singular]
    if tasks is not None:
        if not isinstance(tasks, list):
            raise ValueError("tasks must be an array")
        normalized_tasks = []
        for i, task in enumerate(tasks):
            normalized_tasks.append(
                _validate_reason_task(
                    task,
                    i,
                    task_mode=task_mode,
                    require_auth_scope=require_auth_scope,
                )
            )
        if not tasks and open_tasks_empty and decision not in ("noop", "no_new_high_value"):
            raise ValueError("tasks must not be empty when open_tasks is empty")
        tasks = normalized_tasks[:max_tasks]
        if not tasks:
            if decision == "no_new_high_value":
                return "stable", None
            return "noop", None
        return "tasks", tasks
    if open_tasks_empty:
        raise ValueError("tasks is required when open_tasks is empty")
    return "noop", None


def validate_judge_payload(payload: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    accepted, data = _unwrap_wrapped_payload(payload)
    if accepted is False:
        return "rejected", None
    if accepted is None:
        raise ValueError("accepted must be true or false")
    if not isinstance(data, dict):
        raise ValueError("data must be an object")
    verdict = data.get("verdict")
    if verdict not in ("ready", "not_ready", "blocked"):
        raise ValueError("verdict must be ready, not_ready, or blocked")
    _required_number(data, "score", minimum=0, maximum=100)
    recommended_action = data.get("recommended_action")
    if recommended_action not in JUDGE_RECOMMENDED_ACTIONS:
        raise ValueError("recommended_action is required and must be valid")
    _validate_judge_checklist(data.get("checklist"))
    _required_text_list(data, "blocking_gaps")
    _required_text_list(data, "non_blocking_gaps")
    return "judge", data


def _validate_judge_checklist(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("checklist is required")
    for key in JUDGE_CHECKLIST_KEYS:
        item = value.get(key)
        if not isinstance(item, dict):
            raise ValueError(f"checklist {key} is required")
        _required_number(item, f"checklist {key} score", minimum=0, maximum=20)
        _required_text(item, f"checklist {key} evidence", key="evidence")


def _snapshot_fact_ids(graph_yaml: str) -> set[str]:
    try:
        data = yaml.safe_load(graph_yaml) or {}
    except yaml.YAMLError as exc:
        raise ValueError("graph_yaml must be valid YAML") from exc
    facts = data.get("facts")
    if not isinstance(facts, list):
        raise ValueError("graph_yaml must contain facts")
    fact_ids = set()
    for fact in facts:
        if isinstance(fact, dict) and isinstance(fact.get("id"), str):
            fact_ids.add(fact["id"])
    return fact_ids


def validate_fork_seed_payload(payload: dict[str, Any], graph_yaml: str, max_seed_facts: int = 10) -> tuple[str, dict[str, Any] | None]:
    accepted, data = _unwrap_wrapped_payload(payload)
    if accepted is False:
        return "rejected", None
    if accepted is None:
        raise ValueError("accepted must be true or false")
    if not isinstance(data, dict):
        raise ValueError("data must be an object")
    seed_facts = data.get("seed_facts")
    if not isinstance(seed_facts, list) or not seed_facts:
        raise ValueError("seed_facts must be a non-empty array")
    if len(seed_facts) > max_seed_facts:
        seed_facts = seed_facts[:max_seed_facts]
    valid_fact_ids = _snapshot_fact_ids(graph_yaml)
    normalized = []
    for index, seed_fact in enumerate(seed_facts):
        if not isinstance(seed_fact, dict):
            raise ValueError(f"invalid seed_fact at index {index}")
        title = seed_fact.get("title")
        auth_scope = seed_fact.get("auth_scope")
        candidate_type = seed_fact.get("candidate_type")
        derived_from = seed_fact.get("derived_from")
        description = seed_fact.get("description")
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"seed_fact title is required at index {index}")
        if auth_scope not in ("anonymous", "authenticated"):
            raise ValueError(f"seed_fact auth_scope is invalid at index {index}")
        if not isinstance(candidate_type, str) or not candidate_type.strip():
            raise ValueError(f"seed_fact candidate_type is required at index {index}")
        if not isinstance(derived_from, list) or not derived_from:
            raise ValueError(f"seed_fact derived_from is required at index {index}")
        cleaned_sources = []
        for source in derived_from:
            if not isinstance(source, str) or not source.strip():
                raise ValueError(f"seed_fact derived_from has invalid id at index {index}")
            source_id = source.strip()
            if source_id not in valid_fact_ids:
                raise ValueError(f"seed_fact references unknown source fact {source_id}")
            cleaned_sources.append(source_id)
        if len(set(cleaned_sources)) != len(cleaned_sources):
            raise ValueError(f"seed_fact derived_from must be unique at index {index}")
        if cleaned_sources == ["origin"]:
            raise ValueError(f"seed_fact cannot derive only from origin at index {index}")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"seed_fact description is required at index {index}")
        feature_summary = _optional_text(seed_fact.get("feature_summary"), f"seed_fact feature_summary at index {index}")
        user_actions = _optional_text_list(seed_fact.get("user_actions"), f"seed_fact user_actions at index {index}")
        routes = _optional_text_list(seed_fact.get("routes"), f"seed_fact routes at index {index}")
        apis = _optional_text_list(seed_fact.get("apis"), f"seed_fact apis at index {index}")
        vuln_validation_focus = _optional_text_list(
            seed_fact.get("vuln_validation_focus"),
            f"seed_fact vuln_validation_focus at index {index}",
        )
        known_constraints = _optional_text_list(
            seed_fact.get("known_constraints"),
            f"seed_fact known_constraints at index {index}",
        )
        evidence_refs = _optional_text_list(seed_fact.get("evidence_refs"), f"seed_fact evidence_refs at index {index}")
        normalized.append(
            {
                "title": title.strip(),
                "auth_scope": auth_scope,
                "candidate_type": candidate_type.strip(),
                "derived_from": cleaned_sources,
                "description": description.strip(),
                "feature_summary": feature_summary,
                "user_actions": user_actions,
                "routes": routes,
                "apis": apis,
                "vuln_validation_focus": vuln_validation_focus,
                "known_constraints": known_constraints,
                "evidence_refs": evidence_refs,
            }
        )
    return "fork_seed", {"seed_facts": normalized}


def validate_report_payload(payload: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    accepted, data = _unwrap_wrapped_payload(payload)
    if accepted is False:
        return "rejected", None
    if accepted is None:
        raise ValueError("accepted must be true or false")
    if not isinstance(data, dict):
        raise ValueError("data must be an object")
    report = data.get("report")
    if not isinstance(report, str) or not report.strip():
        raise ValueError("report is required")
    return "report", {"report": report.strip()}


def validate_explore_payload(payload: dict[str, Any], *, task_mode: str) -> tuple[str, dict[str, Any] | None]:
    if task_mode not in ("collection", "vulnerability"):
        raise ValueError("unknown task_mode")
    accepted, data = _unwrap_wrapped_payload(payload)
    if accepted is False:
        return "rejected", None
    if accepted is None:
        if not _looks_like_explore_data(payload):
            raise ValueError("accepted must be true or false")
        data = payload
    if not isinstance(data, dict):
        raise ValueError("accepted must be true or false")
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("description is required")
    evidence = data.get("evidence")
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("evidence is required")
    findings = data.get("findings")
    if task_mode == "collection" and findings:
        raise ValueError("collection task payload cannot include findings")
    if findings is not None:
        if not isinstance(findings, list):
            raise ValueError("findings must be an array")
        for index, finding in enumerate(findings):
            if not isinstance(finding, dict):
                raise ValueError(f"invalid finding at index {index}")
            _validate_finding_payload(finding, index)
    return "fact", {
        "description": description.strip(),
        "evidence": evidence.strip(),
        "findings": findings or None,
    }


def _validate_finding_payload(finding: dict[str, Any], index: int) -> None:
    for field in FINDING_REQUIRED_TEXT_FIELDS:
        _required_text(finding, f"finding {field} at index {index}", key=field)


def _optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    text = value.strip()
    return text or None


def _required_text(data: dict[str, Any], label: str, *, key: str | None = None) -> str:
    value = data.get(key or label)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()


def _required_number(data: dict[str, Any], label: str, *, minimum: int, maximum: int) -> int:
    key = label.rsplit(" ", 1)[-1] if label.startswith("checklist ") else label
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} is required")
    if value < minimum or value > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return value


def _required_text_list(data: dict[str, Any], key: str) -> list[str]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{key} is required")
    cleaned = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"{key} item {index} must be a string")
        text = item.strip()
        if text:
            cleaned.append(text)
    return cleaned


def _optional_text_list(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    cleaned = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{label} items must be strings")
        text = item.strip()
        if text:
            cleaned.append(text)
    return cleaned
