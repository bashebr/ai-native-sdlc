"""Tests for scripts/sync_issues.py (no network, no gh CLI required)."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
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


def many_issues(start: int, count: int) -> str:
    return json.dumps(
        [
            {
                "number": n,
                "title": f"Issue {n}",
                "body": "body",
                "labels": [],
                "createdAt": "2026-08-29T00:00:00Z",
                "author": {"login": "alice"},
            }
            for n in range(start, start + count)
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
        self.assertEqual(state["acme/app"], [1, 3])

        # Second run with the same issues: nothing new.
        res = sync._pull(self.config, dry_run=False)
        self.assertEqual(res, 0)
        self.assertEqual(len(list((self.root / "github").glob("*.md"))), 2)
        state = json.loads((self.root / ".state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["acme/app"], [1, 3])

    @patch.object(sync, "_gh")
    def test_pull_fetches_all_open_issues_in_one_call(self, mock_gh) -> None:
        # `gh issue list` paginates internally up to --limit; it has no --page flag.
        # More than 100 issues, including a low-numbered one, must all be recorded.
        mock_gh.return_value = json.dumps(
            json.loads(many_issues(101, 100))
            + [{"number": 5, "title": "Low issue", "body": "body", "labels": [],
                "createdAt": "2026-08-29T00:00:00Z", "author": {"login": "bob"}}]
        )
        res = sync._pull(self.config, dry_run=False)
        self.assertEqual(res, 0)
        records = [p.name for p in (self.root / "github").glob("*.md")]
        self.assertIn("acme-app-5.md", records)
        self.assertEqual(len(records), 101)
        self.assertEqual(mock_gh.call_count, 1)
        cmd = mock_gh.call_args.args[0]
        self.assertNotIn("--page", cmd)
        self.assertEqual(cmd[cmd.index("--limit") + 1], str(sync.ISSUE_LIMIT))

    @patch.object(sync, "_gh")
    def test_pull_warns_when_the_limit_is_reached(self, mock_gh) -> None:
        mock_gh.return_value = many_issues(1, 3)
        with patch.object(sync, "ISSUE_LIMIT", 3), patch("sys.stderr") as err:
            self.assertEqual(sync._pull(self.config, dry_run=False), 0)
        written = "".join(c.args[0] for c in err.write.call_args_list)
        self.assertIn("limit", written)

    @patch.object(sync, "_gh", return_value=gh_list_stdout())
    def test_pull_accepts_repo_urls_and_names_records_by_owner_repo(self, mock_gh) -> None:
        cfg = json.loads(self.config.read_text(encoding="utf-8"))
        cfg["github"]["repos"] = ["https://github.com/acme/app.git"]
        self.config.write_text(json.dumps(cfg), encoding="utf-8")
        self.assertEqual(sync._pull(self.config, dry_run=False), 0)
        records = sorted(p.name for p in (self.root / "github").glob("*.md"))
        self.assertEqual(records, ["acme-app-1.md", "acme-app-3.md"])
        state = json.loads((self.root / ".state.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(state), ["acme/app"])
        cmd = mock_gh.call_args.args[0]
        self.assertEqual(cmd[cmd.index("--repo") + 1], "acme/app")

    def test_repo_slug(self) -> None:
        for raw in ("acme/app", "https://github.com/acme/app", "https://github.com/acme/app.git",
                    "github.com/acme/app/", "git@github.com:acme/app.git"):
            self.assertEqual(sync._repo_slug(raw), "acme/app", raw)

    @patch.object(sync, "_gh", return_value=gh_list_stdout())
    def test_pull_resolves_relative_paths_against_project_root(self, mock_gh) -> None:
        config = self.root / "relative-config.json"
        config.write_text(
            json.dumps(
                {
                    "github": {
                        "repos": ["acme/app"],
                        "labels": [],
                        "state_file": "org/intake/.state.json",
                        "output_dir": "org/intake/github",
                    }
                }
            ),
            encoding="utf-8",
        )
        with patch.object(sync, "PROJECT_ROOT", self.root):
            res = sync._pull(config, dry_run=False)
        self.assertEqual(res, 0)
        self.assertTrue((self.root / "org" / "intake" / ".state.json").is_file())
        self.assertTrue((self.root / "org" / "intake" / "github" / "acme-app-3.md").is_file())

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


@unittest.skipUnless(shutil.which("gh"), "gh CLI not installed")
class GhFlagTests(unittest.TestCase):
    """Every flag the script passes to gh must exist in the installed gh.

    The other tests mock _gh, so they can't catch an invented flag (a past bug:
    `gh issue list --page`, which gh doesn't have).
    """

    def flags_in_help(self, *sub: str) -> set[str]:
        out = subprocess.run(["gh", *sub, "--help"], capture_output=True, text=True).stdout
        return set(re.findall(r"--[a-z][a-z-]*", out))

    def used_flags(self, call_args) -> set[str]:
        return {a for a in call_args if a.startswith("--")}

    @patch.object(sync, "_gh", return_value="[]")
    def test_pull_uses_only_real_gh_issue_list_flags(self, mock_gh) -> None:
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "config.json"
            cfg.write_text(json.dumps({"github": {"repos": ["acme/app"], "labels": ["x"],
                "state_file": str(Path(d) / "s.json"), "output_dir": str(Path(d) / "gh")}}), encoding="utf-8")
            sync._pull(cfg, dry_run=False)
        used = self.used_flags(mock_gh.call_args.args[0])
        self.assertEqual(used - self.flags_in_help("issue", "list"), set())

    @patch.object(sync, "_gh", return_value="https://github.com/acme/app/issues/9\n")
    def test_push_uses_only_real_gh_issue_create_flags(self, mock_gh) -> None:
        sync._push("T", "B", "acme/app", ["x"], dry_run=False)
        used = self.used_flags(mock_gh.call_args.args[0])
        self.assertEqual(used - self.flags_in_help("issue", "create"), set())
