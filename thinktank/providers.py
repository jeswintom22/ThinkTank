from collections.abc import AsyncGenerator
from dataclasses import dataclass
import os

from openai import APIError, AuthenticationError, AsyncOpenAI, OpenAIError, RateLimitError

from thinktank.models import ProviderId, ProviderValidationRequest


@dataclass(frozen=True)
class ProviderInfo:
    id: ProviderId
    name: str
    color: str
    key_placeholder: str
    models: tuple[str, ...]


class ProviderConfigurationError(RuntimeError):
    pass


PROVIDERS: dict[ProviderId, ProviderInfo] = {
    "gemini": ProviderInfo(
        id="gemini",
        name="Gemini",
        color="#4d9fff",
        key_placeholder="AIza...",
        models=("gemini-2.5-flash", "gemini-2-flash"),
    ),
    "openai": ProviderInfo(
        id="openai",
        name="OpenAI",
        color="#00d084",
        key_placeholder="sk-...",
        models=("gpt-4o", "gpt-4o-mini"),
    ),
}

PROVIDER_KEY_ENV: dict[ProviderId, str] = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def provider_api_key(provider: ProviderId) -> str | None:
    key = os.getenv(PROVIDER_KEY_ENV[provider], "").strip()
    return key or None


def require_provider_api_key(provider: ProviderId) -> str:
    api_key = provider_api_key(provider)
    if api_key is None:
        raise ProviderConfigurationError(f"{PROVIDER_KEY_ENV[provider]} is not configured.")
    return api_key


def configured_providers(providers: dict[ProviderId, ProviderInfo] = PROVIDERS) -> dict[ProviderId, ProviderInfo]:
    return {
        provider_id: provider
        for provider_id, provider in providers.items()
        if provider_api_key(provider_id)
    }


def public_provider_metadata(providers: dict[ProviderId, ProviderInfo]) -> list[dict]:
    return [
        {
            "id": provider.id,
            "name": provider.name,
            "color": provider.color,
            "key_placeholder": provider.key_placeholder,
            "models": list(provider.models),
        }
        for provider in providers.values()
    ]


def provider_error_message(exc: Exception) -> str:
    if isinstance(exc, ProviderConfigurationError):
        return str(exc)
    if isinstance(exc, AuthenticationError):
        return "API key rejected by provider."
    if isinstance(exc, RateLimitError):
        return "Provider rate limit reached."
    if isinstance(exc, APIError):
        return "Provider API error."
    if isinstance(exc, OpenAIError):
        return "Provider request failed."
    return "Provider request failed."


async def validate_provider_key(payload: ProviderValidationRequest) -> dict:
    try:
        api_key = require_provider_api_key(payload.provider)
        chunks: list[str] = []
        async for chunk in stream_llm(
            provider=payload.provider,
            model=payload.model,
            api_key=api_key,
            system_prompt="You are a connection test. Reply with exactly OK.",
            messages=[{"role": "user", "content": "Reply with exactly OK."}],
            temperature=0.0,
            max_tokens=16,
        ):
            chunks.append(chunk)
            if len("".join(chunks)) >= 2:
                break
        return {"valid": True}
    except Exception as exc:
        return {"valid": False, "error": provider_error_message(exc)}


async def stream_llm(
    *,
    provider: ProviderId,
    model: str,
    api_key: str,
    system_prompt: str,
    messages: list[dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 900,
) -> AsyncGenerator[str, None]:
    if provider == "openai":
        async for token in _stream_openai_compatible(
            api_key=api_key,
            model=model,
            system_prompt=system_prompt,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            yield token
        return

    if provider == "gemini":
        async for token in _stream_gemini(
            api_key=api_key,
            model=model,
            system_prompt=system_prompt,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            yield token
        return

    raise ValueError(f"Unsupported provider: {provider}")


async def _stream_openai_compatible(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    base_url: str | None = None,
) -> AsyncGenerator[str, None]:
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system_prompt}, *messages],
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
    )
    async for event in response:
        token = event.choices[0].delta.content if event.choices else None
        if token:
            yield token


async def _stream_gemini(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
) -> AsyncGenerator[str, None]:
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    gemini_model = genai.GenerativeModel(model)
    prompt = "\n\n".join(
        [
            f"System:\n{system_prompt}",
            *[f"{msg['role'].title()}:\n{msg['content']}" for msg in messages],
        ]
    )
    response = await gemini_model.generate_content_async(
        prompt,
        stream=True,
        generation_config={"temperature": temperature, "max_output_tokens": max_tokens},
    )
    async for chunk in response:
        text = getattr(chunk, "text", None)
        if text:
            yield text
