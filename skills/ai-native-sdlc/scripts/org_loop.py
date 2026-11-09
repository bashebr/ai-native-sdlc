#!/usr/bin/env python3
"""Deterministic bounded work loop for the agent org.

`next` is a pure routing function. `advance` is the only state mutator. The
loop stops as solved, gate_required, failed, stalled, or exhausted. It never
extends its own budget and never records a human approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import org_common as common  # noqa: E402
import org_message  # noqa: E402

TERMINAL = ("solved", "gate_required", "failed", "stalled", "exhausted")
RESULTS = ("progress", "no_progress", "verified", "failed")
DEFAULT_HUMAN_GATES = ("code_owner_approve", "release_authorization")
IGNORED_HASH_PATHS = {
    "org/loop/state.yaml",
    "org/loop/history.jsonl",
    "org/memory/context.md",
}


def _root(value: str | None) -> Path:
    return Path(value).expanduser().resolve() if value else SCRIPT_DIR.parent


def _config_path(root: Path) -> Path:
    return root / "org" / "loop" / "config.yaml"


def _state_path(root: Path) -> Path:
    return root / "org" / "loop" / "state.yaml"


def _history_path(root: Path) -> Path:
    return root / "org" / "loop" / "history.jsonl"


def _load_yaml(path: Path, default: dict) -> dict:
    if not path.is_file():
        return default
    data = common.load_yaml_file(path)
    return data if isinstance(data, dict) else default


def load_config(root: Path) -> dict:
    config = _load_yaml(_config_path(root), {})
    config.setdefault("max_turns", 6)
    config.setdefault("max_no_progress", 2)
    config.setdefault("max_review_rounds", 2)
    config.setdefault("require_security_clear", True)
    config.setdefault("review_artifact", "implementation")
    config.setdefault("human_gates", list(DEFAULT_HUMAN_GATES))
    config.setdefault("phases", {})
    return config


def load_state(root: Path) -> dict:
    state = _load_yaml(_state_path(root), {})
    if not state:
        raise ValueError(f"loop state not found at {_state_path(root)}; run init")
    return state


def load_history(root: Path) -> list[dict]:
    path = _history_path(root)
    if not path.is_file():
        return []
    rows: list[dict] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{lineno}: history record must be an object")
        rows.append(row)
    return rows


def _yaml_value(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    text = str(value)
    if not text or any(ch in text for ch in ":#[]{},\n") or text != text.strip():
        return json.dumps(text)
    return text


def _dump_state(state: dict) -> str:
    order = (
        "version",
        "work_item",
        "base_commit",
        "turn",
        "no_progress",
        "phase",
        "status",
        "last_input_hash",
        "stop_reason",
        "last_action",
        "artifacts",
        "acceptance",
        "updated_at",
    )
    return "\n".join(f"{key}: {_yaml_value(state.get(key))}" for key in order) + "\n"


def _write_state(root: Path, state: dict) -> None:
    state["updated_at"] = common.now_iso()
    common.atomic_write(_state_path(root), _dump_state(state))


def _append_history(root: Path, record: dict) -> None:
    rows = load_history(root)
    rows.append(record)
    payload = "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n"
    common.atomic_write(_history_path(root), payload)


def _git(root: Path, args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def _project_path(root: Path, value: str, label: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        raise ValueError(f"{label} must be relative to the project root")
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} escapes the project root: {value!r}") from exc
    return resolved


def _changed_files(root: Path, state: dict) -> list[str]:
    names: set[str] = set()
    base = state.get("base_commit")
    if base:
        names.update(
            line
            for line in _git(root, ["diff", "--name-only", base]).splitlines()
            if line
        )
    else:
        names.update(line for line in _git(root, ["ls-files"]).splitlines() if line)
    names.update(
        line
        for line in _git(
            root, ["ls-files", "--others", "--exclude-standard"]
        ).splitlines()
        if line
    )
    return sorted(name for name in names if name not in IGNORED_HASH_PATHS)


def _file_digest(root: Path, name: str) -> str:
    path = _project_path(root, name, "artifact")
    if not path.is_file():
        return "missing"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _messages(root: Path) -> list[dict]:
    return org_message.load_messages(root)


def _review_approved(root: Path, config: dict) -> bool:
    artifact = config.get("review_artifact")
    if not artifact:
        return True
    status = _load_yaml(root / "org" / "status.yaml", {})
    latest = None
    for entry in status.get("review_queue", []) or []:
        if isinstance(entry, dict) and entry.get("artifact") == artifact:
            latest = entry
    return bool(latest and latest.get("status") == "approved")


def _blocking_findings(messages: list[dict], config: dict) -> list[dict]:
    if not config.get("require_security_clear", True):
        return []
    return [
        message
        for message in messages
        if message.get("kind") == "finding"
        and message.get("status") != "closed"
        and (
            message.get("blocking")
            or message.get("severity") in ("high", "critical")
        )
    ]


def _acceptance(root: Path, config: dict) -> list[dict]:
    commands = _acceptance_commands(config)
    results: list[dict] = []
    for command in commands:
        if not isinstance(command, str) or not command.strip():
            results.append({"command": command, "exit": None, "pass": False})
            continue
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=600,
            )
            results.append(
                {
                    "command": command,
                    "exit": result.returncode,
                    "pass": result.returncode == 0,
                }
            )
        except (OSError, subprocess.TimeoutExpired):
            results.append({"command": command, "exit": None, "pass": False})
    return results


def _acceptance_commands(config: dict) -> list:
    configured = config.get("acceptance", {})
    if not isinstance(configured, dict):
        return []
    commands = configured.get("commands", [])
    return commands if isinstance(commands, list) else []


def _input_hash(
    root: Path,
    state: dict,
    acceptance: list[dict],
    review_approved: bool,
    messages: list[dict],
) -> str:
    payload = {
        "files": [
            [name, _file_digest(root, name)]
            for name in _changed_files(root, state)
        ],
        "acceptance": acceptance,
        "review_approved": review_approved,
        "messages": sorted(
            [
                [str(message.get("id")), str(message.get("status"))]
                for message in messages
                if message.get("status") in ("open", "answered")
            ]
        ),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _human_gate(root: Path, config: dict) -> dict | None:
    graph_path = root / "workflow-graph.yaml"
    if not graph_path.is_file():
        return None
    graph = common.load_yaml_file(graph_path)
    nodes = graph.get("nodes", {})
    human = set(config.get("human_gates", DEFAULT_HUMAN_GATES))
    for name, node in (nodes.items() if isinstance(nodes, dict) else []):
        if not isinstance(node, dict):
            continue
        if node.get("gate") in human and node.get("status") in ("in_review", "pending"):
            return {"node": name, "gate": node.get("gate")}
    return None


def _solved(root: Path, state: dict, config: dict, messages: list[dict]) -> bool:
    commands = _acceptance_commands(config)
    acceptance = state.get("acceptance", [])
    acceptance_ok = (
        bool(commands)
        and isinstance(acceptance, list)
        and len(acceptance) == len(commands)
        and all(
            isinstance(item, dict) and item.get("pass") is True
            for item in acceptance
        )
    )
    return (
        acceptance_ok
        and _review_approved(root, config)
        and not _blocking_findings(messages, config)
    )


def next_action(root: Path) -> dict:
    config = load_config(root)
    state = load_state(root)
    if state.get("status") in TERMINAL:
        return {
            "status": "stop",
            "reason": state.get("stop_reason") or state.get("status"),
        }

    messages = _messages(root)
    open_questions = sorted(
        [
            message
            for message in messages
            if message.get("status") == "open" and message.get("requires_response")
        ],
        key=lambda message: str(message.get("id")),
    )
    if open_questions:
        question = open_questions[0]
        if question.get("to") == "ceo":
            return {
                "status": "stop",
                "reason": "gate_required",
                "detail": f"CEO decision needed for {question.get('id')}",
            }
        return {
            "status": "dispatch",
            "turn": int(state.get("turn", 0)) + 1,
            "phase": state.get("phase"),
            "role": question.get("to"),
            "action": "answer_message",
            "message_id": question.get("id"),
            "reason": f"open message {question.get('id')}",
        }

    gate = _human_gate(root, config)
    if gate:
        return {
            "status": "stop",
            "reason": "gate_required",
            "detail": f"{gate['node']} requires {gate['gate']}",
        }

    phase = state.get("phase")
    phases = config.get("phases", {})
    phase_cfg = phases.get(phase) if isinstance(phases, dict) else None
    if not isinstance(phase_cfg, dict):
        return {
            "status": "stop",
            "reason": "failed",
            "detail": f"unknown phase {phase!r}",
        }
    role = phase_cfg.get("role")
    if not role:
        return {
            "status": "stop",
            "reason": "solved" if _solved(root, state, config, messages) else "gate_required",
        }
    return {
        "status": "dispatch",
        "turn": int(state.get("turn", 0)) + 1,
        "phase": phase,
        "role": role,
        "action": phase_cfg.get("action"),
        "reason": f"phase {phase}",
    }


def _artifact_entry(root: Path, artifact: str | None, existing: list) -> list:
    if not artifact:
        return existing
    digest = _file_digest(root, artifact)
    entries = [
        item
        for item in existing
        if not (isinstance(item, dict) and item.get("path") == artifact)
    ]
    entries.append({"path": artifact, "sha256": digest})
    return entries


def _review_rounds(history: list[dict], role: str, action: str) -> int:
    completed = sum(
        1
        for record in history
        if record.get("role") == "reviewer" and record.get("action") == "review"
    )
    return completed + int(role == "reviewer" and action == "review")


def _answered_question(
    root: Path, role: str, history: list[dict]
) -> str | None:
    messages = _messages(root)
    by_id = {message.get("id"): message for message in messages}
    processed = {
        record.get("message_id")
        for record in history
        if record.get("action") == "answer_message"
    }
    for answer in sorted(messages, key=lambda item: str(item.get("id"))):
        original = by_id.get(answer.get("in_reply_to"))
        if (
            answer.get("kind") == "answer"
            and answer.get("from") == role
            and isinstance(original, dict)
            and original.get("to") == role
            and original.get("requires_response")
            and original.get("status") == "answered"
            and original.get("id") not in processed
        ):
            return str(original.get("id"))
    return None


def advance(
    root: Path,
    role: str,
    action: str,
    result: str,
    artifact: str | None,
    evidence: str | None,
) -> dict:
    if result not in RESULTS:
        raise ValueError(f"invalid result {result!r}")
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("evidence is required for every completed turn")
    if artifact:
        _project_path(root, artifact, "artifact")

    expected = next_action(root)
    if expected.get("status") != "dispatch":
        raise ValueError(f"loop is not dispatchable: {expected.get('reason')}")
    state = load_state(root)
    history = load_history(root)
    answered_message_id = (
        _answered_question(root, role, history)
        if action == "answer_message"
        else None
    )
    if role != expected.get("role") or action != expected.get("action"):
        if not answered_message_id:
            raise ValueError(
                "dispatch does not match: "
                f"expected {expected.get('role')}:{expected.get('action')}, "
                f"got {role}:{action}"
            )
    elif action == "answer_message":
        if not answered_message_id:
            raise ValueError(
                f"message {expected.get('message_id')!r} has not been answered"
            )
        answered_message_id = str(expected.get("message_id"))

    config = load_config(root)
    if not state.get("base_commit"):
        state["base_commit"] = _git(root, ["rev-parse", "HEAD"]).strip() or None
    messages = _messages(root)
    acceptance = _acceptance(root, config)
    review_ok = _review_approved(root, config)
    blocking = _blocking_findings(messages, config)
    input_hash = _input_hash(root, state, acceptance, review_ok, messages)
    previous_no_progress = int(state.get("no_progress", 0))
    no_progress = (
        previous_no_progress + 1
        if input_hash == state.get("last_input_hash")
        else 0
    )
    if result == "no_progress":
        no_progress = max(no_progress, previous_no_progress + 1)

    turn = int(state.get("turn", 0)) + 1
    phase = state.get("phase")
    phases = config.get("phases", {})
    phase_cfg = phases.get(phase, {}) if isinstance(phases, dict) else {}
    phase_after = phase
    status = "in_progress"
    stop_reason = None

    if result == "failed":
        status, stop_reason = "failed", "failed"
    elif action == "answer_message":
        phase_after = phase
    elif phase == "review":
        solved = result == "verified" and _solved(
            root, {**state, "acceptance": acceptance}, config, messages
        )
        if solved:
            status, stop_reason, phase_after = "solved", "solved", "done"
        elif _review_rounds(history, role, action) >= int(
            config.get("max_review_rounds", 2)
        ):
            status, stop_reason = "gate_required", "gate_required"
        else:
            phase_after = "implement"
    elif phase == "security" and blocking:
        phase_after = "implement"
    elif result == "no_progress":
        phase_after = "implement" if phase in ("review", "security") else phase
    else:
        phase_after = phase_cfg.get("next", phase)

    if status == "in_progress":
        if phase_after == "done":
            if _solved(root, {**state, "acceptance": acceptance}, config, messages):
                status, stop_reason = "solved", "solved"
            else:
                status, stop_reason = "gate_required", "gate_required"
        elif no_progress >= int(config.get("max_no_progress", 2)):
            status, stop_reason = "stalled", "stalled"
        elif turn >= int(config.get("max_turns", 6)):
            status, stop_reason = "exhausted", "exhausted"

    state.update(
        {
            "turn": turn,
            "no_progress": no_progress,
            "phase": phase_after,
            "status": status,
            "stop_reason": stop_reason,
            "last_input_hash": input_hash,
            "last_action": f"{role}:{action}",
            "artifacts": _artifact_entry(
                root, artifact, state.get("artifacts", [])
            ),
            "acceptance": acceptance,
        }
    )
    _write_state(root, state)
    record = {
        "turn": turn,
        "role": role,
        "action": action,
        "result": result,
        "artifact": artifact,
        "evidence": evidence,
        "input_hash": input_hash,
        "phase_after": phase_after,
        "status_after": status,
        "review_approved": review_ok,
        "blocking_findings": [
            message.get("id") for message in blocking
        ],
        "acceptance": acceptance,
        "timestamp": common.now_iso(),
    }
    if answered_message_id:
        record["message_id"] = answered_message_id
    _append_history(root, record)
    return state


def check(root: Path) -> list[str]:
    violations: list[str] = []
    try:
        config = load_config(root)
        state = load_state(root)
        history = load_history(root)
    except ValueError as exc:
        return [str(exc)]
    for key in ("max_turns", "max_no_progress", "max_review_rounds"):
        value = config.get(key)
        if not isinstance(value, int) or value < 1:
            violations.append(f"config {key} must be a positive integer")
    acceptance_commands = _acceptance_commands(config)
    if not acceptance_commands:
        violations.append("config acceptance must define at least one command")
    elif any(
        not isinstance(command, str) or not command.strip()
        for command in acceptance_commands
    ):
        violations.append("config acceptance commands must be non-empty strings")
    phases = config.get("phases", {})
    if not isinstance(phases, dict):
        violations.append("config phases must be a mapping")
        phases = {}
    if state.get("phase") not in phases:
        violations.append(
            f"state phase {state.get('phase')!r} is not in config phases"
        )
    if state.get("status") not in TERMINAL + ("in_progress", "not_started"):
        violations.append(f"invalid status {state.get('status')!r}")
    if state.get("turn") != len(history):
        violations.append(
            f"state turn {state.get('turn')} does not match "
            f"{len(history)} history record(s)"
        )
    for index, record in enumerate(history, 1):
        if record.get("turn") != index:
            violations.append(f"history record {index} has turn {record.get('turn')}")
        for key in (
            "role",
            "action",
            "result",
            "evidence",
            "input_hash",
            "phase_after",
            "status_after",
            "timestamp",
        ):
            if not record.get(key):
                violations.append(f"history record {index} missing {key}")
        if record.get("action") == "answer_message" and not record.get("message_id"):
            violations.append(f"history record {index} missing message_id")
    message_ids = [
        record.get("message_id")
        for record in history
        if record.get("action") == "answer_message"
    ]
    if len(message_ids) != len(set(message_ids)):
        violations.append("a message was recorded as answered more than once")
    if state.get("status") in TERMINAL and not state.get("stop_reason"):
        violations.append("terminal state must have a stop_reason")
    if state.get("status") == "in_progress" and state.get("stop_reason"):
        violations.append("in_progress state must not have a stop_reason")
    if state.get("status") == "in_progress" and int(
        state.get("turn", 0)
    ) >= int(config.get("max_turns", 6)):
        violations.append("turn budget exhausted but state is still in_progress")
    if history:
        last = history[-1]
        if last.get("status_after") != state.get("status"):
            violations.append("last history status does not match state")
        if last.get("phase_after") != state.get("phase"):
            violations.append("last history phase does not match state")
    if state.get("status") == "solved" and not _solved(
        root, state, config, _messages(root)
    ):
        violations.append(
            "solved state does not meet acceptance/review/security criteria"
        )
    return violations


def _init(root: Path, work_item: str, force: bool) -> dict:
    work_path = _project_path(root, work_item, "work item")
    if not work_path.is_file():
        raise ValueError(f"work item not found: {work_item}")
    path = _state_path(root)
    if path.exists() and not force:
        raise ValueError(f"loop state already exists at {path}; pass --force to reset")
    base = _git(root, ["rev-parse", "HEAD"]).strip()
    state = {
        "version": 1,
        "work_item": work_item,
        "base_commit": base or None,
        "turn": 0,
        "no_progress": 0,
        "phase": "intent",
        "status": "in_progress",
        "last_input_hash": None,
        "stop_reason": None,
        "last_action": None,
        "artifacts": [],
        "acceptance": [],
        "updated_at": common.now_iso(),
    }
    common.atomic_write(path, _dump_state(state))
    common.atomic_write(_history_path(root), "")
    return state


def _run(root: Path, agent_cmd: str, max_turns: int | None) -> int:
    config = load_config(root)
    configured_limit = int(config.get("max_turns", 6))
    if max_turns is not None and max_turns > configured_limit:
        raise ValueError(
            f"--max-turns {max_turns} exceeds configured budget {configured_limit}"
        )
    limit = configured_limit if max_turns is None else max_turns
    for _ in range(limit):
        action = next_action(root)
        if action.get("status") == "stop":
            print(json.dumps(action, sort_keys=True))
            return 0
        env = {
            **os.environ,
            "ORG_ROLE": str(action.get("role")),
            "ORG_ACTION": str(action.get("action")),
            "ORG_PHASE": str(action.get("phase")),
        }
        result = subprocess.run(agent_cmd, shell=True, cwd=root, env=env)
        outcome = "progress" if result.returncode == 0 else "failed"
        state = advance(
            root,
            str(action.get("role")),
            str(action.get("action")),
            outcome,
            None,
            f"agent exit {result.returncode}",
        )
        if state.get("status") in TERMINAL:
            print(json.dumps({"status": state.get("status")}, sort_keys=True))
            return 0
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser("init")
    init_p.add_argument("--work-item", required=True)
    init_p.add_argument("--force", action="store_true")

    status_p = sub.add_parser("status")
    status_p.add_argument("--json", action="store_true")
    next_p = sub.add_parser("next")
    next_p.add_argument("--json", action="store_true")

    advance_p = sub.add_parser("advance")
    advance_p.add_argument("--role", required=True)
    advance_p.add_argument("--action", required=True)
    advance_p.add_argument("--result", required=True, choices=RESULTS)
    advance_p.add_argument("--artifact", default=None)
    advance_p.add_argument("--evidence", default=None)

    check_p = sub.add_parser("check")
    check_p.add_argument("--json", action="store_true")

    run_p = sub.add_parser("run")
    run_p.add_argument("--agent-cmd", required=True)
    run_p.add_argument("--max-turns", type=int, default=None)

    args = parser.parse_args(argv)
    root = _root(args.root)
    try:
        if args.command == "init":
            state = _init(root, args.work_item, args.force)
            print(json.dumps(state, sort_keys=True))
            return 0
        if args.command == "status":
            print(json.dumps(load_state(root), indent=2, sort_keys=True))
            return 0
        if args.command == "next":
            action = next_action(root)
            if args.json:
                print(json.dumps(action, sort_keys=True))
            else:
                print(
                    f"{action.get('status')}: "
                    f"{action.get('reason') or action.get('detail', '')}"
                )
            return 0
        if args.command == "advance":
            state = advance(
                root,
                args.role,
                args.action,
                args.result,
                args.artifact,
                args.evidence,
            )
            print(json.dumps(state, sort_keys=True))
            return 0
        if args.command == "run":
            return _run(root, args.agent_cmd, args.max_turns)
        violations = check(root)
        if args.json:
            print(json.dumps({"violations": violations}, indent=2))
        else:
            for violation in violations:
                print(f"violation: {violation}")
            print(f"loop check: {len(violations)} violation(s)")
        return 1 if violations else 0
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
