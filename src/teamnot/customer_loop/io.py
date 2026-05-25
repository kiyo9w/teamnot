"""YAML and JSON helpers for customer-loop artifacts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from teamnot.customer_loop.models import (
    CustomerLoopValidationError,
    DomainOutputOracle,
    SeededCustomerState,
)

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


def load_seeded_state(path: str | Path) -> SeededCustomerState:
    try:
        return load_model(path, SeededCustomerState)
    except CustomerLoopValidationError as exc:
        raise CustomerLoopValidationError(
            f"Seeded customer state fixture is invalid. Expected storage_state_path, "
            f"cookies, local_storage, test_account, login_url, cleanup/reset notes, "
            f"workspace_id, and safety_constraints in {path}.\n{exc}"
        ) from exc


def load_domain_oracles(path: str | Path) -> list[DomainOutputOracle]:
    data = load_yaml(path)
    raw_oracles = data.get("oracles", data)
    if isinstance(raw_oracles, dict):
        raw_oracles = [raw_oracles]
    if not isinstance(raw_oracles, list):
        raise CustomerLoopValidationError(f"Expected domain oracle mapping or list in {path}")
    try:
        return [DomainOutputOracle.model_validate(item) for item in raw_oracles]
    except ValidationError as exc:
        raise CustomerLoopValidationError(
            f"Domain oracle fixture is invalid. Expected expected_output, golden_file, "
            f"api_check, semantic_rubric, or manual_checkpoint in {path}.\n{exc}"
        ) from exc


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
