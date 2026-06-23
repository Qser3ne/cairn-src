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
    if keys == {"intents"} or keys == {"decision", "intents"} or keys == {"decision", "coverage", "intents"}:
        return isinstance(payload["intents"], list)
    if keys == {"intent"}:
        intent = payload["intent"]
        return isinstance(intent, dict) and "from" in intent and "description" in intent
    return False


def _validate_reason_intent(intent: Any, index: int, *, require_auth_scope: bool = False) -> None:
    if not isinstance(intent, dict) or "from" not in intent or "description" not in intent:
        raise ValueError(f"invalid intent at index {index}")
    auth_scope = intent.get("auth_scope")
    if require_auth_scope and auth_scope not in ("anonymous", "authenticated"):
        raise ValueError(f"auth_scope is required at index {index}")
    if auth_scope is not None and auth_scope not in ("anonymous", "authenticated"):
        raise ValueError(f"invalid auth_scope at index {index}")


EXPLORE_ALLOWED_KEYS = {
    "description",
    "fact_type",
    "title",
    "summary",
    "details",
    "findings",
}


def _looks_like_explore_data(payload: dict[str, Any]) -> bool:
    return isinstance(payload, dict) and "description" in payload and set(payload) <= EXPLORE_ALLOWED_KEYS


def validate_reason_payload(
    payload: dict[str, Any],
    open_intents_empty: bool,
    max_intents: int,
    require_auth_scope: bool = False,
) -> tuple[str, dict[str, Any] | list[dict[str, Any]] | None]:
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
    intents = data.get("intents")
    # backward compat: accept singular "intent" key from LLMs
    if intents is None:
        singular = data.get("intent")
        if isinstance(singular, dict):
            intents = [singular]
    if intents is not None:
        if not isinstance(intents, list):
            raise ValueError("intents must be an array")
        for i, intent in enumerate(intents):
            _validate_reason_intent(intent, i, require_auth_scope=require_auth_scope)
        if not intents and open_intents_empty and decision not in ("noop", "no_new_high_value"):
            raise ValueError("intents must not be empty when open_intents is empty")
        intents = intents[:max_intents]
        if not intents:
            if decision == "no_new_high_value":
                return "stable", None
            return "noop", None
        return "intents", intents
    if open_intents_empty:
        raise ValueError("intents is required when open_intents is empty")
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
    return "judge", data


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
    report_markdown = data.get("report_markdown")
    if not isinstance(report_markdown, str) or not report_markdown.strip():
        raise ValueError("report_markdown is required")
    report_json = data.get("report_json", {})
    if not isinstance(report_json, dict):
        raise ValueError("report_json must be an object")
    return "report", {"report_markdown": report_markdown.strip(), "report_json": report_json}


def validate_explore_payload(payload: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
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
    fact_type = data.get("fact_type", "observation")
    if fact_type not in ("observation", "feature_surface"):
        raise ValueError("fact_type must be observation or feature_surface")
    title = _optional_text(data.get("title"), "title")
    summary = _optional_text(data.get("summary"), "summary")
    details = data.get("details", {})
    if not isinstance(details, dict):
        raise ValueError("details must be an object")
    findings = data.get("findings")
    if findings is not None:
        if not isinstance(findings, list):
            raise ValueError("findings must be an array")
        for index, finding in enumerate(findings):
            if not isinstance(finding, dict):
                raise ValueError(f"invalid finding at index {index}")
            finding_title = finding.get("title")
            if not isinstance(finding_title, str) or not finding_title.strip():
                raise ValueError(f"finding title is required at index {index}")
    return "fact", {
        "description": description.strip(),
        "fact_type": fact_type,
        "title": title,
        "summary": summary,
        "details": details,
    }


def _optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    text = value.strip()
    return text or None


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
