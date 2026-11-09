#!/usr/bin/env python3
"""Shared helpers for the agent-org tools."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from org_status import load_yaml_file  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_create(path: Path, text: str) -> None:
    """Atomically publish a new file without overwriting an existing one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.link(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def _parse_value(raw: str):
    value = raw.strip()
    if value in ("true", "True"):
        return True
    if value in ("false", "False"):
        return False
    if value in ("null", "None", "~", ""):
        return None if value else ""
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [] if not inner else [
            _parse_value(item.strip()) for item in inner.split(",")
        ]
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def _emit_value(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(json.dumps(str(item)) for item in value) + "]"
    text = str(value)
    if not text or any(ch in text for ch in ":#[]{},\n") or text != text.strip():
        return json.dumps(text)
    return text


def read_frontmatter(text: str) -> tuple[dict, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return {}, text
    fields: dict = {}
    for line in lines[1:end]:
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        fields[key.strip()] = _parse_value(raw)
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return fields, body


def render_frontmatter(fields: dict) -> str:
    return "---\n" + "\n".join(
        f"{key}: {_emit_value(value)}" for key, value in fields.items()
    ) + "\n---\n"


def render_message(fields: dict, body: str) -> str:
    return render_frontmatter(fields) + "\n" + body.rstrip() + "\n"


def agent_names(chart: dict) -> list[str]:
    names: list[str] = []
    agents = chart.get("agents", {})
    if not isinstance(agents, dict):
        return names
    for role, config in agents.items():
        if not isinstance(config, dict) or config.get("enabled", True) is False:
            continue
        if "count" in config:
            count = config.get("count", 1)
            if not isinstance(count, int) or count < 1:
                count = 1
            names.extend(f"{role}-{i}" for i in range(1, count + 1))
        else:
            names.append(role)
    return names
