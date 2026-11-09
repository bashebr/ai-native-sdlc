"""Tests for scripts/org_status.py (F3 — agent-org status tooling)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "skills" / "ai-native-sdlc" / "scripts" / "org_status.py"
)
sys.path.insert(0, str(SCRIPT.parent))
import org_status  # noqa: E402

ORG_CHART = """\
# Autonomous agent org.
version: 1

owner:
  role: ceo
  human: true

agents:
  ceo:
    title: Chief Executive Officer
    human: true
    reports_to: null
    authority: [pr_merge_approval, release_authorization]
  cto:
    title: Chief Technology Officer
    reports_to: ceo
    authority: [dispatch, review_routing]
  product-manager:
    title: Product Manager
    reports_to: cto
    authority: [intent_first_review]
  product-engineer:
    title: Product Engineering Agent
    reports_to: cto
    authority: [file_tickets, draft_intents]
  engineer:
    title: Software Engineer
    reports_to: cto
    count: 1
    authority: [implement]
  reviewer:
    title: Reviewer
    reports_to: cto
    count: 1
    authority: [peer_review]

gates:
  spec: peer_review

escalation:
  max_disagreement_rounds: 2
  route: writer -> reviewer -> cto -> ceo
"""

STATUS = """\
# Live org state.
# This header comment must survive mutations.
version: 1

agents:
  cto: { status: idle, assignment: null, last_report: null }
  product-manager: { status: idle, assignment: null, last_report: null }
  product-engineer: { status: idle, assignment: null, last_report: null }
  engineer-1: { status: idle, assignment: null, last_report: null }
  reviewer-1: { status: idle, assignment: null, last_report: null }

