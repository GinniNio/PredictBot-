"""Soccer 1X2 Elo v1 artifact refresh lifecycle -- CANDIDATE CREATION ONLY,
never promotion.

    training input (raw football-data.co.uk files)
    -> existing training + evaluation code, run once
    -> model_artifact.json / live_snapshot.json / evaluation_report.json,
       all from that SAME execution
    -> model_artifact_manifest.json, built from those exact bytes
       (verifying evidence class, hash provenance, code hash, and
       internal consistency -- aborts before publishing on any failure)
    -> candidate_bundle_manifest.json (immutable, keyed by bundle_hash)
    -> artifact-comparison.json (candidate backtest vs. incumbent backtest,
       kept STRICTLY SEPARATE from the incumbent's own prospective metrics)
    -> promotion-review.json (human-readable; recommendation is always
       HOLD_FOR_PROSPECTIVE_EVIDENCE -- see below)

One command::

    python -m pcbf_calculator refresh-soccer-artifact \\
        --training-input <path> \\
        --incumbent-manifest <path> \\
        --incumbent-performance-dir <path> \\
        --output-dir <path>

**Why the recommendation is always HOLD_FOR_PROSPECTIVE_EVIDENCE.** A
freshly trained candidate has a real BACKTEST evaluation (frozen,
held-out historical splits) the moment it is built, but ZERO prospective
forecasts -- it has never been used to forecast a single real fixture.
The incumbent artifact, by contrast, may have a real prospective record
(actual `SCORED` forecasts against actual settled results -- see
`orchestration/forecast_performance_report.py`). Comparing the
candidate's backtest against the incumbent's prospective record as if
they were the same kind of evidence would be comparing a one-time,
frozen historical test against an ongoing real-world track record --
exactly the confusion this module exists to prevent structurally, not
just by convention. **This module can never produce any recommendation
other than `HOLD_FOR_PROSPECTIVE_EVIDENCE`** -- there is no code path to
anything else. Promotion (and the shadow forecasting a candidate needs
to earn its own prospective record) is explicitly out of scope for this
PR; see this module's own "Explicit boundaries" below.

**Candidate creation is kept structurally separate from promotion.**
This module never registers, classifies, or activates anything. It reads
the incumbent's manifest and prospective-performance report (read-only,
never mutated), and writes an entirely new, self-contained candidate
directory -- the currently active artifact
(`pcbf_calculator/adapters/soccer_1x2_elo_v1/data/`) is never touched.

**Reuses, never reimplements, the existing training/evaluation/manifest
code:**
- `research.soccer_1x2_elo_baseline.train.run_twice_determinism_check`
  for the actual training run (this IS the "run the existing training
  and evaluation code" step -- it runs training TWICE against the same
  input and asserts the two results are byte-identical on every
  deterministic field, which is this module's own "internal
  consistency" check, reused rather than re-derived).
- `research.soccer_1x2_elo_baseline.evaluate.build_evaluation_report`
  and `research.soccer_1x2_elo_baseline.hash_provenance.check_frozen_hashes`
  for the evaluation report and the frozen-hash-vs-`expected_hashes.json`
  provenance check.
- `research.soccer_1x2_elo_baseline.export_live_snapshot.build_live_snapshot`
  for the live ratings snapshot, from the SAME `raw_dir` execution.
- `pcbf_calculator.adapters.soccer_1x2_elo_v1.build_manifest.build_manifest`
  for the manifest itself -- this is where "verify evidence class, hash
  provenance ... and internal consistency" is actually enforced: that
  function already raises `ValueError` when `evidence_class` is not
  `LIVE_SOURCE_VALIDATED` or any hash-provenance check is not
  `CONFIRMED`. This module additionally treats a `has_mismatch` combined
  with `LIVE_SOURCE_VALIDATED` evidence as a hard abort (mirroring
  `evaluate.main()`'s own exit-1 condition) and independently recomputes
  the training code's own hash as one more internal-consistency check.

**Atomic publish -- "any hash or provenance failure aborts before
publishing the output directory."** Every step above runs against a
private staging directory; `--output-dir` is created (via an atomic
rename) ONLY after every verification step has succeeded. A failure at
any point leaves `--output-dir` completely untouched -- never a
partially written candidate.

**Byte-identical bundles for identical inputs, files from different
executions cannot be mixed.** `model_artifact.json`, `live_snapshot.json`,
and `model_artifact_manifest.json` carry no wall-clock timestamp at all
and are therefore genuinely byte-identical files, byte for byte, across
two runs against identical `--training-input` content.
`evaluation_report.json` carries exactly one real wall-clock field
(`prospective_stream_observation.run_timestamp_utc` -- pre-existing,
reviewed `research.soccer_1x2_elo_baseline.train` behavior, not
something this module changes or works around by altering that
package's own output) that legitimately differs between two runs a
moment apart; `candidate_bundle_manifest.json`'s own `bundle_hash` is
computed from each file's ``deterministic_content_hash`` (identical to
its own raw file hash for the three timestamp-free files;
`evaluation_report.json`'s own is computed with that one field stripped
first) -- so `bundle_hash` itself, and `candidate_bundle_manifest.json`'s
own bytes, ARE byte-identical across two runs against identical
training input, even though the raw `evaluation_report.json` file
legitimately is not. `verify_candidate_bundle` recomputes every file's
`deterministic_content_hash` from whatever is currently on disk and
compares it against what `candidate_bundle_manifest.json` claims --
swapping in a file from a genuinely different candidate (different
training data/model) changes its content and is caught; two files from
two runs of the SAME identical input are, correctly, indistinguishable.

**Explicit boundaries (this PR).** No model-admission-registry row
created or modified. No `classification_ceiling` change. No
promotion-threshold change. No staking or ticket-construction change.
No active-artifact/production-file mutation of any kind -- the
incumbent's own files under
`pcbf_calculator/adapters/soccer_1x2_elo_v1/data/` are read-only inputs
here, never touched. No scheduled/automatic retraining (this is a
single, explicit, operator-invoked command). No automatic promotion --
that requires a real prospective record the candidate does not yet
have, and stays a separate, later, human-controlled process. The
follow-up PR this one is designed for adds candidate SHADOW
forecasting, so a candidate can accumulate its own real prospective
history over the same fixtures the incumbent forecasts -- only after
that exists does comparing the two prospectively become meaningful.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]


def _ensure_research_importable() -> None:
    """Same fallback pattern ``orchestration/forecast_ledger_writer.py``
    and ``orchestration/football_data_settlement.py`` already use for
    ``ledgers``/``data_pipeline`` -- see either module's own copy of this
    helper for the full rationale. ``research`` is a repo-root sibling of
    ``src/`` (see ``research/__init__.py``'s own docstring)."""

    try:
        import research  # noqa: F401

        return
    except ImportError:
        pass
    if (REPO_ROOT / "research" / "__init__.py").is_file() and str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))


