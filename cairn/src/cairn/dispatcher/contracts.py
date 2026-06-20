from __future__ import annotations

from typing import Any

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


def _looks_like_explore_data(payload: dict[str, Any]) -> bool:
    return isinstance(payload, dict) and "description" in payload and set(payload) <= {"description", "findings"}


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


def validate_explore_payload(payload: dict[str, Any]) -> tuple[str, str | None]:
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
    findings = data.get("findings")
    if findings is not None:
        if not isinstance(findings, list):
            raise ValueError("findings must be an array")
        for index, finding in enumerate(findings):
            if not isinstance(finding, dict):
                raise ValueError(f"invalid finding at index {index}")
            title = finding.get("title")
            if not isinstance(title, str) or not title.strip():
                raise ValueError(f"finding title is required at index {index}")
    return "fact", description.strip()
