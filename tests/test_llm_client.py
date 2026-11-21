"""LLM client: OpenAI-compatible request shape, both backends, and the API key never leaks."""

import json
import logging

import httpx
import pytest
from pydantic import SecretStr

from app.config import settings
from app.llm import client

KEY = "sk-test-SECRET-1234567890"
SCHEMA = {"type": "object", "properties": {"a": {"type": "object", "properties": {"b": {"type": "string"}}}}}


@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setattr(settings, "llm_backend", "openai")
    monkeypatch.setattr(settings, "llm_base_url", "https://llm.example/v3/")
    monkeypatch.setattr(settings, "edenai_api_key", SecretStr(KEY))


def fake_post(monkeypatch, status=200, content='{"a": {"b": "x"}}', seen=None):
    def post(url, json=None, headers=None, timeout=None):
        if seen is not None:
            seen.update(url=url, body=json, headers=headers)
        req = httpx.Request("POST", url, headers=headers)
        body = {"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3}}
        return httpx.Response(status, json=body if status == 200 else {"error": "bad"}, request=req)

    monkeypatch.setattr(client.httpx, "post", post)


def test_ollama_backend_request(monkeypatch):
    monkeypatch.setattr(settings, "llm_backend", "ollama")
    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:11434")
    seen = {}
    fake_post(monkeypatch, seen=seen)
    r = client.chat_json("sys", "user", SCHEMA, model="m")
    assert r.data == {"a": {"b": "x"}}
    assert seen["url"] == "http://localhost:11434/v1/chat/completions"
    assert seen["headers"] == {}
    rf = seen["body"]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"]["additionalProperties"] is False
    assert rf["json_schema"]["schema"]["properties"]["a"]["additionalProperties"] is False
    assert seen["body"]["temperature"] == 0 and seen["body"]["seed"] == 42


def test_openai_backend_sends_bearer_key(monkeypatch, remote):
    seen = {}
    fake_post(monkeypatch, seen=seen)
    client.chat_json("sys", "user", SCHEMA)
    assert seen["url"] == "https://llm.example/v3/chat/completions"
    assert seen["headers"] == {"Authorization": f"Bearer {KEY}"}


def test_openai_backend_without_key_fails_clearly(monkeypatch, remote):
    monkeypatch.setattr(settings, "edenai_api_key", None)
    with pytest.raises(client.LLMError, match="EDENAI_API_KEY"):
        client.chat_json("sys", "user", SCHEMA)


def test_invalid_json_raises_llm_error(monkeypatch):
    monkeypatch.setattr(settings, "llm_backend", "ollama")
    fake_post(monkeypatch, content='{"a": ')
    with pytest.raises(client.LLMError):
        client.chat_json("sys", "user", SCHEMA)


@pytest.mark.parametrize("failure", ["http", "transport", "json"])
def test_key_never_in_errors_or_logs(monkeypatch, remote, caplog, failure):
    caplog.set_level(logging.DEBUG)
    if failure == "http":
        fake_post(monkeypatch, status=401)
    elif failure == "json":
        fake_post(monkeypatch, content="not json")
    else:
        def post(url, json=None, headers=None, timeout=None):
            raise httpx.ConnectError(f"boom {headers}", request=httpx.Request("POST", url, headers=headers))

        monkeypatch.setattr(client.httpx, "post", post)
    with pytest.raises(Exception) as info:
        client.chat_json("sys", "user", SCHEMA)
    e = info.value
    import traceback

    dumped = [str(e), repr(e), repr(e.__cause__), repr(e.__context__), caplog.text, repr(settings),
              "".join(traceback.format_exception(e))]
    if isinstance(e, httpx.HTTPError):
        dumped += [repr(e.request.headers), str(e.request.headers)]
    assert not any(KEY in d for d in dumped)
    assert KEY not in json.dumps(client.CALLS)


def test_health_remote_does_not_call(monkeypatch, remote):
    import asyncio

    monkeypatch.setattr(client.httpx, "AsyncClient", None)  # any network use would crash
    assert asyncio.run(client.health()) == "ok"
    monkeypatch.setattr(settings, "edenai_api_key", None)
    assert asyncio.run(client.health()) == "no_api_key"
