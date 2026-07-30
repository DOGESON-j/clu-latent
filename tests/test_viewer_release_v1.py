import hashlib
from pathlib import Path

from typer.testing import CliRunner

from clu_latent.archive import pack_package
from clu_latent.cli import app
from clu_latent.conformance_fixtures import build_fixture
from clu_latent.v1_package_index import write_v1_package_index
from clu_latent.v1_playback_viewer import (
    build_playback_viewer_payload,
    render_playback_viewer_html,
)

runner = CliRunner()


def _package(tmp_path: Path) -> Path:
    package = build_fixture("valid_keyframes_audio", tmp_path / "viewer.clulatent")
    write_v1_package_index(package)
    return package


def test_release_viewer_has_v1_safety_status_and_accessibility(tmp_path):
    package = _package(tmp_path)
    payload = build_playback_viewer_payload(
        package, output_path=tmp_path / "viewer.html", max_events=2
    )
    html = render_playback_viewer_html(payload)
    assert "CLULatent V1 evidence playback" in html
    assert (
        "This viewer displays package facts and timestamped evidence. "
        "It does not certify scene meaning."
    ) in html
    assert payload["profile_v1"]["status"] == "COMPATIBLE"
    assert payload["index_v1"]["verification_status"] == "FRESH"
    assert "button:focus-visible" in html
    assert 'aria-label", ev.track' in html
    assert 'data-copy=' in html
    assert "Copy unavailable" in html
    assert html.count("<ul class=\"caveats\">") == 1


def test_open_archive_keeps_external_material_and_never_mutates_archive(tmp_path):
    package = _package(tmp_path)
    archive = tmp_path / "portable.clulatent"
    pack_package(package, archive)
    before = hashlib.sha256(archive.read_bytes()).hexdigest()
    output = tmp_path / "open-output"
    result = runner.invoke(
        app,
        [
            "open",
            str(archive),
            "--output-dir",
            str(output),
            "--no-browser",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (output / "viewer.html").is_file()
    assert (output / "archive-package" / "manifest.json").is_file()
    assert (output / ".archive-source-sha256").read_text().strip() == before
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == before
