"""LLM client: one OpenAI-compatible chat call to Eden AI, optionally constrained to a JSON Schema.

`POST {LLM_BASE_URL}/chat/completions` with `response_format: {type: json_schema, ...}` (`chat_json`) or free
text (`chat_text`, the page transcription).

Config (`app.config.settings`, from env or `.env`):
- LLM_BASE_URL  https://api.edenai.run/v3 by default (LLM_URL is an alias)
- LLM_MODEL (the planner, deepinfra/zai-org/GLM-5.2), TRANSCRIBE_MODEL (the page reader), LLM_TIMEOUT_S
- EDENAI_API_KEY (SecretStr; read at call time, never logged)

Per-model request params (`model_params`): Eden routes reject or ignore some OpenAI params, so `seed` goes
only to Mistral and `reasoning_effort: "none"` (thinking off, as benchmarked) to the models in THINKING_OFF.
Rate limits and provider hiccups (429, 5xx, timeouts) are retried.

Images (`images=` of `chat_text`, data URLs from `app.llm.transcribe.data_url`) go as standard OpenAI content
parts `{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}` before the text. Without
images the user message stays a plain string.
"""

import copy
import logging
import json
import time
from dataclasses import dataclass, field

import httpx

from app.config import settings


class LLMError(ValueError):
    """The model answered, but not with usable JSON (e.g. cut off by max_tokens)."""


class MissingKeyError(LLMError):
    """EDENAI_API_KEY is not set: the server's fault, not the form's (`app.errors` maps it to 503)."""


@dataclass
class ChatResult:
    data: dict  # parsed JSON answer
    raw: str
    seconds: float
    prompt_tokens: int = 0
    output_tokens: int = 0
    stats: dict = field(default_factory=dict)


log = logging.getLogger(__name__)
MAX_TRIES = 3  # per call, for 429 / 5xx / transport errors
CONNECT_TRIES = 6  # a dropped connection (venue Wi-Fi, DNS) often comes back within a minute: waits 2+4+8+16+32 s


# Model name parts whose routes get `reasoning_effort: "none"`: thinking off, as every Eden number was measured.
THINKING_OFF = ("qwen", "mistral", "glm")


def model_params(model: str, seed: int) -> dict:
    """Extra request params the model's route accepts (see module docstring)."""
    name = model.lower()
    out = {}
    if "mistral" in name:
        out["seed"] = seed
    if any(part in name for part in THINKING_OFF):
        out["reasoning_effort"] = "none"
    return out


def _headers() -> dict:
    """Auth header built at call time; the key never leaves this dict (not logged, not in errors)."""
    key = settings.edenai_api_key
    if key is None or not key.get_secret_value():
        raise MissingKeyError("no model API key: set EDENAI_API_KEY in .env")
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


def _post(url: str, body: dict, timeout: float | None = None, tries: int = MAX_TRIES) -> httpx.Response:
    """POST with retries on 429/5xx/transport errors, without leaking the key: failures are
    re-raised outside the `except` block as fresh exceptions (no chained original, whose
    request carries the Authorization header)."""
    attempt = 0
    while True:
        attempt += 1
        failure = None
        try:
            r = httpx.post(url, json=body, headers=_headers(), timeout=timeout or settings.llm_timeout_s)
            r.raise_for_status()
            return r
        except httpx.HTTPStatusError as e:
            failure = ("status", e.response)
        except httpx.TransportError as e:
            failure = ("transport", type(e))
        retryable = failure[0] == "transport" or failure[1].status_code == 429 or failure[1].status_code >= 500
        # no connection at all (fails in milliseconds) gets a longer budget, also over a caller's smaller `tries`
        # (transcribe's page retry then waits up to ~2 min per page while the network is down); every timeout,
        # ConnectTimeout too, keeps `tries`: each attempt there costs the whole timeout
        connect = failure[0] == "transport" and issubclass(failure[1], httpx.ConnectError)
        limit = max(tries, CONNECT_TRIES) if connect else tries
        if not retryable or attempt >= limit:
            break
        wait = _retry_wait(failure[1] if failure[0] == "status" else None, attempt)
        reason = f"HTTP {failure[1].status_code}" if failure[0] == "status" else failure[1].__name__
        log.warning("model API call failed (%s), retry %d of %d in %.0f s", reason, attempt, limit - 1, wait)
        time.sleep(wait)
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
) -> ChatResult:
    """One deterministic call (temperature 0, fixed seed where the route takes one) constrained to `schema`.

    Raises LLMError when the answer is not valid JSON, httpx errors on HTTP failures.
    """
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": name, "schema": strict_schema(schema), "strict": True},
    }
    raw, seconds, stats = _chat(system, user, model, max_tokens, seed, frequency_penalty, None, response_format)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise LLMError(f"model returned invalid JSON (finish_reason={stats['finish_reason']}): {e}") from None
    if not isinstance(data, dict):
        raise LLMError("model returned JSON that is not an object")
    return ChatResult(data=data, raw=raw, seconds=seconds, prompt_tokens=stats["prompt_tokens"] or 0,
                      output_tokens=stats["output_tokens"] or 0, stats=stats)


def chat_text(
    system: str,
    user: str,
    model: str | None = None,
    max_tokens: int = 8000,
    images: list[str] | None = None,
    presence_penalty: float = 0.0,
    timeout: float | None = None,
    tries: int = MAX_TRIES,
    frequency_penalty: float = 0.0,
) -> ChatResult:
    """The same deterministic call without a schema: the answer is free text (`raw`, `data` empty),
    e.g. a page transcribed to Markdown (`app.llm.transcribe`). Raises LLMError when the answer is
    empty or cut off by `max_tokens`. `timeout` (s, unset = LLM_TIMEOUT_S) and `tries` per HTTP attempt."""
    raw, seconds, stats = _chat(system, user, model, max_tokens, 42, frequency_penalty, images, None, presence_penalty,
                                timeout, tries)
    if not raw.strip() or stats["finish_reason"] == "length":
        raise LLMError(f"model returned no usable text (finish_reason={stats['finish_reason']})")
    return ChatResult(data={}, raw=raw, seconds=seconds, prompt_tokens=stats["prompt_tokens"] or 0,
                      output_tokens=stats["output_tokens"] or 0, stats=stats)


def _chat(system, user, model, max_tokens, seed, frequency_penalty, images, response_format, presence_penalty=0.0,
          timeout=None, tries=MAX_TRIES):
    """One chat completion -> (answer text, seconds, stats)."""
    model = model or settings.llm_model
    content = user
    if images:
        content = [{"type": "image_url", "image_url": {"url": url}} for url in images]
        content.append({"type": "text", "text": user})
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        "temperature": 0,
        "max_tokens": max_tokens,
        **model_params(model, seed),
    }
    if response_format:
        body["response_format"] = response_format
    if frequency_penalty:
        body["frequency_penalty"] = frequency_penalty
    if presence_penalty:
        body["presence_penalty"] = presence_penalty
    t0 = time.monotonic()
    out = _post(f"{settings.llm_base_url.rstrip('/')}/chat/completions", body, timeout, tries).json()
    seconds = time.monotonic() - t0
    choice = (out.get("choices") or [{}])[0]
    raw = (choice.get("message") or {}).get("content") or ""
    usage = out.get("usage") or {}
    stats = {
        "prompt_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "finish_reason": choice.get("finish_reason"),
    }
    return raw, seconds, stats


def health() -> str:
    """"ok" when EDENAI_API_KEY is set, else "no_api_key". The API is not called: health checks must not cost money."""
    key = settings.edenai_api_key
    return "ok" if key is not None and key.get_secret_value() else "no_api_key"
