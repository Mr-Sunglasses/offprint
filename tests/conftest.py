"""Shared fixtures.

Tests must never touch the real article database in ~/.cache, so the store is
pointed at a throwaway file before the app (and its lazy store singleton) loads.
"""

import os
import tempfile

os.environ.setdefault(
    "BPC_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="bpc-test-"), "articles.db")
)
os.environ.setdefault("BPC_DISABLE_RATE_LIMIT", "1")

import pytest
from fastapi.testclient import TestClient

from bpc_web.main import app


@pytest.fixture(scope="session")
def client():
    # `with` runs the lifespan, so the job store and browser pool exist.
    with TestClient(app) as test_client:
        yield test_client
