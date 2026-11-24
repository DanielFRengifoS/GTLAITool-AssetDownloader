"""Small, atomic JSON stores for local desktop preferences and history."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def read_json(path: Path, expected_type: type, default):
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default
    return value if isinstance(value, expected_type) else default


def write_json(path: Path, value) -> None:
    """Replace a complete file; a failed write leaves the previous file intact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
