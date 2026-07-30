#!/usr/bin/env python3
"""Release gate: prove public read operations leave package/archive bytes unchanged."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from clu_latent.archive import pack_package
from clu_latent.conformance_fixtures import build_fixture
from clu_latent.v1_package_index import write_v1_package_index


def snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def run(args: list[str], *, env: dict[str, str]) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "clu_latent.cli", *args],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=15,
        check=False,
    )
    if result.returncode:
        raise SystemExit(f"{' '.join(args)} failed ({result.returncode}):\n{result.stdout}")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")
    with tempfile.TemporaryDirectory(prefix="clulatent-readonly-gate-") as temp:
        work = Path(temp)
        package = build_fixture("valid_keyframes_audio", work / "package.clulatent")
        write_v1_package_index(package)
        archive = work / "portable.clulatent"
        pack_package(package, archive)
        package_before = snapshot(package)
        archive_before = hashlib.sha256(archive.read_bytes()).hexdigest()
        commands = [
            ["validate", str(package)],
            ["profile", "verify", str(package)],
            ["package-index", "verify", str(package)],
            ["agent-read", "summary", str(package)],
            ["agent-read", "window", str(package), "--time", "0s"],
            ["archive", "verify", str(archive)],
            [
                "open",
                str(package),
                "--output-dir",
                str(work / "open"),
                "--no-browser",
            ],
        ]
        for command in commands:
            run(command, env=env)
            if snapshot(package) != package_before:
                raise SystemExit(f"package mutated by: {' '.join(command)}")
            if hashlib.sha256(archive.read_bytes()).hexdigest() != archive_before:
                raise SystemExit(f"archive mutated by: {' '.join(command)}")
    print(f"PASS read-only hash gate: {len(commands)} commands")


if __name__ == "__main__":
    main()
