"""LLM client: one OpenAI-compatible chat call whose answer must match a JSON Schema.

Both backends speak the same protocol (`POST {LLM_BASE_URL}/chat/completions` with
`response_format: {type: json_schema, ...}`); Ollama honours the schema on its `/v1`
endpoint, so there is no separate native path.

Config (`app.config.settings`, from env or `.env`):
- LLM_BACKEND   "ollama" (local, no key; "/v1" is appended to the URL if missing) or
                "openai" (any OpenAI-compatible API, e.g. Eden AI; Bearer EDENAI_API_KEY)
- LLM_BASE_URL  e.g. http://localhost:11434/v1 or https://api.edenai.run/v3 (LLM_URL is an alias)
- LLM_MODEL, LLM_TIMEOUT_S, EDENAI_API_KEY (SecretStr; read at call time, never logged)
  Eden AI example: LLM_MODEL=deepinfra/google/gemma-4-31B-it (fallback deepinfra/Qwen/Qwen3.8-27B).

Per-model request params (`model_params`): Eden routes reject or ignore some OpenAI params, so
`seed` goes only to Ollama and Mistral, `reasoning_effort: "none"` (thinking off) only to remote
Qwen and Mistral. Rate limits and provider hiccups (429, 5xx, timeouts) are retried.

Ollama's context window is set on the server (OLLAMA_CONTEXT_LENGTH, 32768 is enough
for the longest form), not per request.

Images (`images=`, data URLs from `app.llm.vision`) go as standard OpenAI content parts
`{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}` before the text;
Ollama's /v1 takes them (base64 only, no remote URLs) and still honours `response_format`.
Without images the user message stays a plain string, so text-only requests do not change.
"""

import copy
import json
import time
from dataclasses import dataclass, field

import httpx

from app.config import settings


class LLMError(ValueError):
    """The model answered, but not with usable JSON (e.g. cut off by max_tokens)."""


@dataclass
class ChatResult:
    data: dict  # parsed JSON answer
    raw: str
    seconds: float
    prompt_tokens: int = 0
    output_tokens: int = 0
    stats: dict = field(default_factory=dict)


# Per-call log (model, seconds, tokens) the bench reads. Cleared by the caller.
CALLS: list[dict] = []
MAX_TRIES = 3  # per call, for 429 / 5xx / transport errors


def model_params(model: str, seed: int) -> dict:
    """Extra request params the model's route accepts (see module docstring)."""
    name = model.lower()
    if settings.llm_backend == "ollama":
        return {"seed": seed}
    out = {}
    if "mistral" in name:
        out["seed"] = seed
    if "qwen" in name or "mistral" in name:
        out["reasoning_effort"] = "none"
    return out


def base_url() -> str:
    url = settings.llm_base_url.rstrip("/")
    if settings.llm_backend == "ollama" and not url.endswith("/v1"):
        url += "/v1"
    return url


def _headers() -> dict:
    """Auth header built at call time; the key never leaves this dict (not logged, not in errors)."""
    if settings.llm_backend == "ollama":
        return {}
    key = settings.edenai_api_key
    if key is None or not key.get_secret_value():
        raise LLMError("LLM_BACKEND needs an API key: set EDENAI_API_KEY in .env")
    return {"Authorization": f"Bearer {key.get_secret_value()}"}


def strict_schema(schema: dict) -> dict:
    """Copy of `schema` with `additionalProperties: false` on every object (OpenAI strict mode)."""
    schema = copy.deepcopy(schema)

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                node.setdefault("additionalProperties", False)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    return schema


def _retry_wait(response: httpx.Response | None, attempt: int) -> float:
    try:
        wait = float(response.headers.get("retry-after", "")) if response is not None else 2.0**attempt
    except ValueError:
        wait = 2.0**attempt
    return min(max(wait, 1.0), 60.0)


