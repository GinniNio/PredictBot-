"""PCBF decision layer: a real rule-evaluation engine over explicit typed
inputs. There is no live evidence/liquidity data source wired up yet, so
every STOP-rule input this engine reads is supplied by the caller in the
request JSON — this is a genuine rule engine, not a hardcoded or fake
classifier. **Gate approval (below) is the one deliberate exception: it is
never taken from caller input, see "Model-admission registry" below.**

Rule evaluation order (first match wins, each is a hard STOP):

1. ``STOP_STALE_DATA`` — ``freshness.data_age_seconds > freshness.max_age_seconds``
2. ``STOP_INSUFFICIENT_EVIDENCE`` — ``evidence.sample_size < evidence.min_sample_size``
3. ``STOP_INSUFFICIENT_LIQUIDITY`` — ``liquidity.available_stake < liquidity.min_required_stake``
4. ``STOP_EXCESSIVE_UNCERTAINTY`` — ``uncertainty.width > uncertainty.max_width``
5. ``STOP_NEGATIVE_EV`` — the priced outcome's confidence-bounded EV is <= 0.
   Layer 1 only produces a real ``lower_bound_ev`` once an admitted adapter
   supplies calibrated uncertainty (Release A: never). When
   ``lower_bound_ev`` is ``null`` this rule evaluates the outcome's
   ``point_ev`` instead — still real, non-fabricated de-vigged market math,
   just without a confidence bound around it. This is safe specifically
   because passing this rule can never by itself authorize ``CASH`` (see
   below); at most it lets a category reach ``PAPER``, and only then
   subject to the model-admission checks below.

**Model-admission registry — the only source of gate approval.** This
engine never trusts a caller-supplied "I am approved" flag, wherever it
might appear (a ``decision_input`` field, or a boolean embedded in an
adapter's own ``ForecastResult``). Instead it takes three plain facts the
adapter reports about the forecast it actually produced —
``forecast_quality.adapter_id`` (never ``sport_id`` — a sport can have
multiple adapters, e.g. soccer's eventual 1X2/totals/BTTS adapters, each
with its own distinct admission identity), ``forecast_quality.model_version``,
and ``forecast_quality.model_artifact_hash`` — and looks up that exact
triple in the committed, versioned
``registries/data/model-admission-registry.yaml`` via
``registries/model_admission.py::resolve_model_admission``. That lookup
resolves fully closed (nothing approved) unless a row exists for the exact
``(adapter_id, model_version)`` pair *and* that row's stored
``model_artifact_hash`` matches the one reported — a mismatch (stale row,
or an attempt to piggyback an unapproved model build on an approved
version's identity) is never trusted. The registry ships with **zero**
admitted rows in this release, so every lookup resolves closed today,
structurally, regardless of what any adapter or caller claims.

**Independent of the registry lookup itself, this engine additionally
refuses to trust the *identity fields* it reads that lookup with at all.**
``TRUSTED_EXECUTION_PROVENANCE_AVAILABLE`` (``decision/trusted_provenance.py``)
is fixed to ``False`` in this release: nothing in this codebase yet
independently verifies that ``forecast_quality``'s ``adapter_id``,
``model_version``, and ``model_artifact_hash`` actually describe what
executed, so while this flag is ``False`` the admission lookup's result is
never trusted to authorize anything — see that module for exactly what
would need to be true before it could ever flip to ``True``. This is a
second, independent safety layer: even a hypothetical fully-populated,
fully-``APPROVED``, hash-matched registry row cannot reach ``PAPER`` or
``CASH`` while this flag is ``False``.

**Fixed, non-configurable policy — three-tier classification, no admitted
forecast can ever reach ``CASH``, and PAPER requires more than a forecast
merely existing.** Once every STOP rule passes:

- **Tier 1 — ``forecast_quality.forecast_available is False``** -> ceiling
  is capped at ``RESEARCH-MODEL``, unconditionally. This is not a tunable
  default and there is no decision-input field, STOP-rule outcome, evidence
  value, or pricing output that can move it: market-implied probabilities
  (layer 1) can support research and price comparison, but proving a
  betting edge by measuring market-derived EV against the very same market
  it was derived from is circular — it cannot be used to authorize real
  capital. Only an independently admitted forecast (layer 2) can do that.
  **Policy history**: prior to this change the no-forecast cap was
  ``PAPER`` (see ``docs/MULTI_SPORT_ARCHITECTURE.md`` decision 3's original
  text); the operator has reversed that decision — a category with no
  admitted forecast now caps at ``RESEARCH-MODEL``, not ``PAPER``, because
  ``PAPER`` is meant to signal "this specific model version has cleared its
  approved backtest gates," which is not true when there is no model at
  all.
- **Tier 2 — a forecast exists, but the model-admission lookup's
  ``backtest_gates_approved`` resolves ``False``** -> ceiling is *also*
  capped at ``RESEARCH-MODEL``, not ``PAPER``. A forecast existing is
  necessary but not sufficient for ``PAPER``: ``PAPER`` requires
  probabilities from a **registered** deterministic forecasting adapter
  whose **specific model version** (identity *and* artifact hash) has
  actually cleared its spec's approved backtest acceptance gates (e.g.
  ``docs/adapters/SOCCER_1X2_ADAPTER_SPEC.md`` section 14, once that
  section's promotion-thresholds ledger is flipped ``APPROVED`` and a real
  backtest report clears them, and a corresponding row is added to
  ``model-admission-registry.yaml`` recording it) — not merely "an adapter
  ran and returned a probability." Since no row exists for any adapter yet,
  **every category in this release resolves to ``RESEARCH-MODEL``
  regardless of any caller-supplied evidence/liquidity/STOP-rule input, and
  regardless of any approval-shaped field a caller or adapter output might
  contain** — there is no live code path that can produce ``PAPER`` today,
  and that is structural, not incidental.
- **Tier 3 — a forecast exists and ``backtest_gates_approved`` resolves
  ``True``** -> ``CASH`` is reachable only when **all three** of the
  model-admission lookup's flags hold: ``backtest_gates_approved``,
  ``prospective_approved`` (the spec's prospective/shadow-mode admission
  gate, section 15 — a model that has cleared backtest gates but is still
  ``PENDING`` or ``REJECTED`` prospective validation must never reach
  ``CASH``), **and** ``cash_admission_approved`` (the spec's manual CASH
  sign-off, section 16). If ``evidence.sample_size <
  evidence.cash_min_sample_size`` (a higher bar than the STOP threshold,
  defaulting to the STOP threshold when unset) **or** any of those three
  flags is not ``True``, the ceiling is capped at ``PAPER``; otherwise the
  ceiling is ``CASH``. ``cash_admission_approved`` being ``True`` in the
  registry is a *mechanical* prerequisite only — it must itself only ever be
  set following the spec's actual manual human sign-off process (section
  16); this engine does not perform or replace that sign-off.

Both cap points (tier 1 and tier 2) are enforced again as a defensive
invariant right before the result is returned (see
``_assert_no_forecast_never_cash`` below), not only by the branch order.

**Stake is tiered by classification, never a single zero/nonzero flag:**

- ``RESEARCH-MODEL`` -> ``cash_stake: 0`` **and** ``simulated_stake: 0``
  (there is no admitted forecast worth sizing anything against, real or
  hypothetical).
- ``PAPER`` -> ``cash_stake: 0`` always, but ``simulated_stake`` is
  permitted to be nonzero (the pricing engine's own EV-sizing ``stake`` from
  ``market_profitability``) — this is exactly what paper-trading means: a
  hypothetical stake may be sized and tracked for evaluation purposes, but
  it must never be mistaken for a real-money authorization. It is kept in a
  distinctly named field for exactly that reason.
- ``CASH`` -> ``cash_stake`` may be nonzero (the pricing engine's own
  EV-sizing ``stake``); ``simulated_stake: 0`` (no longer "simulated" —
  it's real).
- A ``REJECTED`` (STOP-rule) result also always carries ``cash_stake: 0``
  and ``simulated_stake: 0``.

This is enforced defensively (see
``_assert_cash_stake_only_for_cash`` below): ``cash_stake`` must be
provably zero for every classification except ``CASH``.

Universal market-pricing output (layer 1, ``market_profitability``) is
completely unaffected by any of this — pricing and classification level
stay fully independent, exactly as before; de-vigged market probabilities
remain available for research/price-comparison purposes at every tier, they
simply can never *by themselves* promote a category above
``RESEARCH-MODEL``, because they are derived from the very market they
would be used to "beat" (circular).

Forecast quality (layer 2) and market/ticket profitability (layer 1) are
always returned as two distinct sub-objects — ``forecast_quality`` and
``market_profitability`` — never merged into one score, per the task's hard
requirement that these stay independently inspectable.
"""

