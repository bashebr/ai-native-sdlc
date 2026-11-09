#!/usr/bin/env python3
"""Versioned agent-to-agent message bus.

Messages live in org/messages/<id>.md and are validated against
org/org-chart.yaml. Communication is asynchronous: agents check their inbox and
reply or close messages. An open requires_response message blocks the turn.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import org_common as common  # noqa: E402

KINDS = (
    "question",
    "answer",
    "decision",
    "handoff",
    "review",
    "finding",
    "escalation",
    "status",
)
STATUSES = ("open", "answered", "closed")
SEVERITIES = ("low", "medium", "high", "critical")
BLOCKING_SEVERITIES = ("high", "critical")


def _root(value: str | None) -> Path:
    return Path(value).expanduser().resolve() if value else SCRIPT_DIR.parent


def _messages_dir(root: Path) -> Path:
    return root / "org" / "messages"


def _chart(root: Path) -> dict:
    return common.load_yaml_file(root / "org" / "org-chart.yaml")


def _known(root: Path) -> list[str]:
    return common.agent_names(_chart(root))


def _next_id(root: Path, sender: str) -> str:
    pattern = re.compile(rf"^{re.escape(sender)}-(\d{{4}})\.md$")
    used = [
        int(match.group(1))
        for path in _messages_dir(root).glob("*.md")
        if (match := pattern.match(path.name))
    ]
    return f"{sender}-{(max(used, default=0) + 1):04d}"


def _path(root: Path, message_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", message_id):
        raise ValueError(f"invalid message id {message_id!r}")
    return _messages_dir(root) / f"{message_id}.md"


def load_messages(root: Path) -> list[dict]:
    out: list[dict] = []
    for path in sorted(_messages_dir(root).glob("*.md")):
        text = path.read_text(encoding="utf-8")
        fields, body = common.read_frontmatter(text)
        fields["body"] = body
        fields["_path"] = path
        error = _frontmatter_error(text)
        if error:
            fields["_frontmatter_error"] = error
        out.append(fields)
    return out


def _frontmatter_error(text: str) -> str | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return "missing opening delimiter"
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return "missing closing delimiter"
    for line in lines[1:end]:
        if line.strip() and ":" not in line:
            return f"invalid field line {line!r}"
    return None


def _validate_parties(root: Path, sender: str, recipient: str) -> None:
    known = _known(root)
    for label, name in (("sender", sender), ("recipient", recipient)):
        if name not in known:
            raise ValueError(
                f"{label} {name!r} is not an enabled agent in org-chart.yaml"
            )
    if sender == recipient:
        raise ValueError("sender and recipient must differ")


def _is_blocking(kind: str, severity: str | None, blocking: bool) -> bool:
    if kind != "finding":
        if blocking:
            raise ValueError("only findings may be blocking")
        return False
    if blocking and severity not in BLOCKING_SEVERITIES:
        raise ValueError("a blocking finding requires severity high or critical")
    return blocking or severity in BLOCKING_SEVERITIES


def _create_message(path: Path, text: str) -> None:
    """Create a message without overwriting a concurrently allocated id."""
    common.atomic_create(path, text)


def send(
    root: Path,
    sender: str,
    recipient: str,
    kind: str,
    subject: str,
    body: str,
    artifact: str | None = None,
    requires_response: bool = False,
    severity: str | None = None,
    blocking: bool = False,
    in_reply_to: str | None = None,
) -> dict:
    if kind not in KINDS:
        raise ValueError(f"invalid kind {kind!r}")
    if severity is not None and severity not in SEVERITIES:
        raise ValueError(f"invalid severity {severity!r}")
    if not subject.strip():
        raise ValueError("subject is required")
    _validate_parties(root, sender, recipient)
    blocking = _is_blocking(kind, severity, blocking)
    if in_reply_to and not _path(root, in_reply_to).is_file():
        raise ValueError(f"reply target {in_reply_to!r} does not exist")
    if kind == "question":
        requires_response = True

    fields = {
        "id": None,
        "from": sender,
        "to": recipient,
        "kind": kind,
        "subject": subject.strip(),
        "artifact": artifact,
        "requires_response": requires_response,
        "in_reply_to": in_reply_to,
        "status": "open",
        "severity": severity,
        "blocking": blocking,
        "created_at": common.now_iso(),
    }
    for _ in range(1000):
        message_id = _next_id(root, sender)
        fields["id"] = message_id
        path = _path(root, message_id)
        payload = common.render_message(fields, body)
        try:
            _create_message(path, payload)
        except FileExistsError:
            continue
        fields["body"] = body
        fields["_path"] = path
        return fields
    raise ValueError("could not allocate a unique message id")


def _body(path: Path) -> str:
    _, body = common.read_frontmatter(path.read_text(encoding="utf-8"))
    return body


def reply(root: Path, message_id: str, sender: str, body: str) -> dict:
    path = _path(root, message_id)
    if not path.is_file():
        raise ValueError(f"message {message_id!r} does not exist")
    original, _ = common.read_frontmatter(path.read_text(encoding="utf-8"))
    if sender != original.get("to"):
        raise ValueError(f"only {original.get('to')!r} may reply to {message_id}")
    answer = send(
        root,
        sender=sender,
        recipient=str(original.get("from")),
        kind="answer",
        subject=f"Re: {original.get('subject', '')}",
        body=body,
        artifact=original.get("artifact"),
        in_reply_to=message_id,
    )
    original["status"] = "answered"
    common.atomic_write(path, common.render_message(original, _body(path)))
    return answer


def close(root: Path, message_id: str, actor: str, evidence: str | None) -> dict:
    path = _path(root, message_id)
    if not path.is_file():
        raise ValueError(f"message {message_id!r} does not exist")
    fields, body = common.read_frontmatter(path.read_text(encoding="utf-8"))
    if actor not in (fields.get("from"), "orchestrator"):
        raise ValueError("only the sender or orchestrator may close a message")
    if fields.get("kind") == "finding" and fields.get("blocking") and not evidence:
        raise ValueError("a blocking finding requires closure evidence")
    fields["status"] = "closed"
    fields["closed_by"] = actor
    if evidence:
        fields["closed_evidence"] = evidence
    common.atomic_write(path, common.render_message(fields, body))
    return fields


def validate(root: Path) -> list[str]:
    violations: list[str] = []
    known = _known(root)
    seen: set[str] = set()
    messages = load_messages(root)
    ids = {m.get("id") for m in messages}
    for message in messages:
        if message.get("_frontmatter_error"):
            violations.append(
                f"{message['_path']}: malformed frontmatter: "
                f"{message['_frontmatter_error']}"
            )
            continue
        current = message.get("id")
        path = message["_path"]
        if not isinstance(current, str) or not current:
            violations.append(f"{path}: missing id")
            continue
        if current in seen:
            violations.append(f"{path}: duplicate id {current}")
        seen.add(current)
        if path.stem != current:
            violations.append(f"{path}: filename does not match id {current}")
        for field in ("from", "to", "kind", "subject", "status", "created_at"):
            if not message.get(field):
                violations.append(f"{current}: missing {field}")
        if message.get("from") not in known:
            violations.append(f"{current}: unknown sender {message.get('from')!r}")
        if message.get("to") not in known:
            violations.append(f"{current}: unknown recipient {message.get('to')!r}")
        if message.get("kind") not in KINDS:
            violations.append(f"{current}: invalid kind {message.get('kind')!r}")
        if message.get("status") not in STATUSES:
            violations.append(f"{current}: invalid status {message.get('status')!r}")
        reply_to = message.get("in_reply_to")
        if reply_to and reply_to not in ids:
            violations.append(f"{current}: reply target {reply_to!r} not found")
        severity = message.get("severity")
        if severity is not None and severity not in SEVERITIES:
            violations.append(f"{current}: invalid severity {severity!r}")
        if message.get("kind") == "finding" and message.get("blocking"):
            if severity not in BLOCKING_SEVERITIES:
                violations.append(
                    f"{current}: blocking finding needs high/critical severity"
                )
            if message.get("status") == "closed" and not message.get(
                "closed_evidence"
            ):
                violations.append(f"{current}: blocking finding lacks closure evidence")
    return violations


def _read_body(args) -> str:
    if args.body and args.body_file:
        raise ValueError("use --body or --body-file, not both")
    if args.body_file:
        return Path(args.body_file).read_text(encoding="utf-8")
    return args.body or ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", default=None, help="project root (default: script parent)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    send_p = sub.add_parser("send")
    send_p.add_argument("--from", dest="sender", required=True)
    send_p.add_argument("--to", dest="recipient", required=True)
    send_p.add_argument("--kind", required=True, choices=KINDS)
    send_p.add_argument("--subject", required=True)
    send_p.add_argument("--body", default=None)
    send_p.add_argument("--body-file", default=None)
    send_p.add_argument("--artifact", default=None)
    send_p.add_argument("--requires-response", action="store_true")
    send_p.add_argument("--severity", choices=SEVERITIES, default=None)
    send_p.add_argument("--blocking", action="store_true")
    send_p.add_argument("--in-reply-to", default=None)

    inbox_p = sub.add_parser("inbox")
    inbox_p.add_argument("--agent", required=True)
    inbox_p.add_argument("--status", choices=STATUSES, default="open")
    inbox_p.add_argument("--json", action="store_true")

    reply_p = sub.add_parser("reply")
    reply_p.add_argument("--id", required=True)
    reply_p.add_argument("--from", dest="sender", required=True)
    reply_p.add_argument("--body", default=None)
    reply_p.add_argument("--body-file", default=None)

    close_p = sub.add_parser("close")
    close_p.add_argument("--id", required=True)
    close_p.add_argument("--by", required=True)
    close_p.add_argument("--evidence", default=None)

    check_p = sub.add_parser("check")
    check_p.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    root = _root(args.root)
    try:
        if args.command == "send":
            message = send(
                root,
                args.sender,
                args.recipient,
                args.kind,
                args.subject,
                _read_body(args),
                args.artifact,
                args.requires_response,
                args.severity,
                args.blocking,
                args.in_reply_to,
            )
            print(f"sent {message['id']} -> {args.recipient}")
            return 0
        if args.command == "inbox":
            rows = [
                m
                for m in load_messages(root)
                if m.get("to") == args.agent and m.get("status") == args.status
            ]
            if args.json:
                print(json.dumps(rows, default=str, indent=2))
            else:
                for message in rows:
                    print(
                        f"{message.get('id')}  {message.get('from')} -> "
                        f"{message.get('to')}  {message.get('kind')}  "
                        f"{message.get('subject')}"
                    )
                print(f"{len(rows)} message(s)")
            return 0
        if args.command == "reply":
            message = reply(root, args.id, args.sender, _read_body(args))
            print(f"replied with {message['id']}")
            return 0
        if args.command == "close":
            close(root, args.id, args.by, args.evidence)
            print(f"closed {args.id}")
            return 0
        violations = validate(root)
        if args.json:
            print(json.dumps({"violations": violations}, indent=2))
        else:
            for violation in violations:
                print(f"violation: {violation}")
            print(
                f"message check: {len(load_messages(root))} message(s), "
                f"{len(violations)} violation(s)"
            )
        return 1 if violations else 0
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
