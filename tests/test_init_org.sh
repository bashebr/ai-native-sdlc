#!/usr/bin/env bash
# Test suite for skills/ai-native-sdlc/scripts/init_org.py.
# Usage: bash tests/test_init_org.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO_ROOT/skills/ai-native-sdlc/scripts/init_org.py"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

fail=0

# --dry-run must not write anything (not even the project dir)
python3 "$SCRIPT" "$tmp/dry" --dry-run >/dev/null 2>&1 || { echo "FAIL: --dry-run exited non-zero"; fail=1; }
[[ -e "$tmp/dry" ]] && { echo "FAIL: --dry-run created the project dir"; fail=1; }

# scaffold
python3 "$SCRIPT" "$tmp/proj" >/dev/null 2>&1 || { echo "FAIL: scaffold exited non-zero"; fail=1; }
for f in org/org-chart.yaml org/status.yaml org/protocol.md \
         org/roles/ceo.md org/roles/cto.md org/roles/product-manager.md \
         org/roles/product-engineer.md org/roles/engineer.md org/roles/reviewer.md \
         org/intake/README.md org/intake/config.json \
         org/intake/github/.gitkeep org/intake/forms/.gitkeep org/intake/email/.gitkeep \
         org/reviews/README.md scripts/sync_issues.py; do
  [[ -e "$tmp/proj/$f" ]] || { echo "FAIL: missing $f"; fail=1; }
done
python3 -m py_compile "$tmp/proj/scripts/sync_issues.py" || { echo "FAIL: scaffolded sync_issues.py does not compile"; fail=1; }
grep -q 'Chief Executive Officer' "$tmp/proj/org/roles/ceo.md" || { echo "FAIL: ceo role card content wrong"; fail=1; }
grep -q 'peer_review' "$tmp/proj/org/org-chart.yaml" || { echo "FAIL: org-chart reviewer authority missing"; fail=1; }
grep -q 'review_queue' "$tmp/proj/org/status.yaml" || { echo "FAIL: status.yaml review queue missing"; fail=1; }
grep -q 'pr_merge' "$tmp/proj/org/protocol.md" || { echo "FAIL: protocol gates missing"; fail=1; }

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
