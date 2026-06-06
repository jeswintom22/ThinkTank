import re
from dataclasses import dataclass, field
from itertools import count


STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "because",
    "being",
    "could",
    "every",
    "from",
    "have",
    "into",
    "more",
    "must",
    "only",
    "that",
    "their",
    "there",
    "these",
    "they",
    "this",
    "with",
    "would",
    "should",
}

NEGATIVE_WORDS = {
    "ban",
    "cannot",
    "danger",
    "fail",
    "harm",
    "never",
    "not",
    "oppose",
    "problem",
    "risk",
    "threat",
    "worse",
}

POSITIVE_WORDS = {
    "benefit",
    "better",
    "build",
    "create",
    "enable",
    "good",
    "help",
    "improve",
    "opportunity",
    "progress",
    "support",
    "useful",
    "value",
}


@dataclass
class Claim:
    id: str
    round: int
    agent_id: str
    agent_name: str
    text: str
    keywords: set[str]
    polarity: str


@dataclass
class AnalysisResult:
    claims: list[Claim] = field(default_factory=list)
    convergences: list[dict] = field(default_factory=list)
    contradictions: list[dict] = field(default_factory=list)


class ContradictionAnalyzer:
    def __init__(self):
        self._claims: list[Claim] = []
        self._ids = count(1)

    def analyze_argument(
        self,
        *,
        round_number: int,
        agent_id: str,
        agent_name: str,
        text: str,
    ) -> AnalysisResult:
        result = AnalysisResult()
        for sentence in _sentences(text)[:3]:
            keywords = _keywords(sentence)
            if len(keywords) < 2:
                continue
            claim = Claim(
                id=f"claim-{next(self._ids)}",
                round=round_number,
                agent_id=agent_id,
                agent_name=agent_name,
                text=sentence,
                keywords=keywords,
                polarity=_polarity(sentence),
            )

            for previous in self._claims:
                if previous.agent_id == claim.agent_id:
                    continue
                similarity = _jaccard(previous.keywords, claim.keywords)
                if similarity < 0.28:
                    continue

                shared = sorted(previous.keywords & claim.keywords)[:6]
                if previous.polarity != "neutral" and claim.polarity != "neutral" and previous.polarity != claim.polarity:
                    result.contradictions.append(
                        {
                            "source_claim_id": claim.id,
                            "target_claim_id": previous.id,
                            "agents": [previous.agent_name, claim.agent_name],
                            "keywords": shared,
                            "summary": f"{claim.agent_name} challenges {previous.agent_name} on {', '.join(shared)}.",
                        }
                    )
                else:
                    result.convergences.append(
                        {
                            "source_claim_id": claim.id,
                            "target_claim_id": previous.id,
                            "agents": [previous.agent_name, claim.agent_name],
                            "keywords": shared,
                            "summary": f"{previous.agent_name} and {claim.agent_name} independently converge on {', '.join(shared)}.",
                        }
                    )

            self._claims.append(claim)
            result.claims.append(claim)

        return result


def _sentences(text: str) -> list[str]:
    candidates = re.split(r"(?<=[.!?])\s+", text.strip())
    return [sentence.strip() for sentence in candidates if 40 <= len(sentence.strip()) <= 360]


def _keywords(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z'-]{2,}", text.lower())
    return {word.strip("'") for word in words if word not in STOPWORDS}


def _polarity(text: str) -> str:
    words = _keywords(text)
    negative = len(words & NEGATIVE_WORDS)
    positive = len(words & POSITIVE_WORDS)
    if negative > positive:
        return "negative"
    if positive > negative:
        return "positive"
    return "neutral"


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)
