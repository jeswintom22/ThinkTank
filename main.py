import asyncio
import json
import os
import re
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import google.generativeai as genai
from anthropic import APIError as AnthropicAPIError
from anthropic import APIStatusError as AnthropicAPIStatusError
from anthropic import AuthenticationError as AnthropicAuthenticationError
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from openai import APIError as OpenAIAPIError
from openai import APIStatusError as OpenAIAPIStatusError
from openai import AuthenticationError as OpenAIAuthenticationError
from openai import AsyncOpenAI
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parent
PROVIDERS = {"openai", "gemini", "claude", "grok"}
PROVIDER_MODELS = {
    "openai": "gpt-4o",
    "gemini": "gemini-2.0-flash",
    "claude": "claude-opus-4-5",
    "grok": "grok-3",
}
PROVIDER_DISPLAY = {
    "openai": "OpenAI GPT-4o",
    "gemini": "Google Gemini 1.5 Pro",
    "claude": "Anthropic Claude Opus 4.5",
    "grok": "xAI Grok 3",
}
ROUND_LABELS = {
    1: "Opening Strikes",
    2: "Rebuttal Round",
    3: "Final Clash",
}

load_dotenv()

app = FastAPI(title="ThinkTank", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ProviderKeys(BaseModel):
    openai: str = ""
    gemini: str = ""
    claude: str = ""
    grok: str = ""


class Agent(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)
    personality: str = Field(..., min_length=1, max_length=500)
    provider: str
    color: str


class DebateRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=700)
    agents: list[Agent] = Field(..., min_length=2, max_length=5)
    rounds: int = Field(default=2, ge=1, le=3)
    provider_keys: ProviderKeys


def sse_event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def clean_provider(provider: str) -> str:
    return provider.strip().lower()


def get_key(keys: ProviderKeys, provider: str) -> str:
    return getattr(keys, clean_provider(provider), "").strip()


def provider_display_name(provider: str) -> str:
    return PROVIDER_DISPLAY.get(clean_provider(provider), provider)


def normalize_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for message in messages:
        role = message.get("role", "user")
        content = str(message.get("content", "")).strip()
        if role == "system" or not content:
            continue
        if role not in {"user", "assistant"}:
            role = "user"
        if normalized and normalized[-1]["role"] == role:
            normalized[-1]["content"] += f"\n\n{content}"
        else:
            normalized.append({"role": role, "content": content})
    return normalized or [{"role": "user", "content": "Begin."}]


def to_gemini_contents(messages: list[dict[str, str]]) -> list[dict[str, Any]]:
    role_map = {"assistant": "model", "user": "user"}
    contents: list[dict[str, Any]] = []
    for message in normalize_messages(messages):
        gemini_role = role_map.get(message["role"], "user")
        if contents and contents[-1]["role"] == gemini_role:
            contents[-1]["parts"][0] += f"\n\n{message['content']}"
        else:
            contents.append({"role": gemini_role, "parts": [message["content"]]})
    return contents


def _next_gemini_chunk(iterator: Any) -> str | None:
    try:
        chunk = next(iterator)
    except StopIteration:
        return None

    text = getattr(chunk, "text", None)
    if text:
        return text
    parts = getattr(chunk, "parts", None) or []
    extracted: list[str] = []
    for part in parts:
        value = getattr(part, "text", None)
        if value:
            extracted.append(value)
    return "".join(extracted) if extracted else ""


