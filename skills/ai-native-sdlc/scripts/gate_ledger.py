#!/usr/bin/env python3
"""Gate ledger: version-controlled, tamper-evident approval records (F1).

Every gate decision (intent acceptance, spec approval, plan approval, PR
merge, release authorization, on-call triage) is a record in the ledger.
Records are hash-chained: each record stores the hash of the previous one,
so the ledger is append-only by construction and any tampering is detected.

Usage:
    gate_ledger.py --ledger gates/ledger.jsonl record --gate <gate>
        --artifact <name> [--commit <sha>] --approver <who>
        --evidence <ticket-or-link> [--expires-at ISO-8601]
        [--decision approved|rejected] [--id <id>]
    gate_ledger.py --ledger gates/ledger.jsonl list [--gate <gate>]
    gate_ledger.py --ledger gates/ledger.jsonl verify --record <id>
        [--require-committed] [--graph <workflow-graph.yaml> --require-gates]

Record schema (one JSON object per line in ledger.jsonl):
    id            stable identifier, unique per ledger (default <gate>-<n>)
    gate          which gate was crossed (intent | spec | plan | review |
                  release | triage | ...) — must match a graph node's gate
    decision      approved | rejected
    artifact      what was approved (commit, PR, deploy, intent hash)
    commit        artifact commit / PR number / release tag (optional)
    approver      human (or review bot) identity
    evidence      ticket, CI run, or link backing the decision
    decided_at    ISO-8601 UTC
    expires_at    ISO-8601 UTC; absent = never expires (optional)
    prev_hash     sha256 of the previous record's canonical form, or null
    hash          sha256 of this record's canonical form (hash field excluded)

The release gate hook accepts RELEASE_APPROVAL=ledger:<id> and verifies the
record is present, approved, unexpired, and (by default) committed. Exit
codes: 0 = ok, 1 = failure (record/verify), 2 = usage error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LEDGER_DEFAULT = "gates/ledger.jsonl"
CORE_FIELDS = ("gate", "decision", "artifact", "approver", "evidence", "decided_at")
OPTIONAL_FIELDS = ("id", "commit", "expires_at")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "hash"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def _hash(record: dict) -> str:
    return hashlib.sha256(_canonical(record).encode("utf-8")).hexdigest()


def _parse_iso(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        try:
            return float(value)  # epoch seconds also accepted
        except ValueError:
            raise ValueError(f"cannot parse timestamp: {value!r}")


# --------------------------------------------------------------------------
# YAML subset parser (shared with detect_bands.py) for workflow-graph.yaml
# --------------------------------------------------------------------------

def _split_top(s: str) -> list[str]:
    parts, depth, cur, in_q = [], 0, "", None
    for ch in s:
        if in_q:
            cur += ch
            if ch == in_q:
                in_q = None
        elif ch in "\"'":
            in_q, cur = ch, cur + ch
        elif ch in "{[":
            depth += 1
            cur += ch
        elif ch in "}]":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def _parse_scalar(s: str):
    s = s.strip()
    if not s:
        return None
    if s.startswith("{") and s.endswith("}"):
        out = {}
        for part in _split_top(s[1:-1]):
            k, _, v = part.partition(":")
            out[k.strip().strip('"\'')] = _parse_scalar(v.strip())
        return out
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [] if not inner else [_parse_scalar(x.strip()) for x in _split_top(inner)]
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    if s in ("true", "True"):
        return True
    if s in ("false", "False"):
        return False
    if s in ("null", "None"):
        return None
    if s.lstrip("-").isdigit():
        return int(s)
    return s


def parse_simple_yaml(text: str) -> dict:
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    root: dict = {}
    stack: list[tuple[int, str, dict, object]] = []
    for line in lines:
        indent = len(line) - len(line.lstrip())
        content = line.strip()
        if content.startswith("- "):
            while stack and stack[-1][0] >= indent:
                stack.pop()
            if not stack:
                raise ValueError("top-level list not supported")
            _, key, parent, node = stack[-1]
            if not isinstance(node, list):
                node = []
                parent[key] = node
                stack[-1] = (stack[-1][0], key, parent, node)
            node.append(_parse_scalar(content[2:].strip()))
            continue
        key, _, value = content.partition(":")
        key = key.strip().strip('"\'')
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent: dict = stack[-1][3] if stack else root
        if not value.strip():
            node: dict = {}
            parent[key] = node
            stack.append((indent, key, parent, node))
        else:
            parent[key] = _parse_scalar(value.strip())
    return root


def load_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore

        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return data if isinstance(data, dict) else {}
    except ImportError:
        return parse_simple_yaml(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Ledger I/O
# --------------------------------------------------------------------------

def read_ledger(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}")
            out.append(rec)
    return out


def next_id(records: list[dict], gate: str) -> str:
    n = 1 + sum(1 for r in records if r.get("gate") == gate)
    return f"{gate}-{n:03d}"


def cmd_record(args: argparse.Namespace) -> int:
    path = Path(args.ledger)
    records = read_ledger(path)
    if any(r.get("id") == args.id for r in records):
        print(f"error: record id {args.id!r} already exists (ledger is append-only)", file=sys.stderr)
        return 1
    record: dict = {
        "id": args.id or next_id(records, args.gate),
        "gate": args.gate,
        "decision": args.decision,
        "artifact": args.artifact,
        "commit": args.commit,
        "approver": args.approver,
        "evidence": args.evidence,
        "decided_at": _now_iso(),
        "expires_at": args.expires_at,
        "prev_hash": records[-1]["hash"] if records else None,
    }
    record["hash"] = _hash(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    print(json.dumps(record, sort_keys=True))
    print(f"recorded {record['id']} -> {path}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    records = read_ledger(Path(args.ledger))
    if args.gate:
        records = [r for r in records if r.get("gate") == args.gate]
    for r in records:
        print(f"{r.get('decided_at','?'):20} {r.get('gate','?'):10} {r.get('id','?'):16} "
              f"{r.get('decision','?'):9} {r.get('approver','?'):12} {r.get('commit') or r.get('artifact','')}")
    print(f"{len(records)} record(s)")
    return 0


def _verify_chain(records: list[dict], path: Path) -> str | None:
    """Return an error message if the hash chain is broken, else None."""
    for i, rec in enumerate(records):
        expected_prev = records[i - 1]["hash"] if i > 0 else None
        if rec.get("prev_hash") != expected_prev:
            return f"chain broken at record {i} ({rec.get('id')}): prev_hash mismatch"
        if rec.get("hash") != _hash(rec):
            return f"chain broken at record {i} ({rec.get('id')}): hash does not match content"
    return None


def _git_clean(path: Path) -> tuple[bool, str]:
    """Is the ledger committed with no uncommitted changes? (False, reason) if not."""
    try:
        res = subprocess.run(
            ["git", "-C", str(path.parent), "status", "--porcelain", "--", path.name],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, "git unavailable"
    if res.returncode != 0:
        return False, "not a git repository"
    if res.stdout.strip():
        return False, f"uncommitted changes: {res.stdout.strip()}"
    return True, "committed"


def cmd_verify(args: argparse.Namespace) -> int:
    path = Path(args.ledger)
    if not path.is_file():
        print(f"error: ledger not found at {path}", file=sys.stderr)
        return 1
    try:
        records = read_ledger(path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    err = _verify_chain(records, path)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 1

    target = next((r for r in records if r.get("id") == args.record), None)
    if target is None:
        print(f"error: no record with id {args.record!r}", file=sys.stderr)
        return 1
    require_gate = getattr(args, "require_gate", None)
    if require_gate and target.get("gate") != require_gate:
        print(
            f"error: record {args.record} is for gate {target.get('gate')!r}, not "
            f"{require_gate!r} (approvals do not cross gates)",
            file=sys.stderr,
        )
        return 1
    if target.get("decision") != "approved":
        print(f"error: record {args.record} decision is {target.get('decision')!r}, not 'approved'", file=sys.stderr)
        return 1
    if target.get("expires_at"):
        try:
            if _parse_iso(target["expires_at"]) <= time.time():
                print(f"error: record {args.record} expired at {target['expires_at']}", file=sys.stderr)
                return 1
        except ValueError as exc:
            print(f"error: record {args.record} has unparseable expires_at ({exc})", file=sys.stderr)
            return 1

    if args.require_committed:
        ok, why = _git_clean(path)
        if not ok:
            print(f"error: ledger not in committed state: {why}", file=sys.stderr)
            return 1

    if args.graph and args.require_gates:
        graph = load_yaml(Path(args.graph))
        nodes = graph.get("nodes", {}) if isinstance(graph, dict) else {}
        gates = {n.get("gate") for n in nodes.values() if isinstance(n, dict) and n.get("gate")}
        recorded = {r.get("gate") for r in records}
        missing = sorted(gates - recorded)
        if missing:
            print(f"error: gates without any ledger record: {', '.join(missing)}", file=sys.stderr)
            return 1

    print(json.dumps(target, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", default=LEDGER_DEFAULT, help="ledger file (default: gates/ledger.jsonl)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_record = sub.add_parser("record", help="append an approval/rejection record")
    p_record.add_argument("--gate", required=True, help="gate name (matches a graph node's gate)")
    p_record.add_argument("--artifact", required=True, help="what was approved (deploy, PR, commit...)")
    p_record.add_argument("--commit", default=None, help="commit SHA, PR number, or release tag")
    p_record.add_argument("--approver", required=True, help="who approved (human identity or review bot)")
    p_record.add_argument("--evidence", required=True, help="ticket, CI run, or link backing the decision")
    p_record.add_argument("--decision", choices=["approved", "rejected"], default="approved")
    p_record.add_argument("--expires-at", default=None, help="ISO-8601 or epoch; absent = never expires")
    p_record.add_argument("--id", default=None, help="explicit record id (default <gate>-<n>)")
    p_record.set_defaults(fn=cmd_record)

    p_list = sub.add_parser("list", help="list ledger records")
    p_list.add_argument("--gate", default=None)
    p_list.set_defaults(fn=cmd_list)

    p_verify = sub.add_parser("verify", help="verify chain integrity and a specific record")
    p_verify.add_argument("--record", required=True, help="record id to verify")
    p_verify.add_argument("--require-gate", default=None,
                          help="fail unless the record's gate equals this value (no cross-gate approvals)")
    p_verify.add_argument("--require-committed", action="store_true",
                          help="fail unless the ledger is committed with no uncommitted changes")
    p_verify.add_argument("--graph", default=None, help="workflow-graph.yaml for the --require-gates check")
    p_verify.add_argument("--require-gates", action="store_true",
                          help="fail unless every gate in the graph has at least one record")
    p_verify.set_defaults(fn=cmd_verify)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
