"""Tests for scripts/sync_issues.py (no network, no gh CLI required)."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "skills" / "ai-native-sdlc" / "scripts" / "sync_issues.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("sync_issues", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync = _load_module()


def gh_list_stdout() -> str:
    return json.dumps(
        [
            {
                "number": 3,
                "title": "Add export",
                "body": "Users want CSV export.",
                "labels": [{"name": "ai-native"}],
                "createdAt": "2026-08-28T10:00:00Z",
                "author": {"login": "alice"},
            },
            {
                "number": 1,
                "title": "Old issue",
                "body": "Already seen.",
                "labels": [],
                "createdAt": "2026-08-01T10:00:00Z",
                "author": {"login": "bob"},
            },
        ]
    )


class SyncIssuesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / "config.json"
        self.config.write_text(
            json.dumps(
                {
                    "github": {
                        "repos": ["acme/app"],
                        "labels": [],
                        "state_file": str(self.root / ".state.json"),
                        "output_dir": str(self.root / "github"),
                    }
                }
            ),
            encoding="utf-8",
        )

    @patch.object(sync, "_gh", return_value=gh_list_stdout())
    def test_pull_writes_new_records_then_dedupes(self, mock_gh) -> None:
        res = sync._pull(self.config, dry_run=False)
        self.assertEqual(res, 0)
        records = sorted((self.root / "github").glob("*.md"))
        self.assertEqual([p.name for p in records], ["acme-app-1.md", "acme-app-3.md"])
        content = (self.root / "github" / "acme-app-3.md").read_text(encoding="utf-8")
        self.assertIn("Add export", content)
        self.assertIn("record_id: acme/app-3", content)
        self.assertIn("## Status", content)
        self.assertIn("ai-native", content)
        state = json.loads((self.root / ".state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["acme/app"], 3)

        # Second run with the same issues: nothing new.
        res = sync._pull(self.config, dry_run=False)
        self.assertEqual(res, 0)
        self.assertEqual(len(list((self.root / "github").glob("*.md"))), 2)
        state = json.loads((self.root / ".state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["acme/app"], 3)

    @patch.object(sync, "_gh")
    def test_pull_dry_run_writes_nothing(self, mock_gh) -> None:
        res = sync._pull(self.config, dry_run=True)
        self.assertEqual(res, 0)
        mock_gh.assert_not_called()
        self.assertFalse((self.root / "github").exists())
        self.assertFalse((self.root / ".state.json").exists())

    @patch.object(sync, "_gh", return_value="https://github.com/acme/app/issues/9\n")
    def test_push_builds_gh_command(self, mock_gh) -> None:
        res = sync._push(
            "Add CSV export",
            "Users want CSV export.",
            "acme/app",
            ["feature", "ai-native"],
            dry_run=False,
        )
        self.assertEqual(res, 0)
        cmd = mock_gh.call_args.args[0]
        self.assertEqual(cmd[0], "issue")
        self.assertIn("issue", cmd)
        self.assertIn("create", cmd)
        self.assertIn("Add CSV export", cmd)
        self.assertIn("--label", cmd)
        self.assertIn("ai-native", cmd)
        self.assertIn("--repo", cmd)
        self.assertIn("acme/app", cmd)

    @patch.object(sync, "_gh")
    def test_push_dry_run_creates_nothing(self, mock_gh) -> None:
        res = sync._push("T", "B", None, [], dry_run=True)
        self.assertEqual(res, 0)
        mock_gh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
