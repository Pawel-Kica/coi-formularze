"""Tests run with auth on (AUTH_ENABLED=1) so the JWT checks stay covered; open mode has its own test in test_api.py."""

import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def auth_on(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
