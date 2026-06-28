from __future__ import annotations

from pathlib import Path


def test_deploy_script_directly_overwrites_with_local_exclusions() -> None:
    deploy_script = Path(__file__).resolve().parents[2] / "scripts" / "deploy.sh"

    content = deploy_script.read_text()

    assert "HEALTH_URL" not in content
    assert "./start.sh" not in content
    assert "curl -f" not in content
    assert "restarting services" not in content
    assert "git -C" not in content
    assert "push origin" not in content
    assert "pull --ff-only" not in content
    assert "uv run --group dev pytest" not in content
    assert "cp -a" not in content
    assert "rsync -a --delete" in content
    assert "--exclude='.git/'" in content
    assert "--exclude='.github/'" in content
    assert "--exclude='.agents/'" in content
    assert "--exclude='.superpowers/'" in content
    assert "--exclude='.worktrees/'" in content
    assert "--exclude='.pytest_cache/'" in content
    assert "  datas/" in content
    assert "--exclude='datas/'" in content
    assert "--exclude='datas.backup/'" in content
    assert 'log "deployment completed"' in content


def test_python_ci_runs_for_deploy_scripts_and_dispatch_configs() -> None:
    workflow = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "python-ci.yml"

    content = workflow.read_text()

    assert '"cairn/**"' in content
    assert '"scripts/**"' in content
    assert '"dispatch*.yaml"' in content
    assert '".github/workflows/python-ci.yml"' in content
