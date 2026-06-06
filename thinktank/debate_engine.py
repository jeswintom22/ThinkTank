import json
from collections.abc import AsyncGenerator
from uuid import uuid4

from thinktank.analysis import ContradictionAnalyzer
from thinktank.models import CouncilAgent, DebateRequest, TranscriptEntry
from thinktank.providers import provider_error_message, require_provider_api_key, stream_llm


ROUND_LABELS = {
    1: "Opening Arguments",
    2: "Cold Rebuttals",
    3: "Final Statements",
}


def _round_label(round_number: int) -> str:
    if round_number in ROUND_LABELS:
        return ROUND_LABELS[round_number]
    return f"Consensus Round {round_number}"


ROLE_HINTS = {
    "Optimist": "Search for upside, practical paths, and constructive outcomes.",
    "Skeptic": "Challenge assumptions, demand evidence, and expose weak logic.",
    "Devil's Advocate": "Argue the neglected opposing case with discipline.",
    "Pragmatist": "Prioritize tradeoffs, feasibility, timelines, and implementation risk.",
    "Ethicist": "Focus on human impact, fairness, incentives, and moral hazards.",
    "Futurist": "Project second-order effects and long-range consequences.",
}


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def run_debate(payload: DebateRequest) -> AsyncGenerator[str, None]:
    session_id = str(uuid4())
    transcript: list[TranscriptEntry] = []
    analyzer = ContradictionAnalyzer()

    yield sse(
        {
            "type": "session_start",
            "session_id": session_id,
            "topic": payload.topic,
            "rounds": payload.rounds,
            "agents": [agent.model_dump(exclude={"personality"}) for agent in payload.agents],
        }
    )

    consensus_reached = False

    for round_number in range(1, payload.rounds + 1):
        round_convergence_pairs: set[tuple[str, str]] = set()
        round_contradictions = 0

        yield sse(
            {
                "type": "round_start",
                "round": round_number,
                "total_rounds": payload.rounds,
                "label": _round_label(round_number),
            }
        )

        for agent in payload.agents:
            yield sse(
                {
                    "type": "agent_start",
                    "round": round_number,
                    "agent_id": agent.id,
                    "agent_name": agent.name,
                    "provider": agent.provider,
                    "model": agent.model,
                }
            )

            messages = _agent_messages(payload, agent, transcript, round_number)
            system_prompt = _agent_system_prompt(payload.topic, agent, round_number)
            full_text: list[str] = []

            try:
                async for token in stream_llm(
                    provider=agent.provider,
                    model=agent.model,
                    api_key=require_provider_api_key(agent.provider),
                    system_prompt=system_prompt,
                    messages=messages,
                    temperature=0.78,
                    max_tokens=700,
                ):
                    full_text.append(token)
                    yield sse(
                        {
                            "type": "token",
                            "round": round_number,
                            "agent_id": agent.id,
                            "agent_name": agent.name,
                            "text": token,
                        }
                    )
            except Exception as exc:
                yield sse(
                    {
                        "type": "error",
                        "agent_id": agent.id,
                        "agent_name": agent.name,
                        "message": f"{agent.name}: {provider_error_message(exc)}",
                    }
                )
                return

            content = "".join(full_text).strip()
            transcript.append(
                TranscriptEntry(
                    round=round_number,
                    agent_id=agent.id,
                    agent_name=agent.name,
                    role=agent.role,
                    provider=agent.provider,
                    model=agent.model,
                    content=content,
                )
            )
            yield sse(
                {
                    "type": "agent_done",
                    "round": round_number,
                    "agent_id": agent.id,
                    "agent_name": agent.name,
                    "characters": len(content),
                }
            )

            analysis = analyzer.analyze_argument(
                round_number=round_number,
                agent_id=agent.id,
                agent_name=agent.name,
                text=content,
            )
            round_contradictions += len(analysis.contradictions)
            for item in analysis.convergences:
                round_convergence_pairs.add(tuple(sorted(item["agents"])))

            if payload.features.contradiction_map:
                for claim in analysis.claims:
                    yield sse(
                        {
                            "type": "claim_detected",
                            "claim_id": claim.id,
                            "round": claim.round,
                            "agent_id": claim.agent_id,
                            "agent_name": claim.agent_name,
                            "text": claim.text,
                            "keywords": sorted(claim.keywords)[:8],
                            "polarity": claim.polarity,
                        }
                    )
                for item in analysis.convergences:
                    yield sse({"type": "convergence_detected", **item})
                for item in analysis.contradictions:
                    yield sse({"type": "contradiction_detected", **item})

        if _has_round_consensus(
            agents=payload.agents,
            convergence_pairs=round_convergence_pairs,
            contradiction_count=round_contradictions,
        ):
            consensus_reached = True
            yield sse(
                {
                    "type": "consensus_detected",
                    "round": round_number,
                    "convergence_pairs": len(round_convergence_pairs),
                    "message": "Consensus detected. The judge will now summarize the shared position.",
                }
            )
            break

        if payload.features.cold_start_trials and round_number < payload.rounds:
            yield sse(
                {
                    "type": "memory_wipe",
                    "completed_round": round_number,
                    "next_round": round_number + 1,
                    "message": "Agent memories cleared. Next round receives only other agents' prior arguments.",
                }
            )

    if not consensus_reached:
        yield sse(
            {
                "type": "consensus_unresolved",
                "rounds_completed": payload.rounds,
                "message": "Consensus was not detected before the round cap. The judge will decide from the full debate.",
            }
        )

    async for event in _judge(payload, transcript):
        yield event


