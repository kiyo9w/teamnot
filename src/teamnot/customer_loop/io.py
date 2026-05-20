"""YAML and JSON helpers for customer-loop artifacts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from teamnot.customer_loop.models import CustomerLoopValidationError

ModelT = TypeVar("ModelT", bound=BaseModel)


def load_yaml(path: str | Path) -> dict[str, Any]:
    p = Path(path).expanduser()
    if not p.exists():
        raise CustomerLoopValidationError(f"Customer-loop file not found: {p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise CustomerLoopValidationError(f"YAML parse error in {p}: {e}") from e
    if not isinstance(data, dict):
        raise CustomerLoopValidationError(f"Expected mapping in {p}, got {type(data).__name__}")
    return data


def load_model(path: str | Path, model: type[ModelT]) -> ModelT:
    data = load_yaml(path)
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise CustomerLoopValidationError(f"Schema validation failed for {path}:\n{e}") from e


def save_yaml(data: BaseModel | dict[str, Any], path: str | Path) -> Path:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = data.model_dump(mode="json") if isinstance(data, BaseModel) else data
    p.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return p


def save_json(data: BaseModel | dict[str, Any], path: str | Path) -> Path:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = data.model_dump(mode="json") if isinstance(data, BaseModel) else data
    p.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    return p
