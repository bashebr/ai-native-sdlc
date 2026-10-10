#!/usr/bin/env python3
"""Deterministic plan-sync enforcement (F2).

Backs the "Plan mode first" hard rule: implementation changes cannot pass a PR
or pre-commit check without an approved plan.md whose "Files that change"
manifest covers the files, and departures from the plan must be declared in
plan.md in the same change set.

Usage:
    check_plan_sync.py --base <rev> --head <rev>
                       [--graph workflow-graph.yaml]
                       [--ledger gates/ledger.jsonl]
                       [--ignore <glob>]... [--two-dot]
    check_plan_sync.py --hook [--graph workflow-graph.yaml]
                              [--ledger gates/ledger.jsonl]
                              [--ignore <glob>]...

PR mode defaults to a three-dot diff (merge-base). Hook mode validates the
staged diff (HEAD vs index) and reads plan.md from the index. Ledger chain
verification is delegated to gate_ledger.py.

Exit codes: 0 = pass, 1 = violation, 2 = usage error.
"""

from __future__ import annotations

import argparse
import difflib
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import gate_ledger as gl  # noqa: E402

DEFAULT_PLAN_GATE = "engineer_approve"

# Paths that are process artifacts, not implementation. The checker never
# requires a plan for these; adopters may add more via --ignore.
PROCESS_ENTRIES = (
    "intent/",
    "spec.md",
    "plan.md",
    "REVIEW.md",
    "workflow-graph.yaml",
    "gates/",
    "org/",
    "evals/",
    "bands.yaml",
    "hooks/",
    "docs/",
    ".github/",
    "CHANGELOG.md",
    "README.md",
    "LICENSE",
    "SECURITY.md",
)

STATUS_RE = re.compile(r"(?im)^\s*[-*]\s*Status\s*:\s*Approved\s*$")
BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")
PREFIX_RE = re.compile(r"^\s*(?:new|modified|deleted|renamed)\s*[\):]?\s*$", re.I)


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


def _is_process_path(path: str, ignores: list[str]) -> bool:
    if any(fnmatch.fnmatch(path, pattern) for pattern in ignores):
        return True
    normalized = path.lstrip("./")
    for entry in PROCESS_ENTRIES:
        if entry.endswith("/"):
            if normalized == entry.rstrip("/") or normalized.startswith(entry):
                return True
        elif normalized == entry:
            return True
    return False


def _changed_files_pr(repo: Path, base: str, head: str, two_dot: bool) -> tuple[bool, list[str] | str]:
    sep = ".." if two_dot else "..."
    ok, out = _git(repo, ["diff", "--name-only", f"{base}{sep}{head}"])
    if not ok:
        return False, out
    return True, [line for line in out.splitlines() if line.strip()]


def _changed_files_hook(repo: Path) -> tuple[bool, list[str] | str]:
    ok, out = _git(repo, ["diff", "--cached", "--name-only"])
    if not ok:
        return False, out
    return True, [line for line in out.splitlines() if line.strip()]


def _show(repo: Path, spec: str) -> tuple[bool, str]:
    return _git(repo, ["show", f"{spec}:plan.md"])


def _manifest_entries(text: str) -> list[str]:
    """Parse the bullet list under '## Files that change'."""
    lines = text.splitlines()
    start = -1
    for i, line in enumerate(lines):
        if line.strip().lower() == "## files that change":
            start = i
            break
    if start == -1:
        raise ValueError('plan.md has no "Files that change" section')

    section: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("#"):
            break
        section.append(line)

    bullets = [line for line in section if BULLET_RE.match(line)]
    if not bullets:
        raise ValueError(
            'plan.md "Files that change" must be a bullet list of paths/globs'
        )

    entries: list[str] = []
    for line in bullets:
        match = BULLET_RE.match(line)
        assert match is not None
        entry = _normalize_entry(match.group(1))
        if entry and entry != "-":
            entries.append(entry)
    return entries


def _normalize_entry(entry: str) -> str:
    entry = entry.strip()
    if entry.startswith("`") and entry.endswith("`") and len(entry) > 1:
        entry = entry[1:-1]
    else:
        entry = entry.replace("`", "")
    entry = re.sub(r"\s+\((?:new|modified|deleted|renamed|added)\)\s*$", "", entry, flags=re.I)
    entry = re.sub(r"\s+(?:new|modified|deleted|renamed|added)\s*$", "", entry, flags=re.I)
    return entry.strip()


def _plan_is_approved(text: str) -> bool:
    return bool(STATUS_RE.search(text))


def _added_manifest_entries(old_text: str | None, new_text: str) -> list[str]:
    old_lines = (old_text or "").splitlines()
    new_lines = new_text.splitlines()
    try:
        old_entries = _manifest_entries("\n".join(old_lines)) if old_text is not None else []
    except ValueError:
        # Legacy plans may still be prose; migrating them to the bullet
        # manifest in the same change is supported. The head manifest is
        # authoritative, so treat the old manifest as empty.
        old_entries = []
    added: list[str] = []
    if old_entries:
        diff = difflib.unified_diff(old_lines, new_lines, lineterm="", n=0)
        for line in diff:
            if not line.startswith("+"):
                continue
            stripped = line[1:].strip()
            match = BULLET_RE.match(stripped)
            if not match:
                continue
            added.append(_normalize_entry(match.group(1)))
    else:
        added = _manifest_entries(new_text)
    return added


def _plan_gate(repo: Path, graph_path: str | None) -> str:
    if graph_path:
        path = Path(graph_path)
        if path.is_file():
            try:
                graph = gl.load_yaml(path)
                nodes = graph.get("nodes", {})
                plan = nodes.get("plan") if isinstance(nodes, dict) else None
                if isinstance(plan, dict) and isinstance(plan.get("gate"), str):
                    return plan["gate"]
            except Exception:
                pass
    return DEFAULT_PLAN_GATE


