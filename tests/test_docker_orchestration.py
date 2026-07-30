"""Static checks for Docker runtime orchestration files."""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def read_project_file(relative_path: str) -> str:
    """Read a repository file as UTF-8 text."""
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


class TestDockerfile:
    """Dockerfile production runtime checks."""

    @pytest.mark.unit
    def test_uses_project_python_runtime(self):
        """Test image base matches the declared Python 3.12 runtime."""
        dockerfile = read_project_file("Dockerfile")

        assert "FROM python:3.12-slim AS builder" in dockerfile
        assert "FROM python:3.12-slim AS runtime" in dockerfile
        assert "python:3.14" not in dockerfile

    @pytest.mark.unit
    def test_runtime_installs_only_production_dependencies(self):
        """Test image installs production requirements and runs as non-root."""
        dockerfile = read_project_file("Dockerfile")

        assert "COPY requirements.txt ." in dockerfile
        assert "requirements-dev.txt" not in dockerfile
        assert "useradd --system" in dockerfile
        assert "USER app" in dockerfile

    @pytest.mark.unit
    def test_default_command_runs_fastapi_server(self):
        """Test default container command serves the FastAPI app."""
        dockerfile = read_project_file("Dockerfile")

        assert (
            'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", '
            '"--port", "8000", "--no-proxy-headers"]'
        ) in dockerfile


class TestDockerCompose:
    """Docker Compose API and worker orchestration checks."""

    @pytest.mark.unit
    def test_api_service_command_and_env_file_are_defined(self):
        """Test API service serves FastAPI and loads environment from .env."""
        compose = read_project_file("docker-compose.yml")

        assert "  api:" in compose
        assert "      - uvicorn" in compose
        assert "      - app.main:app" in compose
        assert "      - 0.0.0.0" in compose
        assert '      - "8000"' in compose
        assert "      - --no-proxy-headers" in compose
        assert "    env_file:\n      - .env" in compose
        assert '      - "8000:8000"' in compose
        assert "    healthcheck:" in compose

    @pytest.mark.unit
    def test_worker_service_command_and_env_file_are_defined(self):
        """Test worker service runs the autonomous classification daemon."""
        compose = read_project_file("docker-compose.yml")

        assert "  worker:" in compose
        assert "      - python" in compose
        assert "      - -m" in compose
        assert "      - app.jobs.classification_daemon" in compose
        assert "      - --worker-id" in compose
        assert "      - docker-worker-1" in compose
        assert compose.count("    env_file:\n      - .env") == 3
        assert "    stop_grace_period: 2m" in compose

    @pytest.mark.unit
    def test_crm_sync_worker_service_runs_retry_daemon(self):
        """Test scheduled CRM retries have a long-running queue consumer."""
        compose = read_project_file("docker-compose.yml")

        assert "  crm-sync-worker:" in compose
        assert "      - app.jobs.crm_sync_daemon" in compose
        assert "      - docker-crm-sync-worker-1" in compose
        assert "    restart: always" in compose
        assert compose.count("    stop_grace_period: 2m") == 2

    @pytest.mark.unit
    def test_compose_does_not_hardcode_secret_values(self):
        """Test Compose relies on .env instead of inline application secrets."""
        compose = read_project_file("docker-compose.yml")

        assert "OPENAI_API_KEY" not in compose
        assert "SUPABASE_SERVICE_ROLE_KEY" not in compose
        assert "sb_secret_" not in compose
        assert "sk-proj-" not in compose


class TestDockerIgnore:
    """Docker build context safety checks."""

    @pytest.mark.unit
    def test_env_files_are_excluded_but_example_is_available(self):
        """Test local secrets stay outside the Docker build context."""
        dockerignore = read_project_file(".dockerignore")

        assert ".env" in dockerignore.splitlines()
        assert ".env.*" in dockerignore.splitlines()
        assert "!.env.example" in dockerignore.splitlines()
