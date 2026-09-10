"""Dependency-free loader for the Release-A registry YAML files.

The registries in this repository are deliberately written in a restricted
subset of YAML (documented in ``docs/MULTI_SPORT_ARCHITECTURE.md``):

- One top-level key, ``categories``, mapping to a block sequence.
- Each sequence item is a flat mapping of ``key: value`` pairs (two-space
  step indentation), i.e. no nested block mappings inside an item.
- Values are scalars (bare word/number/``null``/``true``/``false``, or a
  double-quoted string) or a flow sequence ``[a, b, c]`` of bare words.
- ``#`` starts a comment when it is not inside a quoted string.

This subset is sufficient for every registry file in this repository and
lets the platform avoid a PyYAML runtime dependency, preserving the
zero-runtime-dependency constraint in ``docs/HOST_CONTRACT.md``. It is not a
general-purpose YAML parser and must not be used outside the registries.
"""

from __future__ import annotations

from typing import Any


def _strip_comment(line: str) -> str:
    in_quotes = False
    for index, char in enumerate(line):
        if char == '"':
            in_quotes = not in_quotes
        elif char == "#" and not in_quotes:
            return line[:index]
    return line


def _parse_scalar(raw: str) -> Any:
    text = raw.strip()
    if text == "null" or text == "~" or text == "":
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        return text[1:-1].replace('\\"', '"')
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(item) for item in inner.split(",")]
    try:
        if any(ch in text for ch in (".", "e", "E")) and text.replace(".", "", 1).replace(
            "-", "", 1
        ).replace("e", "", 1).replace("E", "", 1).replace("+", "", 1).replace("-", "", 1).isdigit():
            return float(text)
        return int(text)
    except ValueError:
        return text


def load_registry(text: str) -> dict[str, list[dict[str, Any]]]:
    """Parse one registry document into ``{"categories": [ {...}, ... ]}``."""
    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    saw_top_key = False

    for raw_line in text.splitlines():
        line = _strip_comment(raw_line).rstrip()
        if not line.strip():
            continue

        if not line.startswith(" "):
            key = line.rstrip(":").strip()
            if key != "categories":
                raise ValueError(f"Unsupported top-level key in registry: {key!r}")
            saw_top_key = True
            continue

        stripped = line.strip()
        if stripped.startswith("- "):
            if current is not None:
                items.append(current)
            current = {}
            stripped = stripped[2:]
        if current is None:
            raise ValueError(f"Registry item field before any '- ' item start: {line!r}")

        if ":" not in stripped:
            raise ValueError(f"Malformed registry line (expected 'key: value'): {line!r}")
        field, _, value = stripped.partition(":")
        current[field.strip()] = _parse_scalar(value)

    if current is not None:
        items.append(current)
    if not saw_top_key:
        raise ValueError("Registry document is missing the top-level 'categories' key")
    return {"categories": items}


def load_registry_file(path: str) -> dict[str, list[dict[str, Any]]]:
    with open(path, "r", encoding="utf-8") as handle:
        return load_registry(handle.read())
