"""Pytest configuration and fixtures."""

import warnings

import pytest
from starlette.exceptions import StarletteDeprecationWarning

from app.main import create_app

# FastAPI/Starlette currently emits this third-party warning on import.
# Remove this once the project upgrades to the replacement test client path.
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
