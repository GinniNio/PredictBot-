"""Append-only CSV storage in the synced data folder.

Layout (inside the data folder):
    captures/                      raw Bet9ja JSON, bytes unchanged
    captures/capture-records.csv   one row per selection sent for benchmarking
    selections/selections.csv      every priced, researched or rejected selection
    selections/screening.csv       every candidate screened, with the reason
    bets/bets.csv                  actual wagers (optional, separate)
    settlements/settlements.csv    results
    closing-prices/closing-prices.csv
    packs/                         research and settlement packs (JSON)

Rows are only ever appended. A record is validated against schemas.py before
it is written; invalid records raise instead of being written.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import schemas

FILES = {
    "capture": ("captures", "capture-records.csv"),
    "selection": ("selections", "selections.csv"),
    "bet": ("bets", "bets.csv"),
    "settlement": ("settlements", "settlements.csv"),
    "closing": ("closing-prices", "closing-prices.csv"),
}
SCREENING_FIELDS = ["screened_at_utc", "pack_id", "event_id", "sport", "competition", "event_name",
                    "kickoff_utc", "status", "reason", "capture_file"]


class InvalidRecord(ValueError):
    pass


class DataFolder:
    def __init__(self, root):
        self.root = Path(root)
        for sub in ("captures", "selections", "bets", "settlements", "closing-prices", "packs"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    def path(self, kind: str) -> Path:
        sub, name = FILES[kind]
        return self.root / sub / name

    # ------------------------------------------------------------- records
    def read(self, kind: str) -> list[dict]:
        p = self.path(kind)
        if not p.exists():
            return []
        with p.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def append(self, kind: str, records: list[dict]) -> int:
        if not records:
            return 0
        cols = schemas.fields(kind)
        for r in records:
            errs = schemas.validate(kind, r)
            if errs:
                raise InvalidRecord(f"{kind} record rejected by schema: {'; '.join(errs)}")
        p = self.path(kind)
        new = not p.exists() or p.stat().st_size == 0
        with p.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            if new:
                w.writeheader()
            for r in records:
                w.writerow({c: r.get(c, "") for c in cols})
        return len(records)

    def append_screening(self, rows: list[dict]) -> None:
        p = self.root / "selections" / "screening.csv"
        new = not p.exists()
        with p.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=SCREENING_FIELDS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerows(rows)

    def read_screening(self) -> list[dict]:
        p = self.root / "selections" / "screening.csv"
        if not p.exists():
            return []
        with p.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    # ------------------------------------------------------------ captures
    def capture_files(self, extra_folders=()) -> list[tuple[Path, bytes]]:
        """Every bet9ja-*.json in captures/ (and any extra folders), each set of
        identical bytes read once."""
        seen, out = set(), []
        for folder in [self.root / "captures", *map(Path, extra_folders)]:
            if not folder.exists():
                continue
            for p in sorted(folder.rglob("bet9ja-*.json")):
                raw = p.read_bytes()
                h = hashlib.sha256(raw).hexdigest()
                if h not in seen:
                    seen.add(h)
                    out.append((p, raw))
        return out

    def odds_walks(self, extra_folders=()) -> list[tuple[Path, bytes]]:
        """public-odds-walk-*.json files from the benchmark capture extension."""
        seen, out = set(), []
        for folder in [self.root / "captures", *map(Path, extra_folders)]:
            if not folder.exists():
                continue
            for p in sorted(folder.rglob("public-odds-walk-*.json")):
                raw = p.read_bytes()
                h = hashlib.sha256(raw).hexdigest()
                if h not in seen:
                    seen.add(h)
                    out.append((p, raw))
        return out

    def import_captures(self, folder) -> int:
        """Copy new bet9ja-*.json and public-odds-walk-*.json files from e.g. Downloads into captures/,
        bytes unchanged, skipping any whose content is already stored."""
        folder = Path(folder)
        if not folder.exists():
            return 0
        dest = self.root / "captures"
        have = {hashlib.sha256(p.read_bytes()).hexdigest() for p in dest.glob("*.json")}
        n = 0
        for src in sorted([*folder.glob("bet9ja-*.json"), *folder.glob("public-odds-walk-*.json")]):
            raw = src.read_bytes()
            h = hashlib.sha256(raw).hexdigest()
            if h in have:
                continue
            target = dest / src.name
            if target.exists():
                target = dest / f"{src.stem}.{h[:8]}.json"
            target.write_bytes(raw)
            have.add(h)
            n += 1
        return n

    # --------------------------------------------------------------- packs
    def save_pack(self, pack: dict) -> None:
        (self.root / "packs" / f"{pack['pack_id']}.json").write_text(json.dumps(pack, indent=1), encoding="utf-8")

    def load_pack(self, kind: str, pack_id: str | None = None) -> dict | None:
        prefix = "R-" if kind == "research" else "S-"
        files = sorted((self.root / "packs").glob(prefix + "*.json"))
        if pack_id:
            files = [p for p in files if p.stem == pack_id]
        return json.loads(files[-1].read_text(encoding="utf-8")) if files else None
