# Host Runtime Test Results Matrix

One canonical `pcbf-runtime-host-test-pack` GitHub Actions artifact is used
for every host below. Each host records its own row after independently
running through [HOST_TEST_CHECKLIST.md](HOST_TEST_CHECKLIST.md); no result
is copied between hosts.

| Host | Install | SHA-256 match | Two-run byte-identical | Python version | Retention limitations | Status |
|---|---|---|---|---|---|---|
| Generic Linux | Offline install succeeded (`--no-index --no-deps`) | Match | Identical (also matches `examples/expected-result.json`) | 3.11.15 | None observed | Mechanically passed using locally rebuilt wheel |
| ChatGPT Project | Offline install succeeded (`--no-index --no-deps`) | Match — wheel `sha256:fbfe8e6600100488794b38cbe9f20bfc16f004c09c986fe091a879fbced30976` matched the manifest and `verify_artifact.py` | Identical — both runs and `expected-result.json` all hash `sha256:9d103de2a8010c92dac38a54fa550ee8f97901d7ae81d2655971020f78b990a4`; `classification_ceiling` confirmed `RESEARCH-MODEL` | 3.12.14 | Uploaded files/environment are session-scoped — the pack must be retained externally and reinstalled each session | PASS |
| Claude Cowork Project | Offline install succeeded (`--no-index --no-deps`) | Match — verified via `verify_artifact.py` (exit 0) | Identical between both runs and `expected-result.json`; `classification_ceiling` confirmed `RESEARCH-MODEL` | 3.11.15 | Network is proxied, not physically air-gapped (`--no-index --no-deps` still enforces the no-download requirement regardless); workspace is ephemeral — nothing persists without re-upload | PASS |
| Gemini orchestration host | Not available — Gemini orchestration hosts cannot execute Python or install a wheel | N/A | N/A | N/A | Cannot retain or execute the artifact at all | FAILED — `HOST_RUNTIME_UNAVAILABLE`: "Python execution and offline wheel installation are unavailable" (`classification_ceiling` reported as `RESEARCH-MODEL`) |

## Notes

- The "Generic Linux" row reflects a mechanical pass produced by rebuilding
  the wheel locally and running the checklist outside any project host. It
  demonstrates the pipeline works; it does not substitute for an in-host
  result.
- ChatGPT Project and Claude Cowork Project each independently ran the full
  checklist and produced byte-identical, hash-verified results — no result
  was copied between hosts, per `docs/DEVELOPER_HANDOFF.md` Agent A
  instructions ("Do not share a pass result between hosts").
- Gemini orchestration host cannot execute Python or install a wheel at all, so it can
  only orchestrate (drive prompts/JSON) and cannot run the runtime artifact
  itself. Its host failure record correctly reports `HOST_RUNTIME_UNAVAILABLE`
  per `docs/HOST_CONTRACT.md` rather than falling back to an LLM-generated
  probability. See `docs/HOST_CONTRACT.md` ("Supported Hosts") for the
  resulting support declaration.
