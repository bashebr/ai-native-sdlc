#!/usr/bin/env python3
"""Scaffold the AI-native SDLC artifact skeleton in a project directory.

Usage:
    python3 init_workflow.py <project-dir> [--name "<project name>"]
        [--framework codex|claude] [--dry-run] [--force] [--git]

Creates:
    intent/intent.md            intent template (copy of assets/intent.md)
    CLAUDE.md / AGENTS.md       repository-memory starter (--framework claude|codex)
    REVIEW.md                   review standards
    hooks/production-gate.sh    release authorization hook (executable)
    hooks/pre-commit.example    secret-scan pre-commit hook (copy to .git/hooks/)
    scripts/gate_ledger.py      approval-record ledger (hash-chained)
    scripts/run_evals.py        eval-suite runner (Phase 4)
    scripts/detect_bands.py     control-band detection (Phase 6)
    scripts/workflow_state.py   workflow graph state runtime (status/advance/check)
    scripts/check_plan_sync.py  deterministic plan-sync enforcement
    scripts/scan_secrets.py     deterministic secret scanner (build guardrail)
    bands.yaml                  monitoring control bands
    workflow-graph.yaml         project state on the loop graph
    evals/example.md            eval case example (markdown)
    evals/README.md             how to add evals (JSON format)
    gates/README.md             gate ledger usage
    .secretsignore              secret-scanner allowlist (starts empty)
    .gitignore                  basic ignore rules

Existing files are skipped unless --force is passed. --dry-run prints the
plan without writing anything (not even the project directory).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ASSET_DIR = SKILL_DIR / "assets"

# dest -> skill-relative source (assets/ for templates, scripts/ for tooling)
FILES = {
    "intent/intent.md": "assets/intent.md",
    "CLAUDE.md": "assets/CLAUDE.md",
    "REVIEW.md": "assets/REVIEW.md",
    "hooks/production-gate.sh": "assets/production-gate.sh",
    "hooks/pre-commit.example": "assets/pre-commit.example",
    "scripts/gate_ledger.py": "scripts/gate_ledger.py",
    "scripts/run_evals.py": "scripts/run_evals.py",
    "scripts/detect_bands.py": "scripts/detect_bands.py",
    "scripts/workflow_state.py": "scripts/workflow_state.py",
    "scripts/check_plan_sync.py": "scripts/check_plan_sync.py",
    "scripts/scan_secrets.py": "scripts/scan_secrets.py",
    "bands.yaml": "assets/bands.yaml",
    "workflow-graph.yaml": "assets/workflow-graph.yaml",
    "evals/example.md": "assets/evals.example.md",
    "evals/README.md": "assets/evals-README.md",
    "gates/README.md": "assets/gates-README.md",
    ".secretsignore": "assets/secrets-ignore.example",
    ".gitignore": "assets/.gitignore",
}


def _bad_name(name: str) -> bool:
    return not name or any(c in name for c in "\r\n\t") or name.startswith("-")


def _git_init_and_commit(root: Path) -> None:
    if (root / ".git").exists():
        print("  git: already a repository; skipping init")
        return
    try:
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "scaffold ai-native-sdlc workflow"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        print("  git: initialized and created the initial commit")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("  git: skipped (git unavailable or commit failed); run git init manually")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "project_dir",
        nargs="?",
        default=".",
        help="target project directory (default: current directory)",
    )
    parser.add_argument(
        "--name",
        help="project/product name to fill into the intent title",
    )
    parser.add_argument(
        "--framework",
        choices=["claude", "codex"],
        default="claude",
        help="agent framework to target: claude writes CLAUDE.md, codex writes AGENTS.md (default: claude)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan without writing anything",
    )
    parser.add_argument(
        "--git",
        action="store_true",
        help="git init and commit the skeleton (requires git)",
    )
    args = parser.parse_args()

    if not SKILL_DIR.is_dir():
        print(f"error: skill directory not found at {SKILL_DIR}", file=sys.stderr)
        return 1
    if args.name is not None and _bad_name(args.name):
        print(
            "error: --name must be non-empty, contain no control characters, and not start with '-'",
            file=sys.stderr,
        )
        return 1

    root = Path(args.project_dir).expanduser().resolve()

    plan: list[tuple[Path, str, str]] = []  # (dest, src, mode)
    for rel_dest, rel_src in FILES.items():
        src = SKILL_DIR / rel_src
        if not src.is_file():
            print(f"error: missing source {src}", file=sys.stderr)
            return 1
        dest = root / rel_dest
        if rel_dest == "CLAUDE.md" and args.framework == "codex":
            dest = root / "AGENTS.md"
        mode = "write" if args.force or not dest.exists() else "skip"
        plan.append((dest, rel_src, mode))

    if args.dry_run:
        print(f"Dry run: would scaffold AI-native SDLC skeleton in {root}")
        for dest, rel_src, mode in plan:
            print(f"  [{mode}] {dest.relative_to(root)}  <- {rel_src}")
        return 0

    root.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    skipped: list[str] = []
    for dest, rel_src, mode in plan:
        if mode == "skip":
            skipped.append(str(dest.relative_to(root)))
            continue
        text = (SKILL_DIR / rel_src).read_text(encoding="utf-8")
        if dest.name == "AGENTS.md":
            text = text.replace(
                "# CLAUDE.md \u2014 repository memory", "# AGENTS.md \u2014 repository memory"
            )
            text = text.replace(
                "\n\n> In Codex projects, the same content lives in AGENTS.md; the role is identical.\n",
                "\n",
            )
        if args.name:
            text = text.replace("<Title>", args.name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        if dest.name == "production-gate.sh":
            dest.chmod(dest.stat().st_mode | 0o111)
        written.append(str(dest.relative_to(root)))

    print(f"Scaffolded AI-native SDLC skeleton in {root}")
    if written:
        print("  created:")
        for name in written:
            print(f"    - {name}")
    if skipped:
        print("  skipped (already exist; use --force to overwrite):")
        for name in skipped:
            print(f"    - {name}")

    if args.git:
        _git_init_and_commit(root)

    print()
    print("Next steps:")
    print("  1. Open intent/intent.md and describe the goal in your own words.")
    print("  2. Tell your agent: run the AI-native SDLC workflow from this intent.")
    print("  3. Commit the skeleton: git add -A && git commit -m 'scaffold ai-native-sdlc workflow'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
