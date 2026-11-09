"""Tests for scripts/org_loop.py."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "skills/ai-native-sdlc/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import org_loop  # noqa: E402

CHART = """\
version: 1
agents:
  ceo: { human: true }
  orchestrator: { reports_to: ceo }
  product-manager: { reports_to: orchestrator }
  architect: { reports_to: orchestrator }
  senior-developer: { reports_to: orchestrator }
  security-researcher: { reports_to: orchestrator }
  reviewer: { reports_to: orchestrator }
  memory-keeper: { reports_to: orchestrator }
"""

STATUS = """\
version: 1
agents:
  orchestrator: { status: idle, assignment: null, last_report: null }
  product-manager: { status: idle, assignment: null, last_report: null }
  architect: { status: idle, assignment: null, last_report: null }
  senior-developer: { status: idle, assignment: null, last_report: null }
  security-researcher: { status: idle, assignment: null, last_report: null }
  reviewer: { status: idle, assignment: null, last_report: null }
  memory-keeper: { status: idle, assignment: null, last_report: null }
review_queue: []
"""

GRAPH = """\
version: 1
nodes:
  implementation:
    stage: build
    artifact: code
    gate: code_owner_approve
    status: in_progress
    done_status: merged
  release:
    stage: deploy
    artifact: release
    gate: release_authorization
    status: not_started
    done_status: done
edges: []
"""

CONFIG = """\
version: 1
max_turns: 6
max_no_progress: 2
max_review_rounds: 2
require_security_clear: true
review_artifact: implementation
human_gates: [code_owner_approve, release_authorization]
acceptance:
  commands:
    - python3 -c "pass"
roles:
  product: product-manager
  design: architect
  implement: senior-developer
  security: security-researcher
  review: reviewer
phases:
  intent: { role: product-manager, action: review_intent, next: spec }
  spec: { role: architect, action: draft_spec, next: plan }
  plan: { role: senior-developer, action: draft_plan, next: implement }
  implement: { role: senior-developer, action: implement, next: security }
  security: { role: security-researcher, action: assess_security, next: review }
  review: { role: reviewer, action: review, next: done }
  done: { role: null, action: null, next: null }
"""


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp))
        for name in (
            "org",
            "org/messages",
            "org/memory",
            "org/loop",
            "scripts",
            "intent",
        ):
            (self.tmp / name).mkdir(parents=True, exist_ok=True)
        (self.tmp / "org" / "org-chart.yaml").write_text(CHART, encoding="utf-8")
        (self.tmp / "org" / "status.yaml").write_text(STATUS, encoding="utf-8")
        (self.tmp / "workflow-graph.yaml").write_text(GRAPH, encoding="utf-8")
        (self.tmp / "org" / "loop" / "config.yaml").write_text(
            CONFIG, encoding="utf-8"
        )
        (self.tmp / "intent" / "intent.md").write_text("# Intent\n", encoding="utf-8")
        for name in (
            "org_common.py",
            "org_status.py",
            "org_message.py",
            "org_memory.py",
            "org_loop.py",
        ):
            shutil.copy2(SCRIPTS / name, self.tmp / "scripts" / name)
        subprocess.run(["git", "init", "-q"], cwd=self.tmp, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=t@e.com",
                "-c",
                "user.name=T",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "base",
            ],
            cwd=self.tmp,
            check=True,
        )
        res = self.run_cli("init", "--work-item", "intent/intent.md")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)

    def run_cli(self, *args: str):
        return subprocess.run(
            [sys.executable, str(self.tmp / "scripts" / "org_loop.py"), *args],
            cwd=self.tmp,
            capture_output=True,
            text=True,
        )

    def next_json(self):
        res = self.run_cli("next", "--json")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        return json.loads(res.stdout)

    def status_json(self):
        res = self.run_cli("status", "--json")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        return json.loads(res.stdout)

    def advance(
        self,
        role,
        action,
        result="progress",
        artifact="spec.md",
        evidence="test",
    ):
        args = [
            "advance",
            "--role",
            role,
            "--action",
            action,
            "--result",
            result,
        ]
        if artifact is not None:
            args += ["--artifact", artifact]
        if evidence is not None:
            args += ["--evidence", evidence]
        return self.run_cli(*args)

    def set_review(self, verdict="approved", rounds=0):
        queue = f"""\
review_queue:
  - id: review-1
    artifact: implementation
    writer: senior-developer
    reviewer: reviewer
    rounds: {rounds}
    verdict: {verdict}
    status: {verdict}
