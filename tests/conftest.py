"""Pytest configuration and fixtures."""

import warnings
from unittest.mock import AsyncMock

import pytest
from starlette.exceptions import StarletteDeprecationWarning
from supabase import AsyncClient

from app.main import create_app
from app.models.lead import Lead

with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message="Using `httpx` with `starlette.testclient` is deprecated.*",
        category=StarletteDeprecationWarning,
    )
    from starlette.testclient import TestClient


@pytest.fixture
def app():
    """Create test FastAPI application."""
    return create_app()


@pytest.fixture
def client(app):
    """Create test client."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def mock_db() -> AsyncMock:
    """Create mock Supabase database client."""
    mock = AsyncMock(spec=AsyncClient)
    return mock


@pytest.fixture
def sample_lead_data() -> dict:
    """Sample lead data for testing."""
    return {
        "email": "john.doe@example.com",
        "first_name": "John",
        "last_name": "Doe",
        "phone": "+1-555-123-4567",
        "company": "Acme Corp",
        "job_title": "Sales Manager",
        "source": "test",
    }


@pytest.fixture
def sample_lead(sample_lead_data) -> Lead:
    """Create sample lead object."""
    return Lead(
        id="test-lead-id",
        email=sample_lead_data["email"],
        first_name=sample_lead_data["first_name"],
        last_name=sample_lead_data["last_name"],
        phone=sample_lead_data["phone"],
        company=sample_lead_data["company"],
        job_title=sample_lead_data["job_title"],
        lead_score=75,
        status="qualified",
        tags=["sales_ready"],
        source=sample_lead_data["source"],
    )
