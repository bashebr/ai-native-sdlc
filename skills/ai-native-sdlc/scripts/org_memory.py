#!/usr/bin/env python3
"""Deterministic shared memory for the agent org.

context.md is the compact main context. events.jsonl is append-only and every
event must cite a source. digest rewrites only the machine-owned block.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import org_common as common  # noqa: E402
import org_message  # noqa: E402

KINDS = ("decision", "fact", "constraint", "question")
MAX_BYTES = 16 * 1024
MAX_LINES = 200
BEGIN = "<!-- machine:begin -->"
END = "<!-- machine:end -->"
REQUIRED_HEADINGS = (
    "## Goal",
    "## Loop",
    "## Agents",
    "## Open messages",
    "## Recent decisions",
    "## Constraints",
    "## Artifacts",
    "## Next action",
    "## Last digest source",
)


def _root(value: str | None) -> Path:
    return Path(value).expanduser().resolve() if value else SCRIPT_DIR.parent


def _memory_dir(root: Path) -> Path:
    return root / "org" / "memory"


def _context_path(root: Path) -> Path:
    return _memory_dir(root) / "context.md"


def _events_path(root: Path) -> Path:
    return _memory_dir(root) / "events.jsonl"


def load_events(root: Path) -> list[dict]:
    path = _events_path(root)
    if not path.is_file():
        return []
    return _parse_events(path.read_text(encoding="utf-8"), path)


def _parse_events(text: str, path: Path) -> list[dict]:
    events: list[dict] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
        if not isinstance(event, dict):
            raise ValueError(f"{path}:{lineno}: event must be an object")
        events.append(event)
    return events


def _next_event_id(events: list[dict]) -> str:
    numbers = [
        int(match.group(1))
        for event in events
        if isinstance(event.get("id"), str)
        and (match := re.fullmatch(r"mem-(\d{4})", event["id"]))
    ]
    return f"mem-{(max(numbers, default=0) + 1):04d}"


def add(
    root: Path,
    kind: str,
    actor: str,
    text: str,
    source: str,
    tags: list[str],
) -> dict:
    if kind not in KINDS:
        raise ValueError(f"invalid kind {kind!r}")
    if not actor.strip() or not text.strip():
        raise ValueError("actor and text are required")
    if not source.strip():
        raise ValueError("source is required")
    path = _events_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as ledger:
        fcntl.flock(ledger.fileno(), fcntl.LOCK_EX)
        try:
            ledger.seek(0)
            existing = ledger.read()
            events = _parse_events(existing, path)
            event = {
                "id": _next_event_id(events),
                "ts": common.now_iso(),
                "kind": kind,
                "actor": actor,
                "text": text.strip(),
                "source": source.strip(),
                "tags": tags,
            }
            if existing and not existing.endswith("\n"):
                ledger.write("\n")
            ledger.write(json.dumps(event, sort_keys=True) + "\n")
            ledger.flush()
            os.fsync(ledger.fileno())
            return event
        finally:
            fcntl.flock(ledger.fileno(), fcntl.LOCK_UN)


def _load_state(root: Path) -> dict:
    path = root / "org" / "loop" / "state.yaml"
    if not path.is_file():
        return {}
    return common.load_yaml_file(path)


def _load_status(root: Path) -> dict:
    path = root / "org" / "status.yaml"
    if not path.is_file():
        return {}
    return common.load_yaml_file(path)


def _machine_block(root: Path) -> str:
    events = load_events(root)
    state = _load_state(root)
    status = _load_status(root)
    messages = org_message.load_messages(root)
    lines = [BEGIN, ""]

    lines += ["## Goal", ""]
    lines.append(str(state.get("work_item") or "(not initialized)"))
    lines.append("")

    lines += ["## Loop", ""]
    for key in ("status", "turn", "phase", "stop_reason"):
        lines.append(f"- {key}: {state.get(key, '(not initialized)')}")
    lines.append("")

    lines += ["## Agents", ""]
    agents = status.get("agents", {})
    for name in sorted(agents) if isinstance(agents, dict) else []:
        value = agents[name]
        if isinstance(value, dict):
            lines.append(
                f"- {name}: {value.get('status', '?')} "
                f"({value.get('assignment') or 'unassigned'})"
            )
    if not agents:
        lines.append("(no status file)")
    lines.append("")

    lines += ["## Open messages", ""]
    open_messages = [m for m in messages if m.get("status") == "open"]
    for message in sorted(open_messages, key=lambda item: str(item.get("id"))):
        lines.append(
            f"- {message.get('id')} {message.get('from')} -> "
            f"{message.get('to')} {message.get('kind')}: "
            f"{message.get('subject')}"
        )
    if not open_messages:
        lines.append("(none)")
    lines.append("")

    lines += ["## Recent decisions", ""]
    decisions = [e for e in events if e.get("kind") == "decision"][-5:]
    for event in decisions:
        lines.append(
            f"- {event.get('id')} {event.get('actor')}: {event.get('text')} "
            f"({event.get('source')})"
        )
    if not decisions:
        lines.append("(none)")
    lines.append("")

    lines += ["## Constraints", ""]
    constraints = [e for e in events if e.get("kind") == "constraint"][-5:]
    for event in constraints:
        lines.append(
            f"- {event.get('id')} {event.get('actor')}: {event.get('text')} "
            f"({event.get('source')})"
        )
    if not constraints:
        lines.append("(none)")
    lines.append("")

    lines += ["## Artifacts", ""]
    artifacts = state.get("artifacts", [])
    for artifact in artifacts if isinstance(artifacts, list) else []:
        if isinstance(artifact, dict):
            lines.append(
                f"- {artifact.get('path')} ({artifact.get('sha256', '?')})"
            )
        else:
            lines.append(f"- {artifact}")
    if not artifacts:
        lines.append("(none)")
    lines.append("")

    lines += ["## Next action", ""]
    lines.append(str(state.get("last_action") or "(run org_loop.py next)"))
    lines.append("")

    lines += ["## Last digest source", ""]
    if events:
        last = events[-1]
        lines.append(f"- {last.get('id')} from {last.get('source')}")
    else:
        lines.append("(no recorded events)")
    lines.append("")
    lines += [END]
    return "\n".join(lines)


def digest(root: Path) -> str:
    path = _context_path(root)
    existing = (
        path.read_text(encoding="utf-8")
        if path.is_file()
        else "# Org memory — main context\n\n" f"{BEGIN}\n{END}\n\n## Curated notes\n\n"
    )
    block = _machine_block(root)
    pattern = re.compile(
        re.escape(BEGIN) + r".*?" + re.escape(END),
        re.DOTALL,
    )
    if pattern.search(existing):
        text = pattern.sub(block, existing, count=1)
    else:
        text = existing.rstrip() + "\n\n" + block + "\n"
    common.atomic_write(path, text)
    return text


def check(root: Path) -> list[str]:
    violations: list[str] = []
    path = _context_path(root)
    if not path.is_file():
        violations.append(f"{path}: missing context file")
    else:
        text = path.read_text(encoding="utf-8")
        if len(text.encode("utf-8")) > MAX_BYTES:
            violations.append(f"context.md exceeds the {MAX_BYTES}-byte budget")
        if len(text.splitlines()) > MAX_LINES:
            violations.append(f"context.md exceeds the {MAX_LINES}-line budget")
        block_match = re.search(
            re.escape(BEGIN) + r".*?" + re.escape(END),
            text,
            re.DOTALL,
        )
        if not block_match:
            violations.append("context.md is missing the machine block")
        else:
            block = block_match.group(0)
            for heading in REQUIRED_HEADINGS:
                if heading not in block:
                    violations.append(f"context.md machine block missing {heading}")
            if block != _machine_block(root):
                violations.append("context.md is stale; run org_memory.py digest")
    try:
        events = load_events(root)
    except ValueError as exc:
        return violations + [str(exc)]
    seen: set[str] = set()
    for event in events:
        event_id = event.get("id")
        if not isinstance(event_id, str) or not event_id:
            violations.append("event without id")
        elif event_id in seen:
            violations.append(f"duplicate event id {event_id}")
        seen.add(event_id)
        if event.get("kind") not in KINDS:
            violations.append(f"{event_id}: invalid kind")
        if not event.get("source"):
            violations.append(f"{event_id}: missing source")
        if not event.get("actor") or not event.get("text"):
            violations.append(f"{event_id}: missing actor or text")
        timestamp = event.get("ts")
        valid_timestamp = isinstance(timestamp, str) and re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", timestamp
        )
        if valid_timestamp:
            try:
                datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                valid_timestamp = False
        if not valid_timestamp:
            violations.append(f"{event_id}: missing or invalid timestamp")
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    add_p = sub.add_parser("add")
    add_p.add_argument("--kind", required=True, choices=KINDS)
    add_p.add_argument("--actor", required=True)
    add_p.add_argument("--text", required=True)
    add_p.add_argument("--source", required=True)
    add_p.add_argument("--tags", default="")

    sub.add_parser("digest")
    check_p = sub.add_parser("check")
    check_p.add_argument("--json", action="store_true")
    sub.add_parser("show")

    args = parser.parse_args(argv)
    root = _root(args.root)
    try:
        if args.command == "add":
            tags = [tag.strip() for tag in args.tags.split(",") if tag.strip()]
            event = add(root, args.kind, args.actor, args.text, args.source, tags)
            print(f"recorded {event['id']}")
            return 0
        if args.command == "digest":
            digest(root)
            print(f"digested -> {_context_path(root)}")
            return 0
        if args.command == "show":
            print(_context_path(root).read_text(encoding="utf-8"))
            return 0
        violations = check(root)
        if args.json:
            print(json.dumps({"violations": violations}, indent=2))
        else:
            for violation in violations:
                print(f"violation: {violation}")
            print(f"memory check: {len(violations)} violation(s)")
        return 1 if violations else 0
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
