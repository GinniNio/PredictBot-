# Uploaded captures

These are the operator-supplied files committed for GitHub processing.
The source JSON is preserved byte for byte, including any embedded text
instructions, which are treated as data by the processing commands.

Run **Process uploaded captures** in GitHub Actions with the batch directory
date and the operator-confirmed currency. The workflow verifies that the
segment fixtures match the aggregate and processes the aggregate once.
It imports tickets, attempts evidence archival and governed manual settlement,
then exports reports, CSV views, and ledgers as a workflow artifact.

Each run uses an isolated batch ledger. It does not import the workstation's
existing ledger or restore a previous run's history. Manual results referring
to older forecasts can therefore be rejected as absent from this batch ledger.
This output is a batch processing result, not the cumulative local ledger.
Rerunning creates a separate artifact from the same committed inputs.

Reports and ledgers remain excluded from Git. Download the workflow artifact
to retain them beyond its 90-day retention period. Rejection reports distinguish
unsupported forecasts, quarantined tickets, and unverifiable manual evidence
from workflow execution errors.
