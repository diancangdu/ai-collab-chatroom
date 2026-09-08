#!/usr/bin/env python3
"""Scan staged files for credentials, private endpoints, and machine paths."""

import os
import re
import subprocess
import sys
from pathlib import Path


PATTERNS = {
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.I),
    "openai key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "github token": re.compile(r"\b(?:ghp_|gho_|ghu_|ghs_|github_pat_)[A-Za-z0-9_]{20,}\b"),
    "slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "aws access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "password assignment": re.compile(r"\b(?:password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{6,}", re.I),
    "api key assignment": re.compile(r"\b(?:api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*['\"][^'\"]{8,}", re.I),
    "windows absolute path": re.compile(r"\b(?:[A-Za-z]:\\(?:Users|desktop|CodexHome|ZCode|OpenCode)[^\\s\"]*)", re.I),
    "machine user name": re.compile(os.environ.get("USERNAME") or r"$^"),
}

IGNORED_PARTS = {".git", "__pycache__", ".venv"}
IGNORED_FILES = {"package-lock.json", "yarn.lock", "poetry.lock"}


def staged_files():
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMRTUXB"],
        capture_output=True, text=True, check=True,
    )
    return [Path(line) for line in result.stdout.splitlines() if line.strip()]


def ignored(path):
    parts = {part.lower() for part in path.parts}
    return bool(parts & IGNORED_PARTS) or path.name in IGNORED_FILES


def main():
    files = [path for path in staged_files() if not ignored(path)]
    findings = []
    for path in files:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            for label, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append((path, line_number, label, line.strip()[:180]))

    if findings:
        for path, line_number, label, sample in findings:
            print(f"FAIL {path}:{line_number} {label}: {sample}")
        print(f"SUMMARY 0/{len(files)} files passed; {len(findings)} findings")
        return 1

    print(f"SUMMARY {len(files)}/{len(files)} staged files passed privacy audit")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL privacy audit setup: {exc}", file=sys.stderr)
        raise SystemExit(2)