def _has_round_consensus(
    *,
    agents: list[CouncilAgent],
    convergence_pairs: set[tuple[str, str]],
    contradiction_count: int,
) -> bool:
    if contradiction_count or not convergence_pairs:
        return False

    represented_agents = {agent for pair in convergence_pairs for agent in pair}
    minimum_pairs = 1 if len(agents) == 2 else len(agents) - 1
    return len(represented_agents) == len(agents) and len(convergence_pairs) >= minimum_pairs


def _agent_system_prompt(topic: str, agent: CouncilAgent, round_number: int) -> str:
    role_hint = ROLE_HINTS.get(agent.role, agent.personality)
    cold_note = (
        "Cold Start Protocol is active: do not rely on your own earlier statements. "
        "Reason freshly from the evidence and other agents' prior arguments."
        if round_number > 1
        else "This is your independent opening position."
    )
    return f"""
You are {agent.name}, a council member in ThinkTank's Pixel Council.
Topic: "{topic}"
Role: {agent.role}
Personality: {agent.personality}
Role discipline: {role_hint}
{cold_note}

Write 3-5 direct, memorable sentences. No bullet points. Make one clear claim, support it, and explicitly challenge weak reasoning when context is available.
If the council is converging, sharpen the shared position instead of forcing disagreement.
""".strip()


def _agent_messages(
    payload: DebateRequest,
    agent: CouncilAgent,
    transcript: list[TranscriptEntry],
    round_number: int,
) -> list[dict[str, str]]:
    if round_number == 1:
        return [{"role": "user", "content": f"Give your opening argument on: {payload.topic}"}]

    previous_round = [entry for entry in transcript if entry.round == round_number - 1]
    if payload.features.cold_start_trials:
        visible_entries = [entry for entry in previous_round if entry.agent_id != agent.id]
        context = "\n\n".join(
            f"{entry.agent_name} ({entry.role}) argued: {entry.content}" for entry in visible_entries
        )
        return [
            {
                "role": "user",
                "content": (
                    "[COLD START MEMORY WIPE]\n"
                    "Your previous answer is hidden from you. You can only see other council members' prior arguments.\n\n"
                    f"{context}\n\n"
                    f"Now produce your fresh round {round_number} argument on: {payload.topic}"
                ),
            }
        ]

    context = "\n\n".join(
        f"{entry.agent_name} ({entry.role}) argued: {entry.content}" for entry in previous_round
    )
    return [
        {
            "role": "user",
            "content": f"Previous round:\n\n{context}\n\nNow produce your round {round_number} argument.",
        }
    ]


async def _judge(payload: DebateRequest, transcript: list[TranscriptEntry]) -> AsyncGenerator[str, None]:
    judge_agent = payload.agents[0]
    yield sse(
        {
            "type": "judge_start",
            "provider": judge_agent.provider,
            "model": judge_agent.model,
        }
    )

    system_prompt = f"""
You are the impartial Pixel Council Judge.
Topic: "{payload.topic}"
Read every argument. Identify strongest claims, contradictions, and independent convergence.
Deliver 7-9 sentences, then end with a final line exactly like: WINNER: Agent Name
Do not use bullet points.
""".strip()
    transcript_text = "\n\n".join(
        f"Round {entry.round} | {entry.agent_name} ({entry.role}, {entry.provider}/{entry.model}): {entry.content}"
        for entry in transcript
    )
    full_text: list[str] = []

    try:
        async for token in stream_llm(
            provider=judge_agent.provider,
            model=judge_agent.model,
            api_key=require_provider_api_key(judge_agent.provider),
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": transcript_text}],
            temperature=0.35,
            max_tokens=900,
        ):
            full_text.append(token)
            yield sse({"type": "judge_token", "text": token})
    except Exception as exc:
        yield sse({"type": "error", "message": f"Judge: {provider_error_message(exc)}"})
        return

    judge_text = "".join(full_text)
    winner = _extract_winner(judge_text, payload.agents)
    yield sse({"type": "winner", "winner": winner})
    yield sse({"type": "done", "winner": winner})


def _extract_winner(judge_text: str, agents: list[CouncilAgent]) -> str | None:
    for line in reversed(judge_text.splitlines()):
        if line.strip().lower().startswith("winner:"):
            winner_text = line.split(":", 1)[1].strip().lower()
            for agent in agents:
                if agent.name.lower() in winner_text:
                    return agent.name
    return None
