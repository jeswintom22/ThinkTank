import asyncio
import json
import os
from pathlib import Path
from typing import AsyncGenerator, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from openai import APIError, AuthenticationError, AsyncOpenAI, OpenAIError, RateLimitError
from pydantic import BaseModel, Field
from dotenv import load_dotenv


MODEL = "gpt-4o"
BASE_DIR = Path(__file__).resolve().parent
load_dotenv()

app = FastAPI(title="ThinkTank", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Agent(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)
    personality: str = Field(..., min_length=1, max_length=400)


class DebateRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=600)
    agents: list[Agent] = Field(..., min_length=2, max_length=5)
    rounds: int = Field(default=2, ge=1, le=3)


def sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def debater_system_prompt(agent: Agent, topic: str) -> str:
    return f"""
You are {agent.name}, an AI debater with this personality: {agent.personality}.
You are participating in a group debate on the topic: "{topic}".
Argue your position clearly and passionately in 3-5 sentences.
Be direct, opinionated, and stay true to your personality.
Do NOT use bullet points. Write naturally like you're speaking.
Occasionally reference or push back on what others have said.
""".strip()


def judge_system_prompt(topic: str) -> str:
    return f"""
You are an impartial Judge AI. You have just observed a full debate between multiple AI agents on the topic: "{topic}".
Read all arguments carefully. Then deliver a final verdict in this structure:
1. A 2-sentence summary of the strongest point made by each debater (mention them by name)
2. A final synthesized answer or conclusion that draws from the best arguments
3. One sentence declaring the overall "winner" of the debate (the agent who made the most compelling case)
Write in a formal but engaging tone. Total response: 6-8 sentences.
""".strip()


def round_label(round_number: int) -> str:
    if round_number == 1:
        return "Opening Arguments"
    if round_number == 2:
        return "Rebuttal Round"
    return "Final Counterpoints"


async def stream_completion(
    client: AsyncOpenAI,
    *,
    messages: list[dict[str, str]],
    agent_name: str,
    role: Literal["debater", "judge"],
) -> AsyncGenerator[tuple[str, str], None]:
    stream = await client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.85 if role == "debater" else 0.35,
        stream=True,
    )

    full_text: list[str] = []
    async for event in stream:
        delta = event.choices[0].delta.content if event.choices else None
        if not delta:
            continue
        full_text.append(delta)
        yield delta, sse_event(
            {
                "type": "message",
                "agent": agent_name,
                "role": role,
                "chunk": delta,
            }
        )

    yield "".join(full_text), ""


def transcript_as_context(transcript: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {"role": "user", "content": f"[{entry['agent']}] said: {entry['content']}"}
        for entry in transcript
    ]


async def debate_stream(payload: DebateRequest) -> AsyncGenerator[str, None]:
    if not os.getenv("OPENAI_API_KEY"):
        yield sse_event(
            {
                "type": "error",
                "message": "OPENAI_API_KEY is not set on the backend.",
            }
        )
        return

    client = AsyncOpenAI()
    full_transcript: list[dict[str, str]] = []
    round_one_transcript: list[dict[str, str]] = []

    try:
        for round_number in range(1, payload.rounds + 1):
            yield sse_event(
                {
                    "type": "round_start",
                    "round": round_number,
                    "label": round_label(round_number),
                }
            )
            await asyncio.sleep(0)

            for agent in payload.agents:
                messages = [
                    {
                        "role": "system",
                        "content": debater_system_prompt(agent, payload.topic),
                    }
                ]

                if round_number == 1:
                    messages.append(
                        {
                            "role": "user",
                            "content": f"Give your opening argument on: {payload.topic}",
                        }
                    )
                elif round_number == 2:
                    messages.extend(transcript_as_context(round_one_transcript))
                    messages.append(
                        {
                            "role": "user",
                            "content": f"Now give your rebuttal as {agent.name}.",
                        }
                    )
                else:
                    messages.extend(transcript_as_context(full_transcript))
                    messages.append(
                        {
                            "role": "user",
                            "content": f"Now give your final counterpoint as {agent.name}.",
                        }
                    )

                agent_chunks: list[str] = []
                yield sse_event(
                    {
                        "type": "message",
                        "agent": agent.name,
                        "role": "debater",
                        "chunk": "",
                    }
                )
                async for content, event in stream_completion(
                    client,
                    messages=messages,
                    agent_name=agent.name,
                    role="debater",
                ):
                    if event:
                        agent_chunks.append(content)
                        yield event

                argument = "".join(agent_chunks).strip()
                transcript_entry = {"agent": agent.name, "content": argument}
                full_transcript.append(transcript_entry)
                if round_number == 1:
                    round_one_transcript.append(transcript_entry)

        yield sse_event({"type": "judge_start"})
        judge_messages = [
            {"role": "system", "content": judge_system_prompt(payload.topic)},
            {
                "role": "user",
                "content": "\n\n".join(
                    f"{entry['agent']}: {entry['content']}" for entry in full_transcript
                ),
            },
        ]

        async for _, event in stream_completion(
            client,
            messages=judge_messages,
            agent_name="Judge",
            role="judge",
        ):
            if event:
                yield event

        yield sse_event({"type": "done"})
    except AuthenticationError:
        yield sse_event(
            {
                "type": "error",
                "message": "OpenAI rejected the API key. Set a valid OPENAI_API_KEY and restart the backend.",
            }
        )
    except RateLimitError:
        yield sse_event(
            {
                "type": "error",
                "message": "OpenAI rate limit reached. Wait a moment and try again.",
            }
        )
    except APIError:
        yield sse_event(
            {
                "type": "error",
                "message": "OpenAI returned an API error. Try again in a moment.",
            }
        )
    except OpenAIError:
        yield sse_event(
            {
                "type": "error",
                "message": "The OpenAI request failed. Check your API key and connection.",
            }
        )
    except Exception:
        yield sse_event({"type": "error", "message": "Something went wrong. Try again."})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html")


@app.post("/debate")
async def debate(payload: DebateRequest) -> StreamingResponse:
    if len({agent.name.strip().lower() for agent in payload.agents}) != len(payload.agents):
        raise HTTPException(status_code=400, detail="Agent names must be unique.")

    clean_payload = DebateRequest(
        topic=payload.topic.strip(),
        agents=[
            Agent(name=agent.name.strip(), personality=agent.personality.strip())
            for agent in payload.agents
        ],
        rounds=payload.rounds,
    )

    return StreamingResponse(
        debate_stream(clean_payload),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
