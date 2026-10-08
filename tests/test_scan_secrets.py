#!/usr/bin/env python3
"""Unit tests for scripts/scan_secrets.py."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "skills/ai-native-sdlc/scripts/scan_secrets.py"
sys.path.insert(0, str(REPO_ROOT / "skills/ai-native-sdlc/scripts"))
import scan_secrets  # noqa: E402


def run(args, cwd=None):
    res = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    return res.returncode, res.stdout + res.stderr


def git(td, *args):
    subprocess.run(["git", "-C", td, *args], check=True, capture_output=True)


class PatternTests(unittest.TestCase):
    def test_detects_common_secret_types(self):
        aws = "AKIA" + "1234567890ABCDEF"
        gh = "ghp_" + "A1" * 18
        self.assertIn("aws-access-key-id", scan_secrets.scan_line(f"AWS_KEY={aws}"))
        self.assertIn("github-token", scan_secrets.scan_line(f"token={gh}"))
        self.assertIn(
            "private-key-block",
            scan_secrets.scan_line("-----BEGIN " + "RSA PRIVATE KEY-----"),
        )

    def test_placeholder_and_suppression_skipped(self):
        self.assertEqual([], scan_secrets.scan_line('api_key = "changeme-example-value"'))
        self.assertEqual(
            [], scan_secrets.scan_line('api_key = "s3cr3tV4lu3LongEnough"  # nosecret')
        )

    def test_quoted_assignment_detected(self):
        value = "Zx9Qw8Er7Ty6Ui5Op4As3Df2Gh1Jk0Lm"
        self.assertIn(
            "quoted-secret-assignment",
            scan_secrets.scan_line(f'client_secret: "{value}"'),
        )

    def test_entropy_is_opt_in(self):
        blob = "Zx9Qw8Er7Ty6Ui5Op4As3Df2Gh1Jk0Lm"
        self.assertEqual([], scan_secrets.scan_line(f"blob = {blob}"))
        self.assertIn("high-entropy-token", scan_secrets.scan_line(f"blob = {blob}", entropy=True))


class CliTests(unittest.TestCase):
    def test_path_scan_flags_and_redacts(self):
        with tempfile.TemporaryDirectory() as td:
            secret = "sk_live_" + "A1" * 12
            (Path(td) / "cfg.txt").write_text(f'stripe = "{secret}"\n', encoding="utf-8")
            code, out = run(["--path", td])
            self.assertEqual(code, 1)
            self.assertIn("stripe-live-key", out)
            self.assertNotIn(secret, out, "the matched value must never be printed")

    def test_clean_tree_exits_zero(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "ok.txt").write_text("hello world\n", encoding="utf-8")
            code, out = run(["--path", td])
            self.assertEqual(code, 0)
            self.assertIn("0 finding", out)

    def test_allowlist_config_skips_paths(self):
        with tempfile.TemporaryDirectory() as td:
            secret = "AKIA" + "1234567890ABCDEF"
            (Path(td) / "cfg.py").write_text(f'x = "{secret}"\n', encoding="utf-8")
            ignore = Path(td) / "ignore.txt"
            ignore.write_text("cfg.py\n", encoding="utf-8")
            code, _ = run(["--path", td, "--config", str(ignore)])
            self.assertEqual(code, 0)

    def test_staged_diff_flags_new_secret(self):
        with tempfile.TemporaryDirectory() as td:
            git(td, "init", "-q")
            target = Path(td) / "f.txt"
            target.write_text("ok = 1\n", encoding="utf-8")
            git(td, "add", "-A")
            git(td, "-c", "user.email=t@e.com", "-c", "user.name=T", "commit", "-q", "-m", "base")
            secret = "github_pat_" + "A" * 30
            target.write_text("ok = 1\n" + f'token = "{secret}"\n', encoding="utf-8")
            git(td, "add", "-A")
            code, out = run(["--repo", td, "--staged"])
            self.assertEqual(code, 1)
            self.assertIn("github-fine-grained-pat", out)
            self.assertNotIn(secret, out)

    def test_no_mode_is_usage_error(self):
        code, _ = run([])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
