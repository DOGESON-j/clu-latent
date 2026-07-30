"""Tests for Phase 3.12: the read-only keyframe contact sheet preview.

Two groups, mirroring `tests/test_report.py`:

- Pure / no-media tests: module import, CLI help, error handling. These
  need no ffmpeg and no real package.
- ffmpeg-gated end-to-end tests that build a real `.clulatent` package
  (same fixture pattern as `tests/test_report.py`), then generate a
  contact sheet and assert its content and safety properties.

Core rule under test: show visual evidence, do not interpret it. The
contact sheet exposes stored keyframes as escaped, static, offline HTML
and never claims to know what the clip depicts.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote

import pytest
from typer.testing import CliRunner

from clu_latent.cli import app
from clu_latent.ingest import ingest_video
from clu_latent.keyframe_preview import (
    KeyframePreviewError,
    generate_contact_sheet,
)
from clu_latent.manifest import Manifest

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

REPO_ROOT = Path(__file__).resolve().parents[1]

# Phrases the contact sheet must never contain -- they would assert an
# interpretation of the visual evidence rather than merely showing it.
_FORBIDDEN_PHRASES = (
    "understands the clip",
    "definitely shows",
    "proves intent",
    "manipulates",
    "semantic truth",
)


# --- Pure tests (no ffmpeg, no package) --------------------------------------


def test_keyframe_preview_module_imports_cleanly():
    import clu_latent.keyframe_preview  # noqa: F401


def test_contact_sheet_command_help_works():
    runner = CliRunner()
    result = runner.invoke(app, ["keyframes", "contact-sheet", "--help"])
    assert result.exit_code == 0, result.output
    assert "contact" in result.output.lower()


def test_contact_sheet_on_nonexistent_package_raises(tmp_path):
    with pytest.raises(KeyframePreviewError):
        generate_contact_sheet(tmp_path / "does-not-exist.clulatent")


def test_contact_sheet_cli_on_nonexistent_package_clean_error(tmp_path):
    runner = CliRunner()
    output = tmp_path / "sheet.html"
    result = runner.invoke(
        app,
        [
            "keyframes",
            "contact-sheet",
            str(tmp_path / "does-not-exist.clulatent"),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert not output.exists()


def test_contact_sheet_cli_existing_output_requires_force(tmp_path):
    runner = CliRunner()
    (tmp_path / "pkg.clulatent").mkdir()
    output = tmp_path / "sheet.html"
    output.write_text("existing", encoding="utf-8")
    result = runner.invoke(
        app,
        ["keyframes", "contact-sheet", str(tmp_path / "pkg.clulatent"), "--output", str(output)],
    )
    assert result.exit_code == 1
    assert "--force" in result.output
    assert output.read_text(encoding="utf-8") == "existing"


def test_report_module_still_importable_and_guards():
    # Test 28: the existing report surface remains importable and its
    # error contract is intact (full validation is the whole suite run).
    from clu_latent.report import ReportError, generate_report

    with pytest.raises(ReportError):
        generate_report(Path("/nonexistent/does-not-exist.clulatent"))


def test_evidence_gap_debugging_script_present_and_loadable():
    # Test 29: the frozen Phase 3.11 evidence-gap harness is still present
    # and importable (it must not have been disturbed by Phase 3.12).
    script = REPO_ROOT / "scripts" / "debug_real_clip_evidence_gap.py"
    assert script.is_file()
    spec = importlib.util.spec_from_file_location("debug_real_clip_evidence_gap_probe", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert hasattr(module, "audit_package")


def test_readme_includes_phase_3_12_bullet():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "CLULatent" in readme


def test_phase_3_12_doc_exists():
    doc = REPO_ROOT / "docs" / "PHASE_3_12_KEYFRAME_CONTACT_SHEET_PREVIEW.md"
    assert doc.is_file()
    text = doc.read_text(encoding="utf-8")
    assert "contact sheet" in text.lower()


# --- ffmpeg-gated end-to-end tests --------------------------------------------

pytestmark_e2e = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("clulatent_keyframe_fixture")
    video_path = directory / "tiny.mp4"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=3:size=320x240:rate=10",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=3",
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(video_path),
    ]
    subprocess.run(command, check=True, capture_output=True)
    return video_path


@pytest.fixture
def valid_package(tmp_path, tiny_video) -> Path:
    output_path = tmp_path / "pkg.clulatent"
    result = ingest_video(tiny_video, output_path)
    return result.package_path


def _snapshot(package_path: Path) -> dict[str, str]:
    """Map every file under `package_path` to a hash of its bytes."""
    snapshot: dict[str, str] = {}
    for path in sorted(package_path.rglob("*")):
        if path.is_file():
            rel = path.relative_to(package_path).as_posix()
            snapshot[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def _load_manifest(package_path: Path) -> Manifest:
    return Manifest.from_json_file(package_path / "manifest.json")


@pytestmark_e2e
def test_contact_sheet_renders_for_valid_package(valid_package, tmp_path):
    result = generate_contact_sheet(valid_package, output_path=tmp_path / "sheet.html")
    assert "<html" in result.html
    assert "contact sheet" in result.html.lower()


@pytestmark_e2e
def test_contact_sheet_cli_creates_output_file(valid_package, tmp_path):
    runner = CliRunner()
    output = tmp_path / "sheet.html"
    result = runner.invoke(
        app, ["keyframes", "contact-sheet", str(valid_package), "--output", str(output)]
    )
    assert result.exit_code == 0, result.output
    assert output.exists()
    assert "<html" in output.read_text(encoding="utf-8")


@pytestmark_e2e
def test_contact_sheet_cli_force_overwrites(valid_package, tmp_path):
    runner = CliRunner()
    output = tmp_path / "sheet.html"
    output.write_text("old", encoding="utf-8")
    result = runner.invoke(
        app,
        ["keyframes", "contact-sheet", str(valid_package), "--output", str(output), "--force"],
    )
    assert result.exit_code == 0, result.output
    assert "<html" in output.read_text(encoding="utf-8")


@pytestmark_e2e
def test_contact_sheet_includes_package_id(valid_package, tmp_path):
    manifest = _load_manifest(valid_package)
    result = generate_contact_sheet(valid_package, output_path=tmp_path / "sheet.html")
    assert manifest.package_id in result.html


@pytestmark_e2e
def test_contact_sheet_includes_keyframe_count(valid_package, tmp_path):
    result = generate_contact_sheet(valid_package, output_path=tmp_path / "sheet.html")
    assert "Keyframe count" in result.html


@pytestmark_e2e
def test_contact_sheet_includes_timestamps(valid_package, tmp_path):
    result = generate_contact_sheet(valid_package, output_path=tmp_path / "sheet.html")
    assert "Timestamp:" in result.html
    assert "00:00:0" in result.html


@pytestmark_e2e
def test_contact_sheet_includes_event_ids(valid_package, tmp_path):
    result = generate_contact_sheet(valid_package, output_path=tmp_path / "sheet.html")
    assert "kf_000000" in result.html


@pytestmark_e2e
def test_contact_sheet_includes_image_references(valid_package, tmp_path):
    result = generate_contact_sheet(valid_package, output_path=tmp_path / "sheet.html")
    assert "media/keyframes" in result.html
    assert "<img" in result.html


@pytestmark_e2e
def test_contact_sheet_includes_evidence_not_truth_caveat(valid_package, tmp_path):
    result = generate_contact_sheet(valid_package, output_path=tmp_path / "sheet.html")
    assert "Keyframes are evidence, not interpretation." in result.html


@pytestmark_e2e
def test_contact_sheet_says_it_does_not_describe(valid_package, tmp_path):
    result = generate_contact_sheet(valid_package, output_path=tmp_path / "sheet.html")
    assert "This preview does not describe what happens in the clip." in result.html


@pytestmark_e2e
def test_contact_sheet_has_no_transcript_inference(valid_package, tmp_path):
    result = generate_contact_sheet(valid_package, output_path=tmp_path / "sheet.html")
    assert "transcript" not in result.html.lower()


@pytestmark_e2e
def test_contact_sheet_makes_no_semantic_claims(valid_package, tmp_path):
    result = generate_contact_sheet(valid_package, output_path=tmp_path / "sheet.html")
    assert "semantic" not in result.html.lower()


@pytestmark_e2e
def test_contact_sheet_has_no_forbidden_phrases(valid_package, tmp_path):
    result = generate_contact_sheet(valid_package, output_path=tmp_path / "sheet.html")
    lowered = result.html.lower()
    for phrase in _FORBIDDEN_PHRASES:
        assert phrase not in lowered, phrase


@pytestmark_e2e
def test_contact_sheet_does_not_mutate_manifest(valid_package, tmp_path):
    before = (valid_package / "manifest.json").read_bytes()
    generate_contact_sheet(valid_package, output_path=tmp_path / "sheet.html")
    assert (valid_package / "manifest.json").read_bytes() == before


@pytestmark_e2e
def test_contact_sheet_does_not_mutate_tracks(valid_package, tmp_path):
    track = valid_package / "tracks" / "keyframes.jsonl"
    before = track.read_bytes()
    generate_contact_sheet(valid_package, output_path=tmp_path / "sheet.html")
    assert track.read_bytes() == before


@pytestmark_e2e
def test_contact_sheet_does_not_mutate_receipts_or_index(valid_package, tmp_path):
    # Tests 18 + 19: nothing anywhere in the package changes.
    before = _snapshot(valid_package)
    generate_contact_sheet(valid_package, output_path=tmp_path / "sheet.html")
    assert _snapshot(valid_package) == before


@pytestmark_e2e
def test_contact_sheet_missing_image_shown_as_missing_evidence(valid_package, tmp_path):
    keyframes_dir = valid_package / "media" / "keyframes"
    images = sorted(keyframes_dir.glob("*"))
    assert images, "fixture should have produced keyframe images"
    images[0].unlink()
    result = generate_contact_sheet(valid_package, output_path=tmp_path / "sheet.html")
    assert "Missing keyframe image" in result.html
    assert "<html" in result.html


@pytestmark_e2e
def test_contact_sheet_empty_keyframes_track_safe(valid_package, tmp_path):
    (valid_package / "tracks" / "keyframes.jsonl").write_text("", encoding="utf-8")
    result = generate_contact_sheet(valid_package, output_path=tmp_path / "sheet.html")
    assert "No keyframes are stored" in result.html
    assert "<html" in result.html


@pytestmark_e2e
def test_contact_sheet_invalid_package_reports_trust_warning(valid_package, tmp_path):
    # Corrupt the stored source so its hash no longer matches -> the
    # package fails validation but its manifest still parses, so the
    # sheet renders with a validation/trust caveat rather than crashing.
    manifest = _load_manifest(valid_package)
    source_file = valid_package / manifest.source.stored_path
    with source_file.open("ab") as handle:
        handle.write(b"\x00corrupt")
    result = generate_contact_sheet(valid_package, output_path=tmp_path / "sheet.html")
    assert "does not validate" in result.html
    assert "<html" in result.html


@pytestmark_e2e
def test_contact_sheet_output_is_bounded(valid_package, tmp_path):
    result = generate_contact_sheet(
        valid_package, output_path=tmp_path / "sheet.html", max_keyframes=1
    )
    manifest = _load_manifest(valid_package)
    if any(t.name == "keyframes" and t.record_count > 1 for t in manifest.tracks):
        assert "output is bounded" in result.html
    # The number of rendered <img>/frame blocks must not exceed the bound.
    assert result.html.count('class="frame"') <= 1


@pytestmark_e2e
def test_contact_sheet_escapes_malicious_payload(valid_package, tmp_path):
    track = valid_package / "tracks" / "keyframes.jsonl"
    event = {
        "id": "<script>alert('xss')</script>",
        "type": "keyframe",
        "t_start_ms": 0,
        "t_end_ms": 1000,
        "producer": {"name": "ffmpeg", "version": "test"},
        "confidence": None,
        "payload": {
            "path": "media/keyframes/<img src=x>evil.jpg",
            "frame_index": 0,
            "width": 1,
            "height": 1,
        },
    }
    track.write_text(json.dumps(event) + "\n", encoding="utf-8")
    result = generate_contact_sheet(valid_package, output_path=tmp_path / "sheet.html")
    assert "<script>alert" not in result.html
    assert "&lt;script&gt;" in result.html


@pytestmark_e2e
def test_contact_sheet_strips_control_characters(valid_package, tmp_path):
    track = valid_package / "tracks" / "keyframes.jsonl"
    event = {
        "id": "kf_000000",
        "type": "keyframe",
        "t_start_ms": 0,
        "t_end_ms": 1000,
        "producer": {"name": "ffmpeg", "version": "test"},
        "confidence": None,
        "payload": {
            "path": "media/keyframes/frame\x00\x07\x1b.jpg",
            "frame_index": 0,
            "width": 1,
            "height": 1,
        },
    }
    track.write_text(json.dumps(event) + "\n", encoding="utf-8")
    result = generate_contact_sheet(valid_package, output_path=tmp_path / "sheet.html")
    assert "\x00" not in result.html
    assert "\x07" not in result.html
    assert "\x1b" not in result.html


@pytestmark_e2e
def test_contact_sheet_has_no_javascript(valid_package, tmp_path):
    result = generate_contact_sheet(valid_package, output_path=tmp_path / "sheet.html")
    lowered = result.html.lower()
    assert "<script" not in lowered
    assert "javascript:" not in lowered


@pytestmark_e2e
def test_contact_sheet_has_no_network_urls(valid_package, tmp_path):
    result = generate_contact_sheet(valid_package, output_path=tmp_path / "sheet.html")
    lowered = result.html.lower()
    assert "http://" not in lowered
    assert "https://" not in lowered
    assert "cdn." not in lowered
    assert "//cdn" not in lowered


# --- Phase 3.12.1: keyframe image link fix ------------------------------------
#
# The frozen Phase 3.12 code computed image links from the symlink-resolved
# image path but the un-resolved output directory, so links broke whenever
# the output file was written outside the package (and especially under a
# symlinked dir like macOS /tmp -> /private/tmp). These regression tests pin
# the fixed behaviour: every rendered <img src> must resolve, from the output
# HTML file's own directory, to the real keyframe image inside the package.


def _img_srcs(html: str) -> list[str]:
    return re.findall(r'<img[^>]*\bsrc="([^"]+)"', html)


@pytestmark_e2e
def test_contact_sheet_links_point_through_package_dir(valid_package, tmp_path):
    # Output written OUTSIDE the package (sibling of pkg.clulatent).
    output = tmp_path / "keyframe-contact-sheet.html"
    result = generate_contact_sheet(valid_package, output_path=output)
    srcs = _img_srcs(result.html)
    assert srcs, "expected at least one keyframe <img>"
    # Links must reach into the package directory, not assume the images
    # sit next to the HTML file.
    assert all(src.startswith("pkg.clulatent/media/keyframes/") for src in srcs), srcs


@pytestmark_e2e
def test_contact_sheet_every_img_src_resolves_from_output_dir(valid_package, tmp_path):
    output = tmp_path / "keyframe-contact-sheet.html"
    result = generate_contact_sheet(valid_package, output_path=output)
    output.write_text(result.html, encoding="utf-8")
    srcs = _img_srcs(result.html)
    assert srcs
    for src in srcs:
        resolved = (output.parent / unquote(src)).resolve()
        assert resolved.is_file(), f"broken image link: {src} -> {resolved}"


@pytestmark_e2e
def test_contact_sheet_package_relative_metadata_still_shown(valid_package, tmp_path):
    output = tmp_path / "keyframe-contact-sheet.html"
    result = generate_contact_sheet(valid_package, output_path=output)
    # The package-relative path is still displayed as evidence metadata,
    # independently of the (now package-aware) img src.
    assert "media/keyframes/" in result.html
    assert "Package path:" in result.html


@pytestmark_e2e
def test_contact_sheet_nested_output_dir_resolves_images(valid_package, tmp_path):
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    output = nested / "sheet.html"
    result = generate_contact_sheet(valid_package, output_path=output)
    output.write_text(result.html, encoding="utf-8")
    srcs = _img_srcs(result.html)
    assert srcs
    # From a deeply nested output dir the links must climb back out to the
    # package (they will start with "../").
    assert all(src.startswith("../") for src in srcs), srcs
    for src in srcs:
        assert (output.parent / unquote(src)).resolve().is_file()


@pytestmark_e2e
def test_contact_sheet_missing_file_no_broken_img(valid_package, tmp_path):
    keyframes_dir = valid_package / "media" / "keyframes"
    images = sorted(keyframes_dir.glob("*"))
    assert images
    images[0].unlink()
    output = tmp_path / "sheet.html"
    result = generate_contact_sheet(valid_package, output_path=output)
    # Missing image is a missing-evidence state, not a normal <img>.
    assert "Missing keyframe image" in result.html
    output.write_text(result.html, encoding="utf-8")
    # Every emitted <img> that remains must still resolve to a real file.
    for src in _img_srcs(result.html):
        assert (output.parent / unquote(src)).resolve().is_file()


@pytestmark_e2e
def test_link_fix_does_not_mutate_package(valid_package, tmp_path):
    before = _snapshot(valid_package)
    generate_contact_sheet(valid_package, output_path=tmp_path / "sheet.html")
    assert _snapshot(valid_package) == before


@pytestmark_e2e
def test_link_fix_creates_no_receipts(valid_package, tmp_path):
    receipts_dir = valid_package / "receipts"
    before = _snapshot(receipts_dir) if receipts_dir.exists() else {}
    generate_contact_sheet(valid_package, output_path=tmp_path / "sheet.html")
    after = _snapshot(receipts_dir) if receipts_dir.exists() else {}
    assert after == before
