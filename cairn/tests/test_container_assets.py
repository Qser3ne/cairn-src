from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


def test_worker_dockerfile_does_not_copy_untracked_agent_directory() -> None:
    dockerfile = (ROOT / "container" / "Dockerfile").read_text(encoding="utf-8")

    assert re.search(r"^\s*(COPY|ADD)\b[^\n]*(?:\./)?\.agents\b", dockerfile, re.MULTILINE) is None


def test_worker_agents_file_uses_placeholders_for_sensitive_values() -> None:
    text = (ROOT / "container" / "AGENTS.md").read_text(encoding="utf-8")

    sensitive_line_patterns = [
        r"公网服务器：\s*(?!<)[^\n]+",
        r"SSH 账号：\s*(?!<)[^\n]+",
        r"SSH 密码：\s*(?!<)[^\n]+",
        r"可用账号：\s*(?!<)[^\n]+",
        r"可用密码：\s*(?!<)[^\n]+",
    ]
    for pattern in sensitive_line_patterns:
        assert re.search(pattern, text) is None
