"""Training orchestration for the Soccer 1X2 Elo baseline.

EVIDENCE STATUS: this module's own test suite runs exclusively against
hand-crafted fixtures under `tests/fixtures/football_data/` — never real
Football-Data content (this sandbox has no live network access — see
`data_pipeline/FEASIBILITY_DECISION.md`). Every number a LOCAL run of this
code produces is therefore fixture-derived PROOF THE CODE BEHAVES
CORRECTLY, never a real-data performance claim.

The real, post-merge `.github/workflows/football-data-feasibility.yml`
GitHub Actions run downloads and fits on genuine football-data.co.uk
content instead. Whether a given `TrainingResult` reflects real,
fixture, missing, or unusable source data is never inferred from row
counts or split status (a synthetic fixture can report a nonzero row
count and `status == "BUILT"` just as easily as a real download) — it is
`TrainingResult.evidence_class`, computed by `evidence.classify_evidence`
from `data_pipeline/retrieval_log.json`'s own record of which files were
actually downloaded from football-data.co.uk. See `evidence.py`'s
docstring for the full classification.

Scope (deliberate — see the operator's own chronological training/
calibration/locked-test/holdout/prospective evaluation table): this module
builds ONE Elo history + one set of pre-match feature vectors across the
CONCATENATION of exactly these five dataset-contract splits, loaded via
`data_pipeline.dataset_builder.build_split()` directly (in-memory — never
depends on pre-existing JSON files on disk, for testability against
fixtures):

    split_training, split_calibration_validation, split_locked_test,
    split_out_of_time_retrospective_holdout, split_genuine_prospective_scoring

`split_closing_line_benchmark` and `split_earlier_research_backtesting` are
NEVER included in this baseline's Elo history or feature construction —
they are out of scope for this baseline entirely.

No-lookahead invariant: every record from all five splits is fed through
`features.build_dataset` in ONE STRICT REAL CHRONOLOGICAL ORDER (parsed
`date_raw`, ascending, via `elo.sort_matches_chronologically`) — a match's
own pre-match feature vector reflects Elo/season-stage state built ONLY
from strictly earlier real matches in this same combined chronological
sequence, regardless of which split that later match happens to belong to.

Fitting boundaries (never crossed):
- The logistic regression (`model.fit`) is fit ONLY on `split_training`'s
  own rows.
- Standardization parameters are fit ONLY on `split_training`'s own
  feature vectors (via `model.fit`'s default `standardization=None` path,
  since only `split_training`'s features/labels are ever passed to it).
- Temperature calibration (`calibration.fit_temperature`) is fit ONLY on
  `split_calibration_validation`'s own rows.
- `split_locked_test`, `split_out_of_time_retrospective_holdout`, and
  `split_genuine_prospective_scoring` are NEVER used for any fitting
  decision (model weights, standardization, regularization strength,
  iteration count, or temperature) — see
  `tests/test_soccer_1x2_elo_baseline.py` for a dedicated test mutating
  their fixture data and asserting the fitted artifact/temperature are
  byte-identical either way.

No closing-price leakage: this module (like `features.py`) never reads any
`price_observations` entry at all when building features or fitting —
price data is comparison-only in this baseline (see `evaluate.py`).

FROZEN model-development inputs vs. the APPEND-ONLY prospective stream
(never conflated — operator-mandated correction): `split_training`,
`split_calibration_validation`, `split_locked_test`, and
`split_out_of_time_retrospective_holdout` are this baseline's actual
frozen model-development inputs (fit / calibrate / final-eval / one-time
out-of-time confirmation) — each of the four gets its OWN individually-
reported SHA-256 hash (`FrozenHashes.split_hashes`), plus one combined
hash over just those four (`FrozenHashes.combined_hash`, reusing
`data_pipeline.dataset_builder.combined_snapshot_hash`). This combined
hash is NEVER computed over, or compared against, `split_genuine_prospective_scoring`
(2026-27) — that split is a ROLLING, APPEND-ONLY OBSERVATION STREAM (its
own content changes every time Football-Data publishes another 2026-27
result — see the dataset contract's `correction_v2_1_0`), so pinning it
inside a "frozen dataset" hash would be actively wrong. It is instead
reported completely separately as `ProspectiveStreamObservation`: its own
content hash PLUS a `run_timestamp_utc` recording when THIS PARTICULAR
observation was taken — never folded into, or described as part of, the
four frozen splits' hashes anywhere in this module's code, docstrings, or
report text.

Three-way (not four-way) hash separation for the FROZEN inputs (never
conflated into one opaque "the hash"):
- `frozen_dataset_hash` — the four-split combined hash described above.
- `code_hash` — SHA-256 over the concatenated UTF-8 bytes of this
  package's own source files (`elo.py`, `features.py`, `model.py`,
  `calibration.py`, `train.py`), in that fixed order, read fresh at run
  time.
- `artifact_hash` — SHA-256 over the fitted model artifact's own
  serialized JSON bytes (`model.ModelArtifact.to_dict()`).
- `combined_hash` — SHA-256 over the three hashes above, joined in a
  fixed, documented order — never presented as if it were one of the
  three on its own, and never includes the prospective stream's hash.

Determinism: `run_twice_determinism_check()` runs this entire pipeline
twice against the same `raw_dir`/`contract_rows` and asserts the two
produced results serialize to byte-identical JSON for every
DETERMINISTIC field (model artifact, all frozen/code/artifact/combined
hashes, and the prospective stream's own content hash) — see the
corresponding test in `tests/test_soccer_1x2_elo_baseline.py`. The
prospective stream's `run_timestamp_utc` is DELIBERATELY EXCLUDED from
that comparison (see `TrainingResult.to_deterministic_dict`): it is real
wall-clock provenance, not a function of the input data, so two calls a
moment apart legitimately differ there without indicating any
nondeterminism bug.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.dataset_builder import LEAGUE_CODES, DEFAULT_RAW_DIR, build_split, combined_snapshot_hash, load_contract_rows, sha256_bytes

from . import calibration, evidence, model
from .elo import EloEngine
from .features import FeatureBuildResult, SeasonStageTracker, build_dataset

# The four FROZEN model-development inputs — fit/calibrate/final-eval/
# one-time out-of-time confirmation. Order matters only for readability;
# `combined_snapshot_hash` itself sorts by split_id internally.
FROZEN_SPLIT_IDS: tuple[str, ...] = (
    "split_training",
    "split_calibration_validation",
    "split_locked_test",
    "split_out_of_time_retrospective_holdout",
)

# The ROLLING, APPEND-ONLY prospective observation stream — reported
# separately, never folded into FROZEN_SPLIT_IDS' combined hash.
PROSPECTIVE_STREAM_SPLIT_ID = "split_genuine_prospective_scoring"

TRAINING_SPLIT_IDS: tuple[str, ...] = FROZEN_SPLIT_IDS + (PROSPECTIVE_STREAM_SPLIT_ID,)

_THIS_PACKAGE_DIR = Path(__file__).resolve().parent
_CODE_HASH_SOURCE_FILES: tuple[str, ...] = ("elo.py", "features.py", "model.py", "calibration.py", "train.py")


def compute_code_hash(package_dir: Path = _THIS_PACKAGE_DIR) -> str:
    """SHA-256 over the concatenated UTF-8 bytes of this package's own
    training-relevant source files, read fresh at run time, in the fixed
    order `_CODE_HASH_SOURCE_FILES` — changing any one of these files (and
    ONLY these files) changes this hash."""

    combined = b""
    for filename in _CODE_HASH_SOURCE_FILES:
        combined += (package_dir / filename).read_bytes()
    return sha256_bytes(combined)


def compute_split_hash(records: list[dict[str, Any]]) -> str:
    """SHA-256 over one split's own concrete record content, sorted-key
    JSON-serialized for byte-stability. Used identically for a FROZEN
    split's hash and for the prospective stream's own content hash — the
    same hashing recipe, reported under very different names/semantics."""

    return sha256_bytes((json.dumps(records, sort_keys=True) + "\n").encode("utf-8"))


@dataclass
class FrozenHashes:
    """The four FROZEN model-development splits' individual hashes plus
    one combined hash over just those four — see this module's docstring
    for why `split_genuine_prospective_scoring` is never part of this."""

    split_hashes: dict[str, str]
    combined_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {"split_hashes": dict(self.split_hashes), "combined_hash": self.combined_hash}


def compute_frozen_hashes(records_by_split: dict[str, list[dict[str, Any]]]) -> FrozenHashes:
    """`records_by_split` must contain exactly `FROZEN_SPLIT_IDS`' records
    (a caller passing `split_genuine_prospective_scoring`'s records here
    by mistake would corrupt the frozen combined hash — `train_pipeline`
    never does this; see `ProspectiveStreamObservation` for that split's
    own, separate hash)."""

    split_hashes = {split_id: compute_split_hash(records) for split_id, records in records_by_split.items()}
    combined = combined_snapshot_hash(split_hashes)
    return FrozenHashes(split_hashes=split_hashes, combined_hash=combined)


@dataclass
class ProspectiveStreamObservation:
    """`split_genuine_prospective_scoring`'s own content hash + the
    wall-clock time THIS observation was taken. Never combined with, or
    compared against, `FrozenHashes` — see this module's docstring."""

    content_hash: str
    run_timestamp_utc: str
    row_count: int

    def to_dict(self) -> dict[str, Any]:
        return {"content_hash": self.content_hash, "run_timestamp_utc": self.run_timestamp_utc, "row_count": self.row_count}


