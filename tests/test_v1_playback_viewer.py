"""Phase 3.23: the V1 evidence playback viewer (`clu_latent.v1_playback_viewer`).

These tests pin the Phase 3.23 deliverable built on the Phase 3.18 reader
and the Phase 3.22 built-in index: a `.clulatent` package renders to a
single, static, self-contained HTML viewer that plays the package's
source media (when a playable file is present) alongside a timeline of
its timestamped evidence tracks. The generator is read-only (it mutates
nothing inside the package, writes no receipt/index/lock), degrades to an
evidence-only view when no media is playable, references package media
only via safe relative links, never emits a forbidden semantic phrase in
its authored prose, and embeds its JSON payload safely.

Fixtures are built by the conformance fixture builder plus the real
evidence-bundle / agent-review writers -- a genuine package with real
keyframe/audio tracks, an evidence bundle, and a review; no
FFmpeg/Pillow/model, no big real video.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clu_latent import v1_playback_viewer as viewer_mod
from clu_latent import v1_package_index as v1_package_index_mod
from clu_latent.agent_context import FORBIDDEN_CONTEXT_PHRASES
from clu_latent.cli import app
from clu_latent.constants import TOOL_NAME, TOOL_VERSION
from clu_latent.evidence_bundle_writer import build_evidence_bundle
from clu_latent.agent_review_writer import run_agent_review
from clu_latent.v1_playback_viewer import (
    DEFAULT_MAX_EVENTS,
    VIEWER_SCHEMA_ID,
    PlaybackViewerError,
    build_playback_viewer,
    build_playback_viewer_payload,
    render_playback_viewer_html,
    write_playback_viewer,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFORMANCE_DIR = REPO_ROOT / "tests" / "fixtures" / "conformance"

_BUNDLE_ID = "eb_000000000000_000000005000"

runner = CliRunner()


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "clulatent_conformance_build_viewer", CONFORMANCE_DIR / "build.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


build = _load_builder()


def _built_package(tmp_path: Path, *, with_evidence: bool = True) -> Path:
    pkg = build.build_fixture("valid_keyframes_audio", tmp_path / "p.clulatent")
    if with_evidence:
        build_evidence_bundle(
            pkg, start_ms=0, end_ms=5000, tool_name=TOOL_NAME, tool_version=TOOL_VERSION
        )
        run_agent_review(pkg, _BUNDLE_ID, tool_name=TOOL_NAME, tool_version=TOOL_VERSION)
    return pkg


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            st = path.stat()
            out[str(path.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return out


# --- 1. payload shape --------------------------------------------------------


def test_payload_has_expected_top_level_shape(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    payload = build_playback_viewer_payload(pkg, output_path=tmp_path / "viewer.html")

    assert payload["schema_id"] == VIEWER_SCHEMA_ID
    assert payload["generated_by"] == {"tool": TOOL_NAME, "version": TOOL_VERSION}
    for key in (
        "package",
        "validation",
        "lock",
        "tracks",
        "media",
        "timeline",
        "keyframes",
        "evidence",
        "index_v1",
        "caveats",
    ):
        assert key in payload, key
    assert payload["package"]["duration_ms"] == 5000


# --- 2. media detection ------------------------------------------------------


def test_media_playable_when_source_present(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    payload = build_playback_viewer_payload(pkg, output_path=tmp_path / "viewer.html")
    media = payload["media"]
    assert media["playable"] is True
    assert media["href"] is not None
    # A relative link, never an absolute path or a network URL.
    assert not media["href"].startswith("/")
    assert "://" not in media["href"]
    assert "sources/source.bin" in media["href"]


def test_media_absent_degrades_gracefully(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    (pkg / "sources" / "source.bin").unlink()
    payload = build_playback_viewer_payload(pkg, output_path=tmp_path / "viewer.html")
    assert payload["media"]["playable"] is False
    html = render_playback_viewer_html(payload)
    assert "not directly playable" in html
    # The timeline still renders even with no media.
    assert 'id="timeline"' in html


# --- 3. keyframe strip -------------------------------------------------------


def test_keyframe_strip_has_relative_hrefs(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    payload = build_playback_viewer_payload(pkg, output_path=tmp_path / "viewer.html")
    kf = payload["keyframes"]
    assert kf["total"] == 2
    assert kf["shown"] == 2
    for entry in kf["events"]:
        assert entry["exists"] is True
        assert entry["href"] is not None
        assert "media/keyframes/" in entry["href"]


def test_missing_keyframe_image_is_marked_not_linked(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    # Remove one keyframe image on disk; its record stays in the track.
    images = sorted((pkg / "media" / "keyframes").glob("*.jpg"))
    images[0].unlink()
    payload = build_playback_viewer_payload(pkg, output_path=tmp_path / "viewer.html")
    missing = [e for e in payload["keyframes"]["events"] if not e["exists"]]
    assert len(missing) == 1
    assert missing[0]["href"] is None


# --- 4. timeline -------------------------------------------------------------


def test_timeline_merges_and_sorts_events(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    payload = build_playback_viewer_payload(pkg, output_path=tmp_path / "viewer.html")
    events = payload["timeline"]["events"]
    tracks = {e["track"] for e in events}
    assert "keyframes" in tracks
    assert "audio_events" in tracks
    starts = [e["t_start_ms"] for e in events]
    assert starts == sorted(starts)


def test_timeline_truncates_at_max_events(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    payload = build_playback_viewer_payload(
        pkg, output_path=tmp_path / "viewer.html", max_events=1
    )
    tl = payload["timeline"]
    assert tl["shown"] == 1
    assert tl["truncated"] is True
    assert tl["total"] > 1


# --- 5. evidence bundles / reviews ------------------------------------------


def test_evidence_section_lists_bundles_and_reviews(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path, with_evidence=True)
    payload = build_playback_viewer_payload(pkg, output_path=tmp_path / "viewer.html")
    ev = payload["evidence"]
    assert any(b["id"] == _BUNDLE_ID for b in ev["evidence_bundles"])
    assert len(ev["agent_reviews"]) == 1


# --- 6. built-in V1 index links ---------------------------------------------


def test_index_absent_by_default(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    payload = build_playback_viewer_payload(pkg, output_path=tmp_path / "viewer.html")
    assert payload["index_v1"]["present"] is False


def test_index_links_when_present(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    v1_package_index_mod.write_v1_package_index(pkg)
    payload = build_playback_viewer_payload(pkg, output_path=tmp_path / "viewer.html")
    idx = payload["index_v1"]
    assert idx["present"] is True
    assert idx["artifacts"]
    names = {a["name"] for a in idx["artifacts"]}
    assert "agent_context.json" in names
    for art in idx["artifacts"]:
        if art["path"]:
            assert "index/v1/" in art["href"]


# --- 7. HTML render surface --------------------------------------------------


def test_html_is_self_contained_and_has_expected_sections(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    payload = build_playback_viewer_payload(pkg, output_path=tmp_path / "viewer.html")
    html = render_playback_viewer_html(payload)

    assert html.startswith("<!DOCTYPE html>")
    assert "<video" in html
    assert 'id="timeline"' in html
    assert "MP4 plays media" in html
    assert 'id="clulatent-viewer-data"' in html
    # No external references / network assets.
    assert "http://" not in html
    assert "https://" not in html
    assert "cdn" not in html.lower()
    assert "<link" not in html


def test_html_authored_prose_has_no_forbidden_phrase(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    payload = build_playback_viewer_payload(pkg, output_path=tmp_path / "viewer.html")
    html = render_playback_viewer_html(payload)
    lowered = html.lower()
    for phrase in FORBIDDEN_CONTEXT_PHRASES:
        assert phrase not in lowered, phrase


# --- 8. authored-language guard ---------------------------------------------


def test_guard_rejects_forbidden_authored_text() -> None:
    with pytest.raises(PlaybackViewerError):
        viewer_mod._assert_safe_authored("the video shows a person moving")


def test_opaque_scary_filename_is_not_censored(tmp_path: Path) -> None:
    # A source filename that contains a scary word is opaque user data and
    # must be preserved verbatim, never scanned or rewritten.
    pkg = _built_package(tmp_path)
    manifest_path = pkg / "manifest.json"
    text = manifest_path.read_text(encoding="utf-8")
    text = text.replace('"source.bin"', '"the_video_shows_intent.bin"')
    text = text.replace('sources/source.bin"', 'sources/the_video_shows_intent.bin"')
    manifest_path.write_text(text, encoding="utf-8")
    (pkg / "sources" / "source.bin").rename(
        pkg / "sources" / "the_video_shows_intent.bin"
    )
    payload = build_playback_viewer_payload(pkg, output_path=tmp_path / "viewer.html")
    assert payload["package"]["source_filename"] == "the_video_shows_intent.bin"
    # Rendering must not raise even though the opaque data contains a phrase.
    html = render_playback_viewer_html(payload)
    assert "the_video_shows_intent.bin" in html


# --- 9. safe JSON embedding --------------------------------------------------


def test_embed_json_neutralises_angle_brackets() -> None:
    blob = viewer_mod._embed_json({"x": "</script><b>hi</b>"})
    assert "</script>" not in blob
    assert "<b>" not in blob
    assert "\\u003c" in blob


# --- 10. read-only + writer --------------------------------------------------


def test_build_does_not_mutate_package(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    before = _snapshot(pkg)
    build_playback_viewer(pkg, output_path=tmp_path / "viewer.html")
    assert _snapshot(pkg) == before


def test_write_creates_file_and_leaves_package_untouched(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    before = _snapshot(pkg)
    out = tmp_path / "viewer.html"
    result = write_playback_viewer(pkg, out)
    assert out.exists()
    assert out.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")
    assert result.output_path == out
    assert _snapshot(pkg) == before


def test_write_refuses_existing_without_force(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    out = tmp_path / "viewer.html"
    out.write_text("existing", encoding="utf-8")
    with pytest.raises(PlaybackViewerError):
        write_playback_viewer(pkg, out)
    # force overwrites.
    write_playback_viewer(pkg, out, force=True)
    assert out.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_write_refuses_missing_parent(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    with pytest.raises(PlaybackViewerError):
        write_playback_viewer(pkg, tmp_path / "nope" / "viewer.html")


def test_unreadable_package_raises(tmp_path: Path) -> None:
    with pytest.raises(PlaybackViewerError):
        build_playback_viewer_payload(tmp_path / "does_not_exist.clulatent")


# --- 11. CLI -----------------------------------------------------------------


def test_cli_playback_viewer_writes_file(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    out = tmp_path / "viewer.html"
    result = runner.invoke(app, ["playback-viewer", str(pkg), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert "<video" in html
    assert 'id="timeline"' in html


def test_cli_refuses_existing_without_force(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    out = tmp_path / "viewer.html"
    out.write_text("existing", encoding="utf-8")
    result = runner.invoke(app, ["playback-viewer", str(pkg), "-o", str(out)])
    assert result.exit_code == 1
    result2 = runner.invoke(app, ["playback-viewer", str(pkg), "-o", str(out), "--force"])
    assert result2.exit_code == 0, result2.output


def test_cli_max_events_option(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    out = tmp_path / "viewer.html"
    result = runner.invoke(
        app, ["playback-viewer", str(pkg), "-o", str(out), "--max-events", "1"]
    )
    assert result.exit_code == 0, result.output
    assert DEFAULT_MAX_EVENTS >= 1
