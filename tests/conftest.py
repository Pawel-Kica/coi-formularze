"""Tests run with auth on (AUTH_ENABLED=1) so the JWT checks stay covered; open mode has its own test in test_api.py."""

import httpx
import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def auth_on(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """No test reaches a model: no API key (a slip fails with LLMError) and httpx.post refuses. Tests of the client
    put their own fake `httpx.post` and key on top."""
    monkeypatch.setattr(settings, "edenai_api_key", None)

    def refuse(url, *args, **kwargs):
        raise AssertionError(f"network call in a test: {url}")

    monkeypatch.setattr(httpx, "post", refuse)