_ensure_research_importable()

from research.soccer_1x2_elo_baseline import (  # noqa: E402
    evaluate,
    evidence,
    export_live_snapshot,
    hash_provenance,
    train,
)

from ..adapters.soccer_1x2_elo_v1 import build_manifest  # noqa: E402

SCHEMA_VERSION_BUNDLE = "pcbf-soccer-artifact-candidate-bundle.v1"
SCHEMA_VERSION_COMPARISON = "pcbf-soccer-artifact-comparison.v1"
SCHEMA_VERSION_PROMOTION_REVIEW = "pcbf-soccer-artifact-promotion-review.v1"

# The ONLY recommendation this module can ever produce -- see this
# module's own docstring for why. Not a default; not overridable by any
# input this module reads. A future PR that adds real promotion logic
# will need its own, separate, explicitly-reviewed code path -- this
# constant is never silently repurposed to mean something else.
RECOMMENDATION_HOLD_FOR_PROSPECTIVE_EVIDENCE = "HOLD_FOR_PROSPECTIVE_EVIDENCE"

# The two frozen, held-out BACKTEST splits `build_manifest.py` already
# records under `provenance.backtest_evidence` -- the only splits this
# module ever compares candidate vs. incumbent on. Never
# `split_genuine_prospective_scoring` (a rolling settlement observation,
# not a backtest -- see `train.py`'s own module docstring) and never any
# notion of the candidate's own "prospective" evidence, which does not
# exist yet.
BACKTEST_SPLIT_IDS: tuple[str, ...] = ("split_locked_test", "split_out_of_time_retrospective_holdout")

CANDIDATE_BUNDLE_FILE_ORDER: tuple[str, ...] = (
    "model_artifact.json",
    "live_snapshot.json",
    "evaluation_report.json",
    "model_artifact_manifest.json",
)


class ArtifactRefreshError(Exception):
    """Base class for every hard-abort condition this module raises.
    Raised BEFORE any output has been published to the caller's
    ``--output-dir`` -- see this module's own "Atomic publish" docstring
    section."""


class InternalConsistencyError(ArtifactRefreshError):
    """``train.run_twice_determinism_check`` found the same input produces
    two different results -- a real bug in the training pipeline itself,
    never a caller input problem. Nothing is published."""


class HashProvenanceError(ArtifactRefreshError):
    """A frozen-split hash (or the combined ``frozen_dataset_hash``) that a
    human has previously pinned in ``expected_hashes.json`` no longer
    matches this run's freshly computed value, AND this run's own
    ``evidence_class`` is ``LIVE_SOURCE_VALIDATED`` -- the real, live
    frozen dataset has drifted since it was pinned (mirrors
    ``evaluate.main()``'s own exit-1 condition). Nothing is published."""


