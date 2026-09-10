"""The interim, explicit "we do not yet trust self-reported execution
identity" safety layer for the model-admission lookup.

Even after the model-admission registry lookup itself is correct (exact
``(adapter_id, model_version, model_artifact_hash)`` match, ordering
invariants enforced — see ``registries/model_admission.py``), the decision
engine (``decision/engine.py``) still has no independent way to verify that
the ``adapter_id``, ``model_version``, and ``model_artifact_hash`` values it
reads out of the caller-facing ``forecast_quality`` dict actually describe
what code executed. That dict is, structurally, just data the adapter
framework/CLI handed back — nothing in this codebase today cross-checks it
against ground truth. A fabricated or malformed ``forecast_quality`` dict
could claim an already-admitted triple and pass the registry lookup even
though nothing verifies that triple against what actually ran.

Release A/B has no trained model artifacts and no execution-time
hash-computation infrastructure, so building a real "trusted execution
receipt" mechanism is out of scope here. Instead this module holds one
explicit, auditable kill switch.

**This is a temporary kill switch, never a trust mechanism in its own
right — see "Eventual replacement" in the constant's own comment below for
what actually retires it.** Nothing in this module, ``decision/engine.py``,
or ``cli.py`` reads an environment variable, a CLI argument, or a
request/``decision_input``/``forecast_quality`` field to determine this
flag's value or to otherwise bypass it — it is a single hardcoded module
constant, full stop. ``tests/test_platform_classification_invariant.py``
(``TrustedProvenanceIsAHardcodedKillSwitchTests``) proves this directly:
setting a plausible env var, or stuffing a
``trusted_execution_provenance_available: true``-shaped field into either
input dict, changes nothing.
"""

from __future__ import annotations

# DO NOT change this to `True` as a standalone edit. It may only change as
# part of a PR that also implements the per-execution trusted-receipt
# verifier described in this constant's docstring below ("Eventual
# replacement") — and when that PR lands, this flag should be DELETED
# entirely, not left behind set to `True`.
TRUSTED_EXECUTION_PROVENANCE_AVAILABLE = False
"""Whether the decision engine may trust the ``adapter_id``, ``model_version``,
and ``model_artifact_hash`` fields it reads from a caller-facing
``forecast_quality`` dict enough to let a model-admission registry lookup's
result actually authorize ``PAPER`` or ``CASH``.

Fixed to ``False`` in this release. While it is ``False``,
``decision/engine.py::evaluate`` treats admission as **fully closed**
(``backtest_gates_approved``, ``prospective_approved``, and
``cash_admission_approved`` all ``False``) for every request, regardless of
what the model-admission registry lookup itself resolves — even a
fully-populated, fully-``APPROVED``, hash-matched registry row cannot reach
``CASH`` while this flag is ``False``. The registry lookup call itself still
happens (so the lookup mechanism — defects fixed alongside this flag — stays
exercised and independently testable); only its result's authority is
withheld.

This is not the same failure mode the registry lookup already guards
against (a stale/mismatched/absent row). It exists because nothing in this
codebase today independently verifies that the identity fields inside
``forecast_quality`` describe what actually executed — they are presently
just self-reported facts an adapter or caller states about itself, and nothing
stops those fields from being fabricated, stale, or simply wrong before this
dict reaches the decision engine.

This flag must not be flipped to ``True`` in a future PR until ALL of the
following are actually true (not merely planned):

(a) The CLI/adapter-dispatch layer resolves ``adapter_id`` from the actual
    ``_ADAPTER_IMPLEMENTATIONS`` registration key that was invoked
    (``adapters/registry.py``) — never from the returned ``ForecastResult``'s
    own self-reported field, which an adapter implementation could get wrong
    or a malformed/forged dict could misstate.
(b) The invoked adapter's own ``declaration.model_version``
    (``AdapterInterfaceDeclaration``, a static, code-defined capability
    declaration) is cross-checked at dispatch time to match what that same
    adapter's ``ForecastResult`` reports for the request it just served, with
    any mismatch treated as a hard error — never silently ignored or merely
    logged.
(c) ``model_artifact_hash`` is computed by the trusted runner itself (e.g.
    hashing the actual loaded model file/config at execution time) — never
    accepted as a value the adapter or caller simply states in
    ``ForecastResult`` or the request.

None of (a)-(c) is built in this release. Flipping this flag without all
three in place would silently reopen the exact gap this module exists to
close.

**Eventual replacement (this global flag is deleted, not flipped, when this
lands).** (a)-(c) above are steps toward retiring this flag, not toward
keeping it around as ``True``. The actual replacement is a **trusted
execution receipt** produced fresh on every single adapter invocation — not
a static, platform-wide boolean like this one, which if simply flipped
``True`` would wrongly vouch for every adapter forever, including ones that
do not exist yet and were never verified. Each receipt must carry:

- the registered adapter id, resolved from ``_ADAPTER_IMPLEMENTATIONS``'s
  actual registration key for the adapter that ran this call — never that
  adapter's own self-reported ``ForecastResult.adapter_id`` field;
- the declared model version, read from that same adapter's own
  ``declaration.model_version`` and cross-checked against what its
  ``ForecastResult`` reports for this call — any mismatch is a hard error;
- a computed artifact hash, produced by the trusted runner itself by
  hashing the actual loaded model file/config at execution time — never a
  value the adapter or caller simply states;
- an input hash, over the actual fixture/request data fed to the adapter
  for this specific call — proving the receipt corresponds to this exact
  invocation, not a replayed or reused one;
- an output hash, over the actual forecast the adapter produced for this
  specific call — proving the receipt corresponds to this exact result, not
  a substituted one.

Once the decision engine requires and verifies such a receipt on every
call, ``TRUSTED_EXECUTION_PROVENANCE_AVAILABLE`` provides no guarantee a
per-call receipt doesn't already provide, strictly better — so this module
and the flag it defines should be deleted outright in that PR, not kept
around toggled ``True``. See also
``docs/MULTI_SPORT_ARCHITECTURE.md``'s "Model-admission registry" section
for the platform-level description of this same design.
"""
