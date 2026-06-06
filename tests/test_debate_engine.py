import json

from thinktank import debate_engine
from thinktank.models import DebateRequest


def _payload(rounds=2):
    return DebateRequest(
        topic="Should AI govern cities?",
        rounds=rounds,
        features={"cold_start_trials": True, "contradiction_map": True},
        agents=[
            {
                "id": "nova",
                "name": "Nova",
                "role": "Optimist",
                "personality": "Sees constructive progress.",
                "provider": "gemini",
                "model": "gemini-2.5-flash",
            },
            {
                "id": "rex",
                "name": "Rex",
                "role": "Skeptic",
                "personality": "Questions hidden risks.",
                "provider": "gemini",
                "model": "gemini-2.5-flash",
            },
            {
                "id": "mira",
                "name": "Mira",
                "role": "Ethicist",
                "personality": "Centers human impact.",
                "provider": "gemini",
                "model": "gemini-2.5-flash",
            },
        ],
    )


def _decode(raw):
    return json.loads(raw.strip().removeprefix("data: "))


def test_debate_stream_contract_and_cold_start_context(monkeypatch):
    calls = []

    async def fake_stream_llm(**kwargs):
        calls.append(kwargs)
        system = kwargs["system_prompt"]
        if "Pixel Council Judge" in system:
            text = "Nova made the clearest case. Rex exposed useful risks. WINNER: Nova"
        elif "You are Nova" in system:
            text = (
                "Solar roofs improve school resilience through local energy audits and student maintenance plans. "
                "That path keeps infrastructure lessons practical while campuses reduce backup power failures."
            )
        elif "You are Rex" in system:
            text = (
                "Budget limits require procurement oversight because vendors can overpromise city service software. "
                "The cautious path is independent review before agencies depend on automated workflows."
            )
        else:
            text = (
                "Privacy safeguards protect families when school tools collect attendance and wellbeing signals. "
                "Clear consent rules matter before dashboards become part of daily classroom decisions."
            )
        for token in text.split(" "):
            yield token + " "

    monkeypatch.setattr(debate_engine, "stream_llm", fake_stream_llm)
    monkeypatch.setattr(debate_engine, "require_provider_api_key", lambda provider: "test-key")

    async def collect():
        events = []
        async for raw in debate_engine.run_debate(_payload()):
            events.append(_decode(raw))
        return events

    import asyncio

    events = asyncio.run(collect())
    types = [event["type"] for event in events]

    for expected in [
        "session_start",
        "round_start",
        "agent_start",
        "token",
        "agent_done",
        "memory_wipe",
        "claim_detected",
        "consensus_unresolved",
        "judge_start",
        "judge_token",
        "winner",
        "done",
    ]:
        assert expected in types

    assert types[-1] == "done"
    assert len([event for event in events if event["type"] == "agent_done"]) == 6
    assert len(calls) == 7

    round_two_nova = calls[3]["messages"][0]["content"]
    assert "Rex" in round_two_nova
    assert "Mira" in round_two_nova
    assert "Nova (" not in round_two_nova


def test_debate_stops_when_consensus_is_detected(monkeypatch):
    calls = []

    async def fake_stream_llm(**kwargs):
        calls.append(kwargs)
        system = kwargs["system_prompt"]
        if "Pixel Council Judge" in system:
            text = "The council found a shared position around public audits. WINNER: Nova"
        else:
            text = (
                "Public audits improve trust because every automated decision has a visible appeal path. "
                "Open review keeps city tools useful while preserving human accountability."
            )
        for token in text.split(" "):
            yield token + " "

    monkeypatch.setattr(debate_engine, "stream_llm", fake_stream_llm)
    monkeypatch.setattr(debate_engine, "require_provider_api_key", lambda provider: "test-key")

    async def collect():
        events = []
        async for raw in debate_engine.run_debate(_payload(rounds=6)):
            events.append(_decode(raw))
        return events

    import asyncio

    events = asyncio.run(collect())
    types = [event["type"] for event in events]

    assert "consensus_detected" in types
    assert "consensus_unresolved" not in types
    assert "memory_wipe" not in types
    assert len([event for event in events if event["type"] == "agent_done"]) == 3
    assert len(calls) == 4


def test_invalid_agent_name_rejected():
    data = _payload().model_dump()
    data["agents"][1]["name"] = "Nova"

    try:
        DebateRequest(**data)
    except ValueError as exc:
        assert "Agent names must be unique" in str(exc)
    else:
        raise AssertionError("Duplicate agent names should be rejected")