async def call_llm_stream(
    provider: str,
    api_key: str,
    system: str,
    messages: list[dict],
    temperature: float,
) -> AsyncGenerator[str, None]:
    provider = clean_provider(provider)
    api_key = api_key.strip()

    if provider not in PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider}")
    if not api_key:
        raise ValueError(f"Missing API key for {provider_display_name(provider)}.")

    if provider in {"openai", "grok"}:
        client_kwargs: dict[str, str] = {"api_key": api_key}
        if provider == "grok":
            client_kwargs["base_url"] = "https://api.x.ai/v1"
        client = AsyncOpenAI(**client_kwargs)
        stream = await client.chat.completions.create(
            model=PROVIDER_MODELS[provider],
            messages=[{"role": "system", "content": system}, *normalize_messages(messages)],
            temperature=temperature,
            stream=True,
        )
        async for event in stream:
            if not event.choices:
                continue
            chunk = event.choices[0].delta.content
            if chunk:
                yield chunk
        return

    if provider == "claude":
        client = AsyncAnthropic(api_key=api_key)
        async with client.messages.stream(
            model=PROVIDER_MODELS[provider],
            max_tokens=700,
            temperature=temperature,
            system=system,
            messages=normalize_messages(messages),
        ) as stream:
            async for text in stream.text_stream:
                if text:
                    yield text
        return

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(PROVIDER_MODELS[provider], system_instruction=system)
    iterator = await asyncio.to_thread(
        lambda: model.generate_content(
            to_gemini_contents(messages),
            generation_config={"temperature": temperature, "max_output_tokens": 700},
            stream=True,
        )
    )
    while True:
        chunk = await asyncio.to_thread(_next_gemini_chunk, iterator)
        if chunk is None:
            break
        if chunk:
            yield chunk


def debater_system_prompt(agent: Agent, topic: str) -> str:
    return f"""
You are {agent.name}, a debater with this personality: {agent.personality}.
You are powered by {provider_display_name(agent.provider)}.
Debate topic: "{topic}"
Argue passionately in 3-5 sentences. Be opinionated. No bullet points. Speak naturally.
Reference and push back on what others have said when possible.
""".strip()


def judge_system_prompt(topic: str) -> str:
    return f"""
You are an impartial Judge. Read the full debate on "{topic}" and deliver a verdict:
1. Name the strongest point from each debater (mention them by name)
2. Synthesize a final answer from the best arguments
3. Declare an overall winner and why
Tone: formal but engaging. Length: 6-8 sentences.
""".strip()


def transcript_context(transcript: list[dict[str, str]]) -> str:
    if not transcript:
        return "No one has spoken yet."
    return "\n\n".join(
        f"{item['agent']} ({provider_display_name(item['provider'])}): {item['content']}"
        for item in transcript
    )


def build_turn_messages(
    *,
    topic: str,
    agent: Agent,
    round_number: int,
    transcript: list[dict[str, str]],
) -> list[dict[str, str]]:
    if round_number == 1:
        prompt = f"Give your opening argument on: {topic}"
    elif round_number == 2:
        prompt = (
            f"Debate so far:\n{transcript_context(transcript)}\n\n"
            f"Now give your rebuttal as {agent.name}. Push back on specific claims."
        )
    else:
        prompt = (
            f"Full debate so far:\n{transcript_context(transcript)}\n\n"
            f"Now give your final counterpoint as {agent.name}. Make it decisive."
        )
    return [{"role": "user", "content": prompt}]


def clamp_score(value: int) -> int:
    return max(0, min(100, int(value)))


def heuristic_score(topic: str, argument: str) -> int:
    words = re.findall(r"[A-Za-z0-9']+", argument)
    unique_words = len(set(word.lower() for word in words))
    topic_terms = set(re.findall(r"[A-Za-z0-9']+", topic.lower()))
    overlap = len(topic_terms.intersection(word.lower() for word in words))
    length_score = min(32, len(words) // 4)
    originality_score = min(24, unique_words // 5)
    topic_score = min(14, overlap * 4)
    punctuation_score = 6 if any(mark in argument for mark in ("?", "!", ";", ":")) else 0
    return clamp_score(34 + length_score + originality_score + topic_score + punctuation_score)


async def score_argument(
    *,
    topic: str,
    agent_name: str,
    argument: str,
    provider_keys: ProviderKeys,
) -> int:
    api_key = provider_keys.openai.strip()
    if not api_key:
        return heuristic_score(topic, argument)

    client = AsyncOpenAI(api_key=api_key)
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a debate scorer. Score this argument 0-100 on "
                        'persuasiveness, clarity, and originality. Reply with ONLY a JSON object: {"score": <integer>}'
                    ),
                },
                {
                    "role": "user",
                    "content": f"Topic: {topic}\n\nArgument by {agent_name}:\n{argument}",
                },
            ],
        )
        content = response.choices[0].message.content or "{}"
        parsed = json.loads(content)
        return clamp_score(parsed.get("score", heuristic_score(topic, argument)))
    except Exception:
        return heuristic_score(topic, argument)


