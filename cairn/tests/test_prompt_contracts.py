from __future__ import annotations

from importlib import resources

from cairn.dispatcher.config import validate_prompt_resources


def _read_default_recon_prompt(name: str) -> str:
    return (
        resources.files("cairn.dispatcher.prompts")
        .joinpath("default")
        .joinpath("recon")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )


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
