"""Loader and lookup for the model-admission registry
(``registries/data/model-admission-registry.yaml``).

This is the **only** authoritative source the decision layer
(``decision/engine.py``) may use to decide whether a specific adapter's
specific model version has cleared backtest gates, prospective validation,
or CASH admission. It is a committed, versioned file, not request input:
nothing in this module ever reads a caller-supplied "I am approved" flag
from a request, a ``decision_input`` block, or an adapter's own
``ForecastResult`` — approval is resolved purely by looking up the exact
``(adapter_id, model_version)`` pair here and checking the row's
``model_artifact_hash`` against the hash the caller/adapter actually
reports for the model it ran. A mismatch (or no row at all) always resolves
to "not approved" — fail closed by construction.
"""

from __future__ import annotations

import importlib.resources
from typing import Any

from .yamlmini import load_registry

FILENAME = "model-admission-registry.yaml"

ALLOWED_GATE_STATUSES = {"PENDING", "APPROVED", "REJECTED"}

REQUIRED_FIELDS = (
    "id",
    "adapter_id",
    "model_version",
    "model_artifact_hash",
    "backtest_gates_status",
    "prospective_status",
    "cash_admission_status",
    "rationale",
)

_CLOSED_ADMISSION: dict[str, bool] = {
    "backtest_gates_approved": False,
    "prospective_approved": False,
    "cash_admission_approved": False,
}


def _read_bundled() -> str:
    resource = importlib.resources.files("pcbf_calculator.registries.data").joinpath(FILENAME)
    return resource.read_text(encoding="utf-8")


def load_model_admission_rows() -> list[dict[str, Any]]:
    """Return every row in the model-admission registry, unfiltered."""
    return load_registry(_read_bundled())["categories"]


def lookup_model_admission(adapter_id: Any, model_version: Any) -> dict[str, Any] | None:
    """Return the row matching this exact ``(adapter_id, model_version)``
    pair, or ``None`` if no such row exists (an unregistered/unknown model
    version — the common case for every model version in this release)."""
    if not adapter_id or not model_version:
        return None
    for row in load_model_admission_rows():
        if row.get("adapter_id") == adapter_id and row.get("model_version") == model_version:
            return row
    return None


def resolve_model_admission(
    adapter_id: Any, model_version: Any, model_artifact_hash: Any
) -> dict[str, bool]:
    """Resolve the three admission booleans for this exact
    ``(adapter_id, model_version, model_artifact_hash)`` triple.

    Defaults fully closed (every flag ``False``) unless:
    1. a row exists for this exact ``adapter_id``/``model_version`` pair,
    2. that row's stored ``model_artifact_hash`` matches ``model_artifact_hash``
       exactly (a mismatch means a stale registry row or an attempt to
       piggyback an unapproved model build on an approved version's
       identity — never trusted), and
    3. the relevant status field on that row is ``APPROVED``.

    Never raises for malformed input — an absent/mismatched/unknown lookup
    is exactly the normal, safe "not approved" case, not an error.

    Defense in depth against a malformed/inconsistent row (validation —
    ``validate_model_admission_registry`` below — should already catch this
    at the file level, but resolution must never trust a row's later-stage
    status in isolation): ``prospective_approved`` can only ever be ``True``
    when ``backtest_gates_status`` on the same row is also ``APPROVED``, and
    ``cash_admission_approved`` can only ever be ``True`` when BOTH
    ``backtest_gates_status`` and ``prospective_status`` are ``APPROVED``. A
    row that violates this ordering resolves closed for the affected flag(s)
    specifically, not the whole row.
    """
    row = lookup_model_admission(adapter_id, model_version)
    if row is None:
        return dict(_CLOSED_ADMISSION)
    if not model_artifact_hash or row.get("model_artifact_hash") != model_artifact_hash:
        return dict(_CLOSED_ADMISSION)
    backtest_gates_approved = row.get("backtest_gates_status") == "APPROVED"
    prospective_approved = backtest_gates_approved and row.get("prospective_status") == "APPROVED"
    cash_admission_approved = (
        backtest_gates_approved
        and prospective_approved
        and row.get("cash_admission_status") == "APPROVED"
    )
    return {
        "backtest_gates_approved": backtest_gates_approved,
        "prospective_approved": prospective_approved,
        "cash_admission_approved": cash_admission_approved,
    }


def validate_model_admission_registry() -> list[str]:
    """Return a list of shape/consistency problems (empty means clean).

    Checked: every row declares every required field; every status field is
    one of the allowed enum values; no duplicate ``(adapter_id,
    model_version)`` pair (which would make lookup ambiguous); no duplicate
    ``id``; and the gate-ordering invariant — a row may not claim
    ``prospective_status: APPROVED`` unless its own ``backtest_gates_status``
    is also ``APPROVED``, and may not claim ``cash_admission_status:
    APPROVED`` unless BOTH ``backtest_gates_status`` and ``prospective_status``
    are ``APPROVED`` (a row cannot skip an earlier gate and still claim a
    later one).
    """
    problems: list[str] = []
    rows = load_model_admission_rows()
    seen_ids: set[str] = set()
    seen_pairs: set[tuple[Any, Any]] = set()
    for row in rows:
        row_id = row.get("id")
        for field in REQUIRED_FIELDS:
            if field not in row or row[field] is None:
                problems.append(f"model-admission-registry.yaml: row '{row_id}' missing field '{field}'")
        for status_field in ("backtest_gates_status", "prospective_status", "cash_admission_status"):
            value = row.get(status_field)
            if value is not None and value not in ALLOWED_GATE_STATUSES:
                problems.append(
                    f"model-admission-registry.yaml: row '{row_id}' has invalid {status_field} "
                    f"'{value}' (allowed: {sorted(ALLOWED_GATE_STATUSES)})"
                )
        if row_id is not None:
            if row_id in seen_ids:
                problems.append(f"model-admission-registry.yaml: duplicate id '{row_id}'")
            seen_ids.add(row_id)
        pair = (row.get("adapter_id"), row.get("model_version"))
        if pair in seen_pairs:
            problems.append(f"model-admission-registry.yaml: duplicate (adapter_id, model_version) {pair}")
        seen_pairs.add(pair)

        backtest_status = row.get("backtest_gates_status")
        prospective_status = row.get("prospective_status")
        cash_status = row.get("cash_admission_status")
        if prospective_status == "APPROVED" and backtest_status != "APPROVED":
            problems.append(
                f"model-admission-registry.yaml: row '{row_id}' has prospective_status APPROVED "
                f"but backtest_gates_status is '{backtest_status}' (must be APPROVED first)"
            )
        if cash_status == "APPROVED" and not (backtest_status == "APPROVED" and prospective_status == "APPROVED"):
            problems.append(
                f"model-admission-registry.yaml: row '{row_id}' has cash_admission_status APPROVED "
                f"but backtest_gates_status is '{backtest_status}' and prospective_status is "
                f"'{prospective_status}' (both must be APPROVED first)"
            )
    return problems
