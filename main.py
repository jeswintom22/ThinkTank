import asyncio
import json
import os
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

# ─── Provider Registry ────────────────────────────────────────────────────────
PROVIDERS = {
    "openai": {
        "name": "OpenAI",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"],
        "color": "#10b981",
        "badge": "GPT",
    },
    "claude": {
        "name": "Claude",
        "models": ["claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5"],
        "color": "#d97757",
        "badge": "CLAUDE",
    },
    "gemini": {
        "name": "Gemini",
        "models": ["gemini-2.0-flash", "gemini-1.5-flash"],
        "color": "#4285f4",
        "badge": "GEMINI",
    },
    "grok": {
        "name": "Grok",
        "models": ["grok-3", "grok-3-mini"],
        "color": "#e5e7eb",
        "badge": "GROK",
    },
}

ROUND_LABELS = {
    1: "Opening Strikes",
    2: "Rebuttal Round",
    3: "Final Clash",
}

# ─── Pydantic Models ──────────────────────────────────────────────────────────
class ProviderConfig(BaseModel):
    provider: str
    model: str
    api_key: str


class Agent(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)
    role: str
    provider_config: ProviderConfig


class DebateRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=600)
    agents: list[Agent] = Field(..., min_length=2, max_length=5)
    rounds: int = Field(default=2, ge=1, le=3)


class ValidateRequest(BaseModel):
    provider: str
    model: str
    api_key: str


