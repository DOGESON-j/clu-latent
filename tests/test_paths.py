import pytest

from clu_latent.paths import normalize_posix, to_posix_relative


def test_to_posix_relative_basic(tmp_path):
    base = tmp_path
    nested = base / "media" / "keyframes" / "000000.jpg"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"x")

    assert to_posix_relative(nested, base) == "media/keyframes/000000.jpg"


def test_normalize_posix_rejects_backslashes():
    # Hardened Phase 1.6 behavior: backslashes are rejected outright
    # rather than silently rewritten to forward slashes — a package's
    # on-disk path string must already be clean POSIX form.
    with pytest.raises(ValueError):
        normalize_posix("media\\keyframes\\000000.jpg")


def test_normalize_posix_accepts_clean_posix_path():
    assert normalize_posix("media/keyframes/000000.jpg") == "media/keyframes/000000.jpg"


def test_normalize_posix_rejects_absolute_path():
    with pytest.raises(ValueError):
        normalize_posix("/etc/passwd")


def test_normalize_posix_rejects_parent_traversal():
    with pytest.raises(ValueError):
        normalize_posix("../../etc/passwd")


def test_normalize_posix_rejects_empty():
    with pytest.raises(ValueError):
        normalize_posix("")


def test_normalize_posix_rejects_windows_drive_path():
    with pytest.raises(ValueError):
        normalize_posix("C:\\Users\\x")


def test_normalize_posix_rejects_unc_path():
    with pytest.raises(ValueError):
        normalize_posix("//server/share")


def test_normalize_posix_rejects_repeated_slashes():
    with pytest.raises(ValueError):
        normalize_posix("tracks//bad")


def test_normalize_posix_rejects_dot_segment():
    with pytest.raises(ValueError):
        normalize_posix("./tracks/x.jsonl")
