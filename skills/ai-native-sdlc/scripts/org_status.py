#!/usr/bin/env python3
"""Agent-org status and review-queue management (F3).

Maintains org/status.yaml through validated commands instead of hand edits:
marks agents busy/idle, assigns reviews to idle non-self reviewers, records
approved/changes verdicts with evidence, and escalates only after the org
chart's maximum disagreement rounds. org-chart.yaml remains the org contract.

Usage:
    org_status.py status [--json]
    org_status.py agent <name> busy --assignment <artifact>
    org_status.py agent <name> idle [--last-report <note>]
    org_status.py review assign --id <id> --artifact <artifact> \\
        --writer <agent> --reviewer <agent>
    org_status.py review submit --id <id> --verdict approved|changes \\
        --evidence <link-or-commit>
    org_status.py review escalate --id <id>

Default paths resolve against the scaffolded project root (the parent of this
script). Exit codes: 0 = ok, 1 = rule/validation violation, 2 = usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
ORG_CHART_DEFAULT = PROJECT_ROOT / "org" / "org-chart.yaml"
STATUS_DEFAULT = PROJECT_ROOT / "org" / "status.yaml"

VALID_STATUSES = ("busy", "idle")
REVIEW_ACTIVE = ("assigned", "changes_requested")
REVIEW_TERMINAL = ("approved", "escalated")


# --------------------------------------------------------------------------
# Minimal YAML subset loader/serializer (no third-party dependency)
# --------------------------------------------------------------------------

def _split_top(s: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    quote: str | None = None
    for ch in s:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            cur.append(ch)
        elif ch in "{[":
            depth += 1
            cur.append(ch)
        elif ch in "}]":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur).strip())
    return parts


def _scalar(value: str):
    value = value.strip()
    if not value:
        return None
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    if value.startswith("{") and value.endswith("}"):
        out: dict = {}
        for part in _split_top(value[1:-1]):
            if ":" not in part:
                continue
            key, _, raw = part.partition(":")
            out[key.strip().strip("'\"")] = _scalar(raw)
        return out
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [] if not inner else [_scalar(item) for item in _split_top(inner)]
    if value in ("true", "True"):
        return True
    if value in ("false", "False"):
        return False
    if value in ("null", "None", "~"):
        return None
    if value.lstrip("-").isdigit():
        return int(value)
    return value


def _yaml_lines(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        out.append((indent, raw.strip()))
    return out


def _parse_mapping(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[dict, int]:
    result: dict = {}
    while index < len(lines):
        ind, line = lines[index]
        if ind < indent:
            break
        if ind > indent:
            raise ValueError("unexpected indentation")
        if line.startswith("- "):
            break
        if ":" not in line:
            raise ValueError(f"expected mapping entry, got {line!r}")
        key, _, raw = line.partition(":")
        key = key.strip().strip("'\"")
        index += 1
        raw = raw.strip()
        if raw:
            result[key] = _scalar(raw)
            continue
        if index < len(lines) and lines[index][0] > indent:
            child_indent = lines[index][0]
            if lines[index][1].startswith("- "):
                value, index = _parse_sequence(lines, index, child_indent)
            else:
                value, index = _parse_mapping(lines, index, child_indent)
            result[key] = value
        else:
            result[key] = None
    return result, index


def _parse_sequence(
    lines: list[tuple[int, str]], index: int, indent: int
) -> tuple[list, int]:
    items: list = []
    while index < len(lines):
        ind, line = lines[index]
        if ind < indent or not line.startswith("- "):
            break
        rest = line[2:].strip()
        index += 1
        if not rest:
            if index < len(lines) and lines[index][0] > indent:
                child_indent = lines[index][0]
                if lines[index][1].startswith("- "):
                    item, index = _parse_sequence(lines, index, child_indent)
                else:
                    item, index = _parse_mapping(lines, index, child_indent)
            else:
                item = None
            items.append(item)
            continue
        if ":" in rest and not rest.startswith(("{", "[")):
            first_key, _, first_raw = rest.partition(":")
            item: dict = {first_key.strip().strip("'\""): _scalar(first_raw)}
            if index < len(lines) and lines[index][0] > indent:
                child_indent = lines[index][0]
                more, index = _parse_mapping(lines, index, child_indent)
                item.update(more)
            items.append(item)
        else:
            items.append(_scalar(rest))
    return items, index


def load_yaml_file(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"file not found: {path}")
    data, _ = _parse_mapping(_yaml_lines(path.read_text(encoding="utf-8")), 0, 0)
    return data


def _emit_scalar(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if text == "" or any(ch in text for ch in ":#{}[],&*!|>'\"%@`"):
        return json.dumps(text)
    return text


def _flat_map(value: object) -> bool:
    return isinstance(value, dict) and all(
        not isinstance(v, (dict, list)) for v in value.values()
    )


def _emit_flow(value: object) -> str:
    if isinstance(value, dict):
        return (
            "{ "
            + ", ".join(f"{k}: {_emit_flow(v)}" for k, v in value.items())
            + " }"
        )
    if isinstance(value, list):
        return "[ " + ", ".join(_emit_flow(v) for v in value) + " ]"
    return _emit_scalar(value)


def _emit_mapping(data: dict, indent: int = 0) -> list[str]:
    pad = " " * indent
    lines: list[str] = []
    for key, value in data.items():
        if isinstance(value, dict) and not _flat_map(value):
            lines.append(f"{pad}{key}:")
            lines.extend(_emit_mapping(value, indent + 2))
        elif isinstance(value, list):
            if not value:
                lines.append(f"{pad}{key}: []")
            elif all(_flat_map(item) for item in value):
                lines.append(f"{pad}{key}:")
                for item in value:
                    lines.append(f"{pad}  - {_emit_flow(item)}")
            else:
                lines.append(f"{pad}{key}:")
                for item in value:
                    lines.append(f"{pad}  - {_emit_flow(item)}")
        else:
            lines.append(f"{pad}{key}: {_emit_flow(value)}")
    return lines


def _header_comments(text: str) -> str:
    header: list[str] = []
    for line in text.splitlines(keepends=True):
        if not line.strip() or line.lstrip().startswith("#"):
            header.append(line)
        else:
            break
    return "".join(header)


def write_status(status_path: Path, data: dict) -> None:
    original = status_path.read_text(encoding="utf-8")
    body = "\n".join(_emit_mapping(data)) + "\n"
    text = _header_comments(original) + body
    status_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{status_path.name}.", dir=str(status_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_name, status_path)
    except Exception:
        with open(tmp_name, "w", encoding="utf-8"):
            pass
        os.unlink(tmp_name)
        raise


# --------------------------------------------------------------------------
# Org model
# --------------------------------------------------------------------------

def _agent_roles(org_chart: dict) -> tuple[dict[str, str], dict[str, bool]]:
    """Return (role_by_name, human_by_name) with count>1 expansions."""
    role_by_name: dict[str, str] = {}
    human_by_name: dict[str, bool] = {}
    agents = org_chart.get("agents", {})
    if not isinstance(agents, dict):
        return role_by_name, human_by_name
    for role, config in agents.items():
        if not isinstance(config, dict):
            continue
        if config.get("enabled", True) is False:
            continue
        has_count = "count" in config
        count = config.get("count", 1)
        if not isinstance(count, int) or count < 1:
            count = 1
        names = (
            [role]
            if not has_count
            else [f"{role}-{i}" for i in range(1, count + 1)]
        )
        for name in names:
            role_by_name[name] = role
            human_by_name[name] = bool(config.get("human", False))
    return role_by_name, human_by_name


def _escalation_owner(org_chart: dict) -> str:
    escalation = org_chart.get("escalation", {})
    if not isinstance(escalation, dict):
        return "cto"
    owner = escalation.get("owner")
    return owner if isinstance(owner, str) and owner else "cto"


def _load_org(org_chart_path: Path, status_path: Path) -> tuple[dict, dict, dict, dict, int]:
    chart = load_yaml_file(org_chart_path)
    status = load_yaml_file(status_path)
    if not isinstance(status.get("agents"), dict):
        raise ValueError(f"{status_path}: 'agents' must be a mapping")
    if not isinstance(status.get("review_queue"), list):
        raise ValueError(f"{status_path}: 'review_queue' must be a list")
    role_by_name, human_by_name = _agent_roles(chart)
    escalation = chart.get("escalation", {})
    if not isinstance(escalation, dict):
        escalation = {}
    max_rounds = escalation.get("max_disagreement_rounds", 2)
    if not isinstance(max_rounds, int):
        max_rounds = 2
    return status, chart, role_by_name, human_by_name, max_rounds


def _agent_error(status: dict, role_by_name: dict, human_by_name: dict, name: str) -> str | None:
    agents = status.get("agents", {})
    if name not in agents:
        return f"agent {name!r} is not in org/status.yaml"
    if name not in role_by_name:
        return f"agent {name!r} is not in org-chart.yaml"
    if human_by_name.get(name, False):
        return f"agent {name!r} is a human role and is not tracked by this CLI"
    return None


def _check_queue_violations(
    status: dict, role_by_name: dict, human_by_name: dict
) -> list[str]:
    violations: list[str] = []
    agents = status.get("agents", {})
    queue = status.get("review_queue", [])
    latest = _latest_by_artifact(queue)
    seen_ids: set[str] = set()

    for i, entry in enumerate(queue):
        if not isinstance(entry, dict):
            violations.append(f"review_queue[{i}] is not a mapping")
            continue
        entry_id = entry.get("id")
        if not isinstance(entry_id, str):
            violations.append(f"review_queue[{i}] missing string id")
        elif entry_id in seen_ids:
            violations.append(f"duplicate review queue id {entry_id!r}")
        seen_ids.add(entry_id or "")

        for field in ("artifact", "writer", "reviewer"):
            if not isinstance(entry.get(field), str) or not entry[field]:
                violations.append(f"review {entry_id!r} missing {field}")
        for who in ("writer", "reviewer"):
            name = entry.get(who)
            if isinstance(name, str):
                if name not in agents:
                    violations.append(f"review {entry_id!r} references unknown agent {name!r}")
                elif name not in role_by_name:
                    violations.append(f"review {entry_id!r} references agent not in org chart: {name!r}")
        entry_status = entry.get("status")
        reviewer = entry.get("reviewer")
        writer = entry.get("writer")
        is_latest = latest.get(entry.get("artifact")) is entry
        if is_latest and entry_status == "assigned":
            if isinstance(reviewer, str):
                agent_state = agents.get(reviewer, {})
                if isinstance(agent_state, dict) and agent_state.get("status") != "busy":
                    violations.append(
                        f"reviewer {reviewer} has active review {entry_id!r} but is not busy"
                    )
        if is_latest and entry_status in ("assigned", "changes_requested"):
            if isinstance(writer, str):
                agent_state = agents.get(writer, {})
                if isinstance(agent_state, dict) and agent_state.get("status") != "busy":
                    violations.append(
                        f"writer {writer} has active review {entry_id!r} but is not busy"
                    )

    for name, state in agents.items():
        if name not in role_by_name:
            violations.append(f"unknown agent in status: {name!r}")
        elif human_by_name.get(name, False):
            violations.append(f"human role {name!r} should not have a status entry")
        if not isinstance(state, dict):
            violations.append(f"agent {name!r} state is not a mapping")
            continue
        agent_status = state.get("status")
        if agent_status not in VALID_STATUSES:
            violations.append(f"agent {name!r} has invalid status {agent_status!r}")
        active = [
            e for e in latest.values()
            if isinstance(e, dict)
            and e.get("status") in ("assigned", "changes_requested")
            and (
                (e.get("writer") == name and e.get("status") in ("assigned", "changes_requested"))
                or (e.get("reviewer") == name and e.get("status") == "assigned")
            )
        ]
        if agent_status == "idle" and active:
            violations.append(f"agent {name!r} is idle with an active review")
    return violations


def cmd_status(args: argparse.Namespace) -> int:
    try:
        status, chart, role_by_name, human_by_name, _ = _load_org(
            Path(args.org_chart), Path(args.status)
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    violations = _check_queue_violations(status, role_by_name, human_by_name)
    agents_out = {}
    for name, state in status.get("agents", {}).items():
        if isinstance(state, dict):
            agents_out[name] = {
                **state,
                "role": role_by_name.get(name),
                "human": bool(human_by_name.get(name, False)),
            }
        else:
            agents_out[name] = {"status": state, "role": role_by_name.get(name)}
    payload = {
        "agents": agents_out,
        "review_queue": status.get("review_queue", []),
        "violations": violations,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print("agent".ljust(20), "status".ljust(12), "assignment".ljust(24), "last_report")
        for name, state in agents_out.items():
            print(
                name.ljust(20),
                str(state.get("status", "")).ljust(12),
                str(state.get("assignment") or "").ljust(24),
                state.get("last_report") or "",
            )
        print(f"review_queue: {len(payload['review_queue'])}")
        for violation in violations:
            print(f"violation: {violation}")
    return 1 if violations else 0


def _save(status_path: Path, status: dict) -> int:
    try:
        write_status(status_path, status)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_agent(args: argparse.Namespace) -> int:
    try:
        status, _, role_by_name, human_by_name, _ = _load_org(
            Path(args.org_chart), Path(args.status)
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    err = _agent_error(status, role_by_name, human_by_name, args.name)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    agents = status["agents"]
    state = agents[args.name]
    current = state.get("status") if isinstance(state, dict) else None
    if args.action == "busy":
        if current == "busy":
            print(f"error: agent {args.name} is already busy", file=sys.stderr)
            return 1
        state["status"] = "busy"
        state["assignment"] = args.assignment
    else:  # idle
        latest = _latest_by_artifact(status.get("review_queue", []))
        active = [
            e for e in latest.values()
            if isinstance(e, dict)
            and e.get("status") in ("assigned", "changes_requested")
            and (
                (e.get("reviewer") == args.name and e.get("status") == "assigned")
                or (e.get("writer") == args.name)
            )
        ]
        if active:
            print(
                f"error: agent {args.name} is carrying an active review "
                f"({active[0].get('id')}); submit or escalate it first",
                file=sys.stderr,
            )
            return 1
        state["status"] = "idle"
        state["assignment"] = None
        state["last_report"] = args.last_report
    return _save(Path(args.status), status)


def _latest_by_artifact(queue: list) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for entry in queue:
        if isinstance(entry, dict) and isinstance(entry.get("artifact"), str):
            latest[entry["artifact"]] = entry
    return latest


def _previous_rounds(status: dict, artifact: str) -> int:
    previous = [
        e for e in status.get("review_queue", [])
        if isinstance(e, dict) and e.get("artifact") == artifact
    ]
    if not previous:
        return 0
    last = previous[-1]
    rounds = last.get("rounds", 0)
    return rounds if isinstance(rounds, int) else 0


def cmd_review_assign(args: argparse.Namespace) -> int:
    try:
        status, _, role_by_name, human_by_name, max_rounds = _load_org(
            Path(args.org_chart), Path(args.status)
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.writer == args.reviewer:
        print("error: writer and reviewer must differ (no self-review)", file=sys.stderr)
        return 1
    for name in (args.writer, args.reviewer):
        err = _agent_error(status, role_by_name, human_by_name, name)
        if err:
            print(f"error: {err}", file=sys.stderr)
            return 1
    if role_by_name.get(args.writer) == "reviewer":
        print(f"error: writer {args.writer} is a reviewer role", file=sys.stderr)
        return 1
    if role_by_name.get(args.reviewer) != "reviewer":
        print(
            f"error: reviewer {args.reviewer} is not assigned to a reviewer role",
            file=sys.stderr,
        )
        return 1
    queue = status.get("review_queue", [])
    if any(isinstance(e, dict) and e.get("id") == args.id for e in queue):
        print(f"error: review id {args.id!r} already exists", file=sys.stderr)
        return 1

    previous = _latest_by_artifact(queue).get(args.artifact)
    if isinstance(previous, dict) and previous.get("status") == "changes_requested":
        rounds = previous.get("rounds", 0)
        if isinstance(rounds, int) and rounds >= max_rounds:
            print(
                f"error: review rounds for {args.artifact} reached {rounds}; "
                "escalation to the CTO is required before another assignment",
                file=sys.stderr,
            )
            return 1

    reviewer_state = status["agents"].get(args.reviewer)
    if not isinstance(reviewer_state, dict) or reviewer_state.get("status") != "idle":
        print(f"error: reviewer {args.reviewer} is not idle", file=sys.stderr)
        return 1

    writer_state = status["agents"].get(args.writer)
    if not isinstance(writer_state, dict):
        print(f"error: writer state missing for {args.writer}", file=sys.stderr)
        return 1
    if writer_state.get("status") == "idle":
        writer_state["status"] = "busy"
        writer_state["assignment"] = args.artifact

    reviewer_state["status"] = "busy"
    reviewer_state["assignment"] = args.artifact
    queue.append(
        {
            "id": args.id,
            "artifact": args.artifact,
            "writer": args.writer,
            "reviewer": args.reviewer,
            "rounds": _previous_rounds(status, args.artifact),
            "verdict": None,
            "status": "assigned",
        }
    )
    return _save(Path(args.status), status)


def cmd_review_submit(args: argparse.Namespace) -> int:
    try:
        status, _, role_by_name, human_by_name, _ = _load_org(
            Path(args.org_chart), Path(args.status)
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    queue = status.get("review_queue", [])
    entry = next((e for e in queue if isinstance(e, dict) and e.get("id") == args.id), None)
    if entry is None:
        print(f"error: no review entry with id {args.id!r}", file=sys.stderr)
        return 1
    if entry.get("status") not in REVIEW_ACTIVE:
        print(f"error: review {args.id} is not active", file=sys.stderr)
        return 1
    if not args.evidence:
        print("error: review submit requires --evidence", file=sys.stderr)
        return 1

    entry["verdict"] = args.verdict
    if args.verdict == "changes":
        rounds = entry.get("rounds", 0)
        entry["rounds"] = (rounds if isinstance(rounds, int) else 0) + 1
        entry["status"] = "changes_requested"
    else:
        entry["status"] = "approved"
    reviewer = entry.get("reviewer")
    if isinstance(reviewer, str):
        reviewer_state = status["agents"].get(reviewer)
        if isinstance(reviewer_state, dict):
            reviewer_state["status"] = "idle"
            reviewer_state["assignment"] = None
            reviewer_state["last_report"] = f"review {args.id}: {args.verdict}"
    return _save(Path(args.status), status)


def cmd_review_escalate(args: argparse.Namespace) -> int:
    try:
        status, chart, _, _, max_rounds = _load_org(
            Path(args.org_chart), Path(args.status)
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    queue = status.get("review_queue", [])
    entry = next((e for e in queue if isinstance(e, dict) and e.get("id") == args.id), None)
    if entry is None:
        print(f"error: no review entry with id {args.id!r}", file=sys.stderr)
        return 1
    artifact = entry.get("artifact")
    latest = [e for e in queue if isinstance(e, dict) and e.get("artifact") == artifact]
    if not latest or latest[-1].get("id") != args.id:
        print(f"error: review {args.id} is not the latest entry for its artifact", file=sys.stderr)
        return 1
    if entry.get("status") != "changes_requested":
        print(
            f"error: review {args.id} status is {entry.get('status')!r}; "
            "escalation requires changes_requested",
            file=sys.stderr,
        )
        return 1
    rounds = entry.get("rounds", 0)
    if not isinstance(rounds, int) or rounds < max_rounds:
        print(
            f"error: review {args.id} has {rounds} round(s); escalation needs "
            f"at least {max_rounds}",
            file=sys.stderr,
        )
        return 1
    entry["status"] = "escalated"
    entry["escalated_to"] = _escalation_owner(chart)
    return _save(Path(args.status), status)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", default=str(STATUS_DEFAULT), help="org/status.yaml")
    parser.add_argument("--org-chart", default=str(ORG_CHART_DEFAULT), help="org/org-chart.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    status_p = sub.add_parser("status", help="print org status and protocol violations")
    status_p.add_argument("--json", action="store_true")
    status_p.set_defaults(fn=cmd_status)

    agent = sub.add_parser("agent", help="mark an agent busy or idle")
    agent.add_argument("name")
    agent_sub = agent.add_subparsers(dest="action", required=True)
    busy = agent_sub.add_parser("busy")
    busy.add_argument("--assignment", required=True)
    idle = agent_sub.add_parser("idle")
    idle.add_argument("--last-report", default=None)
    agent.set_defaults(fn=cmd_agent)

    review = sub.add_parser("review", help="manage the review queue")
    review_sub = review.add_subparsers(dest="action", required=True)
    assign = review_sub.add_parser("assign")
    assign.add_argument("--id", required=True)
    assign.add_argument("--artifact", required=True)
    assign.add_argument("--writer", required=True)
    assign.add_argument("--reviewer", required=True)
    assign.set_defaults(fn=cmd_review_assign)
    submit = review_sub.add_parser("submit")
    submit.add_argument("--id", required=True)
    submit.add_argument("--verdict", choices=["approved", "changes"], required=True)
    submit.add_argument("--evidence", required=True)
    submit.set_defaults(fn=cmd_review_submit)
    escalate = review_sub.add_parser("escalate")
    escalate.add_argument("--id", required=True)
    escalate.set_defaults(fn=cmd_review_escalate)
    review.set_defaults(fn=lambda args: None)

    args = parser.parse_args(argv)
    if args.command == "review":
        fn = {
            "assign": cmd_review_assign,
            "submit": cmd_review_submit,
            "escalate": cmd_review_escalate,
        }[args.action]
        return fn(args)
    if args.command == "agent":
        return cmd_agent(args)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
