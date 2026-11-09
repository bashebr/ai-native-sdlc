#!/usr/bin/env bash
# Test suite for skills/ai-native-sdlc/scripts/init_org.py.
# Usage: bash tests/test_init_org.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO_ROOT/skills/ai-native-sdlc/scripts/init_org.py"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
export PYTHONPYCACHEPREFIX="$tmp/pycache"

fail=0

# --dry-run must not write anything (not even the project dir)
python3 "$SCRIPT" "$tmp/dry" --dry-run >/dev/null 2>&1 || { echo "FAIL: --dry-run exited non-zero"; fail=1; }
[[ -e "$tmp/dry" ]] && { echo "FAIL: --dry-run created the project dir"; fail=1; }

# scaffold
python3 "$SCRIPT" "$tmp/proj" >/dev/null 2>&1 || { echo "FAIL: scaffold exited non-zero"; fail=1; }
for f in org/org-chart.yaml org/status.yaml org/protocol.md \
         org/roles/ceo.md org/roles/orchestrator.md org/roles/product-manager.md \
         org/roles/architect.md org/roles/senior-developer.md \
         org/roles/security-researcher.md org/roles/reviewer.md \
         org/roles/memory-keeper.md \
         org/messages/README.md org/messages/.gitkeep \
         org/memory/README.md org/memory/context.md org/memory/events.jsonl \
         org/intake/README.md org/intake/config.json \
         org/intake/github/.gitkeep org/intake/forms/.gitkeep org/intake/email/.gitkeep \
         org/reviews/README.md scripts/sync_issues.py scripts/org_status.py \
         scripts/intake.py scripts/org_common.py scripts/org_message.py \
         scripts/org_memory.py; do
  [[ -e "$tmp/proj/$f" ]] || { echo "FAIL: missing $f"; fail=1; }
done
python3 -m py_compile "$tmp/proj/scripts/sync_issues.py" || { echo "FAIL: scaffolded sync_issues.py does not compile"; fail=1; }
python3 -m py_compile "$tmp/proj/scripts/org_status.py" || { echo "FAIL: scaffolded org_status.py does not compile"; fail=1; }
python3 -m py_compile "$tmp/proj/scripts/intake.py" || { echo "FAIL: scaffolded intake.py does not compile"; fail=1; }
python3 -m py_compile "$tmp/proj/scripts/org_message.py" || { echo "FAIL: scaffolded org_message.py does not compile"; fail=1; }
python3 -m py_compile "$tmp/proj/scripts/org_memory.py" || { echo "FAIL: scaffolded org_memory.py does not compile"; fail=1; }
grep -q 'Chief Executive Officer' "$tmp/proj/org/roles/ceo.md" || { echo "FAIL: ceo role card content wrong"; fail=1; }
grep -q 'peer_review' "$tmp/proj/org/org-chart.yaml" || { echo "FAIL: org-chart reviewer authority missing"; fail=1; }
grep -q 'review_queue' "$tmp/proj/org/status.yaml" || { echo "FAIL: status.yaml review queue missing"; fail=1; }
grep -q 'pr_merge' "$tmp/proj/org/protocol.md" || { echo "FAIL: protocol gates missing"; fail=1; }
grep -q 'orchestrator' "$tmp/proj/org/org-chart.yaml" || { echo "FAIL: orchestrator missing"; fail=1; }
grep -q 'security-researcher' "$tmp/proj/org/org-chart.yaml" || { echo "FAIL: security role missing"; fail=1; }
grep -q 'memory-keeper' "$tmp/proj/org/status.yaml" || { echo "FAIL: memory keeper missing"; fail=1; }
grep -q 'senior-developer' "$tmp/proj/org/roles/senior-developer.md" || { echo "FAIL: developer card wrong"; fail=1; }

# existing files are skipped; --force overwrites
echo "sentinel" > "$tmp/proj/org/status.yaml"
python3 "$SCRIPT" "$tmp/proj" >/dev/null 2>&1 || { echo "FAIL: re-scaffold exited non-zero"; fail=1; }
grep -q 'sentinel' "$tmp/proj/org/status.yaml" || { echo "FAIL: existing file was overwritten without --force"; fail=1; }
python3 "$SCRIPT" "$tmp/proj" --force >/dev/null 2>&1 || { echo "FAIL: forced scaffold exited non-zero"; fail=1; }
grep -q 'review_queue' "$tmp/proj/org/status.yaml" || { echo "FAIL: --force did not overwrite"; fail=1; }

if [[ $fail -eq 0 ]]; then
  echo "test_init_org: 0 failed"
else
  echo "test_init_org: $fail failed"
fi
exit $fail
