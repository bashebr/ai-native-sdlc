"""Tests for scripts/check_plan_sync.py (F2 — deterministic plan-sync)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "skills" / "ai-native-sdlc" / "scripts" / "check_plan_sync.py"
)
GATE_LEDGER = (
    Path(__file__).resolve().parent.parent
    / "skills" / "ai-native-sdlc" / "scripts" / "gate_ledger.py"
)


PLAN = """\
# Feature

- Status: Approved

## Files that change

{files}

## Proof

- tests cover it.
"""


class PlanSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: os.system(f"rm -rf {self.root}"))
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "test@example.com")
        git(self.root, "config", "user.name", "Test")

    def commit(self, message: str) -> None:
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", message)

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=self.root,
            capture_output=True,
            text=True,
        )

    def base_plan(self) -> str:
        return PLAN.format(files="- src/base.py")

    def initial_commit(self) -> None:
        self.write("src/base.py", "BASE = 1\n")
        self.write("plan.md", self.base_plan())
        self.commit("base")

    def record_plan_approval(self) -> None:
        self.write("src/base.py", "BASE = 1\n")
        self.write("plan.md", self.base_plan())
        self.add_ledger_record("engineer_approve-001")
        self.commit("plan approval")

    def add_ledger_record(self, record_id: str, decision: str = "approved") -> None:
        ledger = self.root / "gates" / "ledger.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                sys.executable,
                str(GATE_LEDGER),
                "--ledger",
                str(ledger),
                "record",
                "--gate",
                "engineer_approve",
                "--artifact",
                "plan.md",
                "--approver",
                "Ada",
                "--evidence",
                f"review-{record_id}",
                "--decision",
                decision,
                "--id",
                record_id,
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_process_only_changes_pass_without_plan(self) -> None:
        self.initial_commit()
        self.write("docs/note.md", "# note\n")
        self.write("README.md", "readme\n")
        self.commit("docs only")
        res = self.run_cli("--base", "HEAD~1", "--head", "HEAD")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)

    def test_missing_plan_fails(self) -> None:
        self.initial_commit()
        self.write("src/new.py", "NEW = 1\n")
        self.write("plan.md", "")
        self.commit("code without plan")
        res = self.run_cli("--base", "HEAD~1", "--head", "HEAD")
        self.assertEqual(res.returncode, 1)
        self.assertIn("plan.md", (res.stdout + res.stderr).lower())

    def test_draft_plan_fails(self) -> None:
        self.initial_commit()
        self.write("src/new.py", "NEW = 1\n")
        self.write("plan.md", PLAN.format(files="- src/new.py").replace("Approved", "Draft"))
        self.commit("draft plan")
        res = self.run_cli("--base", "HEAD~1", "--head", "HEAD")
        self.assertEqual(res.returncode, 1)
        self.assertIn("not approved", (res.stdout + res.stderr).lower())

    def test_planned_files_pass(self) -> None:
        self.initial_commit()
        self.write("src/new.py", "NEW = 1\n")
        self.write("plan.md", PLAN.format(files="- src/base.py\n- src/new.py"))
        self.commit("planned change")
        res = self.run_cli("--base", "HEAD~1", "--head", "HEAD")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)

    def test_unplanned_file_fails(self) -> None:
        self.initial_commit()
        self.write("src/new.py", "NEW = 1\n")
        self.write("plan.md", PLAN.format(files="- src/base.py"))
        self.commit("unplanned change")
        res = self.run_cli("--base", "HEAD~1", "--head", "HEAD")
        self.assertEqual(res.returncode, 1)
        self.assertIn("unplanned", (res.stdout + res.stderr).lower())

    def test_plan_diff_adding_matching_entry_passes(self) -> None:
        self.initial_commit()
        self.write("src/new.py", "NEW = 1\n")
        self.write("plan.md", PLAN.format(files="- src/base.py\n- src/new.py"))
        self.commit("declare departure in plan")
        res = self.run_cli("--base", "HEAD~1", "--head", "HEAD")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)

    def test_legacy_prose_plan_can_migrate_to_manifest(self) -> None:
        self.write("src/base.py", "BASE = 1\n")
        self.write(
            "plan.md",
            "# Feature\n\n- Status: Approved\n\n## Files that change\n\n"
            "<Files to create and modify.>\n",
        )
        self.commit("base with legacy plan")
        self.write("src/new.py", "NEW = 1\n")
        self.write("plan.md", PLAN.format(files="- src/base.py\n- src/new.py"))
        self.commit("migrate plan to bullet manifest")
        res = self.run_cli("--base", "HEAD~1", "--head", "HEAD")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)

    def test_unrelated_plan_edit_does_not_exempt_file(self) -> None:
        self.initial_commit()
        self.write("src/new.py", "NEW = 1\n")
        plan = PLAN.format(files="- src/base.py").replace(
            "## Proof", "## Proof\n\nEdited prose only."
        )
        self.write("plan.md", plan)
        self.commit("prose-only plan change")
        res = self.run_cli("--base", "HEAD~1", "--head", "HEAD")
        self.assertEqual(res.returncode, 1)
        self.assertIn("unplanned", (res.stdout + res.stderr).lower())

    def test_glob_entry_matches_file(self) -> None:
        self.initial_commit()
        self.write("src/util/helper.py", "HELPER = 1\n")
        self.write("plan.md", PLAN.format(files="- src/*.py\n- src/util/helper.py"))
        self.commit("globbed change")
        res = self.run_cli("--base", "HEAD~1", "--head", "HEAD")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)

    def test_ledger_gate_requirement(self) -> None:
        self.initial_commit()
        self.write("src/new.py", "NEW = 1\n")
        self.write("plan.md", PLAN.format(files="- src/base.py\n- src/new.py"))
        self.commit("planned change")
        res = self.run_cli("--base", "HEAD~1", "--head", "HEAD", "--ledger", "gates/ledger.jsonl")
        self.assertEqual(res.returncode, 1)
        self.assertIn("engineer_approve", (res.stdout + res.stderr).lower())

        self.record_plan_approval()
        self.write("src/next.py", "NEXT = 1\n")
        self.write("plan.md", PLAN.format(files="- src/base.py\n- src/new.py\n- src/next.py"))
        self.commit("next planned change")
        res = self.run_cli("--base", "HEAD~1", "--head", "HEAD", "--ledger", "gates/ledger.jsonl")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)

    def test_latest_plan_approval_is_the_one_verified(self) -> None:
        # A second plan (v2) gets a second approval; the check must use it, not v1's.
        self.initial_commit()
        self.record_plan_approval()
        self.add_ledger_record("engineer_approve-002")
        self.commit("plan v2 approval")
        self.write("src/new.py", "NEW = 1\n")
        self.write("plan.md", PLAN.format(files="- src/base.py\n- src/new.py"))
        self.commit("v2 change")
        res = self.run_cli("--base", "HEAD~1", "--head", "HEAD", "--ledger", "gates/ledger.jsonl")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertIn("engineer_approve-002", res.stdout)
        self.assertNotIn("engineer_approve-001", res.stdout)

    def test_later_rejection_overrides_older_approval(self) -> None:
        self.initial_commit()
        self.record_plan_approval()
        self.add_ledger_record("engineer_approve-002", decision="rejected")
        self.commit("plan v2 rejected")
        self.write("src/new.py", "NEW = 1\n")
        self.write("plan.md", PLAN.format(files="- src/base.py\n- src/new.py"))
        self.commit("change after rejection")
        res = self.run_cli("--base", "HEAD~1", "--head", "HEAD", "--ledger", "gates/ledger.jsonl")
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn("engineer_approve-002", res.stdout + res.stderr)

    def test_ledger_chain_break_fails(self) -> None:
        self.initial_commit()
        self.record_plan_approval()
        ledger = self.root / "gates" / "ledger.jsonl"
        rec = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
        rec["approver"] = "Mallory"
        ledger.write_text(json.dumps(rec, sort_keys=True) + "\n", encoding="utf-8")
        self.write("src/new.py", "NEW = 1\n")
        self.write("plan.md", PLAN.format(files="- src/base.py\n- src/new.py"))
        self.commit("change after tamper")
        res = self.run_cli("--base", "HEAD~1", "--head", "HEAD", "--ledger", "gates/ledger.jsonl")
        self.assertEqual(res.returncode, 1)
        self.assertIn("hash", (res.stdout + res.stderr).lower())

    def test_hook_mode_uses_staged_plan(self) -> None:
        self.initial_commit()
        self.write("src/new.py", "NEW = 1\n")
        self.write("plan.md", PLAN.format(files="- src/base.py\n- src/new.py"))
        git(self.root, "add", "-A")
        res = self.run_cli("--hook")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)

    def test_hook_mode_requires_plan_staged_when_code_departs(self) -> None:
        self.initial_commit()
        self.write("src/new.py", "NEW = 1\n")
        git(self.root, "add", "src/new.py")
        res = self.run_cli("--hook")
        self.assertEqual(res.returncode, 1)
        self.assertIn("plan.md", (res.stdout + res.stderr).lower())

    def test_hook_mode_draft_staged_plan_fails(self) -> None:
        self.initial_commit()
        self.write("src/new.py", "NEW = 1\n")
        self.write("plan.md", PLAN.format(files="- src/base.py\n- src/new.py").replace("Approved", "Draft"))
        git(self.root, "add", "-A")
        res = self.run_cli("--hook")
        self.assertEqual(res.returncode, 1)
        self.assertIn("not approved", (res.stdout + res.stderr).lower())


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
