#!/usr/bin/env python3
"""Deterministic secret/credential scanner (build-phase guardrail).

Scans the working tree, a staged diff, or a PR diff for high-signal secrets
before they reach a commit or a pull request. This is the "keep credentials
out of the diff" hook the playbook describes, shipped as code instead of
prose. Deterministic and network-free: stdlib only, no model, no patterns
file fetched from the network.

Usage:
    scan_secrets.py --path <file-or-dir> [--path ...]
    scan_secrets.py --staged
    scan_secrets.py --base <rev> --head <rev>
        [--repo <dir>] [--config .secretsignore] [--entropy] [--json] [--quiet]

Behavior:
  - High-signal provider prefixes (AWS, GitHub, Slack, Stripe, Google,
    private-key blocks) plus quoted secret assignments.
  - Output is always redacted: the matched value is never printed.
  - A line containing NOSECRET, gitleaks:allow, or "pragma: allowlist secret"
    is skipped (inline suppression).
  - Paths listed in the config file (fnmatch globs, one per line, # comments)
    are skipped; the default config is .secretsignore when it exists.
  - --entropy adds a Shannon-entropy rule for long random-looking tokens
    (off by default to keep the false-positive rate low).

Exit codes: 0 = clean, 1 = findings, 2 = usage/IO error.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import math
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_CONFIG = ".secretsignore"
MAX_FILE_BYTES = 2_000_000
SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "__pycache__",
    ".next",
    ".mypy_cache",
    ".pytest_cache",
    ".terraform",
}
SUPPRESS_MARKERS = ("nosecret", "gitleaks:allow", "pragma: allowlist secret")

# Values that look like documentation placeholders, not real secrets.
PLACEHOLDER_RE = re.compile(
    r"(?i)(example|sample|placeholder|changeme|change_me|redacted|dummy|fake|"
    r"your[_-]|xxx+|todo|not[_-]?a[_-]?real|test[_-]?only)"
)

# (name, compiled regex). The last pattern captures the quoted value so
# placeholder filtering can be applied to it.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b")),
    ("aws-secret-access-key", re.compile(r"(?i)aws[^\n]{0,20}['\"][0-9a-zA-Z/+]{40}['\"]")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("github-fine-grained-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("stripe-live-key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("private-key-block", re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")),
    (
        "quoted-secret-assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|apikey|secret|client[_-]?secret|access[_-]?key|"
            r"auth[_-]?token|token|passwd|password|passphrase|private[_-]?key)\b"
            r"\s*[:=]\s*['\"]([^'\"\s]{12,})['\"]"
        ),
    ),
)

ENTROPY_RE = re.compile(r"[A-Za-z0-9+/=_\-]{32,}")


def _shannon(text: str) -> float:
    if not text:
        return 0.0
    counts = {ch: text.count(ch) for ch in set(text)}
    return -sum((n / len(text)) * math.log2(n / len(text)) for n in counts.values())


def _looks_random(token: str) -> bool:
    return (
        len(token) >= 32
        and _shannon(token) >= 3.5
        and any(c.islower() for c in token)
        and any(c.isupper() for c in token)
        and any(c.isdigit() for c in token)
    )


def _suppressed(line: str) -> bool:
    lowered = line.lower()
    return any(marker in lowered for marker in SUPPRESS_MARKERS)


def scan_line(line: str, entropy: bool = False) -> list[str]:
    """Return the pattern names that fired on one line (value never returned)."""
    if _suppressed(line):
        return []
    hits: list[str] = []
    for name, regex in PATTERNS:
        for match in regex.finditer(line):
            value = match.group(1) if match.lastindex else match.group(0)
            if PLACEHOLDER_RE.search(value):
                continue
            hits.append(name)
            break
    if entropy:
        for token in ENTROPY_RE.findall(line):
            if _looks_random(token):
                hits.append("high-entropy-token")
                break
    return hits


def load_ignore(patterns: list[str]) -> list[str]:
    globs: list[str] = []
    for raw in patterns:
        line = raw.strip()
        if line and not line.startswith("#"):
            globs.append(line)
    return globs


def _ignored(path: Path, globs: list[str]) -> bool:
    text = path.as_posix()
    return any(fnmatch.fnmatch(text, g) or fnmatch.fnmatch(path.name, g) for g in globs)


def _iter_files(paths: list[str], globs: list[str]):
    for raw in paths:
        p = Path(raw)
        if p.is_file():
            if not _ignored(p, globs):
                yield p
            continue
        if not p.is_dir():
            raise FileNotFoundError(f"path not found: {raw}")
        for child in sorted(p.rglob("*")):
            if not child.is_file():
                continue
            if any(part in SKIP_DIRS for part in child.parts):
                continue
            if _ignored(child, globs):
                continue
            yield child


def _read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) > MAX_FILE_BYTES or b"\x00" in data[:4096]:
        return None
    return data.decode("utf-8", errors="replace")


def scan_file(path: Path, entropy: bool) -> list[dict]:
    text = _read_text(path)
    if text is None:
        return []
    findings: list[dict] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for name in scan_line(line, entropy):
            findings.append({"path": path.as_posix(), "line": lineno, "pattern": name})
    return findings


def _git(repo: Path, args: list[str]) -> tuple[bool, str]:
    try:
        res = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return False, str(exc)
    if res.returncode != 0:
        return False, (res.stderr or res.stdout).strip()
    return True, res.stdout


def _parse_added(diff_text: str) -> list[tuple[str, int, str]]:
    """Extract (path, line-number, text) for added lines from `git diff -U0`."""
    out: list[tuple[str, int, str]] = []
    path: str | None = None
    lineno = 0
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            if target == "/dev/null":
                path = None
            elif target.startswith("b/"):
                path = target[2:]
            else:
                path = target
        elif line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            lineno = int(match.group(1)) if match else 0
        elif line.startswith("+") and path is not None:
            out.append((path, lineno, line[1:]))
            lineno += 1
    return out


def scan_diff(repo: Path, diff_args: list[str], globs: list[str], entropy: bool) -> list[dict]:
    ok, out = _git(repo, diff_args)
    if not ok:
        raise RuntimeError(f"git diff failed: {out}")
    findings: list[dict] = []
    for path, lineno, text in _parse_added(out):
        if _ignored(Path(path), globs):
            continue
        for name in scan_line(text, entropy):
            findings.append({"path": path, "line": lineno, "pattern": name})
    return findings


def _load_config(args: argparse.Namespace) -> list[str]:
    if args.config:
        path = Path(args.config)
        if not path.is_file():
            raise FileNotFoundError(f"config file not found: {path}")
        return load_ignore(path.read_text(encoding="utf-8").splitlines())
    default = Path(DEFAULT_CONFIG)
    if default.is_file():
        return load_ignore(default.read_text(encoding="utf-8").splitlines())
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", action="append", default=[], help="file or directory to scan (repeatable)")
    parser.add_argument("--staged", action="store_true", help="scan added lines in the git index")
    parser.add_argument("--base", default=None, help="base revision for a PR diff")
    parser.add_argument("--head", default=None, help="head revision for a PR diff")
    parser.add_argument("--repo", default=".", help="repository/worktree root (default: current dir)")
    parser.add_argument("--config", default=None, help="allowlist file (default: .secretsignore if present)")
    parser.add_argument("--entropy", action="store_true", help="also flag long high-entropy tokens")
    parser.add_argument("--json", action="store_true", help="emit findings as JSON")
    parser.add_argument("--quiet", action="store_true", help="print only the summary")
    args = parser.parse_args(argv)

    diff_mode = args.staged or args.base is not None or args.head is not None
    if args.base is not None or args.head is not None:
        if not (args.base and args.head):
            parser.error("--base and --head must be used together")
    if args.path and diff_mode:
        parser.error("choose one of --path, --staged, or --base/--head")
    if not args.path and not diff_mode:
        parser.error("pass --path, --staged, or --base/--head")

    try:
        globs = _load_config(args)
        repo = Path(args.repo)
        if args.staged:
            findings = scan_diff(repo, ["diff", "--cached", "-U0", "--no-color"], globs, args.entropy)
        elif args.base is not None:
            findings = scan_diff(
                repo,
                ["diff", "-U0", "--no-color", f"{args.base}...{args.head}"],
                globs,
                args.entropy,
            )
        else:
            findings = []
            for path in _iter_files(args.path, globs):
                findings.extend(scan_file(path, args.entropy))
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(findings, indent=2))
    else:
        if not args.quiet:
            for finding in findings:
                print(f"{finding['path']}:{finding['line']}  {finding['pattern']}  (value redacted)")
        print(f"scan_secrets: {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