def compute_combined_hash(frozen_dataset_hash: str, code_hash: str, artifact_hash: str) -> str:
    """SHA-256 over the three FROZEN-scope hashes, joined in a fixed order.
    Deliberately takes no prospective-stream input at all — see this
    module's docstring on why that stream is never folded in here."""

    joined = f"frozen_dataset:{frozen_dataset_hash}\ncode:{code_hash}\nartifact:{artifact_hash}"
    return sha256_bytes(joined.encode("utf-8"))


@dataclass
class TrainingResult:
    model_artifact: model.ModelArtifact
    temperature: float
    frozen_hashes: FrozenHashes
    code_hash: str
    artifact_hash: str
    combined_hash: str
    prospective_stream: ProspectiveStreamObservation
    evidence_class: str = evidence.EVIDENCE_SOURCE_UNAVAILABLE
    rows_by_split: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    exclusions: list[dict[str, Any]] = field(default_factory=list)
    split_statuses: dict[str, str] = field(default_factory=dict)
    training_frequency_by_league: dict[str, dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Everything needed to reproduce/verify this run's evidence,
        JSON-serializable — includes the prospective stream's real
        `run_timestamp_utc`, so this dict is NOT itself appropriate for a
        determinism byte-comparison (see `to_deterministic_dict`).

        `implementation_status` is derived ONLY from `evidence_class` —
        never from row counts or split status directly (an earlier version
        of this code inferred "real data" from a nonzero row count plus a
        `BUILT` split status, which a synthetic fixture satisfies just as
        easily as a real download — see `evidence.py`'s own docstring)."""

        return {
            "implementation_status": evidence.describe_evidence_class(self.evidence_class),
            "evidence_class": self.evidence_class,
            "model_artifact": self.model_artifact.to_dict(),
            "temperature": self.temperature,
            "frozen_hashes": self.frozen_hashes.to_dict(),
            "code_hash": self.code_hash,
            "artifact_hash": self.artifact_hash,
            "combined_hash": self.combined_hash,
            "prospective_stream_observation": self.prospective_stream.to_dict(),
            "split_statuses": self.split_statuses,
            "training_frequency_by_league": self.training_frequency_by_league,
            "row_counts_by_split": {split_id: len(rows) for split_id, rows in self.rows_by_split.items()},
            "exclusion_count": len(self.exclusions),
        }

    def to_deterministic_dict(self) -> dict[str, Any]:
        """Same as `to_dict()` but with the prospective stream's real
        wall-clock `run_timestamp_utc` removed — that field is expected to
        differ between two calls of `train_pipeline` a moment apart even
        though every actual DATA-derived field (including the stream's own
        `content_hash`) must stay byte-identical. Used by
        `run_twice_determinism_check`."""

        data = self.to_dict()
        data["prospective_stream_observation"] = {
            k: v for k, v in data["prospective_stream_observation"].items() if k != "run_timestamp_utc"
        }
        return data


def _league_frequency_baseline(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Empirical H/D/A frequency per league, computed from `rows`
    (expected: `split_training` ONLY — see `evaluate.py`'s naive-baseline
    comparison, which calls this with training rows exclusively, never an
    evaluation split's own rows)."""

    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        league_code = row["record_ref"]["league_code"]
        label = row["label"]
        counts.setdefault(league_code, {"H": 0, "D": 0, "A": 0})
        counts[league_code][label] += 1

    frequencies: dict[str, dict[str, float]] = {}
    for league_code, league_counts in counts.items():
        total = sum(league_counts.values())
        frequencies[league_code] = {k: (v / total if total else 0.0) for k, v in league_counts.items()}
    return frequencies


def _empty_artifact() -> model.ModelArtifact:
    """No training data available in this run (e.g. an empty sandbox
    raw_dir with no real files downloaded) — an honest all-zero model
    rather than a crash. `model.fit` requires >=1 row, so this is a
    documented short-circuit."""

    from .features import FEATURE_NAMES as _FEATURE_NAMES
    from .model import CLASS_ORDER as _CLASS_ORDER
    from .model import DUMMY_LEAGUE_CODES as _DUMMY_LEAGUE_CODES

    empty_standardization = model.StandardizationParams(
        means={name: 0.0 for name in model.STANDARDIZED_FEATURE_NAMES},
        stds={name: 1.0 for name in model.STANDARDIZED_FEATURE_NAMES},
    )
    return model.ModelArtifact(
        feature_names=_FEATURE_NAMES,
        class_order=_CLASS_ORDER,
        league_dummy_codes=_DUMMY_LEAGUE_CODES,
        weights=[[0.0] * len(_FEATURE_NAMES) for _ in _CLASS_ORDER],
        biases=[0.0] * len(_CLASS_ORDER),
        standardization=empty_standardization,
        l2_strength=model.DEFAULT_L2_STRENGTH,
        iterations=0,
        learning_rate=model.DEFAULT_LEARNING_RATE,
        final_loss=0.0,
        training_row_count=0,
    )


def train_pipeline(
    raw_dir: Path = DEFAULT_RAW_DIR,
    contract_rows: dict[str, dict[str, Any]] | None = None,
) -> TrainingResult:
    """Run the full training pipeline end-to-end: load the five in-scope
    splits, build one continuous chronological Elo/feature history across
    their concatenation, fit the logistic regression on
    `split_training` only, fit temperature calibration on
    `split_calibration_validation` only, and assemble the frozen-hash +
    prospective-stream evidence (see this module's docstring for why those
    two are never conflated)."""

    contract_rows = contract_rows or load_contract_rows()

    records_by_split: dict[str, list[dict[str, Any]]] = {}
    split_statuses: dict[str, str] = {}
    season_codes_by_split: dict[str, list[str]] = {}
    all_records: list[dict[str, Any]] = []
    for split_id in TRAINING_SPLIT_IDS:
        split_result = build_split(split_id, raw_dir=raw_dir, contract_rows=contract_rows)
        split_statuses[split_id] = split_result["status"]
        records_by_split[split_id] = split_result["records"]
        season_codes_by_split[split_id] = split_result["season_codes"]
        for record in split_result["records"]:
            tagged = dict(record)
            tagged["split_id"] = split_id
            all_records.append(tagged)

    elo_engine = EloEngine()
    season_tracker = SeasonStageTracker()
    build_result: FeatureBuildResult = build_dataset(all_records, elo_engine=elo_engine, season_tracker=season_tracker)

    rows_by_split: dict[str, list[dict[str, Any]]] = {split_id: [] for split_id in TRAINING_SPLIT_IDS}
    for feature_vector, label, record_ref in zip(build_result.features, build_result.labels, build_result.record_refs):
        split_id = record_ref.get("split_id")
        if split_id not in rows_by_split:
            continue
        rows_by_split[split_id].append({"feature_vector": feature_vector, "label": label, "record_ref": record_ref})

    training_rows = rows_by_split["split_training"]
    calibration_rows_raw = rows_by_split["split_calibration_validation"]

    if not training_rows:
        artifact = _empty_artifact()
        temperature = 1.0
    else:
        artifact = model.fit(
            [row["feature_vector"] for row in training_rows],
            [row["label"] for row in training_rows],
        )
        calibration_tuples = [(row["feature_vector"], row["label"]) for row in calibration_rows_raw]
        temperature = calibration.fit_temperature(artifact, calibration_tuples)

    artifact_hash = sha256_bytes((json.dumps(artifact.to_dict(), sort_keys=True) + "\n").encode("utf-8"))

    frozen_records_by_split = {split_id: records_by_split[split_id] for split_id in FROZEN_SPLIT_IDS}
    frozen_hashes = compute_frozen_hashes(frozen_records_by_split)
    code_hash = compute_code_hash()
    combined_hash = compute_combined_hash(frozen_hashes.combined_hash, code_hash, artifact_hash)

    evidence_class = evidence.classify_evidence(
        raw_dir=raw_dir,
        season_codes_by_frozen_split={split_id: season_codes_by_split[split_id] for split_id in FROZEN_SPLIT_IDS},
        league_codes=LEAGUE_CODES,
        frozen_row_count=sum(len(records_by_split[split_id]) for split_id in FROZEN_SPLIT_IDS),
    )

    prospective_records = records_by_split[PROSPECTIVE_STREAM_SPLIT_ID]
    prospective_stream = ProspectiveStreamObservation(
        content_hash=compute_split_hash(prospective_records),
        run_timestamp_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        row_count=len(prospective_records),
    )

    training_frequency_by_league = _league_frequency_baseline(training_rows)

    return TrainingResult(
        model_artifact=artifact,
        temperature=temperature,
        frozen_hashes=frozen_hashes,
        code_hash=code_hash,
        artifact_hash=artifact_hash,
        combined_hash=combined_hash,
        prospective_stream=prospective_stream,
        evidence_class=evidence_class,
        rows_by_split=rows_by_split,
        exclusions=build_result.exclusions,
        split_statuses=split_statuses,
        training_frequency_by_league=training_frequency_by_league,
    )


def run_twice_determinism_check(
    raw_dir: Path = DEFAULT_RAW_DIR,
    contract_rows: dict[str, dict[str, Any]] | None = None,
) -> tuple[bool, TrainingResult, TrainingResult]:
    """Run `train_pipeline` twice against the same input and return
    `(is_identical, first_result, second_result)`, where `is_identical` is
    True iff both results' `to_deterministic_dict()` JSON serialization
    (`json.dumps(..., sort_keys=True)`) is byte-identical — this
    deliberately excludes the prospective stream's real
    `run_timestamp_utc` (see `TrainingResult.to_deterministic_dict`), which
    legitimately differs between two wall-clock calls."""

    contract_rows = contract_rows or load_contract_rows()
    first = train_pipeline(raw_dir=raw_dir, contract_rows=contract_rows)
    second = train_pipeline(raw_dir=raw_dir, contract_rows=contract_rows)
    first_json = json.dumps(first.to_deterministic_dict(), sort_keys=True)
    second_json = json.dumps(second.to_deterministic_dict(), sort_keys=True)
    return first_json == second_json, first, second


def main() -> None:
    result = train_pipeline()
    print(f"evidence_class: {result.evidence_class}")
    print(evidence.describe_evidence_class(result.evidence_class))
    print("Frozen model-development inputs (split_training, split_calibration_validation,")
    print("split_locked_test, split_out_of_time_retrospective_holdout):")
    for split_id, split_hash in result.frozen_hashes.split_hashes.items():
        print(f"  {split_id}: {split_hash}")
    print(f"  frozen_dataset_hash (combined, 4 splits only)={result.frozen_hashes.combined_hash}")
    print(f"code_hash={result.code_hash}")
    print(f"artifact_hash={result.artifact_hash}")
    print(f"combined_hash={result.combined_hash}")
    print(
        "split_genuine_prospective_scoring (ROLLING APPEND-ONLY OBSERVATION STREAM — "
        "never part of the frozen hashes above):"
    )
    print(f"  content_hash={result.prospective_stream.content_hash}")
    print(f"  run_timestamp_utc={result.prospective_stream.run_timestamp_utc}")
    print(f"  row_count={result.prospective_stream.row_count}")
    print(f"temperature={result.temperature}")
    for split_id, status in result.split_statuses.items():
        print(f"  {split_id}: status={status}, rows={len(result.rows_by_split.get(split_id, []))}")
    if result.exclusions:
        print(f"exclusions: {len(result.exclusions)} row(s) excluded from feature construction")
    print(
        "soccer_1x2 stays completely UNREGISTERED regardless of these numbers — no "
        "model-admission-registry row, no promotion-threshold change, no adapter "
        "wiring happens here or anywhere in this PR."
    )

    reports_dir = Path(__file__).resolve().parent / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = reports_dir / "model_artifact.json"
    artifact_path.write_text(json.dumps(result.model_artifact.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {artifact_path}")


if __name__ == "__main__":
    main()
