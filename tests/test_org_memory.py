"""Tests for scripts/org_memory.py."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "skills/ai-native-sdlc/scripts"

CHART = """\
version: 1
agents:
  orchestrator: { reports_to: null }
  senior-developer: { reports_to: orchestrator }
"""

STATUS = """\
version: 1
agents:
  orchestrator: { status: idle, assignment: null, last_report: null }
  senior-developer: { status: busy, assignment: plan.md, last_report: null }
review_queue: []
"""


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp))
        (self.tmp / "org" / "messages").mkdir(parents=True)
        (self.tmp / "org" / "memory").mkdir(parents=True)
        (self.tmp / "org" / "org-chart.yaml").write_text(CHART, encoding="utf-8")
        (self.tmp / "org" / "status.yaml").write_text(STATUS, encoding="utf-8")
        (self.tmp / "scripts").mkdir()
        for name in (
            "org_common.py",
            "org_status.py",
            "org_message.py",
            "org_memory.py",
        ):
            shutil.copy2(SCRIPTS / name, self.tmp / "scripts" / name)

    def run_cli(self, *args: str):
        return subprocess.run(
            [sys.executable, str(self.tmp / "scripts" / "org_memory.py"), *args],
            cwd=self.tmp,
            capture_output=True,
            text=True,
        )

    def context(self) -> str:
        return (self.tmp / "org" / "memory" / "context.md").read_text(
            encoding="utf-8"
        )

    def add(self, text="Use an append-only ledger", kind="decision"):
        res = self.run_cli(
            "add",
            "--kind",
            kind,
            "--actor",
            "architect",
            "--text",
            text,
            "--source",
            "spec.md",
        )
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)

    def test_add_digest_is_deterministic(self):
        self.add()
        self.add("Reviewer must be independent", kind="constraint")
        res = self.run_cli("digest")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        first = self.context()
        self.assertIn("Use an append-only ledger", first)
        self.assertIn("Reviewer must be independent", first)
        self.assertIn("senior-developer", first)
        self.assertIn("## Last digest source", first)
        self.assertEqual(self.run_cli("digest").returncode, 0)
        self.assertEqual(self.context(), first)
        self.assertEqual(self.run_cli("check").returncode, 0)

    def test_digest_reports_last_event_source(self):
        self.add()
        self.assertEqual(self.run_cli("digest").returncode, 0)
        text = self.context()
        self.assertIn("## Last digest source", text)
        self.assertIn("mem-0001", text)
        self.assertIn("spec.md", text)

    def test_digest_preserves_curated_notes(self):
        self.add()
        self.assertEqual(self.run_cli("digest").returncode, 0)
        text = self.context() + "\n## Curated notes\nKeep this narrative.\n"
        (self.tmp / "org" / "memory" / "context.md").write_text(
            text, encoding="utf-8"
        )
        self.assertEqual(self.run_cli("digest").returncode, 0)
        self.assertIn("Keep this narrative.", self.context())

    def test_check_detects_stale_context(self):
        self.add()
        self.assertEqual(self.run_cli("digest").returncode, 0)
        self.add("New decision after digest")
        res = self.run_cli("check")
        self.assertEqual(res.returncode, 1)
        self.assertIn("stale", (res.stdout + res.stderr).lower())

    def test_check_rejects_event_without_source(self):
        self.add()
        events = self.tmp / "org" / "memory" / "events.jsonl"
        record = json.loads(events.read_text(encoding="utf-8").splitlines()[0])
        record["source"] = ""
        events.write_text(json.dumps(record) + "\n", encoding="utf-8")
        res = self.run_cli("check")
        self.assertEqual(res.returncode, 1)
        self.assertIn("source", res.stdout + res.stderr)

    def test_check_rejects_event_without_timestamp(self):
        self.add()
        events = self.tmp / "org" / "memory" / "events.jsonl"
        record = json.loads(events.read_text(encoding="utf-8").splitlines()[0])
        record.pop("ts")
        events.write_text(json.dumps(record) + "\n", encoding="utf-8")
        res = self.run_cli("check")
        self.assertEqual(res.returncode, 1)
        self.assertIn("timestamp", res.stdout + res.stderr)

    def test_concurrent_adds_preserve_events_and_ids(self):
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    str(self.tmp / "scripts" / "org_memory.py"),
                    "add",
                    "--kind",
                    "fact",
                    "--actor",
                    "architect",
                    "--text",
                    f"Concurrent event {index}",
                    "--source",
                    f"concurrency-{index}.md",
                ],
                cwd=self.tmp,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for index in range(12)
        ]
        for process in processes:
            stdout, stderr = process.communicate()
            self.assertEqual(process.returncode, 0, stderr + stdout)

        records = [
            json.loads(line)
            for line in (
                self.tmp / "org" / "memory" / "events.jsonl"
            ).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(records), 12)
        self.assertEqual(len({record["id"] for record in records}), 12)
        self.assertEqual(
            {record["text"] for record in records},
            {f"Concurrent event {index}" for index in range(12)},
        )

    def test_check_enforces_size_budget(self):
        self.add()
        self.assertEqual(self.run_cli("digest").returncode, 0)
        path = self.tmp / "org" / "memory" / "context.md"
        path.write_text(self.context() + ("x" * 20000), encoding="utf-8")
        res = self.run_cli("check")
        self.assertEqual(res.returncode, 1)
        self.assertIn("budget", (res.stdout + res.stderr).lower())


if __name__ == "__main__":
    unittest.main()