def choose_judge_provider(payload: DebateRequest) -> str | None:
    if payload.provider_keys.openai.strip():
        return "openai"
    for agent in payload.agents:
        provider = clean_provider(agent.provider)
        if get_key(payload.provider_keys, provider):
            return provider
    return None


def provider_error_message(provider: str, exc: Exception) -> str:
    display = provider_display_name(provider)
    if isinstance(
        exc,
        (
            OpenAIAuthenticationError,
            AnthropicAuthenticationError,
        ),
    ):
        return f"{display} rejected the API key. Check Settings and try again."
    if isinstance(
        exc,
        (
            OpenAIAPIStatusError,
            OpenAIAPIError,
            AnthropicAPIStatusError,
            AnthropicAPIError,
        ),
    ):
        return f"{display} returned an API error: {str(exc)}"
    return f"{display} failed: {str(exc) or 'Unknown provider error.'}"


async def debate_stream(payload: DebateRequest) -> AsyncGenerator[str, None]:
    transcript: list[dict[str, str]] = []

    try:
        for round_number in range(1, payload.rounds + 1):
            yield sse_event(
                {
                    "type": "round_start",
                    "round": round_number,
                    "label": ROUND_LABELS.get(round_number, f"Round {round_number}"),
                }
            )

            for agent in payload.agents:
                provider = clean_provider(agent.provider)
                api_key = get_key(payload.provider_keys, provider)
                if not api_key:
                    yield sse_event(
                        {
                            "type": "error",
                            "message": f"{agent.name} is assigned to {provider_display_name(provider)}, but no API key is configured.",
                        }
                    )
                    return

                yield sse_event(
                    {"type": "agent_start", "agent": agent.name, "provider": provider}
                )
                chunks: list[str] = []
                try:
                    async for chunk in call_llm_stream(
                        provider=provider,
                        api_key=api_key,
                        system=debater_system_prompt(agent, payload.topic),
                        messages=build_turn_messages(
                            topic=payload.topic,
                            agent=agent,
                            round_number=round_number,
                            transcript=transcript,
                        ),
                        temperature=0.86,
                    ):
                        chunks.append(chunk)
                        yield sse_event(
                            {"type": "message", "agent": agent.name, "chunk": chunk}
                        )
                except Exception as exc:
                    yield sse_event({"type": "error", "message": provider_error_message(provider, exc)})
                    return

                argument = "".join(chunks).strip()
                transcript.append(
                    {
                        "agent": agent.name,
                        "provider": provider,
                        "content": argument,
                    }
                )
                score = await score_argument(
                    topic=payload.topic,
                    agent_name=agent.name,
                    argument=argument,
                    provider_keys=payload.provider_keys,
                )
                yield sse_event({"type": "score", "agent": agent.name, "score": score})

        judge_provider = choose_judge_provider(payload)
        if not judge_provider:
            yield sse_event(
                {
                    "type": "error",
                    "message": "No configured provider is available for the final Judge verdict.",
                }
            )
            return

        yield sse_event({"type": "judge_start"})
        try:
            async for chunk in call_llm_stream(
                provider=judge_provider,
                api_key=get_key(payload.provider_keys, judge_provider),
                system=judge_system_prompt(payload.topic),
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Topic: {payload.topic}\n\nFull debate transcript:\n"
                            f"{transcript_context(transcript)}"
                        ),
                    }
                ],
                temperature=0.34,
            ):
                yield sse_event(
                    {"type": "message", "agent": "Judge", "chunk": chunk, "role": "judge"}
                )
        except Exception as exc:
            yield sse_event(
                {"type": "error", "message": provider_error_message(judge_provider, exc)}
            )
            return

        yield sse_event({"type": "done"})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        yield sse_event({"type": "error", "message": f"Debate failed: {str(exc)}"})


