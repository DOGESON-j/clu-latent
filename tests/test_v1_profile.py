"""Phase 3.27: the CLULatent V1 compatibility profile / format contract.

`clulatent profile inspect v1` describes the contract itself;
`clulatent profile verify PACKAGE` checks whether a package satisfies it.
Both are strictly read-only. A COMPATIBLE result certifies structural
package/profile compatibility only, never semantic truth about media
content.

Fixtures reuse the shared conformance builder (`tests/fixtures/conformance
/build.py`) already used by `test_v1_package_index.py` and
`test_v1_package_index_verify.py` — no new fixture infrastructure.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import time
from pathlib import Path

from typer.testing import CliRunner

from clu_latent.agent_context import FORBIDDEN_CONTEXT_PHRASES
from clu_latent.cli import app
from clu_latent.v1_package_index import INDEX_V1_DIR, write_v1_package_index
from clu_latent.v1_profile import (
    PROFILE_CONTRACT_VERSION,
    PROFILE_ID,
    V1ProfileError,
    get_v1_profile_contract,
    render_v1_profile_contract,
    render_v1_profile_verification,
    verify_v1_profile,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFORMANCE_DIR = REPO_ROOT / "tests" / "fixtures" / "conformance"

runner = CliRunner()


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "clulatent_conformance_build_profile", CONFORMANCE_DIR / "build.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


build = _load_builder()


def _fixture(tmp_path: Path, name: str) -> Path:
    return build.build_fixture(name, tmp_path / "p.clulatent")


def _snapshot(root: Path) -> dict[str, str]:
    """Content-hash snapshot: sha256 per file, so mtime-only changes don't hide a mutation."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _check(result, key: str):
    return next(c for c in result.checks if c.key == key)


def _cap(result, key: str):
    return next(c for c in result.capabilities if c.key == key)


# --- 1-2. contract identity -------------------------------------------------


def test_contract_can_be_loaded():
    contract = get_v1_profile_contract()
    assert contract.requirements
    assert contract.capabilities


def test_contract_has_stable_identifier_and_version():
    contract = get_v1_profile_contract()
    assert contract.profile_id == PROFILE_ID == "clulatent.profile.v1"
    assert contract.contract_version == PROFILE_CONTRACT_VERSION
    assert isinstance(contract.contract_version, str) and contract.contract_version


# --- 3-4. required + optional items explicitly represented ------------------


def test_required_requirements_explicitly_represented():
    contract = get_v1_profile_contract()
    required_keys = {r.key for r in contract.requirements if r.category == "REQUIRED"}
    assert required_keys == {
        "manifest",
        "package_identity",
        "format_version",
        "reader_compatibility",
        "safe_paths",
        "validator_compatibility",
        "package_structure",
        "v1_package_index",
    }
    conditional_keys = {r.key for r in contract.requirements if r.category == "CONDITIONAL"}
    assert conditional_keys == {"declared_tracks", "agent_read_index"}


def test_optional_capabilities_explicitly_represented():
    contract = get_v1_profile_contract()
    capability_keys = {c.key for c in contract.capabilities}
    assert capability_keys == {
        "keyframes",
        "audio_events",
        "speech_events",
        "semantic_events",
        "visual_change_candidates",
        "changed_region_candidates",
        "evidence_bundles",
        "agent_review_events",
        "agent_read_index",
    }


# --- 5-6. known-good packages are COMPATIBLE ---------------------------------


def test_full_v1_package_is_compatible(tmp_path):
    pkg = _fixture(tmp_path, "valid_keyframes_audio")
    write_v1_package_index(pkg)
    result = verify_v1_profile(pkg)
    assert result.compatibility == "COMPATIBLE"
    assert _check(result, "validator_compatibility").status == "PASS"
    assert _check(result, "v1_package_index").status == "PASS"


def test_minimal_baseline_package_is_compatible(tmp_path):
    pkg = _fixture(tmp_path, "valid_minimal")
    write_v1_package_index(pkg)
    result = verify_v1_profile(pkg)
    assert result.compatibility == "COMPATIBLE"
    assert _check(result, "declared_tracks").status == "NOT_APPLICABLE"


# --- 7-8. missing / malformed manifest ---------------------------------------


def test_missing_manifest_is_incompatible(tmp_path):
    pkg = _fixture(tmp_path, "invalid_missing_manifest")
    result = verify_v1_profile(pkg)
    assert result.compatibility == "INCOMPATIBLE"
    assert _check(result, "manifest").status == "MISSING"


