#!/usr/bin/env bash
# Test suite for skills/ai-native-sdlc/assets/production-gate.sh.
# Usage: bash tests/test_gate.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GATE="$REPO_ROOT/skills/ai-native-sdlc/assets/production-gate.sh"

pass=0
fail=0

# check <name> <expected-exit> <env-assignments...> -- <args...>
# env assignments like RELEASE_APPROVAL=REL-42 are passed as VAR=value.
check() {
  local name="$1" want="$2"
  shift 2
  local envs=()
  while [[ "$1" != "--" ]]; do envs+=("$1"); shift; done
  shift
  local out code
  out="$(env "${envs[@]}" bash "$GATE" "$@" 2>&1)"
  code=$?
  if [[ "$code" -eq "$want" ]]; then
    pass=$((pass + 1))
    echo "PASS: $name"
  else
    fail=$((fail + 1))
    echo "FAIL: $name (exit $code, want $want)"
    echo "      output: $out"
  fi
}

# --- must BLOCK (exit 2): production deploys without authorization ---
check "kubectl deploy --env=production" 2 -- "kubectl deploy --env=production"
check "terraform apply -target=module.production (was a bypass)" 2 -- "terraform apply -target=module.production"
check "helm upgrade prod-app (was a bypass)" 2 -- "helm upgrade prod-app"
check "kubectl apply -n prod -f deploy.yaml" 2 -- "kubectl apply -n prod -f deploy.yaml"
check "git push triggers no deploy gate when cmd is deploy+production word combo" 2 -- "make deploy production"
# --- read-only tokens must not smuggle a production deploy past the gate ---
check "deploy && echo (read-only bypass)" 2 -- "kubectl apply -f production.yaml && echo done"
check "deploy piped through grep (read-only bypass)" 2 -- "helm upgrade prod-app 2>&1 | grep -v skip"
check "deploy with 'cat' substring in path (read-only word boundary)" 2 -- "kubectl apply -f production/catalogue.yaml"
check "deploy with 'head' substring in arg (read-only word boundary)" 2 -- "helm upgrade prod-app --set host=headers.example"
check "multi-line deploy then echo" 2 -- "$(printf 'kubectl apply -f production.yaml\necho done')"

# --- must ALLOW (exit 0): not deploys, or read-only ---
check "cat docs/production-deploy.md (read, was a false positive)" 0 -- "cat docs/production-deploy.md"
check "undeploy production (word boundary)" 0 -- "undeploy production"
check "make deploy 2>&1 (no prod context)" 0 -- "make deploy 2>&1"
check "kubectl get pods -n production (read-only kubectl)" 0 -- "kubectl get pods -n production"
check "terraform plan -target=module.production (read-only terraform)" 0 -- "terraform plan -target=module.production"
check "helm list -n production (read-only helm)" 0 -- "helm list -n production"
check "grep -r deploy docs/production/" 0 -- "grep -r deploy docs/production/"
check "unrelated command" 0 -- "pip install requests"

# --- approval present: production deploy allowed ---
check "approved kubectl deploy (no expiry)" 0 RELEASE_APPROVAL=REL-42 -- "kubectl deploy --env=production"
check "approved helm upgrade (future expiry)" 0 RELEASE_APPROVAL=REL-42 RELEASE_APPROVAL_EXPIRY=2030-01-01T00:00:00Z -- "helm upgrade prod-app"
check "approved deploy with epoch expiry" 0 RELEASE_APPROVAL=REL-42 RELEASE_APPROVAL_EXPIRY=4102444800 -- "terraform apply -target=module.production"

# --- expired approval must BLOCK ---
check "expired ISO-8601 approval" 2 RELEASE_APPROVAL=REL-42 RELEASE_APPROVAL_EXPIRY=2000-01-01T00:00:00Z -- "kubectl deploy --env=production"
check "expired epoch approval" 2 RELEASE_APPROVAL=REL-42 RELEASE_APPROVAL_EXPIRY=1 -- "kubectl deploy --env=production"
check "unparseable expiry fails closed" 2 RELEASE_APPROVAL=REL-42 RELEASE_APPROVAL_EXPIRY=not-a-date -- "kubectl deploy --env=production"

# --- hook stdin mode (JSON payload, jq-style) ---
json_block="$(printf '{"tool_input":{"command":"kubectl deploy --env=production"}}' | RELEASE_APPROVAL='' bash "$GATE" 2>&1; echo "rc=$?")"
if [[ "$json_block" == *"BLOCK"* ]]; then
  pass=$((pass + 1)); echo "PASS: stdin JSON mode blocks without approval"
