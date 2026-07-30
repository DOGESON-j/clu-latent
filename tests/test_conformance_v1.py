from typer.testing import CliRunner

from clu_latent.cli import app
from clu_latent.conformance import bundled_fixture_manifest, run_conformance

runner = CliRunner()


def test_manifest_covers_required_directory_and_archive_cases():
    entries = bundled_fixture_manifest()["fixtures"]
    names = {entry["name"] for entry in entries}
    assert {
        "valid_minimal",
        "valid_full",
        "valid_without_agent_read",
        "valid_unknown_future_track",
        "valid_portable_archive",
        "invalid_missing_manifest",
        "invalid_malformed_manifest_json",
        "invalid_unsafe_track_path",
        "invalid_missing_track_file",
        "invalid_malformed_jsonl",
        "invalid_package_id_mismatch",
        "invalid_stale_required_index",
        "invalid_broken_index_artifact_hash",
        "invalid_evidence_reference",
        "invalid_archive_unsafe_member",
        "invalid_archive_limits",
    } <= names


def test_directory_conformance_suite_passes_offline():
    result = run_conformance()
    assert result.passed
    assert len(result.fixtures) == 16


def test_conformance_cli_summary():
    result = runner.invoke(app, ["conformance", "run"])
    assert result.exit_code == 0, result.output
    assert "16/16 fixtures" in result.stdout
