"""Shared fixtures."""

import pytest
from fastapi.testclient import TestClient

from bpc_web.main import app


@pytest.fixture(scope="session")
def client():
    # `with` runs the lifespan, so the job store and browser pool exist.
    with TestClient(app) as test_client:
        yield test_client
