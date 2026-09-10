# Host Runtime Test Results Matrix

One canonical `pcbf-runtime-host-test-pack` GitHub Actions artifact is used
for every host below. Each host records its own row after independently
running through [HOST_TEST_CHECKLIST.md](HOST_TEST_CHECKLIST.md); no result
is copied between hosts.

| Host | Install | SHA-256 match | Two-run byte-identical | Python version | Retention limitations | Status |
|---|---|---|---|---|---|---|
| Generic Linux | Offline install succeeded (`--no-index --no-deps`) | Match | Identical (also matches `examples/expected-result.json`) | 3.11.15 | None observed | Mechanically passed using locally rebuilt wheel |
| ChatGPT Project | TODO | TODO | TODO | TODO | TODO | TODO |
| Claude Cowork Project | TODO | TODO | TODO | TODO | TODO | TODO |
| Gemini Project | TODO | TODO | TODO | TODO | TODO | TODO |

## Notes

- The "Generic Linux" row reflects a mechanical pass produced by rebuilding
  the wheel locally and running the checklist outside any project host. It
  demonstrates the pipeline works; it does not substitute for an in-host
  result.
- Each TODO row must be filled in from that host's own session, per
  `docs/DEVELOPER_HANDOFF.md` Agent A instructions ("Do not share a pass
  result between hosts").
