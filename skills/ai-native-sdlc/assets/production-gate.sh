#!/usr/bin/env bash
# Release gate: block production deploys unless a release authorization is
# present and unexpired.
#
# Runs as a Claude Code PreToolUse hook (reads the tool-input JSON on stdin,
# as Claude Code PreToolUse hooks do) or standalone with the command as the
# first argument.
#
# Exit codes: 0 = ALLOW, 2 = BLOCK (message shown to the agent), 1 = error.
#
# Configuration (environment variables, all optional):
#   RELEASE_APPROVAL        free-form authorization reference (ticket or signer).
#   RELEASE_APPROVAL_EXPIRY ISO-8601 UTC ("2026-08-27T12:00:00Z") or epoch
#                           seconds. Absent = the approval never expires.
#   RELEASE_GATE            gate name a ledger approval must carry
#                           (default: release_authorization). This stops an
#                           approval for some other gate (e.g. intent
#                           acceptance) from authorizing a production deploy.
#   DEPLOY_PATTERNS         extended regex of deploy actions (default below).
#   PROD_CONTEXT_PATTERNS   extended regex of production contexts (default below).
#   READ_ONLY_PATTERNS      extended regex of read-only commands that are never
#                           blocked (default below).
#
# Design notes:
#   - The command is split into shell segments (&&, ||, ;, |, &, newline) and
#     each segment is judged on its own. A read-only segment is ignored, so
#     the gate punishes the *action*, not the word
#     ("cat docs/production-deploy.md" is a read, not a deploy). A production
#     deploy in *any* segment trips the gate, so appending a read-only command
#     ("kubectl apply -f prod.yaml && echo done") cannot smuggle a deploy past
#     the gate.
#   - Read-only tokens are anchored to command position, so "catalogue.yaml"
#     no longer matches the "cat" pattern.
#   - Unknown commands are allowed: this gate is deploy-focused, not a general
#     command denylist. Production-grade setups should extend DEPLOY_PATTERNS
#     with every deploy tool the org uses.
set -euo pipefail

DEPLOY_PATTERNS="${DEPLOY_PATTERNS:-kubectl[[:space:]]+(apply|create|delete|edit|rollout|scale|set|patch|run|deploy)|helm[[:space:]]+(upgrade|install|rollback|uninstall)|terraform[[:space:]]+(apply|destroy|import)|flyctl[[:space:]]+(deploy|launch|release)|serverless[[:space:]]+deploy|sls[[:space:]]+deploy|sam[[:space:]]+deploy|aws[[:space:]]+(deploy|ecs|eks|lambda[[:space:]]+update-function)|cdk[[:space:]]+deploy|docker[[:space:]]+(push|compose[[:space:]]+up)|(^|[^[:alnum:]_])deploy([^[:alnum:]_]|$)}"

PROD_CONTEXT_PATTERNS="${PROD_CONTEXT_PATTERNS:-(^|[^[:alnum:]_])(production|prod|prd)([^[:alnum:]_]|$)}"

RELEASE_GATE="${RELEASE_GATE:-release_authorization}"

# Read-only tokens are anchored to command position so they match the
# *command* (e.g. a segment starting with "cat"), not an arbitrary substring
# (e.g. the file "catalogue.yaml"). Each alternative is
# (^|non-word) token (whitespace|end).
READ_ONLY_PATTERNS="${READ_ONLY_PATTERNS:-((^|[^[:alnum:]_])(cat|grep|less|head|tail|more|sed|awk|echo|printf|ls|find|pwd|jq)([[:space:]]|$))|((^|[^[:alnum:]_])git[[:space:]]+(log|diff|status|show|fetch|pull|branch)([[:space:]]|$))|((^|[^[:alnum:]_])kubectl[[:space:]]+(get|describe|logs|diff|top|explain|api-resources)([[:space:]]|$))|((^|[^[:alnum:]_])helm[[:space:]]+(list|get|status|search|repo)([[:space:]]|$))|((^|[^[:alnum:]_])terraform[[:space:]]+(plan|show|output|validate|fmt|state[[:space:]]+list)([[:space:]]|$))|((^|[^[:alnum:]_])aws[[:space:]]+(s3[[:space:]]+ls|ec2[[:space:]]+describe|sts[[:space:]]+get-caller-identity|cloudwatch[[:space:]]+get-metric-data)([[:space:]]|$))|((^|[^[:alnum:]_])gh[[:space:]]+(pr|run|issue)[[:space:]]+(list|view|diff|status)([[:space:]]|$))}"

