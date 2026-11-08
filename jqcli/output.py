from __future__ import annotations

import json
import sys
from typing import Any

from rich.console import Console

from .errors import JqcliError, error_payload


def write_json(payload: Any, *, err: bool = False) -> None:
    _write_json(payload, err=err, compact=False)


def write_json_line(payload: Any, *, err: bool = False) -> None:
    _write_json(payload, err=err, compact=True)


def _write_json(payload: Any, *, err: bool, compact: bool) -> None:
    stream = sys.stderr if err else sys.stdout
    indent = None if compact else 2
    separators = (",", ":") if compact else None
    text = json.dumps(payload, ensure_ascii=False, indent=indent, separators=separators)
    try:
        stream.write(text + "\n")
    except UnicodeEncodeError:
        text = json.dumps(payload, ensure_ascii=True, indent=indent, separators=separators)
        stream.write(text + "\n")
    if compact:
        stream.flush()


def write_error(error: JqcliError, *, json_format: bool) -> None:
    if json_format:
        write_json(error_payload(error), err=True)
    else:
        Console(stderr=True).print(f"[red]错误:[/red] {error.message}")


def mask_sensitive(value: str) -> str:
    lowered = value.lower()
    for key in ("authorization", "cookie", "token", "password"):
        if key in lowered:
            return "<redacted>"
    return value
