"""manifest.json schema for a .clulatent package.

manifest.json (together with the tracks/*.jsonl files) is canonical.
index/search.sqlite is derived and must never be treated as a source of
truth — see IndexInfo.canonical below, which is always False.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .constants import CLULATENT_VERSION, TOOL_NAME, TOOL_VERSION
from .security.jsonl import read_bytes_bounded
from .security.limits import DEFAULT_LIMITS, Limits


class ToolInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str


class SourceInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    stored_path: str
    sha256: str
    duration_ms: int = Field(ge=0)
    container_format: str
    width: int = Field(ge=0)
    height: int = Field(ge=0)
    fps: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    bitrate: int | None = None
    has_audio: bool
    storage_mode: Literal["embedded"] = "embedded"
    phase_1_single_source: Literal[True] = True


class TimebaseInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit: Literal["ms"] = "ms"
    type: Literal["integer"] = "integer"


class TrackDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    file: str
    schema_id: str
    schema_version: str
    record_count: int = Field(ge=0)
    sorted_by: str


class MediaKeyframesInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dir: str
    method: str
    interval_ms: int = Field(ge=0)
    count: int = Field(ge=0)


class MediaInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyframes: MediaKeyframesInfo


class IndexInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    status: Literal["derived"] = "derived"
    canonical: Literal[False] = False


class ReceiptsInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clulatent_version: str = CLULATENT_VERSION
    package_id: str
    created_at: str
    status: Literal["complete", "partial", "failed"]
    tool: ToolInfo = ToolInfo(name=TOOL_NAME, version=TOOL_VERSION)
    source: SourceInfo
    timebase: TimebaseInfo = TimebaseInfo()
    tracks: list[TrackDescriptor]
    media: MediaInfo
    index: IndexInfo
    receipts: ReceiptsInfo

    def to_json_file(self, path: Path) -> None:
        Path(path).write_text(
            json.dumps(self.model_dump(mode="json"), indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def from_json_file(cls, path: Path, *, limits: Limits = DEFAULT_LIMITS) -> "Manifest":
        """Load and validate manifest.json.

        manifest.json is untrusted input: it is read via
        `read_bytes_bounded` (max_manifest_bytes) rather than an
        unbounded `read_text`, so an oversized manifest.json fails
        cleanly instead of being loaded fully into memory.
        """
        raw = read_bytes_bounded(path, max_bytes=limits.max_manifest_bytes, field_name="manifest.json")
        data = json.loads(raw.decode("utf-8"))
        return cls.model_validate(data)
