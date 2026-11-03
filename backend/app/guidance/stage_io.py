"""Guidance 职责边界：stage_io。"""

from __future__ import annotations

import json
from pathlib import Path


def _required_input(input_path: Path | None) -> Path:
    if input_path is None:
        raise ValueError("stage requires the previous saved contract")
    return input_path

def _write_model(path: Path, model) -> None:
    path.write_text(
        json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}

def _read_json_if_exists(path: Path) -> dict:
    return _read_json(path) if path.is_file() else {}
