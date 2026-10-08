#!/usr/bin/env bash
# Test suite for skills/ai-native-sdlc/scripts/init_workflow.py.
# Usage: bash tests/test_init.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO_ROOT/skills/ai-native-sdlc/scripts/init_workflow.py"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
export PYTHONPYCACHEPREFIX="$tmp/pycache"

fail=0

# --dry-run must not write anything (not even the project dir)
python3 "$SCRIPT" "$tmp/dry" --name "Dry Run" --dry-run >/dev/null 2>&1 || { echo "FAIL: --dry-run exited non-zero"; fail=1; }
[[ -e "$tmp/dry" ]] && { echo "FAIL: --dry-run created the project dir"; fail=1; }

# codex scaffold
python3 "$SCRIPT" "$tmp/proj" --name "Smoke Test" --framework codex >/dev/null 2>&1 || { echo "FAIL: scaffold exited non-zero"; fail=1; }
for f in intent/intent.md AGENTS.md REVIEW.md bands.yaml workflow-graph.yaml .gitignore hooks/production-gate.sh hooks/pre-commit.example evals/example.md evals/README.md scripts/gate_ledger.py scripts/run_evals.py scripts/detect_bands.py scripts/workflow_state.py scripts/check_plan_sync.py scripts/scan_secrets.py gates/README.md .secretsignore; do
  [[ -e "$tmp/proj/$f" ]] || { echo "FAIL: missing $f"; fail=1; }
done
python3 -m py_compile "$tmp/proj/scripts/gate_ledger.py" || { echo "FAIL: scaffolded gate_ledger.py does not compile"; fail=1; }
python3 -m py_compile "$tmp/proj/scripts/workflow_state.py" || { echo "FAIL: scaffolded workflow_state.py does not compile"; fail=1; }
python3 -m py_compile "$tmp/proj/scripts/check_plan_sync.py" || { echo "FAIL: scaffolded check_plan_sync.py does not compile"; fail=1; }
python3 -m py_compile "$tmp/proj/scripts/scan_secrets.py" || { echo "FAIL: scaffolded scan_secrets.py does not compile"; fail=1; }
grep -q '# Smoke Test' "$tmp/proj/intent/intent.md" || { echo "FAIL: --name not interpolated"; fail=1; }
grep -q 'AGENTS.md' "$tmp/proj/AGENTS.md" || { echo "FAIL: codex variant header wrong"; fail=1; }
[[ -f "$tmp/proj/CLAUDE.md" ]] && { echo "FAIL: codex scaffold also wrote CLAUDE.md"; fail=1; }
[[ -x "$tmp/proj/hooks/production-gate.sh" ]] || { echo "FAIL: hook not executable"; fail=1; }
grep -q 'release authorization' "$tmp/proj/hooks/production-gate.sh" || { echo "FAIL: hook content wrong"; fail=1; }

# claude scaffold (default framework)
python3 "$SCRIPT" "$tmp/proj2" --name "Claude Project" >/dev/null 2>&1 || { echo "FAIL: claude scaffold exited non-zero"; fail=1; }
[[ -f "$tmp/proj2/CLAUDE.md" ]] || { echo "FAIL: claude scaffold missing CLAUDE.md"; fail=1; }
[[ -f "$tmp/proj2/AGENTS.md" ]] && { echo "FAIL: claude scaffold also wrote AGENTS.md"; fail=1; }

# idempotent re-run: no overwrite without --force, exit 0
before="$(head -1 "$tmp/proj/intent/intent.md")"
python3 "$SCRIPT" "$tmp/proj" --name "Other Name" >/dev/null 2>&1 || { echo "FAIL: re-run exited non-zero"; fail=1; }
after="$(head -1 "$tmp/proj/intent/intent.md")"
[[ "$before" == "$after" ]] || { echo "FAIL: re-run overwrote existing intent without --force"; fail=1; }

# --name validation: control characters rejected
python3 "$SCRIPT" "$tmp/bad" --name "$(printf 'Bad\nName')" >/dev/null 2>&1 && { echo "FAIL: newline in --name accepted"; fail=1; }
python3 "$SCRIPT" "$tmp/bad" --name "" >/dev/null 2>&1 && { echo "FAIL: empty --name accepted"; fail=1; }

if [[ "$fail" -eq 0 ]]; then
  echo "init: all checks passed"
else
  echo "init: FAILED"
fi
[[ "$fail" -eq 0 ]]
