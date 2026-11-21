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
    monkeypatch.setattr(client.time, "sleep", lambda s: None)  # retries wait for nothing


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


@pytest.mark.parametrize("model,seed,reasoning", [
    ("deepinfra/google/gemma-4-31B-it", False, None),
    ("deepinfra/Qwen/Qwen3.8-27B", False, "none"),
    ("mistral/mistral-small-2603", True, "none"),
])
def test_openai_backend_per_model_params(monkeypatch, remote, model, seed, reasoning):
    """Eden routes: `seed` only for Mistral, `reasoning_effort: none` only for Qwen and Mistral."""
    seen = {}
    fake_post(monkeypatch, seen=seen)
    client.chat_json("sys", "user", SCHEMA, model=model)
    assert ("seed" in seen["body"]) is seed
    assert seen["body"].get("reasoning_effort") == reasoning


def test_ollama_backend_keeps_seed_no_reasoning(monkeypatch):
    monkeypatch.setattr(settings, "llm_backend", "ollama")
    seen = {}
    fake_post(monkeypatch, seen=seen)
    client.chat_json("sys", "user", SCHEMA, model="qwen3:30b-a3b-instruct-2507-q4_K_M")
    assert seen["body"]["seed"] == 42 and "reasoning_effort" not in seen["body"]


def test_retries_rate_limit_then_succeeds(monkeypatch, remote):
    statuses = [429, 503, 200]

    def post(url, json=None, headers=None, timeout=None):
        status = statuses.pop(0)
        body = {"choices": [{"message": {"content": '{"a": {"b": "x"}}'}, "finish_reason": "stop"}]}
        return httpx.Response(status, json=body, headers={"retry-after": "1"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(client.httpx, "post", post)
    assert client.chat_json("sys", "user", SCHEMA).data == {"a": {"b": "x"}}
    assert not statuses


def test_bad_request_is_not_retried(monkeypatch, remote):
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append(1)
        return httpx.Response(400, json={"error": "bad"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(client.httpx, "post", post)
    with pytest.raises(httpx.HTTPStatusError):
        client.chat_json("sys", "user", SCHEMA)
    assert len(calls) == 1


def test_health_ollama_busy_is_not_unreachable(monkeypatch):
    """Ollama answers 500 to /v1/models now and then while it generates: one retry, then "busy"."""
    import asyncio

    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(500) if len(calls) == 1 else httpx.Response(200, json={"data": [{"id": settings.llm_model}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(settings, "llm_backend", "ollama")
    monkeypatch.setattr(client.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    assert asyncio.run(client.health()) == "ok" and len(calls) == 2  # retried
    calls.clear()
    monkeypatch.setattr(client.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(500)), **kw))
    assert asyncio.run(client.health()) == "busy"
