"""File-based, local, append-only ledgers for forecasts and betting tickets.

See ``docs/LEDGER_DAILY_WORKFLOW.md`` for the operator-facing workflow and
this package's own module docstrings for the record/event shapes. Nothing
here is a database, a hosted service, or a UI -- every ledger is a plain
JSONL file the operator points the CLI at, and CSV views are a derived,
read-only export for inspection in Excel, never the source of truth.

soccer_1x2 registration, admission, classification, and promotion
thresholds are completely untouched by this package -- ledgers record
RESEARCH-classified forecasts and manually-entered tickets; they never
change what classification a model or market is allowed.
"""
