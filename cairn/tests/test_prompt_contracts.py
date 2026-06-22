from __future__ import annotations

from importlib import resources

from cairn.dispatcher.config import validate_prompt_resources


def _read_default_prompt(project_kind: str, name: str) -> str:
    return (
        resources.files("cairn.dispatcher.prompts")
        .joinpath("default")
        .joinpath(project_kind)
        .joinpath(name)
        .read_text(encoding="utf-8")
    )


def _read_default_recon_prompt(name: str) -> str:
    return _read_default_prompt("recon", name)


def _without_markdown_code_ticks(text: str) -> str:
    return text.replace("`", "")


def test_default_recon_prompts_have_required_placeholders() -> None:
    validate_prompt_resources("default")


def test_recon_reason_prompt_mentions_auth_scope_and_no_complete() -> None:
    prompt = _read_default_recon_prompt("reason.md")
    plain_prompt = _without_markdown_code_ticks(prompt)

    assert "auth_scope" in prompt
    assert "anonymous" in prompt
    assert "authenticated" in prompt
    assert "不要输出 complete" in plain_prompt or "Reason validator 会拒绝任何 complete payload" in plain_prompt


def test_recon_explore_prompt_forbids_findings() -> None:
    prompt = _without_markdown_code_ticks(_read_default_recon_prompt("explore.md"))

    assert "不包含 findings" in prompt or "不要包含 findings" in prompt or "不要输出 findings" in prompt


def test_recon_judge_prompt_declares_ephemeral_no_graph_write() -> None:
    prompt = _read_default_recon_prompt("judge.md")

    assert "ephemeral" in prompt
    assert "不能创建、修改或建议写入 graph 数据" in prompt
    for graph_resource in ("facts", "intents", "findings", "reports"):
        assert graph_resource in prompt


def test_default_prompts_prefer_chinese_readable_output_without_protocol_translation() -> None:
    prompt_names = {
        "recon": ("reason.md", "explore.md", "explore_conclude.md", "judge.md"),
        "vuln": ("reason.md", "explore.md", "explore_conclude.md"),
    }

    for project_kind, names in prompt_names.items():
        for name in names:
            prompt = _read_default_prompt(project_kind, name)

            assert "建议优先使用简体中文" in prompt
            assert "字段改成中文" in prompt
            assert "JSON 字段名" in prompt
            assert "必须使用简体中文" not in prompt
            assert "必须为中文" not in prompt
