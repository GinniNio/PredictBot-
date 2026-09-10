# PredictBot Project Host Contract

## Boundary

PredictBot is a provider-neutral command-line artifact. ChatGPT, Claude Cowork
and Gemini are interchangeable orchestration hosts only when they satisfy this
contract. Provider prompts, tool names and upload instructions are outside the
calculation package.

## Required host capabilities

A conforming host must be able to:

1. Retain or receive the wheel, release manifest, input JSON and verification
   script for the session.
2. Execute Python in an isolated workspace.
3. Install the wheel with `--no-index --no-deps`.
4. Calculate and compare SHA-256 hashes locally.
5. Invoke `python -m pcbf_football INPUT OUTPUT`.
6. Preserve the resulting JSON without rewriting numeric values.
7. Run the same input twice and compare the output byte for byte.

## Required invocation

```bash
python scripts/verify_artifact.py WHEEL RELEASE_MANIFEST
python -m pip install --no-index --no-deps WHEEL
python -m pcbf_football INPUT RESULT_A
python -m pcbf_football INPUT RESULT_B
```

The host then performs a byte comparison of `RESULT_A` and `RESULT_B`.

## Failure behavior

The orchestration project must stop calculation and emit a host failure when:

- Python execution is unavailable.
- Offline wheel installation fails.
- A declared dependency is unavailable.
- A package or schema hash differs from the registry.
- The CLI exits unsuccessfully.
- Repeated results differ.

The portable failure record is:

```json
{
  "status": "FAILED",
  "failure": {
    "code": "HOST_RUNTIME_UNAVAILABLE",
    "host": "chatgpt|claude_cowork|gemini",
    "reason": "plain description of the failed capability"
  },
  "classification_ceiling": "RESEARCH-MODEL"
}
```

No host may replace a failed or unavailable calculation with an LLM-generated
probability.

## Supported Hosts

Based on the independent, in-host evidence recorded in
[HOST_TEST_RESULTS_MATRIX.md](HOST_TEST_RESULTS_MATRIX.md), each project host
is declared as one of two support levels:

- **`runtime_supported`** — the host can retain the wheel/manifest/input for
  the session, execute Python, install the wheel offline
  (`--no-index --no-deps`), and run the CLI twice with byte-identical,
  hash-verified output. A `runtime_supported` host can carry out the full
  contract in this document end to end.
- **`orchestration_supported` / `runtime_unsupported`** — the host can drive
  the JSON contract and prompt flow (assemble inputs, request a run, parse
  and relay the resulting JSON) but cannot itself execute the Python wheel.
  Such a host must delegate execution to a `runtime_supported` host or a
  human operator, and must emit the `HOST_RUNTIME_UNAVAILABLE` failure record
  above rather than approximate a result.

| Host | Support level |
|---|---|
| ChatGPT Project | `runtime_supported` |
| Claude Cowork Project | `runtime_supported` |
| Gemini Project | `orchestration_supported`, `runtime_unsupported` |

Gemini Project can orchestrate the PCBF prompt/JSON flow but cannot execute
the Python wheel itself (Gemini Projects have no Python execution or offline
package installation available). No hosted API workaround is being built to
make Gemini runtime-capable — see `docs/CALCULATOR_KICKOFF.md` for that
decision and its rationale. This declaration must be revisited only when new
in-host evidence is added to `HOST_TEST_RESULTS_MATRIX.md`.
