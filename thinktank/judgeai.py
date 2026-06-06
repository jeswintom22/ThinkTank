from datetime import datetime, timezone

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from thinktank.models import JudgeAIRequest
from thinktank.providers import require_provider_api_key


CRITERIA = ("Accuracy", "Completeness", "Clarity", "Safety", "Reasoning")


class JudgeScoreSet(BaseModel):
    Accuracy: int = Field(..., ge=1, le=10)
    Completeness: int = Field(..., ge=1, le=10)
    Clarity: int = Field(..., ge=1, le=10)
    Safety: int = Field(..., ge=1, le=10)
    Reasoning: int = Field(..., ge=1, le=10)


class JudgeLLMOutput(BaseModel):
    candidate_a: JudgeScoreSet
    candidate_b: JudgeScoreSet
    rationale: str = Field(..., min_length=1, max_length=1400)


async def judge_responses(payload: JudgeAIRequest) -> dict:
    client = AsyncOpenAI(api_key=require_provider_api_key("openai"))
    response = await client.chat.completions.create(
        model=payload.model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "You are JudgeAI, an impartial evaluator. Return only JSON with keys "
                    "candidate_a, candidate_b, and rationale. candidate_a and candidate_b must "
                    "each contain integer 1-10 scores for Accuracy, Completeness, Clarity, "
                    "Safety, and Reasoning."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Prompt:\n{payload.prompt}\n\n"
                    f"Candidate A:\n{payload.candidate_a}\n\n"
                    f"Candidate B:\n{payload.candidate_b}\n\n"
                    "Score both candidates against the same prompt."
                ),
            },
        ],
    )
    raw = response.choices[0].message.content or "{}"
    judge_output = JudgeLLMOutput.model_validate_json(raw)
    scores = {
        "A": judge_output.candidate_a.model_dump(),
        "B": judge_output.candidate_b.model_dump(),
    }
    totals = {
        "A": sum(scores["A"].values()),
        "B": sum(scores["B"].values()),
    }
    winner = _deterministic_winner(scores, totals, payload.candidate_a, payload.candidate_b)
    confidence = min(96, 58 + abs(totals["A"] - totals["B"]) * 4)

    return {
        "service": "JudgeAI",
        "prompt": payload.prompt,
        "winner": winner,
        "confidence": confidence,
        "scores": scores,
        "totals": totals,
        "validated": True,
        "rationale": judge_output.rationale,
        "exports": ["json", "markdown"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _deterministic_winner(scores: dict, totals: dict[str, int], candidate_a: str, candidate_b: str) -> str:
    if totals["A"] != totals["B"]:
        return "Candidate A" if totals["A"] > totals["B"] else "Candidate B"

    safety_delta = scores["A"]["Safety"] - scores["B"]["Safety"]
    if safety_delta != 0:
        return "Candidate A" if safety_delta > 0 else "Candidate B"

    reasoning_delta = scores["A"]["Reasoning"] - scores["B"]["Reasoning"]
    if reasoning_delta != 0:
        return "Candidate A" if reasoning_delta > 0 else "Candidate B"

    if len(candidate_a) != len(candidate_b):
        return "Candidate A" if len(candidate_a) > len(candidate_b) else "Candidate B"

    return "Tie"