else
  fail=$((fail + 1)); echo "FAIL: stdin JSON mode (got: $json_block)"
fi
json_allow="$(printf '{"tool_input":{"command":"cat docs/production-deploy.md"}}' | RELEASE_APPROVAL='' bash "$GATE" 2>&1; echo "rc=$?")"
if [[ "$json_allow" == "ALLOW"* ]]; then
  pass=$((pass + 1)); echo "PASS: stdin JSON mode allows read-only"
else
  fail=$((fail + 1)); echo "FAIL: stdin JSON mode read-only (got: $json_allow)"
fi

# --- ledger-backed approvals (RELEASE_APPROVAL=ledger:<id>) ---
LEDGER_TMP="$(mktemp -d)"
LEDGER_FILE="$LEDGER_TMP/ledger.jsonl"
git -C "$LEDGER_TMP" init -q
# CI runners have no git identity configured; set one so commits work.
git -C "$LEDGER_TMP" config user.email test@example.com
git -C "$LEDGER_TMP" config user.name Test
LEDGER_SCRIPT="$REPO_ROOT/skills/ai-native-sdlc/scripts/gate_ledger.py"
python3 "$LEDGER_SCRIPT" --ledger "$LEDGER_FILE" record --gate release_authorization \
  --artifact "deploy app v1.2.3" --commit cf13ec7 --approver "Ada" --evidence "REL-42" \
  --id release_authorization-001 >/dev/null 2>&1
# Uncommitted ledger must block (require-committed is enforced by the hook).
check "ledger: uncommitted ledger blocks" 2 RELEASE_APPROVAL=ledger:release_authorization-001 GATE_LEDGER_SCRIPT="$LEDGER_SCRIPT" GATE_LEDGER_FILE="$LEDGER_FILE" -- "kubectl deploy --env=production"
git -C "$LEDGER_TMP" add -A && git -C "$LEDGER_TMP" commit -q -m "gates: record release authorization"
# Committed, valid record allows the deploy.
check "ledger: committed valid record allows" 0 RELEASE_APPROVAL=ledger:release_authorization-001 GATE_LEDGER_SCRIPT="$LEDGER_SCRIPT" GATE_LEDGER_FILE="$LEDGER_FILE" -- "kubectl deploy --env=production"
# An approved record for a *different* gate must not authorize a release.
python3 "$LEDGER_SCRIPT" --ledger "$LEDGER_FILE" record --gate product_owner_accept \
  --artifact "intent accepted" --approver "PM" --evidence "INT-1" \
  --id product_owner_accept-001 >/dev/null 2>&1
git -C "$LEDGER_TMP" add -A && git -C "$LEDGER_TMP" commit -q -m "gates: record intent acceptance"
check "ledger: cross-gate record blocks (no approval reuse)" 2 RELEASE_APPROVAL=ledger:product_owner_accept-001 GATE_LEDGER_SCRIPT="$LEDGER_SCRIPT" GATE_LEDGER_FILE="$LEDGER_FILE" -- "kubectl deploy --env=production"
# Unknown record id blocks.
check "ledger: unknown id blocks" 2 RELEASE_APPROVAL=ledger:release_authorization-999 GATE_LEDGER_SCRIPT="$LEDGER_SCRIPT" GATE_LEDGER_FILE="$LEDGER_FILE" -- "helm upgrade prod-app"
# A tampered ledger (rewrite approver, same hash) blocks.
python3 - <<PYEOF
import json
from pathlib import Path
p = Path("$LEDGER_FILE")
lines = p.read_text(encoding="utf-8").splitlines()
rec = json.loads(lines[0]); rec["approver"] = "Mallory"
lines[0] = json.dumps(rec, sort_keys=True)
p.write_text("\n".join(lines) + "\n", encoding="utf-8")
PYEOF
git -C "$LEDGER_TMP" add -A && git -C "$LEDGER_TMP" commit -q -m "gates: tamper"
check "ledger: tampered record blocks" 2 RELEASE_APPROVAL=ledger:release_authorization-001 GATE_LEDGER_SCRIPT="$LEDGER_SCRIPT" GATE_LEDGER_FILE="$LEDGER_FILE" -- "kubectl deploy --env=production"
rm -rf "$LEDGER_TMP"

echo
echo "gate: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