class CodeHashConsistencyError(ArtifactRefreshError):
    """Defensive check: an independently, freshly computed code hash
    disagrees with what ``train_pipeline`` itself reported. If this ever
    fires it means ``train.compute_code_hash`` is not actually
    deterministic across two calls in the same process -- a bug in that
    function, never a caller input problem."""


class CandidateOutputExistsError(ArtifactRefreshError):
    """``--output-dir`` already exists. Never silently overwritten -- an
    operator who wants a fresh candidate build must remove or rename the
    prior one explicitly."""


class SameRunIntegrityError(ArtifactRefreshError):
    """``live_snapshot.json``'s own ``code_hash`` (computed by
    ``export_live_snapshot.build_live_snapshot``'s own independent call to
    ``train.compute_code_hash()``) disagrees with ``evaluation_report.json``'s
    own ``code_hash`` (already independently verified against
    ``train_pipeline``'s own reported value by
    ``run_training_and_evaluation``'s own ``CodeHashConsistencyError``
    check). Both calls are pure functions of the training package's own
    source files, so they can only disagree if the code changed between
    the two calls within this same process -- exactly the "one execution
    identifier" every candidate file must share. Nothing is published."""


class CandidateBundleVerificationError(ArtifactRefreshError):
    """``verify_candidate_bundle`` found a candidate file whose freshly
    recomputed ``deterministic_content_hash`` disagrees with what
    ``candidate_bundle_manifest.json`` recorded -- the bundle has been
    mixed with a file from a different execution (different training
    input/model), or corrupted. Never resolved automatically."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json_sha256(obj: Any) -> str:
    return _sha256_bytes((json.dumps(obj, sort_keys=True) + "\n").encode("utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _argv_excluding_output_dir(argv: list[str]) -> list[str]:
    """Strips ``--output-dir <value>`` (in either ``--output-dir X`` or
    ``--output-dir=X`` form) out of the recorded ``generation_command``
    before it is embedded in ``model_artifact_manifest.json``. This
    matters for real determinism, not just tidiness: two runs against
    byte-identical ``--training-input``/``--incumbent-*`` content are
    the SAME logical candidate even when written to two different
    ``--output-dir`` paths (you cannot write two runs to the identical
    path without collision) -- embedding the output path would make
    ``model_artifact_manifest.json``, and therefore ``bundle_hash``,
    spuriously differ between what should be two identical bundles.
    ``--training-input``/``--incumbent-manifest``/
    ``--incumbent-performance-dir`` all stay in the recorded command --
    those genuinely identify what this candidate was built from."""

    filtered: list[str] = []
    skip_next = False
    for token in argv:
        if skip_next:
            skip_next = False
            continue
        if token == "--output-dir":
            skip_next = True
            continue
        if token.startswith("--output-dir="):
            continue
        filtered.append(token)
    return filtered


def current_commit_sha(repo_root: Path = REPO_ROOT) -> str:
    """The git commit SHA this code is currently checked out at, for the
    manifest's own provenance record -- never guessed. Falls back to a
    clearly-labeled placeholder (never raises) when there is no ``.git``
    directory to ask (e.g. running from an installed wheel with no git
    checkout at all, which this module's own wheel-install test
    exercises)."""

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "UNKNOWN_NOT_A_GIT_CHECKOUT"


def run_training_and_evaluation(raw_dir: Path) -> tuple[train.TrainingResult, dict[str, Any]]:
    """Runs the existing training pipeline TWICE (via
    ``train.run_twice_determinism_check``) against ``raw_dir`` and builds
    the evaluation report from the first run -- this single call is both
    "run the existing training and evaluation code" and this module's own
    internal-consistency verification. Raises ``InternalConsistencyError``/
    ``HashProvenanceError``/``CodeHashConsistencyError`` (never returns a
    partial result) if any check fails."""

    is_identical, first_result, _second_result = train.run_twice_determinism_check(raw_dir=raw_dir)
    if not is_identical:
        raise InternalConsistencyError(
            "train.run_twice_determinism_check found two runs against the same training input "
            "produced different deterministic results -- this is a bug in the training pipeline "
            "itself. Refusing to build a candidate from a non-reproducible run."
        )
    result = first_result

    recomputed_code_hash = train.compute_code_hash()
    if recomputed_code_hash != result.code_hash:
        raise CodeHashConsistencyError(
            f"train.compute_code_hash() computed {recomputed_code_hash!r} independently, but "
            f"train_pipeline's own result reported code_hash={result.code_hash!r}. These must "
            "always agree -- compute_code_hash is a pure function of the training package's own "
            "source files, read fresh each call."
        )

    hash_report = hash_provenance.check_frozen_hashes(result.frozen_hashes.split_hashes, result.frozen_hashes.combined_hash)
    if hash_report.has_mismatch and result.evidence_class == evidence.EVIDENCE_LIVE_SOURCE_VALIDATED:
        mismatches = [c.split_id for c in hash_report.split_checks if c.status == hash_provenance.STATUS_MISMATCH]
        if hash_report.combined_check.status == hash_provenance.STATUS_MISMATCH:
            mismatches.append(hash_report.combined_check.split_id)
        raise HashProvenanceError(
            f"Frozen-split hash provenance MISMATCH against expected_hashes.json for {mismatches} "
            "on a LIVE_SOURCE_VALIDATED run -- the real, live frozen dataset has drifted since it "
            "was pinned. Refusing to build a candidate; a human must review and, if the new "
            "content is correct, pin the new hash in its own small evidence PR first."
        )

    evaluation_report = evaluate.build_evaluation_report(result)
    evaluation_report["hash_provenance"] = hash_report.to_dict()
    return result, evaluation_report


def _strip_prospective_run_timestamp(evaluation_report: dict[str, Any]) -> dict[str, Any]:
    """``evaluation_report.json``'s ONE real wall-clock field
    (``prospective_stream_observation.run_timestamp_utc``) removed -- see
    this module's own docstring "Byte-identical bundles" section for why.
    Mirrors ``train.TrainingResult.to_deterministic_dict``'s own,
    pre-existing precedent for excluding exactly this field."""

    stream = evaluation_report.get("prospective_stream_observation") or {}
    deterministic = dict(evaluation_report)
    deterministic["prospective_stream_observation"] = {k: v for k, v in stream.items() if k != "run_timestamp_utc"}
    return deterministic


def compute_bundle_file_hashes(candidate_dir: Path) -> dict[str, dict[str, str]]:
    """Reads every one of ``CANDIDATE_BUNDLE_FILE_ORDER``'s files back
    from ``candidate_dir`` (never from in-memory objects still held from
    the write step -- this is what makes ``verify_candidate_bundle``
    meaningful as a check against whatever ACTUALLY ended up on disk) and
    computes both ``file_sha256`` (the file's own raw bytes) and
    ``deterministic_content_hash`` (identical to ``file_sha256`` for
    every file except ``evaluation_report.json``, whose one real
    timestamp field is stripped first)."""

    hashes: dict[str, dict[str, str]] = {}
    for filename in CANDIDATE_BUNDLE_FILE_ORDER:
        path = candidate_dir / filename
        file_bytes = path.read_bytes()
        file_sha256 = _sha256_bytes(file_bytes)
        if filename == "evaluation_report.json":
            deterministic_content_hash = _canonical_json_sha256(
                _strip_prospective_run_timestamp(json.loads(file_bytes.decode("utf-8")))
            )
        else:
            deterministic_content_hash = file_sha256
        hashes[filename] = {"file_sha256": file_sha256, "deterministic_content_hash": deterministic_content_hash}
    return hashes


def compute_bundle_hash(file_hashes: dict[str, dict[str, str]]) -> str:
    """SHA-256 over the four files' own ``deterministic_content_hash``
    values, joined in ``CANDIDATE_BUNDLE_FILE_ORDER`` -- never over
    ``file_sha256`` (which would make this differ across two genuinely
    identical-input runs, purely because of
    ``evaluation_report.json``'s one real timestamp field)."""

    joined = "\n".join(f"{name}:{file_hashes[name]['deterministic_content_hash']}" for name in CANDIDATE_BUNDLE_FILE_ORDER)
    return _sha256_bytes(joined.encode("utf-8"))


def build_candidate_bundle_manifest(candidate_dir: Path) -> dict[str, Any]:
    file_hashes = compute_bundle_file_hashes(candidate_dir)
    return {
        "schema_version": SCHEMA_VERSION_BUNDLE,
        "note": (
            "bundle_hash and every file's own deterministic_content_hash are computed with "
            "evaluation_report.json's one real wall-clock field "
            "(prospective_stream_observation.run_timestamp_utc) stripped first -- two runs "
            "against byte-identical --training-input content produce a byte-identical "
            "bundle_hash (and this entire file) even though the raw evaluation_report.json file "
            "on disk legitimately differs by that one timestamp. file_sha256 is the file's own "
            "raw bytes, recorded for audit -- it is NOT used to compute bundle_hash."
        ),
        "bundle_hash": compute_bundle_hash(file_hashes),
        "file_order": list(CANDIDATE_BUNDLE_FILE_ORDER),
        "files": file_hashes,
    }


def verify_candidate_bundle(candidate_dir: Path) -> None:
    """Recomputes every file's ``deterministic_content_hash`` from
    whatever is CURRENTLY on disk in ``candidate_dir`` and compares it
    (and the recomputed ``bundle_hash``) against
    ``candidate_bundle_manifest.json``'s own recorded values. Raises
    ``CandidateBundleVerificationError`` (naming exactly which file
    disagrees) if anything has been swapped or corrupted since the
    bundle was built -- the concrete, enforced form of "files from
    different executions cannot be mixed," never merely a documented
    expectation."""

    manifest_path = candidate_dir / "candidate_bundle_manifest.json"
    recorded = json.loads(manifest_path.read_text(encoding="utf-8"))
    recomputed_file_hashes = compute_bundle_file_hashes(candidate_dir)

    mismatched_files = [
        filename
        for filename in CANDIDATE_BUNDLE_FILE_ORDER
        if recomputed_file_hashes[filename]["deterministic_content_hash"]
        != recorded["files"][filename]["deterministic_content_hash"]
    ]
    if mismatched_files:
        raise CandidateBundleVerificationError(
            f"candidate_bundle_manifest.json's recorded content hash disagrees with the file(s) "
            f"currently on disk: {mismatched_files}. This candidate directory has been mixed with "
            "file(s) from a different execution, or a file has been modified since the bundle was "
            "built. Never resolved automatically -- rebuild the candidate from scratch."
        )

    recomputed_bundle_hash = compute_bundle_hash(recomputed_file_hashes)
    if recomputed_bundle_hash != recorded["bundle_hash"]:
        raise CandidateBundleVerificationError(
            f"Recomputed bundle_hash {recomputed_bundle_hash!r} disagrees with "
            f"candidate_bundle_manifest.json's recorded {recorded['bundle_hash']!r}, even though "
            "every individual file's own content hash matched -- this should be unreachable; "
            "it would indicate candidate_bundle_manifest.json's own bundle_hash field was "
            "hand-edited or corrupted."
        )


def load_incumbent_prospective_metrics(incumbent_performance_dir: Path) -> dict[str, Any] | None:
    """Reads ``performance-summary.json``'s own ``overall`` block from
    ``incumbent_performance_dir`` (the directory
    ``report-forecast-performance --output-dir`` already writes into --
    see ``orchestration/forecast_performance_report.py``). Returns
    ``None`` (never a fabricated zero-sample block) when that file does
    not exist -- a legitimate state (the incumbent may not have any
    settled forecasts yet), not an error this module hard-fails on."""

    path = incumbent_performance_dir / "performance-summary.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    overall = data.get("overall") or {}
    return {
        "source_file": str(path),
        "reconciles": data.get("reconciles"),
        "sample_count": overall.get("sample_count"),
        "small_sample": overall.get("small_sample"),
        "small_sample_threshold": data.get("small_sample_threshold"),
        "brier_score": overall.get("brier_score"),
        "log_loss": overall.get("log_loss"),
        "accuracy": overall.get("accuracy"),
    }


def build_artifact_comparison(
    candidate_manifest: dict[str, Any],
    incumbent_manifest: dict[str, Any],
    incumbent_prospective_metrics: dict[str, Any] | None,
) -> dict[str, Any]:
    """Candidate-vs-incumbent BACKTEST comparison (two frozen, held-out
    evaluations -- a genuinely apples-to-apples comparison) kept in its
    own block, entirely separate from ``incumbent_prospective_metrics``
    (the incumbent's real forecast track record) and from
    ``candidate_prospective_metrics`` (always ``null`` here -- the
    candidate has never forecast a real fixture). See this module's own
    docstring for why these are never compared against each other."""

    candidate_backtest = candidate_manifest.get("provenance", {}).get("backtest_evidence", {})
    incumbent_backtest = incumbent_manifest.get("provenance", {}).get("backtest_evidence", {})

    backtest_comparison: dict[str, Any] = {}
    for split_id in BACKTEST_SPLIT_IDS:
        candidate_split = candidate_backtest.get(split_id)
        incumbent_split = incumbent_backtest.get(split_id)
        entry: dict[str, Any] = {"candidate": candidate_split, "incumbent": incumbent_split}
        candidate_brier = candidate_split.get("brier_score") if candidate_split else None
        incumbent_brier = incumbent_split.get("brier_score") if incumbent_split else None
        candidate_loss = candidate_split.get("log_loss") if candidate_split else None
        incumbent_loss = incumbent_split.get("log_loss") if incumbent_split else None
        if candidate_brier is not None and incumbent_brier is not None and candidate_loss is not None and incumbent_loss is not None:
            entry["delta_brier_score"] = candidate_brier - incumbent_brier
            entry["delta_log_loss"] = candidate_loss - incumbent_loss
            entry["delta_note"] = "negative delta means the CANDIDATE scored better (lower) on this backtest split."
        else:
            entry["delta_note"] = (
                "No delta computed -- at least one side has zero rows for this split (brier_score/"
                "log_loss are null when a split's own row_count is 0), never a fabricated zero delta."
            )
        backtest_comparison[split_id] = entry

    return {
        "schema_version": SCHEMA_VERSION_COMPARISON,
        "note": (
            "backtest_comparison compares two FROZEN, HELD-OUT BACKTEST evaluations -- an "
            "apples-to-apples comparison, since both sides are the same kind of one-time "
            "historical test. incumbent_prospective_metrics is a SEPARATE, unrelated block: it "
            "describes the incumbent's REAL forecast history against actual settled results over "
            "time -- something fundamentally different from a backtest, and something the "
            "candidate does not yet have any of (candidate_prospective_metrics is null by "
            "construction). NEVER read backtest_comparison's numbers as if they were comparable "
            "to incumbent_prospective_metrics -- they are kept in separate top-level blocks "
            "specifically so they are never confused for equivalent evidence."
        ),
        "candidate": {
            "model_version": candidate_manifest.get("model_version"),
            "artifact_sha256": candidate_manifest.get("artifact_sha256"),
            "evidence_class": candidate_manifest.get("provenance", {}).get("evidence_class"),
            "training_code_content_hash": candidate_manifest.get("provenance", {}).get("training_code_content_hash"),
        },
        "incumbent": {
            "model_version": incumbent_manifest.get("model_version"),
            "artifact_sha256": incumbent_manifest.get("artifact_sha256"),
            "evidence_class": incumbent_manifest.get("provenance", {}).get("evidence_class"),
        },
        "backtest_comparison": backtest_comparison,
        "candidate_prospective_metrics": None,
        "candidate_prospective_metrics_note": (
            "Always null: this candidate has zero prospective forecasts -- it has never been "
            "used to forecast a real fixture. It will only become non-null once a later PR adds "
            "candidate shadow forecasting and that candidate has accumulated real SCORED events "
            "of its own."
        ),
        "incumbent_prospective_metrics": incumbent_prospective_metrics,
    }


def build_promotion_review(
    candidate_manifest: dict[str, Any],
    bundle_manifest: dict[str, Any],
    comparison: dict[str, Any],
) -> dict[str, Any]:
    """Human-readable promotion-review report. The ``recommendation``
    field is ALWAYS ``RECOMMENDATION_HOLD_FOR_PROSPECTIVE_EVIDENCE`` --
    see this module's own docstring for why no other value is reachable
    from this PR's code."""

    return {
        "schema_version": SCHEMA_VERSION_PROMOTION_REVIEW,
        "recommendation": RECOMMENDATION_HOLD_FOR_PROSPECTIVE_EVIDENCE,
        "recommendation_reason": (
            "This candidate was just built and has zero prospective forecasts -- it has never "
            "forecast a single real fixture. A backtest comparison against the incumbent (see "
            "backtest_comparison_summary below) is real evidence, but it is not, by itself, "
            "sufficient to promote or replace a live artifact: the incumbent's own real-world "
            "track record (incumbent_prospective_metrics_summary below) is prospective evidence "
            "this candidate has not yet earned. This tool creates a candidate bundle only -- it "
            "never promotes, and HOLD_FOR_PROSPECTIVE_EVIDENCE is the only recommendation this "
            "PR's code can produce."
        ),
        "candidate": comparison["candidate"],
        "incumbent": comparison["incumbent"],
        "bundle_hash": bundle_manifest["bundle_hash"],
        "backtest_comparison_summary": comparison["backtest_comparison"],
        "incumbent_prospective_metrics_summary": comparison["incumbent_prospective_metrics"],
        "candidate_prospective_metrics_summary": None,
        "next_steps": [
            "Build candidate shadow forecasting so this candidate accumulates its own real "
            "prospective forecast history over the same fixtures the incumbent forecasts (the "
            "next planned PR).",
            "Only once the candidate has a real, non-trivial prospective sample can a future, "
            "separate, human-controlled promotion-review process meaningfully compare it against "
            "the incumbent's own prospective record -- never against a backtest alone.",
        ],
        "explicit_boundaries": [
            "No model-admission-registry row created or modified.",
            "No classification_ceiling change.",
            "No promotion-threshold change.",
            "No staking or ticket-construction change.",
            "No active-artifact/production-file mutation of any kind.",
        ],
    }


def _publish_staging_root(staging_root: Path, output_dir: Path) -> None:
    """Moves ``staging_root`` to become ``output_dir``, safely and (on a
    single filesystem) atomically -- the real enforcement behind "any
    hash or provenance failure aborts before publishing the output
    directory" and "the current active artifact remains unchanged" for
    two failure modes ``shutil.move`` alone does not handle correctly:

    1. **A directory already exists at ``output_dir`` by the time we
       reach this line** (a concurrent ``refresh-soccer-artifact`` run
       targeting the same path, or anything else that created it after
       ``refresh_soccer_artifact``'s own early ``output_dir.exists()``
       check ran, which is a courtesy fast-fail, not the real guard).
       ``shutil.move(src, dst)`` does NOT raise in this case when ``dst``
       is an existing directory -- it silently moves ``src`` INSIDE
       ``dst`` (``dst/basename(src)``), so the published files would
       silently end up at ``output_dir/staging/candidate/...`` instead of
       ``output_dir/candidate/...``, while the caller sees no error and a
       normal "OK" result. ``os.rename`` never does this: on POSIX it
       either atomically replaces an EMPTY destination directory or
       fails outright (``ENOTEMPTY``/``EEXIST``) against a non-empty one
       -- exactly "refuse, never silently nest or overwrite."
    2. **Cross-filesystem publish** (``--output-dir`` on a different
       filesystem than the staging tempdir): ``os.rename`` alone would
       raise ``EXDEV`` here. We handle it by copying the FULL staged tree
       to a hidden sibling of ``output_dir`` on ``output_dir``'s OWN
       filesystem first (so the one remaining ``os.rename`` is same-
       filesystem and therefore atomic), and only removing the original
       staging tree after that final rename has actually succeeded -- an
       interruption (process killed, disk full) during the copy step
       leaves only the still-hidden, never-linked-to sibling and the
       original staging tree; ``output_dir`` itself is never touched
       until the single atomic rename that either fully succeeds or
       fully fails.

    Either way, a failure here raises ``CandidateOutputExistsError`` (a
    directory already occupies ``output_dir``) or propagates the
    underlying ``OSError`` (anything else) -- ``output_dir`` is never
    left partially written, and nothing at ``output_dir`` is ever
    overwritten if it already existed and was non-empty."""

    def _conflict_error(exc: OSError) -> CandidateOutputExistsError:
        return CandidateOutputExistsError(
            f"{output_dir} already exists (created after this run's own initial check -- likely "
            "a concurrent refresh-soccer-artifact run targeting the same --output-dir). Refusing "
            "to overwrite or nest inside it. This candidate's staged build was never published."
        )

    try:
        os.rename(staging_root, output_dir)
        return
    except OSError as exc:
        if exc.errno in (errno.EEXIST, errno.ENOTEMPTY):
            raise _conflict_error(exc) from exc
        if exc.errno != errno.EXDEV:
            raise

    # Cross-device (EXDEV): build the full tree on output_dir's OWN
    # filesystem first (as a hidden, not-yet-linked-to sibling), then do
    # one same-filesystem atomic rename. output_dir itself is untouched
    # until that final rename either fully succeeds or fully fails.
    same_fs_staging = output_dir.parent / f".pcbf-soccer-artifact-refresh-staging-{uuid.uuid4().hex}"
    shutil.copytree(staging_root, same_fs_staging)
    try:
        os.rename(same_fs_staging, output_dir)
    except OSError as exc:
        shutil.rmtree(same_fs_staging, ignore_errors=True)
        if exc.errno in (errno.EEXIST, errno.ENOTEMPTY):
            raise _conflict_error(exc) from exc
        raise
    shutil.rmtree(staging_root, ignore_errors=True)


def refresh_soccer_artifact(
    training_input: Path,
    incumbent_manifest_path: Path,
    incumbent_performance_dir: Path,
    output_dir: Path,
    generation_command: str,
) -> dict[str, Any]:
    """Runs the full candidate-creation pipeline. Raises an
    ``ArtifactRefreshError`` subclass (see this module's own exception
    classes) BEFORE touching ``output_dir`` at all if any verification
    step fails. Returns the same dict of parsed JSON objects it writes,
    for direct use by tests without re-reading files from disk."""

    if output_dir.exists():
        raise CandidateOutputExistsError(
            f"{output_dir} already exists -- refusing to overwrite a prior candidate build. "
            "Remove or rename it first if you want a fresh candidate here."
        )

    incumbent_manifest = json.loads(incumbent_manifest_path.read_text(encoding="utf-8"))
    incumbent_prospective_metrics = load_incumbent_prospective_metrics(incumbent_performance_dir)

    with tempfile.TemporaryDirectory(prefix="pcbf-soccer-artifact-refresh-") as tmp:
        staging_root = Path(tmp) / "staging"
        candidate_dir = staging_root / "candidate"
        candidate_dir.mkdir(parents=True)

        result, evaluation_report = run_training_and_evaluation(training_input)
        live_snapshot = export_live_snapshot.build_live_snapshot(training_input)

        # Same-run integrity: both calls above are pure functions of the
        # SAME training package source tree, called against the SAME
        # raw_dir, one right after the other in this one process -- their
        # own independently-computed code_hash values must therefore
        # agree. This is the concrete, enforced version of "every
        # candidate file must originate from one execution identifier,"
        # not merely an assumption from passing the same raw_dir twice.
        if live_snapshot["code_hash"] != evaluation_report["code_hash"]:
            raise SameRunIntegrityError(
                f"live_snapshot.json's code_hash ({live_snapshot['code_hash']!r}) disagrees with "
                f"evaluation_report.json's code_hash ({evaluation_report['code_hash']!r}) -- these "
                "two files must come from the exact same training-package code, computed moments "
                "apart in this same process. Refusing to build a candidate bundle from "
                "inconsistent files."
            )

        _write_json(candidate_dir / "model_artifact.json", result.model_artifact.to_dict())
        _write_json(candidate_dir / "live_snapshot.json", live_snapshot)
        _write_json(candidate_dir / "evaluation_report.json", evaluation_report)

        candidate_manifest = build_manifest.build_manifest(
            model_artifact_path=candidate_dir / "model_artifact.json",
            evaluation_report_path=candidate_dir / "evaluation_report.json",
            live_snapshot_path=candidate_dir / "live_snapshot.json",
            workflow_run_url="LOCAL_REFRESH_RUN",
            workflow_run_id="LOCAL_REFRESH_RUN",
            commit_sha=current_commit_sha(),
            model_version=f"soccer_1x2_elo_v1-candidate-{result.artifact_hash[:12]}",
            generation_command=generation_command,
            # A freshly built candidate has, by definition, never been
            # human-reviewed -- requiring already-CONFIRMED hash
            # provenance (build_manifest's own default, correct for
            # installing the SHIPPED adapter's active artifact) would
            # make building a candidate at all impossible. This is the
            # ONLY place in this codebase that passes
            # EVIDENCE_TRACK_CANDIDATE_UNCONFIRMED -- see build_manifest's
            # own docstring for exactly what stays enforced even on this
            # track.
            evidence_track=build_manifest.EVIDENCE_TRACK_CANDIDATE_UNCONFIRMED,
            source_manifest_entry_count=None,
            successful_source_downloads=None,
            source_manifest_note=(
                "Candidate built locally via refresh-soccer-artifact -- source download counts "
                "are not tracked by this path (see evidence_class and frozen_split_hashes above "
                "for this candidate's own real data provenance instead)."
            ),
        )
        _write_json(candidate_dir / "model_artifact_manifest.json", candidate_manifest)

        bundle_manifest = build_candidate_bundle_manifest(candidate_dir)
        _write_json(candidate_dir / "candidate_bundle_manifest.json", bundle_manifest)

        # Self-verification before publishing -- proves the bundle we are
        # about to publish is internally consistent, using the exact same
        # check a later, independent caller would run.
        verify_candidate_bundle(candidate_dir)

        comparison = build_artifact_comparison(candidate_manifest, incumbent_manifest, incumbent_prospective_metrics)
        _write_json(staging_root / "artifact-comparison.json", comparison)

        review = build_promotion_review(candidate_manifest, bundle_manifest, comparison)
        _write_json(staging_root / "promotion-review.json", review)

        # Atomic publish: output_dir is created only now, after every
        # verification above has already succeeded. See
        # `_publish_staging_root`'s own docstring for exactly what "atomic"
        # means here (same-filesystem rename, or copy-to-a-hidden-sibling-
        # then-rename cross-filesystem) and how it safely refuses rather
        # than silently nesting or overwriting if output_dir already
        # exists by the time we get here.
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        _publish_staging_root(staging_root, output_dir)

    return {
        "model_artifact": result.model_artifact.to_dict(),
        "live_snapshot": live_snapshot,
        "evaluation_report": evaluation_report,
        "candidate_manifest": candidate_manifest,
        "bundle_manifest": bundle_manifest,
        "artifact_comparison": comparison,
        "promotion_review": review,
    }


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="python -m pcbf_calculator refresh-soccer-artifact",
        description=__doc__,
    )
    parser.add_argument("--training-input", type=Path, required=True, help="Raw football-data.co.uk directory (raw_dir)")
    parser.add_argument("--incumbent-manifest", type=Path, required=True, help="Path to the incumbent's model_artifact_manifest.json")
    parser.add_argument(
        "--incumbent-performance-dir", type=Path, required=True,
        help="Directory containing the incumbent's performance-summary.json (report-forecast-performance's own output-dir)",
    )
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory to create the candidate bundle in (must not already exist)")
    args = parser.parse_args(argv)

    generation_command = "python -m pcbf_calculator refresh-soccer-artifact " + " ".join(
        _argv_excluding_output_dir(argv)
    )

    try:
        result = refresh_soccer_artifact(
            training_input=args.training_input,
            incumbent_manifest_path=args.incumbent_manifest,
            incumbent_performance_dir=args.incumbent_performance_dir,
            output_dir=args.output_dir,
            generation_command=generation_command,
        )
    except ArtifactRefreshError as exc:
        print(f"ABORTED: {exc}")
        return 2

    counts = {
        split_id: entry.get("candidate", {}).get("row_count")
        for split_id, entry in result["artifact_comparison"]["backtest_comparison"].items()
    }
    print(
        f"OK: candidate bundle_hash={result['bundle_manifest']['bundle_hash']} "
        f"evidence_class={result['candidate_manifest']['provenance']['evidence_class']} "
        f"backtest_row_counts={counts} "
        f"recommendation={result['promotion_review']['recommendation']} -> {args.output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