review_queue: []
"""


class OrgStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: os.system(f"rm -rf {self.tmp}"))
        org = self.tmp / "org"
        org.mkdir(parents=True)
        (org / "org-chart.yaml").write_text(ORG_CHART, encoding="utf-8")
        (org / "status.yaml").write_text(STATUS, encoding="utf-8")
        scripts = self.tmp / "scripts"
        scripts.mkdir()
        shutil.copy2(SCRIPT, scripts / "org_status.py")

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.tmp / "scripts" / "org_status.py"), *args],
            cwd=self.tmp,
            capture_output=True,
            text=True,
        )

    def status_text(self) -> str:
        return (self.tmp / "org" / "status.yaml").read_text(encoding="utf-8")

    def status_json(self) -> dict:
        res = self.run_cli("status", "--json")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        return json.loads(res.stdout)

    def assign(self, review_id: str, artifact: str = "spec.md") -> None:
        res = self.run_cli(
            "review",
            "assign",
            "--id",
            review_id,
            "--artifact",
            artifact,
            "--writer",
            "engineer-1",
            "--reviewer",
            "reviewer-1",
        )
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)

    def test_status_json_is_clean(self) -> None:
        data = self.status_json()
        self.assertEqual(data["agents"]["engineer-1"]["status"], "idle")
        self.assertEqual(data["review_queue"], [])
        self.assertEqual(data["violations"], [])

    def test_busy_idle_rules_and_unknown_agent(self) -> None:
        res = self.run_cli("agent", "engineer-1", "busy", "--assignment", "spec.md")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(self.status_text().startswith("# Live org state."))
        self.assertIn("engineer-1: { status: busy", self.status_text())

        res = self.run_cli("agent", "engineer-1", "busy", "--assignment", "plan.md")
        self.assertEqual(res.returncode, 1)

        res = self.run_cli("agent", "engineer-1", "idle", "--last-report", "shipped")
        self.assertEqual(res.returncode, 0, res.stderr)
        data = self.status_json()
        self.assertEqual(data["agents"]["engineer-1"]["status"], "idle")
        self.assertEqual(data["agents"]["engineer-1"]["last_report"], "shipped")

        res = self.run_cli("agent", "ghost", "busy", "--assignment", "x")
        self.assertEqual(res.returncode, 1)

    def test_assignment_requires_idle_reviewer_and_rejects_self_review(self) -> None:
        res = self.run_cli(
            "review",
            "assign",
            "--id",
            "r-1",
            "--artifact",
            "spec.md",
            "--writer",
            "reviewer-1",
            "--reviewer",
            "reviewer-1",
        )
        self.assertEqual(res.returncode, 1)

        res = self.run_cli("agent", "reviewer-1", "busy", "--assignment", "other")
        self.assertEqual(res.returncode, 0)
        res = self.run_cli(
            "review",
            "assign",
            "--id",
            "r-2",
            "--artifact",
            "spec.md",
            "--writer",
            "engineer-1",
            "--reviewer",
            "reviewer-1",
        )
        self.assertEqual(res.returncode, 1)

    def test_review_submit_and_rounds(self) -> None:
        self.assign("review-1")
        res = self.run_cli(
            "review",
            "submit",
            "--id",
            "review-1",
            "--verdict",
            "changes",
            "--evidence",
            "https://example.com/review-1",
        )
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        data = self.status_json()
        entry = data["review_queue"][0]
        self.assertEqual(entry["status"], "changes_requested")
        self.assertEqual(entry["rounds"], 1)
        self.assertEqual(entry["verdict"], "changes")
        self.assertEqual(data["agents"]["reviewer-1"]["status"], "idle")
        self.assertEqual(data["agents"]["engineer-1"]["status"], "busy")

        res = self.run_cli(
            "review",
            "submit",
            "--id",
            "review-1",
            "--verdict",
            "approved",
            "--evidence",
            "x",
        )
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        entry = self.status_json()["review_queue"][0]
        self.assertEqual(entry["status"], "approved")
        self.assertEqual(entry["verdict"], "approved")

    def test_next_assignment_carries_rounds(self) -> None:
        self.assign("review-1")
        self.run_cli(
            "review",
            "submit",
            "--id",
            "review-1",
            "--verdict",
            "changes",
            "--evidence",
            "round-1",
        )
        res = self.run_cli(
            "review",
            "assign",
            "--id",
            "review-2",
            "--artifact",
            "spec.md",
            "--writer",
            "engineer-1",
            "--reviewer",
            "reviewer-1",
        )
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        entries = {
            e["id"]: e for e in self.status_json()["review_queue"]
        }
        self.assertEqual(entries["review-2"]["rounds"], 1)
        self.assertEqual(entries["review-2"]["status"], "assigned")

    def test_escalation_requires_two_unresolved_rounds(self) -> None:
        self.assign("review-1")
        self.run_cli(
            "review", "submit", "--id", "review-1", "--verdict", "changes",
            "--evidence", "round-1",
        )
        res = self.run_cli("review", "escalate", "--id", "review-1")
        self.assertEqual(res.returncode, 1)

        self.assign("review-2")
        self.run_cli(
            "review", "submit", "--id", "review-2", "--verdict", "changes",
            "--evidence", "round-2",
        )
        res = self.run_cli("review", "escalate", "--id", "review-2")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        data = self.status_json()
        entry = next(e for e in data["review_queue"] if e["id"] == "review-2")
        self.assertEqual(entry["status"], "escalated")
        self.assertEqual(entry["escalated_to"], "cto")

    def test_assignment_blocked_after_max_rounds(self) -> None:
        self.assign("review-1")
        self.run_cli(
            "review", "submit", "--id", "review-1", "--verdict", "changes",
            "--evidence", "round-1",
        )
        self.assign("review-2")
        self.run_cli(
            "review", "submit", "--id", "review-2", "--verdict", "changes",
            "--evidence", "round-2",
        )
        res = self.run_cli(
            "review", "assign", "--id", "review-3", "--artifact", "spec.md",
            "--writer", "engineer-1", "--reviewer", "reviewer-1",
        )
        self.assertEqual(res.returncode, 1)
        self.assertIn("escalat", (res.stdout + res.stderr).lower())

    def test_writer_can_idle_after_resolved_rereview(self) -> None:
        self.assign("review-1")
        self.run_cli(
            "review", "submit", "--id", "review-1", "--verdict", "changes",
            "--evidence", "round-1",
        )
        self.assign("review-2")
        self.run_cli(
            "review", "submit", "--id", "review-2", "--verdict", "approved",
            "--evidence", "round-2",
        )
        res = self.run_cli("agent", "engineer-1", "idle", "--last-report", "resolved")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        data = self.status_json()
        self.assertEqual(data["agents"]["engineer-1"]["status"], "idle")
        self.assertEqual(data["violations"], [])

    def test_status_flags_protocol_violations(self) -> None:
        text = self.status_text().replace(
            "review_queue: []",
            "review_queue:\n"
            "  - id: dup\n"
            "    artifact: spec.md\n"
            "    writer: engineer-1\n"
            "    reviewer: reviewer-1\n"
            "    rounds: 0\n"
            "    verdict: null\n"
            "    status: assigned\n",
        )
        (self.tmp / "org" / "status.yaml").write_text(text, encoding="utf-8")
        res = self.run_cli("status")
        self.assertEqual(res.returncode, 1)
        self.assertIn("violation", (res.stdout + res.stderr).lower())

    def test_disabled_role_is_skipped(self) -> None:
        chart = {
            "agents": {
                "orchestrator": {"reports_to": "ceo"},
                "reviewer": {"reports_to": "orchestrator"},
                "product-engineer": {
                    "reports_to": "orchestrator",
                    "enabled": False,
                },
            }
        }
        roles, humans = org_status._agent_roles(chart)
        self.assertIn("orchestrator", roles)
        self.assertIn("reviewer", roles)
        self.assertNotIn("product-engineer", roles)
        self.assertFalse(humans["reviewer"])

    def test_escalation_owner_is_read_from_chart(self) -> None:
        self.assertEqual(
            org_status._escalation_owner(
                {"escalation": {"owner": "orchestrator"}}
            ),
            "orchestrator",
        )
        self.assertEqual(
            org_status._escalation_owner({"escalation": {}}),
            "cto",
        )

    def test_escalation_uses_chart_owner(self) -> None:
        chart = self.tmp / "org" / "org-chart.yaml"
        text = chart.read_text(encoding="utf-8").replace(
            "escalation:\n  max_disagreement_rounds: 2",
            "escalation:\n  owner: orchestrator\n  max_disagreement_rounds: 2",
        )
        chart.write_text(text, encoding="utf-8")
        self.assign("review-1")
        self.run_cli(
            "review",
            "submit",
            "--id",
            "review-1",
            "--verdict",
            "changes",
            "--evidence",
            "round-1",
        )
        self.assign("review-2")
        self.run_cli(
            "review",
            "submit",
            "--id",
            "review-2",
            "--verdict",
            "changes",
            "--evidence",
            "round-2",
        )
        res = self.run_cli("review", "escalate", "--id", "review-2")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        entry = next(
            entry
            for entry in self.status_json()["review_queue"]
            if entry["id"] == "review-2"
        )
        self.assertEqual(entry["escalated_to"], "orchestrator")


if __name__ == "__main__":
    unittest.main()
