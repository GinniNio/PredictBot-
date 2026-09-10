"""Dependency-free loader for `data_pipeline`'s own restricted-YAML config
files (currently just `sources/football_data_sources.yaml`).

This is a deliberate, standalone copy of the same restricted-YAML subset
`src/pcbf_calculator/registries/yamlmini.py` implements (documented in
`docs/MULTI_SPORT_ARCHITECTURE.md`), not an import of it. `data_pipeline`
is a separate, additive tool tree that must not depend on
`src/pcbf_calculator` being importable (e.g. `python3 data_pipeline/
download.py` run without `PYTHONPATH=src` set) and must not modify or
couple itself to that package's internals. Keeping this parser
self-contained also means this PR never edits any existing
`pcbf_calculator` source file.

Grammar (identical subset):
- One top-level key, `categories`, mapping to a block sequence.
- Each sequence item is a flat mapping of `key: value` pairs.
- Values are scalars (bare word/number/`null`/`true`/`false`, or a
  double-quoted string) or a flow sequence `[a, b, c]` of bare words.
- `#` starts a comment when not inside a quoted string.

Zero third-party dependencies — stdlib only.
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
    if text in ("null", "~", ""):
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
        if any(ch in text for ch in ".eE") and text.replace(".", "", 1).lstrip("+-").isdigit():
            return float(text)
        return int(text)
    except ValueError:
        return text


def load_yaml_categories(text: str) -> list[dict[str, Any]]:
    """Parse one document into its `categories` list of flat row dicts."""
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
                raise ValueError(f"Unsupported top-level key: {key!r}")
            saw_top_key = True
            continue

        stripped = line.strip()
        if stripped.startswith("- "):
            if current is not None:
                items.append(current)
            current = {}
            stripped = stripped[2:]
        if current is None:
            raise ValueError(f"Field before any '- ' item start: {line!r}")

        if ":" not in stripped:
            raise ValueError(f"Malformed line (expected 'key: value'): {line!r}")
        field, _, value = stripped.partition(":")
        current[field.strip()] = _parse_scalar(value)

    if current is not None:
        items.append(current)
    if not saw_top_key:
        raise ValueError("Document is missing the top-level 'categories' key")
    return items


def load_yaml_categories_file(path: str) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as handle:
        return load_yaml_categories(handle.read())
