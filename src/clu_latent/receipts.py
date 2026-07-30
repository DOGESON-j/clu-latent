"""receipts/ingest.jsonl: an append-only audit log of what ingest did.

Receipts are not track events (no shared envelope), but they are still
canonical, human-readable JSONL — one record per ingest operation.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .constants import TOOL_NAME, TOOL_VERSION


class ReceiptLog:
    """Collects receipt entries in memory; flushed to disk once via write().

    Kept in memory (rather than appended incrementally) so ingest can
    stay atomic: nothing is written to the real output path until the
    whole temp package, receipts included, is complete and gets renamed
    into place.
    """

    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []

    def add(
        self,
        *,
        operation: str,
        status: str,
        source_path: str | None = None,
        output_path: str | None = None,
        source_hash: str | None = None,
        files_created: list[str] | None = None,
        ffmpeg_command_success: bool | None = None,
        ffprobe_command_success: bool | None = None,
        errors: list[str] | None = None,
        warnings: list[str] | None = None,
        tool_path: str | None = None,
        timed_out: bool | None = None,
        stdout_truncated: bool | None = None,
        stderr_truncated: bool | None = None,
        stderr_tail: str | None = None,
        limits_snapshot: dict[str, Any] | None = None,
    ) -> None:
        """Record one ingest operation.

        The `tool_path`/`timed_out`/`*_truncated`/`stderr_tail` fields
        surface the security-relevant facts about an ffmpeg/ffprobe
        invocation (`security.subprocess.ToolResult`) so a failed or
        suspicious run leaves an audit trail — not just "it failed" but
        which resolved binary ran, whether it was killed for exceeding
        its timeout or output cap, and a bounded tail of its stderr.
        `limits_snapshot` records the `security.limits.Limits` in force
        for the run this entry belongs to. `warnings` records non-fatal
        problems the operation still succeeded despite — e.g. Phase
        1.7.1's out-of-duration transcription segments being clamped or
        skipped rather than aborting the whole ingest.
        """
        self._entries.append(
            {
                "id": str(uuid.uuid4()),
                "timestamp": datetime.now(UTC).isoformat(),
                "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
                "operation": operation,
                "status": status,
                "source_path": source_path,
                "output_path": output_path,
                "source_hash": source_hash,
                "files_created": files_created or [],
                "ffmpeg_command_success": ffmpeg_command_success,
                "ffprobe_command_success": ffprobe_command_success,
                "errors": errors or [],
                "warnings": warnings or [],
                "tool_path": tool_path,
                "timed_out": timed_out,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
                "stderr_tail": stderr_tail,
                "limits_snapshot": limits_snapshot,
            }
        )

    @property
    def entries(self) -> list[dict[str, Any]]:
        return list(self._entries)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for entry in self._entries:
                handle.write(json.dumps(entry) + "\n")
