from __future__ import annotations

import stat
from pathlib import Path

import pytest

from clu_latent.security.limits import Limits
from clu_latent.security.subprocess import (
    ToolNotFoundError,
    resolve_tool,
    reset_tool_cache,
    run_tool,
)


def _make_fake_tool(tmp_path: Path, name: str, script: str) -> Path:
    path = tmp_path / name
    path.write_text(f"#!/bin/sh\n{script}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def test_run_tool_success_exit_zero(tmp_path):
    tool = _make_fake_tool(tmp_path, "fake_ok", 'echo "hello"; exit 0')
    result = run_tool(str(tool), [], timeout_s=5.0)
    assert result.success is True
    assert result.returncode == 0
    assert "hello" in result.stdout
    assert result.timed_out is False


def test_run_tool_nonzero_exit(tmp_path):
    tool = _make_fake_tool(tmp_path, "fake_fail", 'echo "boom" 1>&2; exit 3')
    result = run_tool(str(tool), [], timeout_s=5.0)
    assert result.success is False
    assert result.returncode == 3
    assert "boom" in result.stderr_tail


def test_run_tool_kills_hanging_process_on_timeout(tmp_path):
    tool = _make_fake_tool(tmp_path, "fake_hang", "sleep 30; exit 0")
    result = run_tool(str(tool), [], timeout_s=0.3)
    assert result.timed_out is True
    assert result.success is False


def test_run_tool_bounds_spammy_stderr(tmp_path):
    tool = _make_fake_tool(
        tmp_path,
        "fake_spam",
        'while true; do echo "spamspamspamspamspamspamspamspamspamspam" 1>&2; done',
    )
    tight_limits = Limits(max_stderr_capture_bytes=4096, stderr_tail_bytes=1024)
    result = run_tool(str(tool), [], timeout_s=10.0, limits=tight_limits)
    assert result.stderr_truncated is True
    assert len(result.stderr_tail.encode("utf-8")) <= tight_limits.stderr_tail_bytes
    # Must not have waited out the full 10s timeout to notice the spam.
    assert result.timed_out is False


def test_resolve_tool_missing_raises_clean_error(monkeypatch):
    reset_tool_cache()
    monkeypatch.setenv("PATH", "/definitely/not/a/real/path")
    with pytest.raises(ToolNotFoundError):
        resolve_tool("ffmpeg_does_not_exist_anywhere")
    reset_tool_cache()


def test_resolve_tool_finds_and_caches(tmp_path, monkeypatch):
    reset_tool_cache()
    tool = _make_fake_tool(tmp_path, "myffmpeg", "exit 0")
    monkeypatch.setenv("PATH", str(tmp_path))
    resolved = resolve_tool("myffmpeg")
    assert resolved == str(tool.resolve())
    reset_tool_cache()
