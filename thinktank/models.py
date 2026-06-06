from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


ProviderId = Literal["openai", "gemini"]


class ProviderValidationRequest(BaseModel):
    provider: ProviderId
    model: str = Field(..., min_length=1, max_length=120)


class FeatureFlags(BaseModel):
    cold_start_trials: bool = True
    contradiction_map: bool = True


class CouncilAgent(BaseModel):
    id: str = Field(..., min_length=1, max_length=80)
    name: str = Field(..., min_length=1, max_length=40)
    role: str = Field(..., min_length=1, max_length=80)
    personality: str = Field(..., min_length=1, max_length=500)
    provider: ProviderId
    model: str = Field(..., min_length=1, max_length=120)

    @field_validator("id", "name", "role", "personality", "model")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class DebateRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=800)
    rounds: int = Field(default=6, ge=1, le=10)
    agents: list[CouncilAgent] = Field(..., min_length=2, max_length=5)
    provider_keys: dict[ProviderId, str] = Field(default_factory=dict)
    features: FeatureFlags = Field(default_factory=FeatureFlags)

    @field_validator("topic")
    @classmethod
    def strip_topic(cls, value: str) -> str:
        return value.strip()

    @field_validator("provider_keys")
    @classmethod
    def validate_provider_key_lengths(cls, value: dict[ProviderId, str]) -> dict[ProviderId, str]:
        for provider, key in value.items():
            if len(key.strip()) > 4096:
                raise ValueError(f"API key for {provider} is too long.")
        return value

    @model_validator(mode="after")
    def validate_agent_uniqueness_and_keys(self):
        ids = [agent.id.lower() for agent in self.agents]
        names = [agent.name.lower() for agent in self.agents]
        if len(ids) != len(set(ids)):
            raise ValueError("Agent ids must be unique.")
        if len(names) != len(set(names)):
            raise ValueError("Agent names must be unique.")

        self.provider_keys = {
            provider: key.strip() for provider, key in self.provider_keys.items() if key.strip()
        }
        return self


class JudgeAIRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=4000)
    candidate_a: str = Field(..., min_length=1, max_length=12000)
    candidate_b: str = Field(..., min_length=1, max_length=12000)
    model: str = Field(default="gpt-4o-mini", min_length=1, max_length=120)

    @field_validator("prompt", "candidate_a", "candidate_b", "model")
    @classmethod
    def strip_judge_text(cls, value: str) -> str:
        return value.strip()


class TranscriptEntry(BaseModel):
    round: int
    agent_id: str
    agent_name: str
    role: str
    provider: ProviderId
    model: str
    content: str
