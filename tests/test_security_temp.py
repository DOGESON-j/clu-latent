from __future__ import annotations

import pytest

from clu_latent.security.temp import PackageFinalizeError, commit_package, staged_package_dir


def test_staged_package_dir_created_next_to_output(tmp_path):
    output_path = tmp_path / "pkg.clulatent"
    with staged_package_dir(output_path) as temp_dir:
        assert temp_dir.exists()
        assert temp_dir.parent == output_path.parent
        assert temp_dir.name.startswith(f".{output_path.name}.tmp-")
    # Cleaned up automatically since commit_package was never called.
    assert not temp_dir.exists()


def test_successful_atomic_commit(tmp_path):
    output_path = tmp_path / "pkg.clulatent"
    with staged_package_dir(output_path) as temp_dir:
        (temp_dir / "manifest.json").write_text("{}")
        commit_package(temp_dir, output_path)

    assert output_path.exists()
    assert (output_path / "manifest.json").exists()
    assert not temp_dir.exists()


def test_temp_dir_cleaned_up_on_failure_before_commit(tmp_path):
    output_path = tmp_path / "pkg.clulatent"
    captured_temp_dir = None
    with pytest.raises(RuntimeError):
        with staged_package_dir(output_path) as temp_dir:
            captured_temp_dir = temp_dir
            (temp_dir / "partial.txt").write_text("oops")
            raise RuntimeError("simulated ingest failure")

    assert not output_path.exists()
    assert not captured_temp_dir.exists()


def test_commit_refuses_existing_output_without_force(tmp_path):
    output_path = tmp_path / "pkg.clulatent"
    output_path.mkdir()
    (output_path / "existing.txt").write_text("keep me")

    with staged_package_dir(output_path) as temp_dir:
        (temp_dir / "manifest.json").write_text("{}")
        with pytest.raises(PackageFinalizeError):
            commit_package(temp_dir, output_path, force=False)

    # Original output must be untouched.
    assert (output_path / "existing.txt").read_text() == "keep me"


def test_commit_with_force_overwrites_existing_output_directory(tmp_path):
    output_path = tmp_path / "pkg.clulatent"
    output_path.mkdir()
    (output_path / "old.txt").write_text("old")

    with staged_package_dir(output_path) as temp_dir:
        (temp_dir / "manifest.json").write_text("{}")
        commit_package(temp_dir, output_path, force=True)

    assert (output_path / "manifest.json").exists()
    assert not (output_path / "old.txt").exists()


def test_commit_refuses_existing_output_symlink_even_with_force(tmp_path):
    real_target = tmp_path / "real_target"
    real_target.mkdir()
    output_path = tmp_path / "pkg.clulatent"
    output_path.symlink_to(real_target)

    with staged_package_dir(output_path) as temp_dir:
        (temp_dir / "manifest.json").write_text("{}")
        with pytest.raises(PackageFinalizeError, match="symlink"):
            commit_package(temp_dir, output_path, force=True)

    # The symlink and its target must be untouched.
    assert output_path.is_symlink()
    assert real_target.exists()
