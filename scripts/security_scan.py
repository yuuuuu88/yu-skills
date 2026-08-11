#!/usr/bin/env python3
"""Fail-closed hygiene checks for files that may be published.

This deliberately complements, rather than replaces, GitHub secret scanning and
CodeQL.  It catches repository-specific leakage that a secret scanner usually
does not catch, especially home-directory paths and real-looking email
addresses.  Findings never print the matching value.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_TEXT_BYTES = 8 * 1024 * 1024

ALLOWED_EMAIL_DOMAINS = {
    "example.com",
    "example.net",
    "example.org",
    "example.invalid",
    "users.noreply.github.com",
}

GENERIC_HOME_NAMES = {
    "developer",
    "root",
    "runner",
    "runneradmin",
    "sandbox",
    "shared",
    "user",
    "vscode",
}

# Build the prefixes from fragments so this scanner does not match its own
# source code merely because it contains the patterns it is looking for.
_LOCAL_PREFIX = r"(?:/" + r"(?:home|Users)" + r"/|/mnt/c/Users/|file:///" + r"(?:home|Users)" + r"/)"
LOCAL_PATH_RE = re.compile(
    _LOCAL_PREFIX + r"(?P<user>[A-Za-z0-9._-]+)(?=$|[/\\])(?:[/\\][^\s\"'`<>]*)?"
    r"|(?i:[A-Z]:\\Users\\)(?P<windows_user>[A-Za-z0-9._-]+)(?=$|\\)(?:\\[^\s\"'`<>]*)?"
)

EMAIL_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9._%+\-])"
    r"(?P<local>[A-Za-z0-9._%+\-]+)@(?P<domain>[A-Za-z0-9.\-]+\.[A-Za-z]{2,})"
    r"(?![A-Za-z0-9._%+\-])"
)

PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z0-9 ]+ PRIVATE KEY-----")
SECRET_TOKEN_PATTERNS = (
    re.compile(r"(?i)\b(?:sk-(?:proj|svc|live|test)-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{30,})\b"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"),
    re.compile(r"\b(?:xox[baprs]-[A-Za-z0-9-]{20,}|whsec_[A-Za-z0-9]{20,})\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\bsb_(?:publishable|secret)_[A-Za-z0-9_-]{20,}\b"),
)

SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:OPENAI_API_KEY|SUPABASE_SERVICE_ROLE_KEY|"
    r"STRIPE_SECRET_KEY|STRIPE_WEBHOOK_SECRET|AWS_SECRET_ACCESS_KEY|"
    r"GITHUB_TOKEN)\b\s*[:=]\s*[\"']?"
    r"(?P<value>[^\s\"'`]{16,})"
)

PLACEHOLDER_RE = re.compile(
    r"(?i)^(?:your[-_].*|replace[-_]?me.*|change[-_]?me.*|"
    r"example.*|dummy.*|fixture.*|test.*|todo.*|none|null|"
    r"redacted.*|<[^>]+>|\$\{[^}]+\}|process\.env\..*)$"
)

PRIVATE_SUFFIXES = {".pem", ".p12", ".pfx", ".key", ".csr"}
PRIVATE_NAMES = {".env", ".env.local", ".env.production", "credentials.json"}


def _run_git(*args: str) -> bytes:
    return subprocess.check_output(("git", *args), cwd=ROOT)


def _candidate_files() -> list[Path]:
    try:
        raw = _run_git("ls-files", "-co", "--exclude-standard", "-z")
        paths = [ROOT / item for item in raw.decode().split("\0") if item]
    except (OSError, subprocess.CalledProcessError):
        paths = [
            path
            for path in ROOT.rglob("*")
            if path.is_file() and ".git" not in path.parts
        ]
    return sorted({path for path in paths if path.is_file()})


def _read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_TEXT_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _reachable_blobs() -> list[tuple[str, str, str]]:
    """Return (label, path, text) for reachable historical text blobs."""

    try:
        entries = _run_git("rev-list", "--objects", "--all").decode().splitlines()
    except (OSError, subprocess.CalledProcessError, UnicodeDecodeError):
        return []

    results: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for entry in entries:
        object_id, separator, object_path = entry.partition(" ")
        if not separator or object_id in seen:
            continue
        seen.add(object_id)
        try:
            if _run_git("cat-file", "-t", object_id).decode().strip() != "blob":
                continue
            size = int(_run_git("cat-file", "-s", object_id).decode().strip())
            if size > MAX_TEXT_BYTES:
                continue
            data = _run_git("cat-file", "blob", object_id)
            if b"\0" in data:
                continue
            text = data.decode("utf-8")
        except (OSError, subprocess.CalledProcessError, UnicodeDecodeError, ValueError):
            continue
        results.append((f"git:{object_id[:12]}", object_path, text))
    return results


def _is_generic_home_name(name: str) -> bool:
    return name.casefold() in GENERIC_HOME_NAMES


def _scan_text(label: str, path: str, text: str, findings: set[tuple[str, str, int]]) -> None:
    for line_number, line in enumerate(text.splitlines(), 1):
        for match in LOCAL_PATH_RE.finditer(line):
            username = match.group("user") or match.group("windows_user") or ""
            if not _is_generic_home_name(username):
                findings.add(("local path", f"{label}:{path}", line_number))

        for match in EMAIL_RE.finditer(line):
            domain = match.group("domain").casefold()
            if domain not in ALLOWED_EMAIL_DOMAINS:
                findings.add(("email address", f"{label}:{path}", line_number))

        if PRIVATE_KEY_RE.search(line):
            findings.add(("private-key header", f"{label}:{path}", line_number))

        if any(pattern.search(line) for pattern in SECRET_TOKEN_PATTERNS):
            findings.add(("secret-like token", f"{label}:{path}", line_number))

        assignment = SECRET_ASSIGNMENT_RE.search(line)
        if assignment and not PLACEHOLDER_RE.match(assignment.group("value")):
            findings.add(("secret assignment", f"{label}:{path}", line_number))


def _scan_file_names(paths: list[Path], findings: set[tuple[str, str, int]]) -> None:
    for path in paths:
        name = path.name.casefold()
        if name in PRIVATE_NAMES or path.suffix.casefold() in PRIVATE_SUFFIXES:
            findings.add(("private-material filename", "working-tree:" + str(path.relative_to(ROOT)), 0))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-history",
        action="store_true",
        help="scan only the current tracked/untracked publishable files",
    )
    args = parser.parse_args()

    findings: set[tuple[str, str, int]] = set()
    paths = _candidate_files()
    _scan_file_names(paths, findings)
    for path in paths:
        text = _read_text(path)
        if text is not None:
            _scan_text("working-tree", str(path.relative_to(ROOT)), text, findings)

    if not args.no_history:
        for label, path, text in _reachable_blobs():
            _scan_text(label, path, text, findings)

    if findings:
        print("Public-release security scan failed:", file=sys.stderr)
        for kind, location, line_number in sorted(findings):
            suffix = f":{line_number}" if line_number else ""
            print(f"- {kind}: {location}{suffix}", file=sys.stderr)
        print("Matching values are intentionally not printed.", file=sys.stderr)
        return 1

    history = "including reachable Git history" if not args.no_history else "current publishable files only"
    print(f"Public-release security scan passed ({history}; {len(paths)} files checked).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