from __future__ import annotations

from typing import Any

from ..errors import (
    MISSING_DECISION_INPUT,
    STOP_EXCESSIVE_UNCERTAINTY,
    STOP_INSUFFICIENT_EVIDENCE,
    STOP_INSUFFICIENT_LIQUIDITY,
    STOP_NEGATIVE_EV,
    STOP_STALE_DATA,
)
from ..registries.model_admission import resolve_model_admission
from .trusted_provenance import TRUSTED_EXECUTION_PROVENANCE_AVAILABLE

REQUIRED_BLOCKS = ("evidence", "freshness", "liquidity", "uncertainty")


class DecisionInputError(Exception):
    def __init__(self, field: str, reason: str) -> None:
        self.code = MISSING_DECISION_INPUT
        self.field = field
        self.reason = reason
        super().__init__(f"{MISSING_DECISION_INPUT}: {reason}")


def _require_block(decision_input: dict[str, Any], name: str, keys: tuple[str, ...]) -> dict[str, Any]:
    block = decision_input.get(name)
    if not isinstance(block, dict):
        raise DecisionInputError(name, f"Required decision input block '{name}' is missing or not an object.")
    for key in keys:
        if key not in block:
            raise DecisionInputError(f"{name}.{key}", f"Required field '{name}.{key}' is missing.")
        value = block[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise DecisionInputError(f"{name}.{key}", f"Field '{name}.{key}' must be a number.")
    return block


def _stake_fields(classification: str, market_profitability: dict[str, Any]) -> tuple[Any, Any]:
    """Return ``(cash_stake, simulated_stake)`` for this classification.

    ``pricing_stake`` is the pricing engine's own EV-sizing ``stake``
    parameter (default 1.0) — a distinct concept from either of these
    fields, which answer "should any stake actually be placed/tracked,"
    never "how big is the stake used inside the EV formula."
    """
    pricing_stake = market_profitability.get("stake", 1.0) if isinstance(market_profitability, dict) else 1.0
    if classification == "CASH":
        return pricing_stake, 0
    if classification == "PAPER":
        return 0, pricing_stake
    # RESEARCH-MODEL and REJECTED: no stake of any kind, real or simulated.
    return 0, 0


def evaluate(
    decision_input: dict[str, Any],
    market_profitability: dict[str, Any],
    forecast_quality: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate STOP rules and assign a classification.

    ``market_profitability`` is one outcome's pricing dict from
    ``pricing.engine.analyze_market`` (must contain ``lower_bound_ev``).
    ``forecast_quality`` is a ``ForecastResult.to_dict()`` from layer 2
    (must contain ``forecast_available``; ``adapter_id``, ``model_version``
    and ``model_artifact_hash``, when present, are used — never trusted
    blindly — to look up gate approval in the committed model-admission
    registry; see the module docstring). Raises ``DecisionInputError`` if a
    required block is missing/malformed — callers should turn that into the
    typed ``MISSING_DECISION_INPUT`` failure record. The returned dict
    always carries ``cash_stake`` and ``simulated_stake`` (see the module
    docstring's stake-tiering section; ``cash_stake`` is provably zero for
    every classification except ``CASH``).
    """
    evidence = _require_block(decision_input, "evidence", ("sample_size", "min_sample_size"))
    freshness = _require_block(decision_input, "freshness", ("data_age_seconds", "max_age_seconds"))
    liquidity = _require_block(decision_input, "liquidity", ("available_stake", "min_required_stake"))
    uncertainty = _require_block(decision_input, "uncertainty", ("width", "max_width"))

    if "lower_bound_ev" not in market_profitability or "point_ev" not in market_profitability:
        raise DecisionInputError(
            "market_profitability.lower_bound_ev",
            "market_profitability must include lower_bound_ev and point_ev from the pricing engine.",
        )
    if "forecast_available" not in forecast_quality:
        raise DecisionInputError(
            "forecast_quality.forecast_available",
            "forecast_quality must include forecast_available from the adapter framework.",
        )

    def rejected(stop_rule: str, reason: str) -> dict[str, Any]:
        return {
            "status": "REJECTED",
            "classification": "REJECTED",
            "stop_rule": stop_rule,
            "reason": reason,
            "forecast_quality": forecast_quality,
            "market_profitability": market_profitability,
            # A STOP-rejected result never authorizes any stake, real or
            # simulated.
            "cash_stake": 0,
            "simulated_stake": 0,
        }

    if freshness["data_age_seconds"] > freshness["max_age_seconds"]:
        return rejected(
            STOP_STALE_DATA,
            f"data_age_seconds {freshness['data_age_seconds']} exceeds max_age_seconds "
            f"{freshness['max_age_seconds']}.",
        )

    if evidence["sample_size"] < evidence["min_sample_size"]:
        return rejected(
            STOP_INSUFFICIENT_EVIDENCE,
            f"sample_size {evidence['sample_size']} is below min_sample_size "
            f"{evidence['min_sample_size']}.",
        )

    if liquidity["available_stake"] < liquidity["min_required_stake"]:
        return rejected(
            STOP_INSUFFICIENT_LIQUIDITY,
            f"available_stake {liquidity['available_stake']} is below min_required_stake "
            f"{liquidity['min_required_stake']}.",
        )

    if uncertainty["width"] > uncertainty["max_width"]:
        return rejected(
            STOP_EXCESSIVE_UNCERTAINTY,
            f"uncertainty width {uncertainty['width']} exceeds max_width {uncertainty['max_width']}.",
        )

    lower_bound_ev = market_profitability["lower_bound_ev"]
    ev_basis = "lower_bound_ev"
    effective_ev = lower_bound_ev
    if effective_ev is None:
        # No calibrated uncertainty (Release A: always). Fall back to the
        # still-real, non-fabricated point EV rather than inventing a
        # confidence bound. Safe because forecast_available False (the only
        # case where lower_bound_ev is ever null today) already caps the
        # ceiling at RESEARCH-MODEL below, regardless of how this rule
        # resolves.
        effective_ev = market_profitability["point_ev"]
        ev_basis = "point_ev"

    if effective_ev <= 0:
        return rejected(
            STOP_NEGATIVE_EV,
            f"{ev_basis} {effective_ev} is not positive.",
        )

    forecast_available = bool(forecast_quality.get("forecast_available"))
    # Gate approval NEVER comes from forecast_quality's own fields (or any
    # caller-supplied field) directly — only from the committed
    # model-admission registry, looked up by the plain facts the adapter
    # reports about the model it actually ran. Any other key a caller or
    # adapter stuffs into forecast_quality (e.g. a forged
    # "backtest_approved": true) is simply never read below. The lookup key
    # is adapter_id, never sport_id — a sport can have multiple adapters
    # (soccer's eventual 1X2/totals/BTTS adapters), each with its own
    # distinct admission identity.
    admission = resolve_model_admission(
        forecast_quality.get("adapter_id"),
        forecast_quality.get("model_version"),
        forecast_quality.get("model_artifact_hash"),
    )
    if not TRUSTED_EXECUTION_PROVENANCE_AVAILABLE:
        # Independent safety layer (see decision/trusted_provenance.py): the
        # lookup above still runs (so the lookup mechanism itself stays
        # exercised and independently testable), but its result is not yet
        # trusted to authorize anything — nothing today independently
        # verifies that forecast_quality's adapter_id/model_version/
        # model_artifact_hash describe what actually executed. Treat
        # admission as fully closed regardless of what the registry
        # resolved, until that verification exists.
        admission = {
            "backtest_gates_approved": False,
            "prospective_approved": False,
            "cash_admission_approved": False,
        }
    backtest_gates_approved = forecast_available and admission["backtest_gates_approved"]

    if not forecast_available or not backtest_gates_approved:
        # HARD, non-configurable, three-tier policy (see module docstring):
        # Tier 1 (no forecast at all) and Tier 2 (a forecast exists but its
        # exact model version/hash has no APPROVED row in the
        # model-admission registry) both cap at RESEARCH-MODEL.
        classification = "RESEARCH-MODEL"
    else:
        cash_min_sample_size = evidence.get("cash_min_sample_size", evidence["min_sample_size"])
        below_cash_sample_bar = evidence["sample_size"] < cash_min_sample_size
        # CASH requires all three admission flags, not just the last one:
        # backtest gates approved, prospective/shadow-mode approved, AND
        # cash admission approved.
        cash_eligible = (
            admission["backtest_gates_approved"]
            and admission["prospective_approved"]
            and admission["cash_admission_approved"]
        )
        if below_cash_sample_bar or not cash_eligible:
            classification = "PAPER"
        else:
            classification = "CASH"

    _assert_no_forecast_never_cash(forecast_available, backtest_gates_approved, classification)

    cash_stake, simulated_stake = _stake_fields(classification, market_profitability)
    _assert_cash_stake_only_for_cash(classification, cash_stake)

    return {
        "status": "PASSED",
        "classification": classification,
        "stop_rule": None,
        "reason": None,
        "forecast_quality": forecast_quality,
        "market_profitability": market_profitability,
        "cash_stake": cash_stake,
        "simulated_stake": simulated_stake,
    }


def _assert_no_forecast_never_cash(
    forecast_available: bool, backtest_gates_approved: bool, classification: str
) -> None:
    """Defense-in-depth invariant, independent of the branch above: it must
    be structurally impossible for a category to come out of this function
    classified ``CASH`` (or ``PAPER``) unless it has both a real forecast
    *and* an admission-registry-approved backtest-gate signal. If this ever
    fires it is a bug in this engine, not a caller input problem."""
    if not forecast_available and classification in ("CASH", "PAPER"):
        raise AssertionError(
            "Policy violation: forecast_available is False but classification "
            f"resolved to {classification}. No admitted forecast can ever authorize "
            "PAPER or CASH — the ceiling must be RESEARCH-MODEL."
        )
    if forecast_available and not backtest_gates_approved and classification in ("CASH", "PAPER"):
        raise AssertionError(
            "Policy violation: forecast_available is True but the model-admission "
            f"registry did not resolve backtest_gates_approved, yet classification "
            f"resolved to {classification}. A forecast existing is not sufficient for "
            "PAPER/CASH without an APPROVED, hash-matched model-admission row — the "
            "ceiling must be RESEARCH-MODEL."
        )


def _assert_cash_stake_only_for_cash(classification: str, cash_stake: Any) -> None:
    """Defense-in-depth invariant: ``cash_stake`` must be provably zero for
    every classification except ``CASH``. If this ever fires it is a bug in
    this engine, not a caller input problem."""
    if classification != "CASH" and cash_stake != 0:
        raise AssertionError(
            f"Policy violation: classification is {classification!r} but cash_stake "
            f"resolved to {cash_stake!r}, not 0. Only a CASH classification may ever "
            "carry a nonzero cash_stake."
        )
