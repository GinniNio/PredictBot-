# PCBF Build Handoff

## Approved architecture

The system has two repositories and three interchangeable project hosts.

- `GinniNio/PredictBot-` owns deterministic calculation artifacts.
- `GinniNio/kasiro-brain` owns PCBF governance, host project packs, schemas,
  adapter admission and operating state.
- ChatGPT, Claude Cowork and Gemini are orchestration hosts. They consume the
  same files and must never create substitute probabilities.
- Live forecast and betting ledgers remain outside Git. Repository fixtures are
  fictional test records only.
- Bet placement remains manual.
- Settlement starts as connected-browser, ticket-by-ticket reconciliation.

## Current delivery

This PR supplies the dependency-free runtime probe. It is deliberately not a
forecasting model. It proves offline wheel installation, hash verification,
deterministic JSON, complete opposing-price validation and typed failure.

## Agent work packages

### Agent A Runtime verification — DONE

Test the built wheel separately in ChatGPT, Claude Cowork and Gemini.

For each host, record:

- Python version
- Wheel installation result with network disabled
- SHA-256 verification result
- First and second output hashes
- Byte-comparison result
- Any file-retention or upload limitation

Do not share a pass result between hosts. Each host has its own evidence record.

**Status: complete.** All three intended hosts were independently tested.
ChatGPT Project and Claude Cowork Project each passed (offline install,
matching SHA-256, byte-identical repeated runs). Gemini Project cannot
execute Python or install a wheel and correctly reported
`HOST_RUNTIME_UNAVAILABLE`. There is no formal GitHub milestone tracking this
work (none exists in this repository), so this line is the closure record.
Full per-host evidence: `docs/HOST_TEST_RESULTS_MATRIX.md`. Resulting support
declaration: `docs/HOST_CONTRACT.md` ("Supported Hosts").

### Agent B Governance integration

Work in `GinniNio/kasiro-brain`.

Add:

- `products/pcbf/BRAIN.md`
- `products/pcbf/CURRENT_STATE.md`
- canonical project instructions
- generated ChatGPT, Claude Cowork and Gemini project packs
- host capability and failure schema
- forecast-ledger and betting-ledger CSV schemas
- hash-pinned adapter registry

The canonical instructions are authoritative. Provider files contain only the
minimum syntax and tool-name differences needed by each host.

### Agent C Fixture intake

After at least one host passes the runtime proof, implement the Bet9ja fixture
capture under `kasiro-brain`.

Scope:

- Soccer only
- Prematch only
- Current day only
- 1X2 only
- Read-only page access
- Complete opposing prices
- Raw and normalized JSON
- No betslip, placement or settlement interaction

### Agent D Football calculation artifact

After the runtime decision, replace the probe with the first real implementation
under `src/pcbf_football/`.

The work must include the full feature contract, fail-closed null policy,
probabilities, uncertainty bounds, fair odds, de-vigged probabilities, point EV,
lower EV, calculation hash, deterministic rerun result, typed failure codes,
temporal-integrity tests and leakage tests.

Do not add pandas, NumPy, scikit-learn or another runtime dependency until the
three-host dependency strategy is explicitly approved from runtime evidence.

**Scope update:** Agent D's scope was expanded from football-only to a
multi-sport platform covering every Bet9ja category before any football-only
forecasting model was built. `src/pcbf_calculator/` now holds the Release A
universal market-pricing engine, adapter framework, and PCBF decision layer
described in `docs/MULTI_SPORT_ARCHITECTURE.md` (which supersedes
`docs/CALCULATOR_KICKOFF.md`). Per-sport forecasting logic (soccer 1X2,
tennis match-winner, basketball moneyline) remains out of scope until
Release B/C, exactly as originally planned here — only the "football-only"
framing changed, not the sequencing.

## Sequence gate

1. Merge and build the runtime probe.
2. Test it in each intended host.
3. Choose the supported-host matrix and dependency packaging strategy.
4. Integrate governance and project packs.
5. Build fixture capture.
6. Build and evaluate the real football artifact.
7. Add placement recording.
8. Add connected-browser settlement.

Failure at a host-runtime gate changes the supported-host matrix or runtime
architecture. It does not permit an LLM probability fallback.
