# Host Runtime Test Checklist

Use this checklist when testing the `pcbf-runtime-host-test-pack` artifact in
a project host (ChatGPT Project, Claude Cowork Project, Gemini orchestration host, or a
local/generic environment). Record the outcome for each item in the
[results matrix](HOST_TEST_RESULTS_MATRIX.md).

## Steps

1. **Offline install**
   - Disable network access for the workspace/session if the host allows it.
   - Run `python -m pip install --no-index --no-deps <wheel file>`.
   - Record: did the install succeed with no network access?

2. **SHA-256 re-verification**
   - Run `python scripts/verify_artifact.py <wheel file> release-manifest.json`.
   - Confirm the script reports a match and does not raise.
   - Record: did the locally recomputed SHA-256 match `release-manifest.json`?

3. **Two-run byte comparison**
   - Run `python -m pcbf_football examples/mock-fixture.json result-a.json`.
   - Run `python -m pcbf_football examples/mock-fixture.json result-b.json`.
   - Compare `result-a.json` and `result-b.json` byte-for-byte (e.g. `cmp`).
   - Compare either result file byte-for-byte against the checked-in
     `examples/expected-result.json`.
   - Record: were both comparisons identical?

4. **Python version recorded**
   - Run `python --version` (or `python3 --version`) in the same environment
     used for steps 1-3.
   - Record: the exact Python version string.

5. **Retention / storage limitations observed**
   - Note anything about the host that affects reuse of this pack: files that
     disappear between sessions, upload size limits, inability to keep a
     persistent virtual environment, sandboxed/no network default, etc.
   - Record: a short plain-language description, or "none observed".

## Pass conditions

A host passes the runtime proof only if all of the following hold:

- The wheel installs with network access disabled.
- The wheel hash matches `release-manifest.json`.
- The CLI runs successfully against `examples/mock-fixture.json`.
- Both runs produce byte-identical output, and that output matches
  `examples/expected-result.json`.
- The result stays at `classification_ceiling: RESEARCH-MODEL`.

Do not report a pass by copying another host's result. Each host must produce
its own evidence in its own row of the results matrix.