# ─── FastAPI App ──────────────────────────────────────────────────────────────
app = FastAPI(title="ThinkTank")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Unified LLM Streaming ────────────────────────────────────────────────────
async def stream_llm(
    provider_config: ProviderConfig,
    system: str,
    messages: list[dict],
    temperature: float = 0.85,
) -> AsyncGenerator[str, None]:
    provider = provider_config.provider
    api_key = provider_config.api_key
    model = provider_config.model

    if provider == "openai":
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key)
        full_messages = [{"role": "system", "content": system}] + messages
        stream = await client.chat.completions.create(
            model=model,
            messages=full_messages,
            temperature=temperature,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    elif provider == "grok":
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
        full_messages = [{"role": "system", "content": system}] + messages
        stream = await client.chat.completions.create(
            model=model,
            messages=full_messages,
            temperature=temperature,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    elif provider == "claude":
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=api_key)
        async with client.messages.stream(
            model=model,
            max_tokens=1024,
            system=system,
            messages=messages,
            temperature=temperature,
        ) as stream:
            async for text in stream.text_stream:
                yield text

    elif provider == "gemini":
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        gemini_model = genai.GenerativeModel(model)

        # Convert messages to Gemini format
        gemini_messages = []
        for i, msg in enumerate(messages):
            role = msg["role"]
            content = msg["content"]
            if role == "system":
                # Prepend to first user message
                continue
            elif role == "user":
                # Check if we need to prepend system prompt
                if i == 0 or (i == 1 and messages[0]["role"] == "system"):
                    content = f"System: {system}\n\n{content}"
                gemini_messages.append({"role": "user", "parts": [content]})
            elif role == "assistant":
                gemini_messages.append({"role": "model", "parts": [content]})

        if not gemini_messages:
            gemini_messages = [{"role": "user", "parts": [f"System: {system}\n\n{messages[-1]['content']}"]}]

        response = await gemini_model.generate_content_async(
            gemini_messages,
            stream=True,
            generation_config={"temperature": temperature, "max_output_tokens": 1024},
        )
        async for chunk in response:
            if chunk.text:
                yield chunk.text

    else:
        raise ValueError(f"Unknown provider: {provider}")


# ─── System Prompts ───────────────────────────────────────────────────────────
ROLE_CONTEXT = {
    "Optimist": "You see the best possible outcomes and opportunities in everything. Champion progress and possibility.",
    "Skeptic": "You question assumptions and demand evidence for every claim. Nothing passes without scrutiny.",
    "Devil's Advocate": "You argue the opposite of the obvious position, even if you personally disagree. Challenge consensus.",
    "Neutral": "You present balanced perspectives, weighing pros and cons carefully. Seek nuance over polarization.",
}


def debater_system_prompt(agent: Agent, topic: str, round_num: int) -> str:
    role_context = ROLE_CONTEXT.get(agent.role, agent.role)
    provider_name = PROVIDERS.get(agent.provider_config.provider, {}).get("name", agent.provider_config.provider)
    memory_note = (
        "CRITICAL: Your memory has been fully reset. You have NO recollection of your previous arguments. "
        "Reason completely fresh from first principles — do NOT reference or repeat what you said before."
        if round_num > 1
        else ""
    )
    return f"""You are {agent.name}, a debater powered by {provider_name}.
Your role: {agent.role}
{role_context}
{memory_note}
Topic: "{topic}"
Argue passionately in 3-5 sentences. Be direct and opinionated. No bullet points. Speak naturally.
When you've seen others' arguments, push back on them forcefully.""".strip()


def judge_system_prompt(topic: str) -> str:
    return f"""You are an impartial Judge AI synthesizing a multi-round, multi-model debate on: "{topic}"

Each agent had their memory fully reset between rounds — so each round represents genuinely independent reasoning, not position defense. This is the Cold Start Protocol: same model, potentially different conclusion each round.

Your verdict must:
1. Identify the most compelling argument from each debater across all rounds (name them explicitly)
2. Highlight where agents INDEPENDENTLY converged on the same point — this is the strongest signal of truth
3. Note any dramatic position shifts between rounds (evidence of genuine fresh reasoning)
4. Synthesize a final answer drawing from the strongest cross-model reasoning
5. Declare a winner — the agent whose reasoning was most consistent and intellectually compelling

Be formal but engaging. 7-9 sentences. End with "WINNER: [Agent Name]" on its own line."""


# ─── SSE Helpers ─────────────────────────────────────────────────────────────
def sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


# ─── Debate Stream ────────────────────────────────────────────────────────────
async def debate_stream(payload: DebateRequest) -> AsyncGenerator[str, None]:
    all_rounds_transcript = []

    for round_num in range(1, payload.rounds + 1):
        label = ROUND_LABELS.get(round_num, f"Round {round_num}")
        yield sse({"type": "round_start", "round": round_num, "total": payload.rounds, "label": label})
        await asyncio.sleep(0.1)

        round_transcript = []

        for agent in payload.agents:
            messages = []
            if round_num > 1:
                prev_round = all_rounds_transcript[-1]
                others = [e for e in prev_round if e["agent"] != agent.name]
                if others:
                    others_text = "\n\n".join(
                        f"{e['agent']} argued: {e['content']}" for e in others
                    )
                    messages.append({
                        "role": "user",
                        "content": (
                            f"[COLD START — your memory has been wiped]\n\n"
                            f"Other perspectives from the previous round:\n\n{others_text}\n\n"
                            f"Now give YOUR fresh argument on: {payload.topic}"
                        ),
                    })
                else:
                    messages.append({
                        "role": "user",
                        "content": f"Give your fresh argument on: {payload.topic}",
                    })
            else:
                messages.append({
                    "role": "user",
                    "content": f"Give your opening argument on: {payload.topic}",
                })

            system = debater_system_prompt(agent, payload.topic, round_num)

            yield sse({
                "type": "agent_start",
                "agent": agent.name,
                "provider": agent.provider_config.provider,
                "model": agent.provider_config.model,
                "round": round_num,
            })

            full_response = []
            try:
                async for chunk in stream_llm(agent.provider_config, system, messages):
                    yield sse({"type": "chunk", "agent": agent.name, "text": chunk})
                    full_response.append(chunk)
            except Exception as e:
                yield sse({"type": "error", "message": f"{agent.name} ({agent.provider_config.provider}): {str(e)}"})
                full_response = ["[Error generating response]"]

            agent_content = "".join(full_response)
            round_transcript.append({"agent": agent.name, "content": agent_content})
            yield sse({"type": "agent_done", "agent": agent.name})
            await asyncio.sleep(0.2)

        all_rounds_transcript.append(round_transcript)

        if round_num < payload.rounds:
            yield sse({"type": "memory_reset", "round": round_num + 1})
            await asyncio.sleep(0.8)

    # Judge
    yield sse({"type": "judge_start"})

    all_text_parts = []
    for i, round_t in enumerate(all_rounds_transcript, 1):
        all_text_parts.append(f"=== ROUND {i}: {ROUND_LABELS.get(i, f'Round {i}')} ===")
        for entry in round_t:
            all_text_parts.append(f"{entry['agent']}: {entry['content']}")

    judge_messages = [{"role": "user", "content": "\n\n".join(all_text_parts)}]
    judge_provider = payload.agents[0].provider_config

    winner = None
    judge_text = []
    try:
        async for chunk in stream_llm(judge_provider, judge_system_prompt(payload.topic), judge_messages, temperature=0.3):
            yield sse({"type": "chunk", "agent": "Judge", "text": chunk, "role": "judge"})
            judge_text.append(chunk)
    except Exception as e:
        yield sse({"type": "error", "message": f"Judge error: {str(e)}"})

    # Extract winner from judge text
    full_judge = "".join(judge_text)
    for line in full_judge.split("\n"):
        if line.strip().startswith("WINNER:"):
            winner_raw = line.split(":", 1)[1].strip()
            for agent in payload.agents:
                if agent.name.lower() in winner_raw.lower():
                    winner = agent.name
                    break

    yield sse({"type": "done", "winner": winner})


# ─── Endpoints ────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return FileResponse("index.html")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/providers")
async def get_providers():
    return PROVIDERS


@app.post("/validate")
async def validate(req: ValidateRequest):
    try:
        chunks = []
        async for chunk in stream_llm(
            ProviderConfig(provider=req.provider, model=req.model, api_key=req.api_key),
            "You are a test assistant.",
            [{"role": "user", "content": "Reply with exactly: OK"}],
            temperature=0.0,
        ):
            chunks.append(chunk)
            if len("".join(chunks)) > 50:
                break
        return {"valid": True, "error": None}
    except Exception as e:
        return {"valid": False, "error": str(e)}


@app.post("/debate")
async def debate(payload: DebateRequest):
    return StreamingResponse(
        debate_stream(payload),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ─── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8010))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)