import pytest

from clu_latent.security.paths import (
    PathSecurityError,
    resolve_in_package,
    validate_relative_posix,
)

BAD_LEXICAL = [
    "../secret",
    "..\\secret",
    "/etc/passwd",
    "C:\\Users\\x",
    "C:/Users/x",
    "//server/share",
    "tracks/../secret",
    "tracks//bad",
    "./tracks/x.jsonl",
    "",
    ".",
    "..",
]


@pytest.mark.parametrize("value", BAD_LEXICAL)
def test_validate_relative_posix_rejects_bad_values(value):
    with pytest.raises(PathSecurityError):
        validate_relative_posix(value)


@pytest.mark.parametrize(
    "value",
    ["tracks/transcript.jsonl", "media/source.mp4", "manifest.json", "a/b/c.txt"],
)
def test_validate_relative_posix_accepts_good_values(value):
    assert validate_relative_posix(value) == value


def test_resolve_in_package_accepts_valid_existing_file(tmp_path):
    package_root = tmp_path / "pkg"
    (package_root / "tracks").mkdir(parents=True)
    target = package_root / "tracks" / "transcript.jsonl"
    target.write_text("{}\n")

    resolved = resolve_in_package(package_root, "tracks/transcript.jsonl")
    assert resolved == target.resolve()


def test_resolve_in_package_missing_file_returns_path_not_raises(tmp_path):
    package_root = tmp_path / "pkg"
    (package_root / "tracks").mkdir(parents=True)

    resolved = resolve_in_package(package_root, "tracks/missing.jsonl")
    assert resolved.name == "missing.jsonl"
    assert not resolved.exists()


@pytest.mark.parametrize("value", BAD_LEXICAL)
def test_resolve_in_package_rejects_bad_lexical_values(tmp_path, value):
    package_root = tmp_path / "pkg"
    package_root.mkdir()
    with pytest.raises(PathSecurityError):
        resolve_in_package(package_root, value)


def test_resolve_in_package_rejects_symlink_read(tmp_path):
    package_root = tmp_path / "pkg"
    (package_root / "tracks").mkdir(parents=True)
    outside_target = tmp_path / "outside_secret.txt"
    outside_target.write_text("secret")
    symlink_path = package_root / "tracks" / "evil.jsonl"
    symlink_path.symlink_to(outside_target)

    with pytest.raises(PathSecurityError, match="symlink"):
        resolve_in_package(package_root, "tracks/evil.jsonl")


def test_resolve_in_package_rejects_symlinked_directory_escape(tmp_path):
    package_root = tmp_path / "pkg"
    package_root.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.jsonl").write_text("{}\n")

    # "tracks" itself is a symlink pointing outside the package root.
    (package_root / "tracks").symlink_to(outside_dir)

    with pytest.raises(PathSecurityError):
        resolve_in_package(package_root, "tracks/secret.jsonl")


def test_resolve_in_package_for_write_requires_existing_parent(tmp_path):
    package_root = tmp_path / "pkg"
    package_root.mkdir()

    with pytest.raises(PathSecurityError):
        resolve_in_package(package_root, "index/search.sqlite", for_write=True)


def test_resolve_in_package_for_write_refuses_symlink_target(tmp_path):
    package_root = tmp_path / "pkg"
    (package_root / "index").mkdir(parents=True)
    outside_target = tmp_path / "outside.sqlite"
    outside_target.write_text("x")
    (package_root / "index" / "search.sqlite").symlink_to(outside_target)

    with pytest.raises(PathSecurityError, match="symlink"):
        resolve_in_package(package_root, "index/search.sqlite", for_write=True)


def test_resolve_in_package_for_write_accepts_valid_new_file(tmp_path):
    package_root = tmp_path / "pkg"
    (package_root / "index").mkdir(parents=True)

    resolved = resolve_in_package(package_root, "index/search.sqlite", for_write=True)
    assert resolved.parent == (package_root / "index").resolve()
