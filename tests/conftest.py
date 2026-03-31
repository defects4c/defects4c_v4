"""
conftest.py — Shared pytest fixtures for Defects4C test suite.

Usage:
    D4C_WEBAPP_URL=http://127.0.0.1:8092 python -m pytest tests/ -v
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("D4C_WEBAPP_URL", "http://127.0.0.1:8095")
TIMEOUT = float(os.environ.get("D4C_TEST_TIMEOUT", "60"))


@pytest.fixture(scope="session")
def session():
    s = requests.Session()
    yield s
    s.close()


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def timeout():
    return TIMEOUT


@pytest.fixture(scope="session")
def first_bug_id(session, base_url, timeout):
    """Fetch the first available bug_id from the running service."""
    try:
        r = session.get(f"{base_url}/list_defects_bugid", timeout=timeout)
        data = r.json()
        if data.get("selected"):
            return data["selected"][0]
        if data.get("defects"):
            return data["defects"][0]
    except Exception:
        pass
    return None
