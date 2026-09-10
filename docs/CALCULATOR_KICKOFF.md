# PCBF Football Calculator — Kickoff

This records the scope decisions made when starting the real deterministic
football calculator (`src/pcbf_calculator/`), per `docs/DEVELOPER_HANDOFF.md`
Agent D. It is the start of a much larger initiative and is not itself the
statistical/probability model.

## What this PR does

- Scaffolds `src/pcbf_calculator/` as a new, separate package alongside
  `src/pcbf_football/`.
- Gives it a placeholder CLI entry point (`python -m pcbf_calculator INPUT
  [OUTPUT]`, and a `pcbf-calculator` console script) that mirrors the
  `pcbf_football` host contract: JSON in, JSON out, the same
  `classification_ceiling` field, and the same typed-failure shape used by
  `runtime_probe.py`'s `M02_TEST_FAIL` convention.
- Returns a typed `NOT_IMPLEMENTED` failure for every input. It never
  computes or returns a probability, fair odds, de-vigged odds, or EV — none
  of that has a design spec yet, and inventing one in this PR would be
  overreach.

## What this PR deliberately does NOT do

- No feature contract, fail-closed null policy, probabilities, uncertainty
  bounds, fair odds, de-vigged probabilities, point EV, lower EV, or leakage
  tests. Those are the actual Agent D scope in
  `docs/DEVELOPER_HANDOFF.md` and need their own design spec first.
- No changes to `src/pcbf_football/` (the runtime probe), Bet9ja capture, or
  anything under `kasiro-brain` scope.
- No new runtime dependencies (pandas, NumPy, scikit-learn, etc.) — per
  `docs/DEVELOPER_HANDOFF.md`, those wait on an explicit three-host
  dependency strategy approved from runtime evidence.

## Decision: the interface stays host-neutral

`pcbf_calculator`'s CLI contract (JSON file in, JSON file out, no host-specific
code, no network calls, no host SDKs) is fixed from the start, matching
`docs/HOST_CONTRACT.md`. Any of the three orchestration hosts (ChatGPT
Project, Claude Cowork Project, Gemini Project) that satisfies the same
capabilities already required for `pcbf_football` can drive this package. The
calculator package itself must never contain provider prompts, tool names, or
upload instructions — those stay in the orchestration layer, outside this
package, exactly as already stated for `pcbf_football` in
`docs/HOST_CONTRACT.md`.

## Decision: no hosted API workaround for Gemini, not yet

Per `docs/HOST_TEST_RESULTS_MATRIX.md`, Gemini Project is
`orchestration_supported` / `runtime_unsupported` (see
`docs/HOST_CONTRACT.md` → "Supported Hosts"): it can drive the JSON contract
and prompt flow, but it cannot execute the Python wheel itself.

We are explicitly **not** building a hosted API (or any other workaround) to
make Gemini runtime-capable as part of this kickoff. Gemini being
orchestration-only is acceptable for now because the calculator itself is
not yet in normal daily operation — there is nothing to run on a schedule
that requires Gemini to execute Python. This decision should be revisited
only when Gemini orchestration of the calculator is actually needed for
day-to-day operation; building the workaround earlier would be speculative
infrastructure for a need that does not yet exist.
