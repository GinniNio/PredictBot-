# Uploaded captures

These are the operator-supplied files committed for GitHub processing.
The source JSON is preserved byte for byte, including any embedded text
instructions, which are treated as data by the processing commands.

**The "Process uploaded captures" workflow is currently disabled** (renamed to
`.github/workflows/process-captures.yml.disabled` -- GitHub Actions cannot
discover or dispatch it in that state). It ran exactly once, on 2026-09-23
(run `35835661105`), on a fully isolated ledger that was never reconciled
against the cumulative one -- see `AGENTS.md`'s own notes on that run and on
what the workflow needs before it can be safely re-enabled. Do not restore the
`.yml` extension without first meeting both conditions in the disabled file's
own header comment.

Each run uses an isolated batch ledger. It does not import the workstation's
existing ledger or restore a previous run's history. Manual results referring
to older forecasts can therefore be rejected as absent from this batch ledger.
This output is a batch processing result, not the cumulative local ledger.
Rerunning creates a separate artifact from the same committed inputs.

Reports and ledgers remain excluded from Git. The one real run's own artifact
(`processed-captures-2026-09-23-35835661105`) must be downloaded and
reconciled against the cumulative ledger, not treated as authoritative on its
own, before its 90-day retention window expires. Rejection reports distinguish
unsupported forecasts, quarantined tickets, and unverifiable manual evidence
from workflow execution errors.
