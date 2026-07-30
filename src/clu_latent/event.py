"""The shared event envelope used by every CLULatent track file.

Every record in every tracks/*.jsonl file (keyframes, audio_events,
speech_events, semantic_events, and any future perception track) is one
line of JSON matching this envelope. New perception types in later
phases should only ever add a new `type` + `payload` shape, never a new
top-level container schema.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .constants import EVENT_ENVELOPE_SCHEMA_ID, EVENT_ENVELOPE_SCHEMA_VERSION


class Producer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str


class EventEnvelope(BaseModel):
    """Canonical per-record schema for every track file.

    schema_id/schema_version identify this envelope shape for tooling
    (e.g. a future Rust compiler) that wants to validate records without
    hardcoding the shape.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    type: str
    t_start_ms: int = Field(ge=0)
    t_end_ms: int = Field(ge=0)
    producer: Producer
    confidence: float | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_time_range(self) -> "EventEnvelope":
        if self.t_end_ms < self.t_start_ms:
            raise ValueError(
                f"t_end_ms ({self.t_end_ms}) must be >= t_start_ms ({self.t_start_ms})"
            )
        return self

    @model_validator(mode="after")
    def _check_confidence_range(self) -> "EventEnvelope":
        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be within [0.0, 1.0], got {self.confidence}")
        return self


SCHEMA_ID = EVENT_ENVELOPE_SCHEMA_ID
SCHEMA_VERSION = EVENT_ENVELOPE_SCHEMA_VERSION
