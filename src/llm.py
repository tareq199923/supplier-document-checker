"""One small LLM layer that works with several providers.

The rest of the pipeline only calls `complete(system, user)`. It gets back raw text
plus usage numbers. Parsing and validation happen later, in the gates (validators.py).

Provider is chosen with LLM_PROVIDER in .env: anthropic | openai | gemini | mock.
Every provider error becomes LLMError, so the pipeline has ONE clean failure path.
SDK-level retries are turned off: the pipeline owns the (single) retry.
"""
import os
import time
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


class LLMError(Exception):
    """Any failure talking to the model (timeout, API error, missing key, bad config)."""


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_s: float


# ---- mock provider (tests run with no API key) ---------------------------------
_MOCK_RESPONSES: list[str] = []


def set_mock_responses(responses: list[str]) -> None:
    """Queue raw text answers; each complete() call in mock mode pops the next one."""
    _MOCK_RESPONSES.clear()
    _MOCK_RESPONSES.extend(responses)


def _call_mock(system, user, model, timeout):
    if not _MOCK_RESPONSES:
        raise LLMError("mock provider has no queued response")
    return _MOCK_RESPONSES.pop(0), 0, 0


# ---- real providers (SDKs are imported only when used) -------------------------
def _call_anthropic(system, user, model, timeout):
    import anthropic

    client = anthropic.Anthropic(api_key=_key("ANTHROPIC_API_KEY"), timeout=timeout, max_retries=0)
    resp = client.messages.create(
        model=model,
        max_tokens=4096,  # 26 fields with quotes need ~1.5k tokens; 2000 left too little room
        temperature=0,  # if a newer model rejects this parameter, remove it
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    return text, resp.usage.input_tokens, resp.usage.output_tokens


def _call_openai(system, user, model, timeout):
    from openai import OpenAI

    client = OpenAI(
        api_key=os.getenv("OPENAI_API_KEY") or "not-needed-for-local",
        base_url=os.getenv("OPENAI_BASE_URL") or None,
        timeout=timeout,
        max_retries=0,
    )
    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    usage = resp.usage
    return (
        resp.choices[0].message.content or "",
        getattr(usage, "prompt_tokens", None),
        getattr(usage, "completion_tokens", None),
    )


def _call_gemini(system, user, model, timeout):
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=_key("GEMINI_API_KEY"),
        http_options=types.HttpOptions(timeout=int(timeout * 1000)),  # milliseconds
    )
    resp = client.models.generate_content(
        model=model,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=0,
            response_mime_type="application/json",
        ),
    )
    meta = resp.usage_metadata
    return (
        resp.text or "",
        getattr(meta, "prompt_token_count", None),
        getattr(meta, "candidates_token_count", None),
    )


_PROVIDERS = {
    "anthropic": _call_anthropic,
    "openai": _call_openai,
    "gemini": _call_gemini,
    "mock": _call_mock,
}


def _key(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise LLMError(f"{name} is not set (see .env.example)")
    return value


def complete(system: str, user: str) -> LLMResult:
    """Send one prompt to the configured provider. Raises LLMError on any failure."""
    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if provider not in _PROVIDERS:
        raise LLMError(f"LLM_PROVIDER must be one of {sorted(_PROVIDERS)}, got {provider!r}")
    model = (os.getenv("LLM_MODEL") or "").strip() or ("mock" if provider == "mock" else "")
    if not model:
        raise LLMError("LLM_MODEL is not set (see .env.example)")
    timeout = float(os.getenv("LLM_TIMEOUT_SECONDS") or 60)

    start = time.perf_counter()
    try:
        text, tokens_in, tokens_out = _PROVIDERS[provider](system, user, model, timeout)
    except LLMError:
        raise
    except Exception as exc:  # timeouts, auth errors, rate limits, network, SDK errors
        raise LLMError(f"{provider} call failed: {type(exc).__name__}: {exc}") from exc
    return LLMResult(text, provider, model, tokens_in, tokens_out, time.perf_counter() - start)
