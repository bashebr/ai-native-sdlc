#!/usr/bin/env python3
"""GitHub issue intake for the agent org (product engineering agent).

Usage:
    python3 sync_issues.py pull [--config org/intake/config.json] [--dry-run]
    python3 sync_issues.py push --title TITLE --body BODY [--repo OWNER/REPO]
        [--labels a,b] [--dry-run]

pull:
    Fetch open issues for the configured repo(s), write
    org/intake/github/<repo>-<number>.md for new issues, and remember the
    latest seen issue number per repo in the state file. Idempotent:
    already-seen issues are skipped. Uses the gh CLI (must be authenticated).

push:
    Create a GitHub issue from a record (e.g. a consolidated intake record).

Exit 0 on success, 1 on failure. --dry-run prints what would happen without
writing anything or calling GitHub.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

DEFAULT_CONFIG = "org/intake/config.json"


def _gh(args: list[str]) -> str:
    try:
        res = subprocess.run(["gh", *args], capture_output=True, text=True, check=True)
    except FileNotFoundError:
        print(
            "error: gh CLI not found — install GitHub CLI and run `gh auth login`",
            file=sys.stderr,
        )
        raise
    except subprocess.CalledProcessError as exc:
        print(
            f"error: gh {' '.join(args)} failed: {exc.stderr.strip()}",
            file=sys.stderr,
        )
        raise
    return res.stdout


def _load_json(path: Path, default: object) -> dict:
    if not path.is_file():
        return default if isinstance(default, dict) else {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError as exc:
        print(f"error: {path} is not valid JSON: {exc}", file=sys.stderr)
        raise


def _safe_name(repo: str | None, number: int) -> str:
    prefix = repo.replace("/", "-") if repo else "current"
    return f"{prefix}-{number}"


def _pull(config_path: Path, dry_run: bool) -> int:
    config = _load_json(config_path, {})
    github = config.get("github", {})
    repos = github.get("repos") or []
    labels = github.get("labels") or []
    state_path = Path(github.get("state_file", "org/intake/.state.json"))
    output_dir = Path(github.get("output_dir", "org/intake/github"))
    state = _load_json(state_path, {})

    total_new = 0
    for repo in repos or [None]:
        key = repo or "current"
        args = [
            "issue",
            "list",
            "--state",
            "open",
            "--limit",
            "100",
            "--json",
            "number,title,body,labels,createdAt,author",
        ]
        if repo:
            args += ["--repo", repo]
        for label in labels:
            args += ["--label", label]

        if dry_run:
            print(f"  would run: gh {' '.join(args)}")
            continue

        stdout = _gh(args)
        try:
            issues = json.loads(stdout or "[]")
        except json.JSONDecodeError:
            print(f"error: gh returned invalid JSON for {key}", file=sys.stderr)
            return 1

        last_seen = int(state.get(key, 0))
        new_issues = [i for i in issues if int(i.get("number", 0)) > last_seen]
        new_issues.sort(key=lambda i: int(i["number"]))

        for issue in new_issues:
            number = int(issue["number"])
            author = (issue.get("author") or {}).get("login", "unknown")
            label_names = ", ".join(sorted(l.get("name", "") for l in issue.get("labels", [])))
            record = (
                "---\n"
                f"source: github\n"
                f"record_id: {key}-{number}\n"
                f"received_at: {issue.get('createdAt', '')}\n"
                f"author: {author}\n"
                "priority: normal\n"
                "---\n\n"
                "## Summary\n\n"
                f"{issue.get('title', '').strip()}\n\n"
                "## Details\n\n"
                f"{issue.get('body', '').strip()}\n\n"
                "## Labels\n\n"
                f"{label_names or '(none)'}\n\n"
                "## Status\n\nnew\n"
            )
            output_dir.mkdir(parents=True, exist_ok=True)
            record_path = output_dir / f"{_safe_name(repo, number)}.md"
            record_path.write_text(record, encoding="utf-8")
            print(f"  wrote {record_path}")
            total_new += 1

        max_seen = max([last_seen] + [int(i["number"]) for i in new_issues])
        state[key] = max_seen

    if not dry_run:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    print(f"pull: {total_new} new issue(s) recorded" + (" (dry run)" if dry_run else ""))
    return 0


def _push(title: str, body: str, repo: str | None, labels: list[str], dry_run: bool) -> int:
    args = ["issue", "create", "--title", title, "--body", body]
    if repo:
        args += ["--repo", repo]
    for label in labels:
        args += ["--label", label]

    if dry_run:
        print(f"  would run: gh {' '.join(args)}")
        print("push: dry run — no issue created")
        return 0

    url = _gh(args).strip()
    print(f"push: created {url}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    pull = sub.add_parser("pull", help="fetch open issues into org/intake/github/")
    pull.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help=f"JSON config file (default: {DEFAULT_CONFIG})",
    )
    pull.add_argument("--dry-run", action="store_true", help="print the plan without writing")

    push = sub.add_parser("push", help="create a GitHub issue from a record")
    push.add_argument("--title", required=True, help="issue title")
    push.add_argument("--body", required=True, help="issue body")
    push.add_argument("--repo", help="owner/repo (default: current repository)")
    push.add_argument("--labels", help="comma-separated labels")
    push.add_argument("--dry-run", action="store_true", help="print the plan without creating")

    args = parser.parse_args()

    try:
        if args.command == "pull":
            return _pull(Path(args.config), args.dry_run)
        labels = [l.strip() for l in (args.labels or "").split(",") if l.strip()]
        return _push(args.title, args.body, args.repo, labels, args.dry_run)
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