def test_malformed_manifest_is_incompatible(tmp_path):
    pkg = _fixture(tmp_path, "invalid_malformed_manifest_json")
    result = verify_v1_profile(pkg)
    assert result.compatibility == "INCOMPATIBLE"
    assert _check(result, "manifest").status == "FAIL"


# --- 9. unsafe declared path --------------------------------------------------


def test_unsafe_track_path_is_incompatible(tmp_path):
    pkg = _fixture(tmp_path, "invalid_unsafe_track_path")
    result = verify_v1_profile(pkg)
    assert result.compatibility == "INCOMPATIBLE"
    assert _check(result, "safe_paths").status == "FAIL"


# --- 10. declared but missing/broken required track --------------------------


def test_missing_track_file_is_incompatible(tmp_path):
    pkg = _fixture(tmp_path, "invalid_missing_track_file")
    result = verify_v1_profile(pkg)
    assert result.compatibility == "INCOMPATIBLE"
    assert _check(result, "validator_compatibility").status == "FAIL"


# --- 11-12. missing optional lanes/index never auto-invalidate ---------------


def test_missing_optional_evidence_lane_does_not_invalidate(tmp_path):
    pkg = _fixture(tmp_path, "valid_minimal")
    write_v1_package_index(pkg)
    result = verify_v1_profile(pkg)
    assert result.compatibility == "COMPATIBLE"
    assert _cap(result, "evidence_bundles").status == "MISSING"


def test_missing_optional_agent_read_index_does_not_invalidate(tmp_path):
    pkg = _fixture(tmp_path, "valid_keyframes_audio")
    write_v1_package_index(pkg)
    # deliberately never write the Phase 3.24 agent-read index
    result = verify_v1_profile(pkg)
    assert result.compatibility == "COMPATIBLE"
    assert _check(result, "agent_read_index").status == "NOT_APPLICABLE"
    assert _cap(result, "agent_read_index").status == "MISSING"


# --- 13. invalid/stale required V1 package index -----------------------------


def test_invalid_required_package_index_is_incompatible(tmp_path):
    pkg = _fixture(tmp_path, "valid_keyframes_audio")
    write_v1_package_index(pkg)
    manifest_path = pkg / INDEX_V1_DIR / "index_manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["package_id"] = "not-the-real-package-id"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    result = verify_v1_profile(pkg)
    assert result.compatibility == "INCOMPATIBLE"
    assert _check(result, "v1_package_index").status == "FAIL"


def test_stale_required_package_index_is_incompatible(tmp_path):
    pkg = _fixture(tmp_path, "valid_keyframes_audio")
    write_v1_package_index(pkg)
    manifest_path = pkg / INDEX_V1_DIR / "index_manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["package"]["track_counts"] = {"keyframes": 999}
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    result = verify_v1_profile(pkg)
    assert result.compatibility == "INCOMPATIBLE"
    assert _check(result, "v1_package_index").status == "FAIL"


# --- 14-15. optional capability presence/absence reported --------------------


def test_optional_capability_presence_reported(tmp_path):
    pkg = _fixture(tmp_path, "valid_keyframes_audio")
    write_v1_package_index(pkg)
    result = verify_v1_profile(pkg)
    assert _cap(result, "keyframes").status == "AVAILABLE"
    assert _cap(result, "audio_events").status == "AVAILABLE"


def test_optional_capability_absence_reported(tmp_path):
    pkg = _fixture(tmp_path, "valid_minimal")
    write_v1_package_index(pkg)
    result = verify_v1_profile(pkg)
    assert _cap(result, "keyframes").status == "MISSING"
    assert _cap(result, "speech_events").status == "MISSING"


# --- 16. unknown state remains UNKNOWN, not PASS ------------------------------


def test_unknown_state_stays_unknown(tmp_path, monkeypatch):
    import clu_latent.v1_profile as v1_profile_mod

    pkg = _fixture(tmp_path, "valid_minimal")
    write_v1_package_index(pkg)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated validator crash")

    monkeypatch.setattr(v1_profile_mod, "validate_package", _boom, raising=False)
    # validate_package is imported lazily inside verify_v1_profile, so patch
    # the source module it is imported from instead.
    import clu_latent.validate as validate_mod

    monkeypatch.setattr(validate_mod, "validate_package", _boom)
    result = verify_v1_profile(pkg)
    assert _check(result, "validator_compatibility").status == "UNKNOWN"
    assert result.compatibility == "UNKNOWN"


# --- 17. read-only guarantees --------------------------------------------------