def _post(url: str, body: dict) -> httpx.Response:
    """POST with retries on 429/5xx/transport errors, without leaking the key: failures are
    re-raised outside the `except` block as fresh exceptions (no chained original, whose
    request carries the Authorization header)."""
    for attempt in range(1, MAX_TRIES + 1):
        failure = None
        try:
            r = httpx.post(url, json=body, headers=_headers(), timeout=settings.llm_timeout_s)
            r.raise_for_status()
            return r
        except httpx.HTTPStatusError as e:
            failure = ("status", e.response)
        except httpx.TransportError as e:
            failure = ("transport", type(e))
        retryable = failure[0] == "transport" or failure[1].status_code == 429 or failure[1].status_code >= 500
        if not retryable or attempt == MAX_TRIES:
            break
        time.sleep(_retry_wait(failure[1] if failure[0] == "status" else None, attempt))
    if failure[0] == "status":
        response = failure[1]
        raise httpx.HTTPStatusError(f"LLM API returned HTTP {response.status_code}",
                                    request=httpx.Request("POST", url), response=response)
    raise failure[1](f"LLM API unreachable: {failure[1].__name__}", request=httpx.Request("POST", url))


def chat_json(
    system: str,
    user: str,
    schema: dict,
    model: str | None = None,
    max_tokens: int = 16000,
    seed: int = 42,
    frequency_penalty: float = 0.0,
    name: str = "answer",
    images: list[str] | None = None,
) -> ChatResult:
    """One deterministic call (temperature 0, fixed seed where the route takes one) constrained to `schema`.
    `images`: data URLs sent before `user` in the same message (vision models only).

    Raises LLMError when the answer is not valid JSON, httpx errors on HTTP failures.
    """
    model = model or settings.llm_model
    content = user
    if images:
        content = [{"type": "image_url", "image_url": {"url": url}} for url in images]
        content.append({"type": "text", "text": user})
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": name, "schema": strict_schema(schema), "strict": True},
        },
        "temperature": 0,
        "max_tokens": max_tokens,
        **model_params(model, seed),
    }
    if frequency_penalty:
        body["frequency_penalty"] = frequency_penalty
    t0 = time.monotonic()
    out = _post(f"{base_url()}/chat/completions", body).json()
    seconds = time.monotonic() - t0
    choice = (out.get("choices") or [{}])[0]
    raw = (choice.get("message") or {}).get("content") or ""
    usage = out.get("usage") or {}
    stats = {
        "prompt_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "finish_reason": choice.get("finish_reason"),
    }
    CALLS.append({"model": model, "seconds": round(seconds, 2), **stats})
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise LLMError(f"model returned invalid JSON (finish_reason={stats['finish_reason']}): {e}") from None
    if not isinstance(data, dict):
        raise LLMError("model returned JSON that is not an object")
    return ChatResult(data=data, raw=raw, seconds=seconds, prompt_tokens=stats["prompt_tokens"] or 0,
                      output_tokens=stats["output_tokens"] or 0, stats=stats)


async def health() -> str:
    """"ok" | "model_missing" | "busy" | "unreachable" for Ollama (lists its models, 2 s timeout, one retry);
    "ok" | "no_api_key" for a remote API (not called: health checks must not cost money).
    "busy" = Ollama is up but its answer failed or came late: while it generates, /v1/models
    now and then returns 500, which is not "unreachable". Only a refused connection is."""
    if settings.llm_backend != "ollama":
        key = settings.edenai_api_key
        return "ok" if key is not None and key.get_secret_value() else "no_api_key"
    want = settings.llm_model if ":" in settings.llm_model else f"{settings.llm_model}:latest"
    status = "unreachable"
    async with httpx.AsyncClient(timeout=2) as client:
        for _ in range(2):
            try:
                r = await client.get(f"{base_url()}/models")
                r.raise_for_status()
                have = {m["id"] for m in r.json()["data"]}
                return "ok" if want in have else "model_missing"
            except httpx.ConnectError:
                return "unreachable"
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                status = "busy"
    return status
