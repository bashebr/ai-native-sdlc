#!/usr/bin/env python3
"""Unit tests for scripts/gate_ledger.py."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills/ai-native-sdlc/scripts"))
import gate_ledger  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def make_ledger() -> tuple[str, str]:
    td = tempfile.mkdtemp()
    return td, os.path.join(td, "ledger.jsonl")


class RecordAndChainTests(unittest.TestCase):
    def test_record_appends_and_hashes_chain(self):
        td, ledger = make_ledger()
        self.addCleanup(lambda: os.system(f"rm -rf {td}"))
        args = gate_ledger.argparse.Namespace(
            ledger=ledger, gate="release_authorization", decision="approved",
            artifact="deploy app v1.2.3", commit="abc123", approver="Ada",
            evidence="REL-1", expires_at=None, id=None,
        )
        self.assertEqual(gate_ledger.cmd_record(args), 0)
        args2 = gate_ledger.argparse.Namespace(**{**vars(args), "id": None})
        self.assertEqual(gate_ledger.cmd_record(args2), 0)
        records = gate_ledger.read_ledger(Path(ledger))
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["prev_hash"], None)
        self.assertEqual(records[1]["prev_hash"], records[0]["hash"])
        self.assertEqual(gate_ledger._verify_chain(records, Path(ledger)), None)
        self.assertEqual(records[0]["id"], "release_authorization-001")
        self.assertEqual(records[1]["id"], "release_authorization-002")

    def test_duplicate_id_rejected(self):
        td, ledger = make_ledger()
        self.addCleanup(lambda: os.system(f"rm -rf {td}"))
        common = dict(ledger=ledger, gate="release_authorization", decision="approved",
                      artifact="x", commit=None, approver="Ada", evidence="e", expires_at=None)
        self.assertEqual(gate_ledger.cmd_record(gate_ledger.argparse.Namespace(**common, id="r-1")), 0)
        self.assertEqual(gate_ledger.cmd_record(gate_ledger.argparse.Namespace(**common, id="r-1")), 1)

    def test_tamper_detected(self):
        td, ledger = make_ledger()
        self.addCleanup(lambda: os.system(f"rm -rf {td}"))
        common = dict(ledger=ledger, gate="release_authorization", decision="approved",
                      artifact="x", commit=None, approver="Ada", evidence="e", expires_at=None)
        gate_ledger.cmd_record(gate_ledger.argparse.Namespace(**common, id="r-1"))
        gate_ledger.cmd_record(gate_ledger.argparse.Namespace(**common, id="r-2"))
        # Rewrite the first record's approver in place: chain must break.
        path = Path(ledger)
        lines = path.read_text(encoding="utf-8").splitlines()
        rec = json.loads(lines[0])
        rec["approver"] = "Mallory"
        lines[0] = json.dumps(rec, sort_keys=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        records = gate_ledger.read_ledger(path)
        err = gate_ledger._verify_chain(records, path)
        self.assertIsNotNone(err)
        self.assertIn("hash does not match content", err)

    def test_verify_rejects_wrong_decision_and_expired(self):
        td, ledger = make_ledger()
        self.addCleanup(lambda: os.system(f"rm -rf {td}"))
        common = dict(ledger=ledger, gate="release_authorization", decision="approved",
                      artifact="x", commit=None, approver="Ada", evidence="e", expires_at=None)
        gate_ledger.cmd_record(gate_ledger.argparse.Namespace(**common, id="r-ok"))
        rej = {**common, "decision": "rejected"}
        gate_ledger.cmd_record(gate_ledger.argparse.Namespace(**rej, id="r-rej"))
        exp = {**common, "expires_at": "2000-01-01T00:00:00Z"}
        gate_ledger.cmd_record(gate_ledger.argparse.Namespace(**exp, id="r-exp"))
        ok = gate_ledger.cmd_verify(gate_ledger.argparse.Namespace(
            ledger=ledger, record="r-ok", require_committed=False, graph=None, require_gates=False))
        self.assertEqual(ok, 0)
        self.assertEqual(gate_ledger.cmd_verify(gate_ledger.argparse.Namespace(
            ledger=ledger, record="r-rej", require_committed=False, graph=None, require_gates=False)), 1)
        self.assertEqual(gate_ledger.cmd_verify(gate_ledger.argparse.Namespace(
            ledger=ledger, record="r-exp", require_committed=False, graph=None, require_gates=False)), 1)
        self.assertEqual(gate_ledger.cmd_verify(gate_ledger.argparse.Namespace(
            ledger=ledger, record="nope", require_committed=False, graph=None, require_gates=False)), 1)

    def test_require_committed(self):
        td = tempfile.mkdtemp()
        self.addCleanup(lambda: os.system(f"rm -rf {td}"))
        subprocess.run(["git", "init", td], check=True, capture_output=True)
        # CI runners have no git identity configured; set one so commits work.
        subprocess.run(["git", "-C", td, "config", "user.email", "test@example.com"], check=True, capture_output=True)
        subprocess.run(["git", "-C", td, "config", "user.name", "Test"], check=True, capture_output=True)
        ledger = os.path.join(td, "ledger.jsonl")
        common = dict(ledger=ledger, gate="release_authorization", decision="approved",
                      artifact="x", commit=None, approver="Ada", evidence="e", expires_at=None)
        gate_ledger.cmd_record(gate_ledger.argparse.Namespace(**common, id="r-1"))
        # Uncommitted: --require-committed must fail.
        self.assertEqual(gate_ledger.cmd_verify(gate_ledger.argparse.Namespace(
            ledger=ledger, record="r-1", require_committed=True, graph=None, require_gates=False)), 1)
        subprocess.run(["git", "add", "-A"], cwd=td, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "ledger"], cwd=td, check=True, capture_output=True)
        self.assertEqual(gate_ledger.cmd_verify(gate_ledger.argparse.Namespace(
            ledger=ledger, record="r-1", require_committed=True, graph=None, require_gates=False)), 0)

    def test_require_gates_from_graph(self):
        td, ledger = make_ledger()
        self.addCleanup(lambda: os.system(f"rm -rf {td}"))
        graph = os.path.join(REPO_ROOT, "skills/ai-native-sdlc/assets/workflow-graph.yaml")
        common = dict(ledger=ledger, gate="release_authorization", decision="approved",
                      artifact="x", commit=None, approver="Ada", evidence="e", expires_at=None)
        gate_ledger.cmd_record(gate_ledger.argparse.Namespace(**common, id="r-1"))
        # Only one of the graph's gates has a record -> must fail with --require-gates.
        self.assertEqual(gate_ledger.cmd_verify(gate_ledger.argparse.Namespace(
            ledger=ledger, record="r-1", require_committed=False, graph=graph, require_gates=True)), 1)

    def test_require_gate_rejects_cross_gate_approval(self):
        td, ledger = make_ledger()
        self.addCleanup(lambda: os.system(f"rm -rf {td}"))
        common = dict(ledger=ledger, decision="approved", artifact="x", commit=None,
                      approver="Ada", evidence="e", expires_at=None)
        gate_ledger.cmd_record(gate_ledger.argparse.Namespace(**common, gate="release_authorization", id="rel-1"))
        gate_ledger.cmd_record(gate_ledger.argparse.Namespace(**common, gate="product_owner_accept", id="int-1"))
        # Matching gate verifies.
        self.assertEqual(gate_ledger.cmd_verify(gate_ledger.argparse.Namespace(
            ledger=ledger, record="rel-1", require_gate="release_authorization",
            require_committed=False, graph=None, require_gates=False)), 0)
        # A valid record for another gate must not satisfy the release gate.
        self.assertEqual(gate_ledger.cmd_verify(gate_ledger.argparse.Namespace(
            ledger=ledger, record="int-1", require_gate="release_authorization",
            require_committed=False, graph=None, require_gates=False)), 1)
        # Omitting --require-gate keeps the previous behavior (backward compatible).
        self.assertEqual(gate_ledger.cmd_verify(gate_ledger.argparse.Namespace(
            ledger=ledger, record="int-1", require_committed=False,
            graph=None, require_gates=False)), 0)


if __name__ == "__main__":
    unittest.main()