# Return 0 when the ISO-8601 / epoch expiry is still in the future,
# 1 when expired or unparseable (fail closed).
_expiry_ok() {
  local value="$1" exp_epoch now
  if [[ "$value" =~ ^[0-9]+$ ]]; then
    exp_epoch="$value"
  else
    # BSD/macOS first, GNU as fallback.
    exp_epoch="$(date -j -u -f "%Y-%m-%dT%H:%M:%SZ" "$value" +%s 2>/dev/null || true)"
    if [[ -z "$exp_epoch" ]]; then
      exp_epoch="$(date -u -d "$value" +%s 2>/dev/null || true)"
    fi
    if [[ -z "$exp_epoch" ]]; then
      return 1
    fi
  fi
  now="$(date +%s)"
  [[ "$exp_epoch" -gt "$now" ]]
}

cmd="${1:-}"
if [[ -z "$cmd" && ! -t 0 ]]; then
  input="$(cat)"
  if command -v jq >/dev/null 2>&1; then
    cmd="$(jq -r '.tool_input.command // empty' <<<"$input" 2>/dev/null || true)"
  fi
  if [[ -z "$cmd" ]]; then
    # jq-less fallback: pull the command field straight out of the JSON.
    cmd="$(printf '%s' "$input" | sed -n 's/.*"command"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
  fi
  [[ -z "$cmd" ]] && cmd="$input"
fi

[[ -z "$cmd" ]] && { echo "ALLOW"; exit 0; }

# Split the command into shell segments and judge each on its own. A read-only
# segment is ignored; a production deploy in *any* segment is a gated action.
segments="$(printf '%s' "$cmd" | awk '{ gsub(/&&|\|\||[;&|]/, "\n"); print }')"

production_deploy=""
while IFS= read -r segment; do
  [[ -z "${segment//[[:space:]]/}" ]] && continue
  # Read-only segments never trip the gate.
  if printf '%s' "$segment" | grep -Eq "$READ_ONLY_PATTERNS"; then
    continue
  fi
  # A deploy action in a production context is a gated action.
  if printf '%s' "$segment" | grep -Eq "$DEPLOY_PATTERNS" \
     && printf '%s' "$segment" | grep -Eq "$PROD_CONTEXT_PATTERNS"; then
    production_deploy="$segment"
    break
  fi
done <<< "$segments"

# Nothing that looks like a production deploy: allow.
if [[ -z "$production_deploy" ]]; then
  echo "ALLOW"
  exit 0
fi

# A production deploy: require an unexpired release authorization.
approval="${RELEASE_APPROVAL:-}"
if [[ -z "$approval" ]]; then
  echo "BLOCK: production deploys need a release authorization." >&2
  echo "An authorized human must set RELEASE_APPROVAL=<ticket-or-signer> or RELEASE_APPROVAL=ledger:<record-id>, or approve via the org's release process. The env vars are a stand-in for your approval service." >&2
  exit 2
fi

# Ledger-backed approval: RELEASE_APPROVAL=ledger:<id> must match a verified,
# unexpired, committed record in gates/ledger.jsonl (see gate_ledger.py).
if [[ "$approval" == ledger:* ]]; then
  ledger_id="${approval#ledger:}"
  hook_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  project_dir="$(dirname "$hook_dir")"
  ledger_script="${GATE_LEDGER_SCRIPT:-$project_dir/scripts/gate_ledger.py}"
  ledger_file="${GATE_LEDGER_FILE:-$project_dir/gates/ledger.jsonl}"
  if [[ ! -f "$ledger_script" ]]; then
    echo "BLOCK: ledger approval requested but $ledger_script not found." >&2
    exit 2
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    echo "BLOCK: ledger verification requires python3." >&2
    exit 2
  fi
  if ! out="$(python3 "$ledger_script" --ledger "$ledger_file" verify --record "$ledger_id" \
        --require-gate "$RELEASE_GATE" --require-committed 2>&1)"; then
    echo "BLOCK: ledger record $ledger_id failed verification: $out" >&2
    exit 2
  fi
  echo "ALLOW (release authorization: ledger $ledger_id, gate $RELEASE_GATE)"
  exit 0
fi

# Free-form approval (ticket or signer), with optional expiry.
if [[ -n "${RELEASE_APPROVAL_EXPIRY:-}" ]] && ! _expiry_ok "$RELEASE_APPROVAL_EXPIRY"; then
  echo "BLOCK: release authorization ($approval) expired at $RELEASE_APPROVAL_EXPIRY." >&2
  echo "An authorized human must issue a fresh release authorization." >&2
  exit 2
fi

echo "ALLOW (release authorization: $approval)"
exit 0
