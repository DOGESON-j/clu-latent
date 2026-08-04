#!/usr/bin/env python3
"""Fail when the public repository contains common private-data hazards.

This is intentionally dependency-free and conservative. It scans only files
tracked by Git, skips binary/large files, and reports paths plus rule names
without echoing any suspected secret value.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_TEXT_BYTES = 2_000_000

FORBIDDEN_BASENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "secrets.json",
    "id_rsa",
    "id_ed25519",
}

FORBIDDEN_SUFFIXES = {
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".mobileprovision",
}

CONTENT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GitHub token", re.compile(r"\b(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,})\b")),
    ("OpenAI-style secret", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
    ("Jay local home path", re.compile(r"/Users/(?:jaydenkambule|jayden)(?:/|\b)", re.IGNORECASE)),
    ("Jay Mac hostname", re.compile(r"Jaydens-Mac-mini", re.IGNORECASE)),
    (
        "private GRAFT repository reference",
        re.compile(
            r"DOGESON-j/(?:THE-GRID|project-clu|CLU-Rig|house-of-hidden-saints|PrintMaxxing)\b",
            re.IGNORECASE,
        ),
    ),
)

WORKFLOW_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("self-hosted GitHub runner", re.compile(r"runs-on\s*:\s*(?:\[.*\bself-hosted\b.*\]|self-hosted)", re.IGNORECASE)),
    ("pull_request_target trigger", re.compile(r"\bpull_request_target\s*:", re.IGNORECASE)),
    ("write-all workflow permission", re.compile(r"permissions\s*:\s*write-all", re.IGNORECASE)),
)


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) > MAX_TEXT_BYTES or b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def main() -> int:
    findings: list[tuple[str, str]] = []

    for path in tracked_files():
        rel = path.relative_to(ROOT).as_posix()
        lower_name = path.name.lower()
        lower_suffix = path.suffix.lower()

        if lower_name in FORBIDDEN_BASENAMES:
            findings.append((rel, "forbidden sensitive filename"))
        if lower_suffix in FORBIDDEN_SUFFIXES:
            findings.append((rel, f"forbidden sensitive file type ({lower_suffix})"))

        text = read_text(path)
        if text is None:
            continue

        for rule_name, pattern in CONTENT_RULES:
            if pattern.search(text):
                findings.append((rel, rule_name))

        if rel.startswith(".github/workflows/"):
            for rule_name, pattern in WORKFLOW_RULES:
                if pattern.search(text):
                    findings.append((rel, rule_name))

    if findings:
        print("Public release guard failed. Potential exposure found:", file=sys.stderr)
        for rel, rule in sorted(set(findings)):
            print(f"- {rel}: {rule}", file=sys.stderr)
        print(
            "Review the file, remove the sensitive material, rotate any exposed credential, "
            "and rewrite Git history when necessary.",
            file=sys.stderr,
        )
        return 1

    print("Public release guard passed: no configured exposure patterns found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