async def validate_openai_like(provider: str, api_key: str) -> bool:
    client_kwargs: dict[str, str] = {"api_key": api_key}
    if provider == "grok":
        client_kwargs["base_url"] = "https://api.x.ai/v1"
    client = AsyncOpenAI(**client_kwargs)
    await client.chat.completions.create(
        model=PROVIDER_MODELS[provider],
        messages=[{"role": "user", "content": "Reply with OK."}],
        max_tokens=2,
        temperature=0,
    )
    return True


async def validate_gemini(api_key: str) -> bool:
    def run() -> bool:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(PROVIDER_MODELS["gemini"])
        model.generate_content("Reply with OK.", generation_config={"max_output_tokens": 2})
        return True

    return await asyncio.to_thread(run)


async def validate_claude(api_key: str) -> bool:
    client = AsyncAnthropic(api_key=api_key)
    await client.messages.create(
        model=PROVIDER_MODELS["claude"],
        max_tokens=2,
        temperature=0,
        messages=[{"role": "user", "content": "Reply with OK."}],
    )
    return True


async def validate_provider(provider: str, api_key: str) -> bool:
    if not api_key.strip():
        return False
    try:
        if provider in {"openai", "grok"}:
            return await validate_openai_like(provider, api_key.strip())
        if provider == "gemini":
            return await validate_gemini(api_key.strip())
        if provider == "claude":
            return await validate_claude(api_key.strip())
    except Exception:
        return False
    return False


def validate_payload(payload: DebateRequest) -> DebateRequest:
    clean_agents: list[Agent] = []
    for agent in payload.agents:
        provider = clean_provider(agent.provider)
        if provider not in PROVIDERS:
            raise HTTPException(status_code=400, detail=f"Unsupported provider: {agent.provider}")
        name = agent.name.strip()
        personality = agent.personality.strip()
        if not name or not personality:
            raise HTTPException(
                status_code=400,
                detail="Every agent needs a non-empty name and personality.",
            )
        clean_agents.append(
            Agent(
                name=name,
                personality=personality,
                provider=provider,
                color=agent.color.strip(),
            )
        )

    if len({agent.name.lower() for agent in clean_agents}) != len(clean_agents):
        raise HTTPException(status_code=400, detail="Agent names must be unique.")

    return DebateRequest(
        topic=payload.topic.strip(),
        agents=clean_agents,
        rounds=payload.rounds,
        provider_keys=ProviderKeys(
            openai=payload.provider_keys.openai.strip(),
            gemini=payload.provider_keys.gemini.strip(),
            claude=payload.provider_keys.claude.strip(),
            grok=payload.provider_keys.grok.strip(),
        ),
    )


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/validate-keys")
async def validate_keys(provider_keys: ProviderKeys) -> dict[str, bool]:
    tasks = {
        provider: validate_provider(provider, get_key(provider_keys, provider))
        for provider in ("openai", "gemini", "claude", "grok")
    }
    results = await asyncio.gather(*tasks.values())
    return dict(zip(tasks.keys(), results, strict=True))


@app.post("/debate")
async def debate(payload: DebateRequest) -> StreamingResponse:
    clean_payload = validate_payload(payload)
    return StreamingResponse(
        debate_stream(clean_payload),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8010")))
