"""Static safety checks for the GitHub Actions verification workflow."""

from pathlib import Path


WORKFLOW_PATH = Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_ci_runs_for_main_and_pull_requests() -> None:
    workflow = _workflow_text()

    assert "branches: [main]" in workflow
    assert "pull_request:" in workflow
    assert "workflow_dispatch:" in workflow


def test_ci_uses_read_only_permissions_and_current_official_actions() -> None:
    workflow = _workflow_text()

    assert "permissions:\n  contents: read" in workflow
    assert "actions/checkout@v7" in workflow
    assert "actions/setup-python@v7" in workflow
    assert workflow.count("persist-credentials: false") == 2
    assert 'python-version: "3.12"' in workflow


def test_ci_verifies_code_compose_and_container() -> None:
    workflow = _workflow_text()

    assert "python -m ruff check ." in workflow
    assert "python -m pytest" in workflow
    assert "--cov=app" in workflow
    assert "--cov-fail-under=90" in workflow
    assert "cp .env.example .env" in workflow
    assert "docker compose config --quiet" in workflow
    assert "docker build --tag lead-triage:ci ." in workflow
    assert "--env PORT=10000" in workflow
    assert "http://127.0.0.1:10000/health" in workflow
