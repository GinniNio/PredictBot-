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
