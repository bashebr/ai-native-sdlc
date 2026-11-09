"""Safety and shape tests for the agent-org GitHub Actions example."""
import unittest
from pathlib import Path

WORKFLOW = (
    Path(__file__).resolve().parent.parent
    / "skills/ai-native-sdlc/assets/org-loop.yml.example"
)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_triggers_and_bounds(self):
        self.assertIn("workflow_dispatch:", self.text)
        self.assertIn("issues:", self.text)
        self.assertIn("concurrency:", self.text)
        self.assertIn("timeout-minutes: 30", self.text)
        self.assertIn("agent-loop", self.text)

    def test_least_privilege(self):
        self.assertIn("contents: write", self.text)
        self.assertIn("pull-requests: write", self.text)
        self.assertIn("issues: read", self.text)

    def test_one_deterministic_turn(self):
        self.assertIn("org_loop.py next", self.text)
        self.assertIn("org_loop.py advance", self.text)
        self.assertIn("org_message.py check", self.text)
        self.assertIn("org_memory.py check", self.text)
        self.assertIn("org_loop.py check", self.text)

    def test_manual_work_item_is_validated(self):
        self.assertIn('default: ""', self.text)
        self.assertIn("Check selected work item", self.text)
        self.assertIn("org_loop.py status --json", self.text)
        self.assertIn("does not match loop state", self.text)

    def test_never_merges_or_releases(self):
        self.assertNotIn("gh pr merge", self.text)
        self.assertNotIn("git push origin main", self.text)
        self.assertNotIn("production-gate.sh", self.text)


if __name__ == "__main__":
    unittest.main()
