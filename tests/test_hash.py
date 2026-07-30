import hashlib
from dataclasses import replace

import pytest

from clu_latent.hash import read_sha256_sidecar, sha256_file, write_sha256_sidecar
from clu_latent.security.jsonl import JsonlLimitError
from clu_latent.security.limits import DEFAULT_LIMITS


def test_sha256_file_matches_hashlib(tmp_path):
    file_path = tmp_path / "data.bin"
    file_path.write_bytes(b"hello clulatent" * 1000)

    expected = hashlib.sha256(file_path.read_bytes()).hexdigest()
    assert sha256_file(file_path) == expected


def test_sha256_file_empty_file(tmp_path):
    file_path = tmp_path / "empty.bin"
    file_path.write_bytes(b"")
    assert sha256_file(file_path) == hashlib.sha256(b"").hexdigest()


def test_write_and_read_sha256_sidecar_roundtrip(tmp_path):
    digest = "a" * 64
    sidecar_path = tmp_path / "source.sha256"
    write_sha256_sidecar(sidecar_path, digest, "source.mp4")

    content = sidecar_path.read_text()
    assert content == f"{digest}  source.mp4\n"
    assert read_sha256_sidecar(sidecar_path) == digest


def test_different_content_produces_different_hash(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"content a")
    b.write_bytes(b"content b")
    assert sha256_file(a) != sha256_file(b)


def test_read_sha256_sidecar_rejects_oversized_sidecar(tmp_path):
    # sources/source.sha256 is package-controlled (untrusted) input; an
    # oversized sidecar must fail cleanly via the bounded reader instead
    # of being loaded fully into memory.
    sidecar_path = tmp_path / "source.sha256"
    tight_limits = replace(DEFAULT_LIMITS, max_sidecar_bytes=16)
    sidecar_path.write_text(("a" * 64) + "  source.mp4\n", encoding="utf-8")

    with pytest.raises(JsonlLimitError):
        read_sha256_sidecar(sidecar_path, limits=tight_limits)


def test_read_sha256_sidecar_accepts_normal_sidecar_within_default_limits(tmp_path):
    digest = "b" * 64
    sidecar_path = tmp_path / "source.sha256"
    write_sha256_sidecar(sidecar_path, digest, "source.mp4")

    assert read_sha256_sidecar(sidecar_path) == digest