def _approved_plan_record(repo: Path, ledger_path: Path, gate: str) -> tuple[bool, dict | None]:
    if not ledger_path.is_file():
        return False, None
    try:
        records = gl.read_ledger(ledger_path)
    except ValueError as exc:
        raise ValueError(f"cannot read ledger {ledger_path}: {exc}") from exc
    for record in records:
        if record.get("gate") == gate and record.get("decision") == "approved":
            return True, record
    return False, None


def _verify_ledger_record(repo: Path, ledger_path: Path, record: dict) -> bool:
    args = argparse.Namespace(
        ledger=str(ledger_path),
        record=record.get("id"),
        require_gate=None,
        require_committed=False,
        graph=None,
        require_gates=False,
    )
    return gl.cmd_verify(args) == 0


def _check(
    repo: Path,
    changed: list[str],
    plan_text: str | None,
    plan_diff_base: str | None,
    ledger_path: str | None,
    graph_path: str | None,
    ignores: list[str],
    hook_mode: bool,
) -> int:
    implementation = [
        path for path in changed if not _is_process_path(path, ignores)
    ]
    if not implementation:
        return 0

    if plan_text is None:
        print("error: no plan.md", file=sys.stderr)
        return 1
    if not _plan_is_approved(plan_text):
        print("error: plan.md not Approved", file=sys.stderr)
        return 1

    try:
        entries = _manifest_entries(plan_text)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    gate = _plan_gate(repo, graph_path)
    if ledger_path is not None:
        path = Path(ledger_path)
        found, record = _approved_plan_record(repo, path, gate)
        if hook_mode:
            # The plan approval may be committed in the same change; only
            # enforce when a plan approval record already exists.
            if found and not _verify_ledger_record(repo, path, record):
                print(
                    f"error: ledger record {record.get('id')} for gate {gate} "
                    "failed verification",
                    file=sys.stderr,
                )
                return 1
        else:
            if not found:
                print(
                    f"error: no approved plan record for gate {gate}",
                    file=sys.stderr,
                )
                return 1
            if not _verify_ledger_record(repo, path, record):
                print(
                    f"error: ledger record {record.get('id')} for gate {gate} "
                    "failed verification",
                    file=sys.stderr,
                )
                return 1

    if plan_diff_base is not None:
        old_ok, old_text = _show(repo, plan_diff_base)
        if not old_ok:
            old_text = None
    else:
        old_text = None
    added = _added_manifest_entries(old_text, plan_text) if old_text is not None else []

    unplanned: list[str] = []
    for path in implementation:
        if any(fnmatch.fnmatch(path, entry) for entry in entries):
            continue
        if any(fnmatch.fnmatch(path, entry) for entry in added):
            continue
        unplanned.append(path)

    if unplanned:
        details = " ".join(
            f'{path} (add a matching entry under "Files that change" in plan.md)'
            for path in sorted(unplanned)
        )
        print(f"error: unplanned files: {details}", file=sys.stderr)
        return 1
    return 0


def cmd_pr(args: argparse.Namespace, repo: Path) -> int:
    ok, changed = _changed_files_pr(repo, args.base, args.head, args.two_dot)
    if not ok:
        print(f"error: git diff failed: {changed}", file=sys.stderr)
        return 1
    ok, head_plan = _show(repo, args.head)
    if not ok:
        head_plan = ""
    if not head_plan.strip():
        head_plan = None
    return _check(
        repo,
        changed,
        head_plan,
        args.base,
        args.ledger,
        args.graph,
        args.ignore,
        hook_mode=False,
    )


def cmd_hook(args: argparse.Namespace, repo: Path) -> int:
    ok, changed = _changed_files_hook(repo)
    if not ok:
        print(f"error: git diff failed: {changed}", file=sys.stderr)
        return 1
    ok, staged_plan = _show(repo, "")
    if not ok:
        staged_plan = ""
    if not staged_plan.strip():
        staged_plan = None
    if staged_plan is None:
        # Only fail when implementation files actually need a plan; process-only
        # changes pass in cmd_pr/_check before this branch.
        implementation = [
            path for path in changed if not _is_process_path(path, args.ignore)
        ]
        if implementation:
            print(
                "error: plan.md is not staged; stage plan.md with the implementation",
                file=sys.stderr,
            )
            return 1
        return 0
    return _check(
        repo,
        changed,
        staged_plan,
        "HEAD",
        args.ledger,
        args.graph,
        args.ignore,
        hook_mode=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hook", action="store_true", help="check the staged diff")
    parser.add_argument("--base", default=None, help="base revision (PR mode)")
    parser.add_argument("--head", default=None, help="head revision (PR mode)")
    parser.add_argument("--graph", default=None, help="workflow-graph.yaml (default: plan gate engineer_approve)")
    parser.add_argument("--ledger", default=None, help="gates/ledger.jsonl when ledger-gating is wanted")
    parser.add_argument("--ignore", action="append", default=[], help="additional process-path glob")
    parser.add_argument(
        "--two-dot",
        action="store_true",
        help="use literal base..head instead of merge-base base...head",
    )
    args = parser.parse_args(argv)

    if args.hook and (args.base or args.head):
        parser.error("--hook cannot be combined with --base/--head")
    if not args.hook and not (args.base and args.head):
        parser.error("pass --hook or both --base and --head")

    repo = Path.cwd()
    if args.hook:
        return cmd_hook(args, repo)
    return cmd_pr(args, repo)


if __name__ == "__main__":
    raise SystemExit(main())
