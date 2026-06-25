from __future__ import annotations

from importlib import resources

from cairn.dispatcher.config import validate_prompt_resources
from cairn.dispatcher.prompting import load_prompt


def _read_default_prompt(task_mode: str, name: str) -> str:
    return (
        resources.files("cairn.dispatcher.prompts")
        .joinpath("default")
        .joinpath(task_mode)
        .joinpath(name)
        .read_text(encoding="utf-8")
    )


def _read_mock_prompt(name: str) -> str:
    return resources.files("cairn.dispatcher.prompts").joinpath("mock").joinpath(name).read_text(encoding="utf-8")


def _read_default_collection_prompt(name: str) -> str:
    return _read_default_prompt("collection", name)


def _read_default_validation_prompt(name: str) -> str:
    return _read_default_prompt("validation", name)


def _without_markdown_code_ticks(text: str) -> str:
    return text.replace("`", "")


def test_default_prompts_have_required_placeholders() -> None:
    validate_prompt_resources("default")


def test_mock_reason_prompt_uses_task_mode_not_project_kind() -> None:
    prompt = _read_mock_prompt("reason.md")

    assert '"task_mode": "{task_mode}"' in prompt
    assert "project_kind" not in prompt


def test_collection_reason_prompt_mentions_auth_scope_mapping_and_no_complete() -> None:
    prompt = _read_default_collection_prompt("reason.md")
    plain_prompt = _without_markdown_code_ticks(prompt)

    assert "collection task" in prompt or "information collection" in prompt or "信息收集" in prompt
    assert "recon 项目" not in prompt
    assert "auth_scope" in prompt
    assert "anonymous" in prompt
    assert "authenticated" in prompt
    assert "不要输出 complete" in plain_prompt or "Reason validator 会拒绝任何 complete payload" in plain_prompt
    assert "功能地图优先策略" in prompt
    assert "[feature_mapping]" in prompt
    assert "[feature_api_binding]" in prompt
    assert "route/API" in prompt
    assert "未覆盖页面" in prompt
    assert "认证边界" in prompt
    assert "snapshot" not in prompt.lower()
    assert "validation seed" in prompt


def test_collection_explore_prompt_forbids_findings_and_report() -> None:
    prompt = _without_markdown_code_ticks(_read_default_collection_prompt("explore.md"))

    assert "不包含 findings" in prompt or "不要包含 findings" in prompt or "不要输出 findings" in prompt
    assert "不要输出 report" in prompt or "不要包含 report" in prompt
    assert "feature_surface" in prompt
    assert "user_actions" in prompt
    assert "routes" in prompt
    assert "apis" in prompt


def test_validation_reason_prompt_mentions_collection_facts_and_validation_seeds() -> None:
    prompt = _read_default_validation_prompt("reason.md")

    assert "validation task" in prompt or "漏洞验证任务" in prompt
    assert "collection facts" in prompt
    assert "validation seed facts" in prompt
    assert "recon snapshot" not in prompt
    assert "feature_surface" in prompt
    assert "feature_summary" in prompt
    assert "decision=noop" in prompt
    assert "每个功能点" in prompt


def test_validation_explore_prompt_allows_findings() -> None:
    prompt = _read_default_validation_prompt("explore.md")

    assert "findings" in prompt
    assert "可提交 SRC 漏洞" in prompt


def test_report_prompt_lives_under_report_mode() -> None:
    prompt = load_prompt("default", "report.md", "report")

    assert prompt == _read_default_prompt("report", "report.md")
    assert "report_markdown" in prompt


def test_default_prompts_prefer_chinese_readable_output_without_protocol_translation() -> None:
    prompt_names = {
        "collection": ("reason.md", "explore.md", "explore_conclude.md"),
        "validation": ("reason.md", "explore.md", "explore_conclude.md"),
    }

    for task_mode, names in prompt_names.items():
        for name in names:
            prompt = _read_default_prompt(task_mode, name)

            assert "建议优先使用简体中文" in prompt
            assert "字段改成中文" in prompt
            assert "JSON 字段名" in prompt
            assert "必须使用简体中文" not in prompt
            assert "必须为中文" not in prompt
