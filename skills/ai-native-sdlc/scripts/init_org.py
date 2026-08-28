#!/usr/bin/env python3
"""Scaffold the autonomous agent org into a project directory.

Usage:
    python3 init_org.py <project-dir> [--dry-run] [--force]

Creates:
    org/org-chart.yaml          role model (reports-to, authority, gates)
    org/status.yaml             live agent states + review queue
    org/protocol.md             reporting, peer review, escalation, intake
    org/roles/*.md              role cards (ceo, cto, product-manager,
                                product-engineer, engineer, reviewer)
    org/intake/README.md        multi-channel demand intake (github/forms/email)
    org/intake/config.json      GitHub intake configuration
    org/reviews/README.md       evidence-backed review record format
    scripts/sync_issues.py      GitHub issue intake helper

Existing files are skipped unless --force is passed. --dry-run prints the
plan without writing anything (not even the project directory).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ASSET_DIR = SKILL_DIR / "assets"

# dest -> skill-relative source (assets/ for templates, scripts/ for tooling)
FILES = {
    "org/org-chart.yaml": "assets/org/org-chart.yaml",
    "org/status.yaml": "assets/org/status.yaml",
    "org/protocol.md": "assets/org/protocol.md",
    "org/roles/ceo.md": "assets/org/roles/ceo.md",
    "org/roles/cto.md": "assets/org/roles/cto.md",
    "org/roles/product-manager.md": "assets/org/roles/product-manager.md",
    "org/roles/product-engineer.md": "assets/org/roles/product-engineer.md",
    "org/roles/engineer.md": "assets/org/roles/engineer.md",
    "org/roles/reviewer.md": "assets/org/roles/reviewer.md",
    "org/intake/README.md": "assets/org/intake/README.md",
    "org/intake/config.json": "assets/org/intake/config.json",
    "org/intake/github/.gitkeep": "assets/org/intake/github/.gitkeep",
    "org/intake/forms/.gitkeep": "assets/org/intake/forms/.gitkeep",
    "org/intake/email/.gitkeep": "assets/org/intake/email/.gitkeep",
    "org/reviews/README.md": "assets/org/reviews/README.md",
    "scripts/sync_issues.py": "scripts/sync_issues.py",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "project_dir",
        nargs="?",
        default=".",
        help="target project directory (default: current directory)",
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
    args = parser.parse_args()

    if not SKILL_DIR.is_dir():
        print(f"error: skill directory not found at {SKILL_DIR}", file=sys.stderr)
        return 1

    root = Path(args.project_dir).expanduser().resolve()

    plan: list[tuple[Path, str, str]] = []
    for rel_dest, rel_src in FILES.items():
        src = SKILL_DIR / rel_src
        if not src.is_file():
            print(f"error: missing source {src}", file=sys.stderr)
            return 1
        dest = root / rel_dest
        mode = "write" if args.force or not dest.exists() else "skip"
        plan.append((dest, rel_src, mode))

    if args.dry_run:
        print(f"Dry run: would scaffold the agent org in {root}")
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
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        written.append(str(dest.relative_to(root)))

    print(f"Scaffolded the agent org in {root}")
    if written:
        print("  created:")
        for name in written:
            print(f"    - {name}")
    if skipped:
        print("  skipped (already exist; use --force to overwrite):")
        for name in skipped:
            print(f"    - {name}")

    print()
    print("Next steps:")
    print("  1. Commit the skeleton: git add -A && git commit -m 'scaffold agent org'")
    print("  2. Tell your agent: run the AI-native SDLC loop with the agent org.")
    print("  3. Configure org/intake/config.json and run: python3 scripts/sync_issues.py pull")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