def test_verify_does_not_mutate_content_hashes(tmp_path):
    pkg = _fixture(tmp_path, "valid_keyframes_audio")
    write_v1_package_index(pkg)
    before = _snapshot(pkg)
    verify_v1_profile(pkg)
    assert _snapshot(pkg) == before


def test_verify_creates_no_receipts(tmp_path):
    pkg = _fixture(tmp_path, "valid_keyframes_audio")
    write_v1_package_index(pkg)
    receipts_before = sorted((pkg / "receipts").glob("*.jsonl"))
    verify_v1_profile(pkg)
    assert sorted((pkg / "receipts").glob("*.jsonl")) == receipts_before


def test_verify_does_not_change_lock_state(tmp_path):
    pkg = _fixture(tmp_path, "valid_keyframes_audio")
    write_v1_package_index(pkg)
    lock_dir_before = sorted((pkg / "lock").glob("*")) if (pkg / "lock").is_dir() else []
    verify_v1_profile(pkg)
    lock_dir_after = sorted((pkg / "lock").glob("*")) if (pkg / "lock").is_dir() else []
    assert lock_dir_before == lock_dir_after


# --- 18. package not found raises, everything else is reported ---------------


def test_missing_package_raises(tmp_path):
    import pytest

    with pytest.raises(V1ProfileError):
        verify_v1_profile(tmp_path / "does_not_exist.clulatent")


# --- 19-21. CLI wiring ---------------------------------------------------------


def test_cli_profile_inspect_v1():
    result = runner.invoke(app, ["profile", "inspect", "v1"])
    assert result.exit_code == 0, result.output
    assert "Required contract" in result.stdout
    assert "clulatent.profile.v1" in result.stdout


def test_cli_profile_verify_compatible_package(tmp_path):
    pkg = _fixture(tmp_path, "valid_keyframes_audio")
    write_v1_package_index(pkg)
    result = runner.invoke(app, ["profile", "verify", str(pkg)])
    assert result.exit_code == 0, result.output
    assert "COMPATIBLE" in result.stdout


def test_cli_profile_verify_incompatible_package(tmp_path):
    pkg = _fixture(tmp_path, "invalid_missing_manifest")
    result = runner.invoke(app, ["profile", "verify", str(pkg)])
    assert result.exit_code == 2, result.output
    assert "INCOMPATIBLE" in result.stdout


def test_cli_help_registration():
    top = runner.invoke(app, ["--help"])
    assert top.exit_code == 0
    assert "profile" in top.stdout

    profile_help = runner.invoke(app, ["profile", "--help"])
    assert profile_help.exit_code == 0
    assert "inspect" in profile_help.stdout
    assert "verify" in profile_help.stdout

    inspect_help = runner.invoke(app, ["profile", "inspect", "--help"])
    assert inspect_help.exit_code == 0

    verify_help = runner.invoke(app, ["profile", "verify", "--help"])
    assert verify_help.exit_code == 0


# --- 22. verify does not mutate an unsafe/broken package either --------------


def test_cli_profile_verify_does_not_mutate(tmp_path):
    pkg = _fixture(tmp_path, "valid_keyframes_audio")
    write_v1_package_index(pkg)
    before = _snapshot(pkg)
    runner.invoke(app, ["profile", "verify", str(pkg)])
    assert _snapshot(pkg) == before


# --- 23. import stays fast ----------------------------------------------------


def test_cli_help_is_fast():
    start = time.perf_counter()
    result = runner.invoke(app, ["--help"])
    elapsed = time.perf_counter() - start
    assert result.exit_code == 0
    assert elapsed < 5.0


# --- 24-25. no semantic-overclaim language ------------------------------------


def test_contract_render_has_no_forbidden_language():
    contract = get_v1_profile_contract()
    text = render_v1_profile_contract(contract)
    lowered = text.lower().replace(
        "clulatent v1 compatibility certifies package/profile compatibility, "
        "not semantic truth about media content.",
        "",
    )
    # "package identity" is a required structural term, and the directive
    # mandates the exact negated "semantic truth" disclaimer above.
    for phrase in FORBIDDEN_CONTEXT_PHRASES:
        if phrase in {"identity", "truth"}:
            continue
        assert phrase not in lowered, f"profile contract render leaked {phrase!r}"


def test_verification_render_smoke(tmp_path):
    pkg = _fixture(tmp_path, "valid_keyframes_audio")
    write_v1_package_index(pkg)
    result = verify_v1_profile(pkg)
    text = render_v1_profile_verification(result)
    assert "Compatibility: COMPATIBLE" in text
    assert "Optional capabilities:" in text
