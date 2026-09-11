"""Structural validation for both ledgers' JSONL records, against the
versioned schema files under ``ledgers/schemas/``.

A small, hand-rolled checker (type/required/enum/const/pattern/minimum/
exclusiveMinimum/minItems, recursing through ``properties`` and array
``items``) -- not a full JSON-Schema implementation, and deliberately not a
dependency on the third-party ``jsonschema`` package (this repository ships
zero runtime dependencies). It covers exactly the constraints the two
schema files under ``ledgers/schemas/`` actually use.

Every envelope is checked against the top-level schema's own
``required``/``properties`` first, then against that specific
``event_type``'s entry in the schema's ``event_payloads`` mapping.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"

_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "object": (dict,),
    "array": (list,),
    "boolean": (bool,),
    "null": (type(None),),
}


class ValidationError(Exception):
    """Raised with a list of human-readable, path-qualified error strings
    -- never a single opaque message -- so a caller validating a whole
    JSONL file can report every line's every problem in one pass."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def load_schema(name: str) -> dict[str, Any]:
    """``name`` is e.g. ``forecast_ledger.v1`` -- loads
    ``ledgers/schemas/<name>.schema.json``."""

    return json.loads((SCHEMAS_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def _check_type(value: Any, type_spec: Any, path: str, errors: list[str]) -> bool:
    if type_spec is None:
        return True
    allowed_names = type_spec if isinstance(type_spec, list) else [type_spec]
    allowed_types: tuple[type, ...] = tuple(t for name in allowed_names for t in _TYPE_MAP.get(name, ()))
    # bool is a subclass of int in Python -- never accept a bool where an
    # integer/number was asked for, and vice versa, unless "boolean" is
    # explicitly one of the allowed names.
    if isinstance(value, bool) and "boolean" not in allowed_names:
        errors.append(f"{path}: expected {allowed_names}, got boolean")
        return False
    if not isinstance(value, allowed_types):
        errors.append(f"{path}: expected {allowed_names}, got {type(value).__name__}")
        return False
    return True


def _check_node(value: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}, got {value!r}")
        return

    if "type" in schema and not _check_type(value, schema["type"], path, errors):
        return

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")

    if "pattern" in schema and isinstance(value, str) and not re.match(schema["pattern"], value):
        errors.append(f"{path}: {value!r} does not match pattern {schema['pattern']!r}")

    if "minimum" in schema and isinstance(value, (int, float)) and value < schema["minimum"]:
        errors.append(f"{path}: {value!r} is below minimum {schema['minimum']}")

    if "exclusiveMinimum" in schema and isinstance(value, (int, float)) and value <= schema["exclusiveMinimum"]:
        errors.append(f"{path}: {value!r} must be strictly greater than {schema['exclusiveMinimum']}")

    if "minItems" in schema and isinstance(value, list) and len(value) < schema["minItems"]:
        errors.append(f"{path}: has {len(value)} item(s), needs at least {schema['minItems']}")

    if isinstance(value, dict):
        for required_key in schema.get("required", []):
            if required_key not in value:
                errors.append(f"{path}: missing required field {required_key!r}")
        properties = schema.get("properties", {})
        for key, sub_schema in properties.items():
            if key in value:
                _check_node(value[key], sub_schema, f"{path}.{key}", errors)

    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            _check_node(item, schema["items"], f"{path}[{i}]", errors)


def validate_envelope(record: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Validate one JSONL record's envelope fields against ``schema``
    (top-level ``required``/``properties``, excluding ``payload``'s own
    contents), then its ``payload`` against ``schema["event_payloads"]
    [record["event_type"]]`` if that event type is recognized. Returns a
    list of error strings (empty if valid) -- never raises on its own; the
    caller decides whether to raise."""

    errors: list[str] = []

    if not isinstance(record, dict):
        return [f"record: expected object, got {type(record).__name__}"]

    for required_key in schema.get("required", []):
        if required_key not in record:
            errors.append(f"record: missing required field {required_key!r}")
    for key, sub_schema in schema.get("properties", {}).items():
        if key in record and key != "payload":
            _check_node(record[key], sub_schema, key, errors)

    event_type = record.get("event_type")
    payload = record.get("payload")
    event_payloads = schema.get("event_payloads", {})
    if event_type in event_payloads:
        if not isinstance(payload, dict):
            errors.append(f"payload: expected object for event_type {event_type!r}, got {type(payload).__name__}")
        else:
            _check_node(payload, event_payloads[event_type], "payload", errors)
    elif event_type is not None:
        errors.append(f"event_type: {event_type!r} is not a recognized event type for this schema")

    return errors


def validate_record(record: dict[str, Any], schema_name: str) -> None:
    """Raises ``ValidationError`` (never returns a boolean) if ``record``
    does not conform to ``schema_name`` (e.g. ``forecast_ledger.v1``)."""

    schema = load_schema(schema_name)
    errors = validate_envelope(record, schema)
    if errors:
        raise ValidationError(errors)


def validate_jsonl_file(path: Path, schema_name: str) -> list[str]:
    """Validate every line of a JSONL ledger file. Returns a list of
    ``"line N: ..."``-prefixed error strings across the whole file --
    empty if every line is valid. A malformed (non-JSON) line is reported
    by line number rather than crashing the whole validation pass."""

    schema = load_schema(schema_name)
    all_errors: list[str] = []
    if not path.exists():
        return [f"{path}: file does not exist"]
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            all_errors.append(f"line {line_number}: invalid JSON ({exc})")
            continue
        for error in validate_envelope(record, schema):
            all_errors.append(f"line {line_number}: {error}")
    return all_errors
