"""Tests for scripts/org_common.py."""
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "skills/ai-native-sdlc/scripts"))
import org_common  # noqa: E402


class FrontmatterTests(unittest.TestCase):
    def test_round_trip(self):
        fields = {
            "id": "architect-0001",
            "subject": "Cache: invalidation",
            "requires_response": True,
            "blocking": False,
            "severity": None,
            "tags": ["a", "b"],
        }
        text = org_common.render_message(fields, "## Message\nHello.\n")
        parsed, body = org_common.read_frontmatter(text)
        self.assertEqual(parsed["id"], "architect-0001")
        self.assertEqual(parsed["subject"], "Cache: invalidation")
        self.assertIs(parsed["requires_response"], True)
        self.assertIs(parsed["blocking"], False)
        self.assertIsNone(parsed["severity"])
        self.assertEqual(parsed["tags"], ["a", "b"])
        self.assertEqual(body, "## Message\nHello.")

    def test_agent_names_skip_disabled_and_expand_count(self):
        chart = {
            "agents": {
                "orchestrator": {"reports_to": None},
                "reviewer": {"reports_to": "orchestrator", "count": 2},
                "product-engineer": {"enabled": False},
            }
        }
        self.assertEqual(
            org_common.agent_names(chart),
            ["orchestrator", "reviewer-1", "reviewer-2"],
        )

    def test_atomic_write_replaces_content(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x.txt"
            org_common.atomic_write(path, "one\n")
            org_common.atomic_write(path, "two\n")
            self.assertEqual(path.read_text(encoding="utf-8"), "two\n")


if __name__ == "__main__":
    unittest.main()
