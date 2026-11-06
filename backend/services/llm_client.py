from openai import OpenAI
from services.app_settings import get_ai_settings
from services.usage_tracker import estimate_tokens, record_token_usage

LLM_MAX_OUTPUT_TOKENS = 393216

_client = None
_client_signature = None


def get_client() -> OpenAI:
    global _client, _client_signature
    settings = get_ai_settings()
    signature = (settings.get("api_key") or "", settings.get("base_url") or "")
    if _client is None or _client_signature != signature:
        _client = OpenAI(api_key=signature[0], base_url=signature[1])
        _client_signature = signature
    return _client


def get_default_model() -> str:
    return get_ai_settings().get("model") or "gpt-4o"


def chat_with_diagnostics(
    messages: list[dict],
    model: str = None,
    temperature: float = 0.7,
    max_tokens: int | None = LLM_MAX_OUTPUT_TOKENS,
    thinking: str | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
) -> tuple[str, dict]:
    max_tokens = LLM_MAX_OUTPUT_TOKENS if max_tokens is None else max_tokens
    client = get_client()
    transport_options = {}
    if timeout is not None:
        transport_options["timeout"] = timeout
    if max_retries is not None:
        transport_options["max_retries"] = max_retries
    if transport_options:
        client = client.with_options(**transport_options)
    request = {
        "model": model or get_default_model(),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if thinking:
        request["extra_body"] = {"thinking": {"type": thinking}}
    resp = client.chat.completions.create(
        **request,
    )
    choice = resp.choices[0]
    usage = resp.usage
    prompt_text = "\n".join(str(message.get("content") or "") for message in messages)
    completion_text = choice.message.content or ""
    reported_prompt_tokens = getattr(usage, "prompt_tokens", None)
    reported_completion_tokens = getattr(usage, "completion_tokens", None)
    reported_total_tokens = getattr(usage, "total_tokens", None)
    estimated = not bool(reported_total_tokens)
    prompt_tokens = int(reported_prompt_tokens or estimate_tokens(prompt_text))
    completion_tokens = int(reported_completion_tokens or estimate_tokens(completion_text))
    total_tokens = int(reported_total_tokens or (prompt_tokens + completion_tokens))
    diagnostics = {
        "model": resp.model,
        "finish_reason": choice.finish_reason,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "max_tokens": max_tokens,
        "thinking": thinking,
        "estimated": estimated,
    }
    record_token_usage(diagnostics)
    return completion_text, diagnostics


def chat(messages: list[dict], model: str = None, temperature: float = 0.7, max_tokens: int | None = LLM_MAX_OUTPUT_TOKENS) -> str:
    content, _ = chat_with_diagnostics(messages, model, temperature, max_tokens)
    return content


def chat_stream(messages: list[dict], model: str = None, temperature: float = 0.7, max_tokens: int | None = LLM_MAX_OUTPUT_TOKENS):
    max_tokens = LLM_MAX_OUTPUT_TOKENS if max_tokens is None else max_tokens
    client = get_client()
    request = {
        "model": model or get_default_model(),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    stream = client.chat.completions.create(**request)
    completion_parts = []
    try:
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                delta = chunk.choices[0].delta.content
                completion_parts.append(delta)
                yield delta
    finally:
        prompt_text = "\n".join(str(message.get("content") or "") for message in messages)
        completion_text = "".join(completion_parts)
        prompt_tokens = estimate_tokens(prompt_text)
        completion_tokens = estimate_tokens(completion_text)
        record_token_usage({
            "model": model or get_default_model(),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "estimated": True,
        })
