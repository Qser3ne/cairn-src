from __future__ import annotations

import py_compile
from pathlib import Path


def test_dispatcher_task_modules_compile() -> None:
    src = Path(__file__).resolve().parents[1] / "src" / "cairn" / "dispatcher" / "tasks"

    for module in ("explore.py", "reason.py", "report.py"):
        py_compile.compile(str(src / module), doraise=True)