"""
        text = STATUS.replace("review_queue: []\n", queue)
        (self.tmp / "org" / "status.yaml").write_text(text, encoding="utf-8")

    def write_artifact(self, name: str, content: str) -> None:
        path = self.tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_next_is_deterministic(self):
        first = self.run_cli("next", "--json")
        second = self.run_cli("next", "--json")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(json.loads(first.stdout)["role"], "product-manager")

    def test_six_turn_loop_reaches_solved(self):
        sequence = [
            (
                "product-manager",
                "review_intent",
                "progress",
                "intent-review.md",
            ),
            ("architect", "draft_spec", "progress", "spec.md"),
            ("senior-developer", "draft_plan", "progress", "plan.md"),
            ("senior-developer", "implement", "progress", "implementation.txt"),
            ("security-researcher", "assess_security", "progress", "security.md"),
            ("reviewer", "review", "verified", "review.md"),
        ]
        for role, action, result, artifact in sequence:
            nxt = self.next_json()
            self.assertEqual(nxt["role"], role)
            self.assertEqual(nxt["action"], action)
            if role == "reviewer":
                self.set_review()
            self.write_artifact(artifact, f"{role}:{action}\n")
            res = self.advance(role, action, result, artifact=artifact)
            self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        status = self.status_json()
        self.assertEqual(status["status"], "solved")
        self.assertEqual(status["turn"], 6)
        self.assertEqual(self.next_json()["status"], "stop")
        self.assertEqual(self.next_json()["reason"], "solved")

    def test_unchanged_input_stalls(self):
        res = self.advance("product-manager", "review_intent", "no_progress")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        nxt = self.next_json()
        self.assertEqual(nxt["role"], "product-manager")
        res = self.advance("product-manager", "review_intent", "no_progress")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        status = self.status_json()
        self.assertEqual(status["status"], "stalled")
        self.assertEqual(status["stop_reason"], "stalled")
        self.assertEqual(status["no_progress"], 2)

    def test_max_turns_exhausts(self):
        config = self.tmp / "org" / "loop" / "config.yaml"
        config.write_text(
            config.read_text(encoding="utf-8").replace("max_turns: 6", "max_turns: 2"),
            encoding="utf-8",
        )
        self.assertEqual(
            self.advance("product-manager", "review_intent").returncode, 0
        )
        self.assertEqual(self.advance("architect", "draft_spec").returncode, 0)
        status = self.status_json()
        self.assertEqual(status["status"], "exhausted")
        self.assertEqual(status["turn"], 2)

    def test_review_round_cap_requires_a_human_gate(self):
        config = self.tmp / "org" / "loop" / "config.yaml"
        config.write_text(
            config.read_text(encoding="utf-8").replace("max_turns: 6", "max_turns: 9"),
            encoding="utf-8",
        )
        artifact = self.tmp / "implementation.txt"
        artifact.write_text("v1\n", encoding="utf-8")
        sequence = [
            ("product-manager", "review_intent", "intent-review.md"),
            ("architect", "draft_spec", "spec.md"),
            ("senior-developer", "draft_plan", "plan.md"),
            ("senior-developer", "implement", "implementation.txt"),
            ("security-researcher", "assess_security", "security-1.md"),
        ]
        for role, action, name in sequence:
            if name != "implementation.txt":
                self.write_artifact(name, f"{role}:{action}\n")
            self.assertEqual(
                self.advance(role, action, "progress", artifact=name).returncode,
                0,
            )
        self.set_review("changes", rounds=1)
        self.write_artifact("review-1.md", "first review\n")
        self.assertEqual(
            self.advance(
                "reviewer", "review", "no_progress", artifact="review-1.md"
            ).returncode,
            0,
        )
        artifact.write_text("v2\n", encoding="utf-8")
        for role, action, name in (
            ("senior-developer", "implement", "implementation.txt"),
            ("security-researcher", "assess_security", "security-2.md"),
        ):
            if name != "implementation.txt":
                self.write_artifact(name, f"{role}:{action}\n")
            self.assertEqual(
                self.advance(role, action, "progress", artifact=name).returncode,
                0,
            )
        self.write_artifact("review-2.md", "second review\n")
        self.assertEqual(
            self.advance(
                "reviewer", "review", "no_progress", artifact="review-2.md"
            ).returncode,
            0,
        )
        status = self.status_json()
        self.assertEqual(status["status"], "gate_required")
        self.assertEqual(status["stop_reason"], "gate_required")

    def test_human_gate_stops_before_dispatch(self):
        graph = (self.tmp / "workflow-graph.yaml").read_text(encoding="utf-8")
        graph = graph.replace(
            "  release:\n    stage: deploy\n    artifact: release\n"
            "    gate: release_authorization\n    status: not_started",
            "  release:\n    stage: deploy\n    artifact: release\n"
            "    gate: release_authorization\n    status: pending",
        )
        (self.tmp / "workflow-graph.yaml").write_text(graph, encoding="utf-8")
        result = self.next_json()
        self.assertEqual(result["status"], "stop")
        self.assertEqual(result["reason"], "gate_required")

    def test_blocking_security_finding_routes_back_to_implementation(self):
        for role, action, name in (
            ("product-manager", "review_intent", "intent-review.md"),
            ("architect", "draft_spec", "spec.md"),
            ("senior-developer", "draft_plan", "plan.md"),
            ("senior-developer", "implement", "implementation.txt"),
        ):
            self.write_artifact(name, f"{role}:{action}\n")
            self.assertEqual(
                self.advance(role, action, "progress", artifact=name).returncode,
                0,
            )
        self.assertEqual(self.next_json()["role"], "security-researcher")
        res = subprocess.run(
            [
                sys.executable,
                str(self.tmp / "scripts" / "org_message.py"),
                "--root",
                str(self.tmp),
                "send",
                "--from",
                "security-researcher",
                "--to",
                "senior-developer",
                "--kind",
                "finding",
                "--subject",
                "Unsafe authorization boundary",
                "--severity",
                "high",
                "--blocking",
                "--body",
                "The implementation bypasses the authorization check.",
            ],
            cwd=self.tmp,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.write_artifact("security.md", "blocking finding\n")
        res = self.advance(
            "security-researcher",
            "assess_security",
            "progress",
            artifact="security.md",
        )
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        status = self.status_json()
        self.assertEqual(status["phase"], "implement")
        self.assertEqual(status["status"], "in_progress")
        self.assertEqual(self.next_json()["role"], "senior-developer")

    def test_open_question_dispatches_recipient(self):
        res = subprocess.run(
            [
                sys.executable,
                str(self.tmp / "scripts" / "org_message.py"),
                "--root",
                str(self.tmp),
                "send",
                "--from",
                "senior-developer",
                "--to",
                "architect",
                "--kind",
                "question",
                "--subject",
                "Which boundary?",
                "--body",
                "Please decide.",
            ],
            cwd=self.tmp,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        nxt = self.next_json()
        self.assertEqual(nxt["role"], "architect")
        self.assertEqual(nxt["action"], "answer_message")
        message_path = next((self.tmp / "org" / "messages").glob("*.md"))
        message_id = message_path.stem
        res = subprocess.run(
            [
                sys.executable,
                str(self.tmp / "scripts" / "org_message.py"),
                "--root",
                str(self.tmp),
                "reply",
                "--id",
                message_id,
                "--from",
                "architect",
                "--body",
                "Use the service boundary.",
            ],
            cwd=self.tmp,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        res = self.advance(
            "architect",
            "answer_message",
            artifact=None,
            evidence="org/messages",
        )
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.status_json()["turn"], 1)

    def test_completed_answer_cannot_authorize_another_turn(self):
        res = subprocess.run(
            [
                sys.executable,
                str(self.tmp / "scripts" / "org_message.py"),
                "--root",
                str(self.tmp),
                "send",
                "--from",
                "senior-developer",
                "--to",
                "architect",
                "--kind",
                "question",
                "--subject",
                "Which boundary?",
                "--body",
                "Please decide.",
            ],
            cwd=self.tmp,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        message_id = next((self.tmp / "org" / "messages").glob("*.md")).stem
        res = subprocess.run(
            [
                sys.executable,
                str(self.tmp / "scripts" / "org_message.py"),
                "--root",
                str(self.tmp),
                "reply",
                "--id",
                message_id,
                "--from",
                "architect",
                "--body",
                "Use the service boundary.",
            ],
            cwd=self.tmp,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        res = self.advance(
            "architect",
            "answer_message",
            artifact=None,
            evidence="org/messages",
        )
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)

        res = self.advance(
            "architect",
            "answer_message",
            artifact=None,
            evidence="org/messages",
        )

        self.assertEqual(res.returncode, 1)
        self.assertIn("does not match", res.stderr)
        self.assertEqual(self.status_json()["turn"], 1)

    def test_empty_acceptance_cannot_mark_a_loop_solved(self):
        config = self.tmp / "org" / "loop" / "config.yaml"
        config.write_text(
            config.read_text(encoding="utf-8").replace(
                'acceptance:\n  commands:\n    - python3 -c "pass"\n',
                "acceptance:\n  commands: []\n",
            ),
            encoding="utf-8",
        )
        self.set_review()

        self.assertFalse(
            org_loop._solved(
                self.tmp,
                {
                    "acceptance": [],
                },
                org_loop.load_config(self.tmp),
                [],
            )
        )
        self.assertIn(
            "acceptance",
            " ".join(org_loop.check(self.tmp)).lower(),
        )

    def test_wrong_role_is_rejected(self):
        res = self.advance(
            "architect", "draft_spec", artifact="spec.md", evidence="x"
        )
        self.assertEqual(res.returncode, 1)
        self.assertIn("does not match", res.stderr)

    def test_advance_requires_evidence(self):
        res = self.advance(
            "product-manager", "review_intent", artifact="spec.md", evidence=None
        )
        self.assertEqual(res.returncode, 1)
        self.assertIn("evidence", res.stderr)

    def test_check_detects_tampered_turn(self):
        state_path = self.tmp / "org" / "loop" / "state.yaml"
        text = state_path.read_text(encoding="utf-8").replace("turn: 0", "turn: 3")
        state_path.write_text(text, encoding="utf-8")
        res = self.run_cli("check")
        self.assertEqual(res.returncode, 1)
        self.assertIn("turn", (res.stdout + res.stderr).lower())


if __name__ == "__main__":
    unittest.main()
