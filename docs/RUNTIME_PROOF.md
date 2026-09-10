# ChatGPT Runtime Proof

## Purpose

This package answers one question before model development begins: can a
supported project host install and execute a hash-pinned PredictBot wheel
offline?

The probe is not a forecasting model. Its probabilities are fixed test values,
its status is `RUNTIME_PROBE`, and its maximum PCBF classification is
`RESEARCH-MODEL`.

## Dependency decision

The probe and its installed wheel use only the Python standard library. The
wheel declares no runtime dependencies. The production model must follow one
of these rules:

1. Prefer standard-library calculations where practical.
2. Vendor approved pure-Python dependencies inside the wheel when licensing
   and security review permit it.
3. Package required binary wheels beside the PredictBot wheel and install every
   file with `--no-index --no-deps`.
4. Reject the ChatGPT runtime and move calculation to a private service if the
   platform cannot install the complete offline wheelhouse reliably.

The production artifact must never depend on downloading packages during a
session.

## Host-neutral contract

PredictBot has no provider-specific integration. Every host receives the same
four files, executes the same commands, and consumes the same JSON result:

- ChatGPT Project
- Claude Cowork Project
- Gemini Project

Host-specific prompts belong in `kasiro-brain`, not this repository. If a host
cannot install and execute the wheel, it records `HOST_RUNTIME_UNAVAILABLE` and
keeps the session at `RESEARCH-MODEL`. It must not imitate the calculator.

## Build outside the project host

```bash
python -m pip wheel . --no-deps --no-build-isolation --wheel-dir dist
```

Calculate the SHA-256 digest, copy it into the release manifest, and commit the
full source commit SHA into the same manifest.

## Project host runtime test

Upload these files to the selected project:

- `pcbf_football-0.0.1-py3-none-any.whl`
- completed `release-manifest.json`
- `examples/mock-fixture.json`
- `scripts/verify_artifact.py`

Run offline installation and execution:

```bash
python scripts/verify_artifact.py pcbf_football-0.0.1-py3-none-any.whl release-manifest.json
python -m pip install --no-index --no-deps pcbf_football-0.0.1-py3-none-any.whl
python -m pcbf_football examples/mock-fixture.json result.json
```

Run the final command twice. The two result files must be byte-for-byte equal.

## Pass conditions

- The package installs with network access disabled.
- The wheel hash matches the release manifest.
- The artifact runs against the mock fixture.
- Repeated execution produces identical JSON.
- Missing mandatory input produces `M02_TEST_FAIL`.
- The result remains capped at `RESEARCH-MODEL`.

Failure of any condition blocks production model work until the runtime choice
is changed or repaired.
