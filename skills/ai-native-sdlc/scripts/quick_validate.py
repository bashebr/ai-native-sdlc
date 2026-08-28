#!/usr/bin/env python3
"""Validate the ai-native-sdlc skill/plugin bundle (self-check).

Usage:
    python3 quick_validate.py [skill-dir]     (default: ./skills/ai-native-sdlc)

Checks (stdlib only; PyYAML is used when available, otherwise a structural
YAML sanity check):
  1. SKILL.md exists with name/description/version frontmatter.
  2. Every relative path linked from SKILL.md exists.
  3. plugin.json (repo root) name/version match the skill frontmatter.
  4. Every asset referenced by the scaffold script exists.
  5. YAML/JSON assets parse.
  6. Shell assets pass `bash -n`.
  7. The release gate passes its test suite (tests/test_gate.sh).
  8. The scaffold script smoke-scaffolds into a temp dir.

Exit 0 on success, 1 on any failure.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# Files the skill promises, beyond what markdown links already cover.
PROMISED = [
    "SKILL.md",
    "agents/openai.yaml",
    "references/playbook.md",
    "references/adoption.md",
    "references/graph.md",
    "references/org.md",
    "scripts/init_workflow.py",
    "scripts/init_org.py",
    "scripts/sync_issues.py",
    "scripts/gate_ledger.py",
    "scripts/run_evals.py",
    "scripts/detect_bands.py",
    "assets/intent.md",
    "assets/spec.md",
    "assets/plan.md",
    "assets/CLAUDE.md",
    "assets/REVIEW.md",
    "assets/bands.yaml",
    "assets/production-gate.sh",
    "assets/evals.example.md",
    "assets/evals.example.json",
    "assets/evals-README.md",
    "assets/gates-README.md",
    "assets/workflow-graph.example.yaml",
    "assets/workflow-graph.yaml",
    "assets/incident.md",
    "assets/PULL_REQUEST_TEMPLATE.md",
    "assets/runbooks/rollback-deploy.md",
    "assets/hook-settings.example.json",
    "assets/agent-evals.yml.example",
    "assets/managed-settings.example.json",
    "assets/.gitignore",
    "assets/org/org-chart.yaml",
    "assets/org/status.yaml",
    "assets/org/protocol.md",
    "assets/org/roles/ceo.md",
    "assets/org/roles/cto.md",
    "assets/org/roles/product-manager.md",
    "assets/org/roles/product-engineer.md",
    "assets/org/roles/engineer.md",
    "assets/org/roles/reviewer.md",
    "assets/org/intake/README.md",
    "assets/org/intake/config.json",
    "assets/org/intake/github/.gitkeep",
    "assets/org/intake/forms/.gitkeep",
    "assets/org/intake/email/.gitkeep",
    "assets/org/reviews/README.md",
]

failures: list[str] = []
passes: list[str] = []


def ok(msg: str) -> None:
    passes.append(msg)
    print(f"  PASS: {msg}")


def bad(msg: str) -> None:
    failures.append(msg)
    print(f"  FAIL: {msg}")


def check(condition: bool, msg: str) -> None:
    (ok if condition else bad)(msg)


def parse_frontmatter(text: str) -> dict[str, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip().strip('"\'')
    return out


def yaml_ok(path: Path) -> bool:
    """Parse YAML with PyYAML if available; else structural sanity check."""
    try:
        import yaml  # type: ignore

        with open(path, encoding="utf-8") as fh:
            yaml.safe_load(fh)
        return True
    except ImportError:
        text = path.read_text(encoding="utf-8")
        if "\t" in text:
            return False
        if text.count("{") != text.count("}") or text.count("[") != text.count("]"):
            return False
        return True
    except Exception:
        return False


def run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    try:
        res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=180)
        return res.returncode, (res.stdout + res.stderr).strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return -1, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "skill_dir",
        nargs="?",
        default="skills/ai-native-sdlc",
        help="path to the skill folder (default: skills/ai-native-sdlc)",
    )
    args = parser.parse_args()

    skill = Path(args.skill_dir).resolve()
    repo_root = skill.parent.parent
    print(f"Validating skill at {skill} (repo root: {repo_root})")

    # 1. SKILL.md frontmatter
    skill_md = skill / "SKILL.md"
    check(skill_md.is_file(), "SKILL.md exists")
    if not skill_md.is_file():
        bad("cannot continue without SKILL.md")
        return 1
    fm = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    check(bool(fm.get("name")), "SKILL.md has a name")
    check(bool(fm.get("description")), "SKILL.md has a description")
    check(bool(fm.get("version")), "SKILL.md has a version")

    # 2. Every relative markdown link resolves.
    text = skill_md.read_text(encoding="utf-8")
    for link in LINK_RE.findall(text):
        if link.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = (skill / link).resolve()
        check(target.exists(), f"linked file exists: {link}")

    # 3. plugin.json consistency
    plugin = repo_root / ".codex-plugin" / "plugin.json"
    if plugin.is_file():
        try:
            data = json.loads(plugin.read_text(encoding="utf-8"))
            check(data.get("name") == fm.get("name"), "plugin.json name matches SKILL.md")
            check(data.get("version") == fm.get("version"), "plugin.json version matches SKILL.md")
            skills_dir = repo_root / (data.get("skills") or "skills")
            check(skills_dir.is_dir(), f"plugin.json skills dir exists ({skills_dir})")
        except json.JSONDecodeError as exc:
            bad(f"plugin.json is not valid JSON: {exc}")
    else:
        bad("plugin.json not found")

    # 4. Promised files exist.
    for rel in PROMISED:
        check((skill / rel).exists(), f"promised file exists: {rel}")

    # 5. YAML/JSON assets parse.
    for path in sorted((skill / "assets").rglob("*")):
        if path.suffix in (".yaml", ".yml"):
            check(yaml_ok(path), f"YAML parses: {path.name}")
        elif path.suffix == ".json":
            try:
                json.loads(path.read_text(encoding="utf-8"))
                ok(f"JSON parses: {path.name}")
            except json.JSONDecodeError as exc:
                bad(f"JSON parses: {path.name} ({exc})")

    # 6. Shell syntax.
    for path in sorted(list((skill / "assets").glob("*.sh")) + list((skill / "scripts").glob("*.sh"))):
        code, _ = run(["bash", "-n", str(path)])
        check(code == 0, f"bash -n: {path.name}")

    # 7. Release gate test suite.
    gate_tests = repo_root / "tests" / "test_gate.sh"
    if gate_tests.is_file():
        code, out = run(["bash", str(gate_tests)])
        last_line = out.splitlines()[-1] if out else ""
        check(code == 0 and "failed" in last_line and last_line.endswith("0 failed"), "release gate test suite passes")
    else:
        bad("tests/test_gate.sh not found")

    # 8. Scaffold smoke test.
    with tempfile.TemporaryDirectory() as td:
        smoke = Path(td) / "smoke"
        code, out = run(["python3", str(skill / "scripts" / "init_workflow.py"), str(smoke), "--name", "Smoke"])
        check(code == 0, "init_workflow.py scaffold smoke test")
        check((smoke / "intent" / "intent.md").is_file(), "scaffold writes intent/intent.md")
        check((smoke / "workflow-graph.yaml").is_file(), "scaffold writes workflow-graph.yaml")
        check((smoke / "hooks" / "production-gate.sh").is_file(), "scaffold writes hooks/production-gate.sh")

    # 9. Org scaffold smoke test.
    with tempfile.TemporaryDirectory() as td:
        org = Path(td) / "org-smoke"
        code, out = run(["python3", str(skill / "scripts" / "init_org.py"), str(org)])
        check(code == 0, "init_org.py scaffold smoke test")
        check((org / "org" / "org-chart.yaml").is_file(), "org scaffold writes org/org-chart.yaml")
        check((org / "org" / "status.yaml").is_file(), "org scaffold writes org/status.yaml")
        check((org / "org" / "roles" / "cto.md").is_file(), "org scaffold writes role cards")
        check((org / "org" / "intake" / "README.md").is_file(), "org scaffold writes intake README")
        check((org / "scripts" / "sync_issues.py").is_file(), "org scaffold writes scripts/sync_issues.py")

    # 10. Python scripts compile cleanly.
    for script in ("run_evals.py", "detect_bands.py", "gate_ledger.py", "init_org.py", "sync_issues.py"):
        code, _ = run(["python3", "-m", "py_compile", str(skill / "scripts" / script)])
        check(code == 0, f"py_compile: {script}")

    print()
    print(f"quick_validate: {len(passes)} passed, {len(failures)} failed")
    if failures:
        print("Run the failing checks above; keep plugin.json and SKILL.md in sync.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
