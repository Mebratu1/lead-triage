"""Static safety checks for the Render production Blueprint."""

from pathlib import Path

import pytest
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BLUEPRINT = yaml.safe_load(
    (PROJECT_ROOT / "render.yaml").read_text(encoding="utf-8")
)


def _service(name: str) -> dict:
    return next(
        service
        for service in BLUEPRINT["services"]
        if service["name"] == name
    )


def _environment(service: dict) -> dict[str, dict]:
    return {
        item["key"]: item
        for item in service["envVars"]
        if "key" in item
    }


@pytest.mark.unit
def test_blueprint_provisions_only_the_active_production_scope() -> None:
    assert BLUEPRINT["previews"]["generation"] == "off"
    assert "databases" not in BLUEPRINT

    services = BLUEPRINT["services"]
    assert {(service["name"], service["type"]) for service in services} == {
        ("lead-triage", "web"),
        ("lead-triage-worker", "worker"),
    }
    assert all("crm" not in service["name"] for service in services)


@pytest.mark.unit
@pytest.mark.parametrize(
    "service_name",
    ["lead-triage", "lead-triage-worker"],
)
def test_services_share_safe_deployment_defaults(service_name: str) -> None:
    service = _service(service_name)

    assert service["runtime"] == "docker"
    assert service["plan"] == "starter"
    assert service["region"] == "oregon"
    assert service["numInstances"] == 1
    assert service["autoDeployTrigger"] == "checksPass"
    assert service["dockerfilePath"] == "./Dockerfile"
    assert service["dockerContext"] == "."


@pytest.mark.unit
def test_api_uses_liveness_health_check_and_prompted_secrets() -> None:
    api = _service("lead-triage")
    environment = _environment(api)

    assert api["healthCheckPath"] == "/health"
    assert "dockerCommand" not in api
    assert api["maxShutdownDelaySeconds"] == 30

    prompted = {
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "ALLOWED_ORIGINS",
    }
    generated = {"ADMIN_TOKEN", "QUEUE_METRICS_TOKEN", "JWT_SECRET"}
    assert all(environment[key]["sync"] is False for key in prompted)
    assert all(environment[key]["generateValue"] is True for key in generated)
    assert environment["TRUSTED_PROXY_CIDRS"]["value"] == "[]"


@pytest.mark.unit
def test_worker_reuses_api_secrets_and_allows_graceful_shutdown() -> None:
    worker = _service("lead-triage-worker")
    environment = _environment(worker)

    assert worker["dockerCommand"] == (
        "python -m app.jobs.classification_daemon "
        "--worker-id render-worker-1 --limit 10 --sleep-seconds 30"
    )
    assert worker["maxShutdownDelaySeconds"] == 120

    shared_keys = {
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "ALLOWED_ORIGINS",
        "ADMIN_TOKEN",
        "QUEUE_METRICS_TOKEN",
        "JWT_SECRET",
    }
    for key in shared_keys:
        assert environment[key]["fromService"] == {
            "type": "web",
            "name": "lead-triage",
            "envVarKey": key,
        }
