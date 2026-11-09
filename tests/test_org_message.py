"""Tests for scripts/org_message.py."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "skills/ai-native-sdlc/scripts"
sys.path.insert(0, str(SCRIPTS))

import org_common  # noqa: E402
import org_message  # noqa: E402

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


class MessageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp))
        (self.tmp / "org" / "messages").mkdir(parents=True)
        (self.tmp / "org" / "org-chart.yaml").write_text(CHART, encoding="utf-8")
        (self.tmp / "scripts").mkdir()
        for name in ("org_common.py", "org_message.py", "org_status.py"):
            shutil.copy2(SCRIPTS / name, self.tmp / "scripts" / name)

    def run_cli(self, *args: str):
        return subprocess.run(
            [sys.executable, str(self.tmp / "scripts" / "org_message.py"), *args],
            cwd=self.tmp,
            capture_output=True,
            text=True,
        )

    def messages(self):
        return sorted((self.tmp / "org" / "messages").glob("*.md"))

    def test_send_inbox_reply_close(self):
        res = self.run_cli(
            "send", "--from", "senior-developer", "--to", "architect",
            "--kind", "question", "--subject", "Cache boundary",
            "--body", "## Message\nWhich layer owns invalidation?",
            "--artifact", "spec.md",
        )
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(len(self.messages()), 1)
        original = self.messages()[0]
        self.assertTrue(original.name.startswith("senior-developer-"))
        text = original.read_text(encoding="utf-8")
        self.assertIn("requires_response: true", text)
        self.assertIn("status: open", text)

        res = self.run_cli("inbox", "--agent", "architect")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertIn("Cache boundary", res.stdout)

        msg_id = original.stem
        res = self.run_cli(
            "reply", "--id", msg_id, "--from", "architect",
            "--body", "## Message\nThe repository layer.",
        )
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(len(self.messages()), 2)
        self.assertIn("status: answered", original.read_text(encoding="utf-8"))

        res = self.run_cli("close", "--id", msg_id, "--by", "senior-developer")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertIn("status: closed", original.read_text(encoding="utf-8"))

    def test_invalid_party_is_rejected(self):
        res = self.run_cli(
            "send", "--from", "senior-developer", "--to", "ghost",
            "--kind", "question", "--subject", "x", "--body", "y",
        )
        self.assertEqual(res.returncode, 1)
        self.assertIn("ghost", res.stderr)

    def test_ids_are_per_sender_sequences(self):
        for subject in ("one", "two"):
            res = self.run_cli(
                "send", "--from", "senior-developer", "--to", "architect",
                "--kind", "question", "--subject", subject, "--body", "y",
            )
            self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        names = [p.stem for p in self.messages()]
        self.assertEqual(names, ["senior-developer-0001", "senior-developer-0002"])

    def test_check_detects_missing_reply_target(self):
        res = self.run_cli(
            "send", "--from", "architect", "--to", "senior-developer",
            "--kind", "answer", "--subject", "x", "--body", "y",
            "--in-reply-to", "missing-0001",
        )
        self.assertEqual(res.returncode, 1)
        self.assertIn("missing-0001", res.stderr)

    def test_check_rejects_malformed_frontmatter(self):
        path = self.tmp / "org" / "messages" / "senior-developer-0001.md"
        path.write_text(
            "---\n"
            "id: senior-developer-0001\n"
            "from: senior-developer\n"
            "to: architect\n"
            "kind: question\n"
            "subject: Missing closing delimiter\n"
            "status: open\n"
            "created_at: 2026-11-08T10:00:00Z\n"
            "\n"
            "Body without a closing delimiter.\n",
            encoding="utf-8",
        )

        res = self.run_cli("check")

        self.assertEqual(res.returncode, 1, res.stderr + res.stdout)
        self.assertIn("malformed frontmatter", res.stdout)

    def test_check_ignores_message_bus_readme(self):
        readme = self.tmp / "org" / "messages" / "README.md"
        readme.write_text("# Agent messages\n\nHuman documentation.\n", encoding="utf-8")

        res = self.run_cli("check")

        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertIn("0 violation", res.stdout)

    def test_create_message_never_publishes_partial_file(self):
        path = self.tmp / "org" / "messages" / "senior-developer-0001.md"
        with mock.patch.object(
            org_common.os, "link", side_effect=OSError("simulated publish failure")
        ):
            with self.assertRaises(OSError):
                org_message._create_message(path, "complete message\n")

        self.assertFalse(path.exists())
        self.assertEqual(list(path.parent.glob(f".{path.name}.*")), [])

    def test_blocking_finding_requires_severity(self):
        res = self.run_cli(
            "send", "--from", "security-researcher", "--to", "orchestrator",
            "--kind", "finding", "--subject", "Auth bypass",
            "--body", "y", "--blocking",
        )
        self.assertEqual(res.returncode, 1)
        self.assertIn("severity", res.stderr)

    def test_high_finding_is_blocking_by_default(self):
        res = self.run_cli(
            "send", "--from", "security-researcher", "--to", "orchestrator",
            "--kind", "finding", "--subject", "Auth bypass",
            "--body", "y", "--severity", "high",
        )
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        text = self.messages()[0].read_text(encoding="utf-8")
        self.assertIn("blocking: true", text)

    def test_blocking_finding_cannot_close_without_evidence(self):
        res = self.run_cli(
            "send", "--from", "security-researcher", "--to", "orchestrator",
            "--kind", "finding", "--subject", "Auth bypass",
            "--body", "y", "--severity", "critical",
        )
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        res = self.run_cli(
            "close", "--id", self.messages()[0].stem,
            "--by", "security-researcher",
        )
        self.assertEqual(res.returncode, 1)
        self.assertIn("evidence", res.stderr)


if __name__ == "__main__":
    unittest.main()
