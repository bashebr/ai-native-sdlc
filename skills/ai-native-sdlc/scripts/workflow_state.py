#!/usr/bin/env python3
"""Deterministic workflow state runtime (F1).

Reads workflow-graph.yaml as a state machine: reports the shared pipeline
view (status), closes a node only through a matching, chain-verified ledger
record (advance), and detects drift between the graph, committed artifacts,
and the ledger (check). Framework-neutral and deterministic: no model input.

Usage:
    workflow_state.py status [--graph workflow-graph.yaml]
                             [--ledger gates/ledger.jsonl] [--json]
    workflow_state.py advance --node <name> --record <id>
                              [--graph workflow-graph.yaml]
                              [--ledger gates/ledger.jsonl]
                              [--require-committed]
    workflow_state.py check [--graph workflow-graph.yaml]
                            [--ledger gates/ledger.jsonl] [--strict]

Exit codes: 0 = ok, 1 = validation/failure, 2 = usage error. Ledger chain
verification is delegated to gate_ledger.py; this script never re-implements
or skips it.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import gate_ledger as gl  # noqa: E402

DEFAULT_GRAPH = "workflow-graph.yaml"
DEFAULT_LEDGER = "gates/ledger.jsonl"
WORKING_STATUSES = ("not_started", "in_progress", "in_review", "pending")
DEFAULT_DONE_BY_GATE = {
    "product_owner_accept": "accepted",
    "product_owner_approve": "approved",
    "engineer_approve": "approved",
    "code_owner_approve": "merged",
    "release_authorization": "done",
    "on_call_triage": "done",
}


def _read_graph(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"graph not found at {path}")
    try:
        graph = gl.load_yaml(path)
    except Exception as exc:
        raise ValueError(f"cannot read graph {path}: {exc}") from exc
    if not isinstance(graph, dict):
        raise ValueError(f"graph {path} must contain a YAML mapping")
    return graph


def _read_ledger(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        return gl.read_ledger(path)
    except ValueError as exc:
        raise ValueError(f"cannot read ledger {path}: {exc}") from exc


def _approved_for_gate(records: list[dict], gate: str) -> bool:
    return any(
        r.get("gate") == gate and r.get("decision") == "approved" for r in records
    )


def _looks_like_file_artifact(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    # Artifacts such as "code + tests" and "authorized release" describe
    # results rather than a single path, so existence checks do not apply.
    return Path(value).is_file()


def _next_actions(graph: dict, node_name: str, node: dict) -> list[str]:
    status = node.get("status", "not_started")
    done_status = node.get("done_status")
    if done_status and status == done_status:
        return []
    edges = graph.get("edges") or []
    out = []
    for edge in edges if isinstance(edges, list) else []:
        if not isinstance(edge, dict):
            continue
        if edge.get("from") == node_name:
            target = edge.get("to")
            if isinstance(target, str):
                out.append(target)
    return sorted(set(out))


def _status_view(graph: dict, ledger_path: Path) -> tuple[list[dict], list[str]]:
    records = _read_ledger(ledger_path)
    nodes = graph.get("nodes", {})
    rows: list[dict] = []
    global_warnings: list[str] = []

    for name, node in (nodes.items() if isinstance(nodes, dict) else []):
        if not isinstance(node, dict):
            global_warnings.append(f"node {name!r} is not a mapping")
            continue
        status = node.get("status", "not_started")
        done_status = node.get("done_status")
        gate = node.get("gate")
        warnings: list[str] = []

        if (
            isinstance(status, str)
            and status == "not_started"
            and _looks_like_file_artifact(node.get("artifact"))
        ):
            warnings.append(f"artifact exists but node {name} is not_started")

        if done_status and status == done_status:
            if not (isinstance(gate, str) and _approved_for_gate(records, gate)):
                warnings.append(
                    f"node {name} is at done_status {done_status!r} but the ledger has "
                    f"no approved record for gate {gate!r}"
                )

        if isinstance(gate, str) and _approved_for_gate(records, gate):
            if status in ("in_review", "pending"):
                warnings.append(
                    f"ledger has an approved record for {gate!r}; node {name} is at "
                    f"{status!r} (pending advance)"
                )
            elif not (done_status and status == done_status):
                warnings.append(
                    f"ledger has an approved record for {gate!r} while node {name} is "
                    f"still {status!r} (recorded too early)"
                )

        rows.append(
            {
                "node": name,
                "stage": node.get("stage"),
                "status": status,
                "gate": gate,
                "done_status": done_status,
                "next_actions": _next_actions(graph, name, node),
                "warnings": warnings,
            }
        )

    return rows, global_warnings


def _print_status(rows: list[dict], warnings: list[str]) -> None:
    headers = ("node", "stage", "status", "gate", "done_status", "next", "warnings")
    widths = {h: len(h) for h in headers}
    for row in rows:
        values = {
            "node": str(row["node"]),
            "stage": str(row["stage"] or ""),
            "status": str(row["status"]),
            "gate": str(row["gate"] or ""),
            "done_status": str(row["done_status"] or ""),
            "next": ", ".join(row["next_actions"]),
            "warnings": "; ".join(row["warnings"]),
        }
        for key in widths:
            widths[key] = max(widths[key], len(values[key]))
    fmt = "  ".join("{" + h + ":<" + str(widths[h]) + "}" for h in headers)
    print(fmt.format(**{h: h for h in headers}))
    for row in rows:
        print(
            fmt.format(
                node=str(row["node"]),
                stage=str(row["stage"] or ""),
                status=str(row["status"]),
                gate=str(row["gate"] or ""),
                done_status=str(row["done_status"] or ""),
                next=", ".join(row["next_actions"]),
                warnings="; ".join(row["warnings"]),
            )
        )
    for warning in warnings:
        print(f"warning: {warning}")


def cmd_status(args: argparse.Namespace) -> int:
    try:
        graph = _read_graph(Path(args.graph))
        rows, warnings = _status_view(graph, Path(args.ledger))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"graph": args.graph, "nodes": rows, "warnings": warnings}, indent=2))
    else:
        _print_status(rows, warnings)
    return 0


def _verified_record_id(ledger_path: Path, record_id: str, require_committed: bool) -> bool:
    """Delegate the whole chain/expiry/committed check to gate_ledger."""
    args = argparse.Namespace(
        ledger=str(ledger_path),
        record=record_id,
        require_gate=None,
        require_committed=require_committed,
        graph=None,
        require_gates=False,
    )
    with contextlib.redirect_stdout(io.StringIO()):
        return gl.cmd_verify(args) == 0


def _replace_status_line(text: str, node: str, new_status: str) -> str:
    """Replace node's status line in a block-form YAML graph, preserving comments."""
    lines = text.splitlines(keepends=True)
    node_indent = -1
    in_node = False
    field_indent = 0
    replaced = False
    out: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        indent = len(line) - len(line.lstrip())
        if not in_node:
            if stripped == f"{node}:":
                in_node = True
                node_indent = indent
                field_indent = indent + 2
            out.append(line)
            continue

        if indent <= node_indent and not stripped.startswith("-"):
            in_node = False
            out.append(line)
            continue
        if indent == field_indent and stripped.startswith("status:"):
            prefix = line[: len(line) - len(line.lstrip())]
            out.append(f"{prefix}status: {new_status}\n")
            replaced = True
            continue
        out.append(line)

    if not replaced:
        raise ValueError(f"graph node {node!r} has no status field to update")
    return "".join(out)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_name, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def cmd_advance(args: argparse.Namespace) -> int:
    graph_path = Path(args.graph)
    ledger_path = Path(args.ledger)
    try:
        graph = _read_graph(graph_path)
        records = _read_ledger(ledger_path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    nodes = graph.get("nodes", {})
    node = nodes.get(args.node) if isinstance(nodes, dict) else None
    if not isinstance(node, dict):
        print(f"error: no node named {args.node!r} in {graph_path}", file=sys.stderr)
        return 1
    done_status = node.get("done_status")
    if not done_status:
        print(
            f"error: node {args.node!r} has no done_status (nothing to advance to)",
            file=sys.stderr,
        )
        return 1

    if not _verified_record_id(ledger_path, args.record, args.require_committed):
        return 1  # gate_ledger already printed the failure reason to stderr

    target = next((r for r in records if r.get("id") == args.record), None)
    if target is None:
        print(f"error: no record with id {args.record!r}", file=sys.stderr)
        return 1
    if target.get("gate") != node.get("gate"):
        print(
            f"error: record {args.record} is for gate {target.get('gate')!r}, not "
            f"node {args.node}'s gate {node.get('gate')!r} (no gate skipping)",
            file=sys.stderr,
        )
        return 1

    status = node.get("status", "not_started")
    if status not in ("in_review", "pending"):
        print(
            f"error: node {args.node} status is {status!r}; advance requires "
            "in_review or pending",
            file=sys.stderr,
        )
        return 1

    try:
        text = graph_path.read_text(encoding="utf-8")
        updated = _replace_status_line(text, args.node, done_status)
        _atomic_write(graph_path, updated)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        fresh = _read_graph(graph_path)
        rows, warnings = _status_view(fresh, ledger_path)
        _print_status(rows, warnings)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _validate_graph(graph: dict, path: Path) -> list[str]:
    violations: list[str] = []
    if "version" not in graph:
        violations.append(f"{path}: missing version")
    nodes = graph.get("nodes")
    if not isinstance(nodes, dict):
        violations.append(f"{path}: nodes must be a mapping")
        return violations
    if "edges" not in graph:
        violations.append(f"{path}: missing edges")

    edges = graph.get("edges")
    if not isinstance(edges, list):
        violations.append(f"{path}: edges must be a list")
        return violations

    seen: set[tuple[str, str]] = set()
    for edge in edges:
        if not isinstance(edge, dict):
            violations.append(f"{path}: every edge must be a mapping")
            continue
        source = edge.get("from")
        target = edge.get("to")
        if not isinstance(source, str) or not isinstance(target, str):
            violations.append(f"{path}: edge needs string 'from' and 'to'")
            continue
        if source == target:
            violations.append(f"{path}: self-loop edge {source} -> {source}")
        pair = (source, target)
        if pair in seen:
            violations.append(f"{path}: duplicate edge {source} -> {target}")
        seen.add(pair)

    for name, node in nodes.items():
        if not isinstance(node, dict):
            violations.append(f"{path}: node {name!r} is not a mapping")
            continue
        for field in ("gate", "stage"):
            if field not in node:
                violations.append(f"{path}: node {name!r} missing {field}")
        status = node.get("status", "not_started")
        done_status = node.get("done_status")
        if not isinstance(status, str):
            violations.append(f"{path}: node {name!r} status must be a string")
            continue
        if status not in WORKING_STATUSES and status != done_status:
            violations.append(
                f"{path}: node {name!r} has unknown status {status!r}"
            )
    return violations


def _uncommitted_ledger(ledger_path: Path) -> tuple[bool, str]:
    if not ledger_path.is_file():
        return True, "ledger does not exist"
    clean, reason = gl._git_clean(ledger_path)
    return not clean, reason


def cmd_check(args: argparse.Namespace) -> int:
    graph_path = Path(args.graph)
    ledger_path = Path(args.ledger)
    try:
        graph = _read_graph(graph_path)
        records = _read_ledger(ledger_path)
        if ledger_path.is_file():
            chain_err = gl._verify_chain(records, ledger_path)
            if chain_err:
                print(f"error: {chain_err}", file=sys.stderr)
                return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    violations = _validate_graph(graph, graph_path)
    nodes = graph.get("nodes", {})

    for name, node in nodes.items() if isinstance(nodes, dict) else []:
        if not isinstance(node, dict):
            continue
        gate = node.get("gate")
        done_status = node.get("done_status")
        if (
            done_status
            and isinstance(gate, str)
            and node.get("status", "not_started") == done_status
            and not _approved_for_gate(records, gate)
        ):
            violations.append(
                f"node {name} is at done_status {done_status!r} but the ledger has "
                f"no approved record for gate {gate!r}"
            )

    if args.strict:
        dirty, reason = _uncommitted_ledger(ledger_path)
        for name, node in nodes.items() if isinstance(nodes, dict) else []:
            if not isinstance(node, dict):
                continue
            gate = node.get("gate")
            if (
                isinstance(gate, str)
                and _approved_for_gate(records, gate)
                and node.get("status", "not_started") in ("in_review", "pending")
                and dirty
            ):
                violations.append(
                    f"record for gate {gate!r} is advance-ready but the ledger is "
                    f"not committed ({reason})"
                )

    if violations:
        for violation in violations:
            print(f"violation: {violation}")
        return 1
    print(f"check: consistent ({len(nodes)} nodes, {len(records)} ledger record(s))")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="print the pipeline state and drift warnings")
    status.add_argument("--graph", default=DEFAULT_GRAPH)
    status.add_argument("--ledger", default=DEFAULT_LEDGER)
    status.add_argument("--json", action="store_true")
    status.set_defaults(fn=cmd_status)

    advance = sub.add_parser("advance", help="close a gate with a verified ledger record")
    advance.add_argument("--node", required=True)
    advance.add_argument("--record", required=True)
    advance.add_argument("--graph", default=DEFAULT_GRAPH)
    advance.add_argument("--ledger", default=DEFAULT_LEDGER)
    advance.add_argument(
        "--require-committed",
        action="store_true",
        help="fail unless the ledger is committed and clean",
    )
    advance.set_defaults(fn=cmd_advance)

    check = sub.add_parser("check", help="validate graph/ledger consistency")
    check.add_argument("--graph", default=DEFAULT_GRAPH)
    check.add_argument("--ledger", default=DEFAULT_LEDGER)
    check.add_argument(
        "--strict",
        action="store_true",
        help="also fail on advance-ready uncommitted ledger records",
    )
    check.set_defaults(fn=cmd_check)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
