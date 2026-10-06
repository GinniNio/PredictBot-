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
    rechecks/rechecks.csv          Bet9ja prices checked again before betting
    reviews/match-reviews.csv      operator decisions on fuzzy benchmark matches
    reviews/evidence-reviews.csv   written evidence reviews
    rules/settlement-rules.csv     confirmed settlement-rule matches
    predictbot-data.json           data-schema version and last writer
    predictbot-session.json        which laptop is running the app (synced warning)

Rows are only ever appended. A record is validated against schemas.py before
it is written; invalid records raise instead of being written. When a newer
app adds columns, the file is rewritten once with the wider header (values
unchanged, original kept in .backup/). A data folder written by a newer app
is read-only to an older one.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import schemas

FILES = {
    "capture": ("captures", "capture-records.csv"),
    "selection": ("selections", "selections.csv"),
    "bet": ("bets", "bets.csv"),
    "settlement": ("settlements", "settlements.csv"),
    "closing": ("closing-prices", "closing-prices.csv"),
    "recheck": ("rechecks", "rechecks.csv"),
    "match_review": ("reviews", "match-reviews.csv"),
    "rule": ("rules", "settlement-rules.csv"),
    "evidence_review": ("reviews", "evidence-reviews.csv"),
}
# Files checked for OneDrive conflict copies besides the record files.
EXTRA_SYNCED = {("selections", "screening.csv")}
VERSION_FILE = "predictbot-data.json"
FILE_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})T\d{2}")
SESSION_FILE = "predictbot-session.json"
SESSION_STALE = timedelta(minutes=15)
SCREENING_FIELDS = ["screened_at_utc", "pack_id", "event_id", "sport", "competition", "event_name",
                    "kickoff_utc", "status", "reason", "capture_file"]


class InvalidRecord(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _read_json(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_atomic(p: Path, text: str) -> None:
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    os.replace(tmp, p)


class DataFolder:
    def __init__(self, root, host: str = "", app_version: str = schemas.APP_VERSION):
        self.root = Path(root)
        self.host = host or "this laptop"
        self.app_version = app_version
        self.handover_confirmed = False
        for sub in ("captures", "selections", "bets", "settlements", "closing-prices", "packs",
                    "rechecks", "reviews", "rules"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------- write guard, versions
    def data_version(self) -> int:
        return int(_read_json(self.root / VERSION_FILE).get("data_schema_version") or 1)

    def write_block(self) -> str | None:
        """Why this app must not write here right now, or None."""
        v = self.data_version()
        if v > schemas.DATA_SCHEMA_VERSION:
            return (f"this data folder was written by a newer PredictBot (data schema v{v}; this app is "
                    f"v{schemas.DATA_SCHEMA_VERSION}). Run git pull on this laptop, then restart the app.")
        conflicts = self.conflict_copies()
        if conflicts:
            return ("OneDrive sync-conflict copies found: " + ", ".join(str(c.relative_to(self.root)) for c in conflicts)
                    + ". Merge them on the Pending page before writing.")
        other = self.foreign_session()
        if other and not self.handover_confirmed:
            return (f"{other.get('host', 'another laptop')} last ran PredictBot on this data folder "
                    f"(last seen {other.get('heartbeat_utc', '?')}) and did not close it here. "
                    "Follow the handover steps, then confirm on the Pending page.")
        return None

    def _check_writable(self) -> None:
        reason = self.write_block()
        if reason:
            raise InvalidRecord("read-only: " + reason)
        vf = self.root / VERSION_FILE
        info = _read_json(vf)
        if (int(info.get("data_schema_version") or 1) < schemas.DATA_SCHEMA_VERSION
                or info.get("last_writer") != self.host or info.get("app_version") != self.app_version):
            _write_atomic(vf, json.dumps({"data_schema_version": schemas.DATA_SCHEMA_VERSION,
                                          "app_version": self.app_version, "last_writer": self.host,
                                          "updated_utc": _stamp(_now())}, indent=1))

    # ------------------------------------------------ OneDrive conflict copies
    def conflict_pairs(self) -> list[tuple[Path, Path]]:
        """(conflict copy, original) pairs. OneDrive keeps both versions on a
        sync conflict as 'name-HOST.ext' or 'name (1).ext' next to the
        original. Record files and the two state files only."""
        out = []
        for sub in {s for s, _ in FILES.values()} | {"."}:
            d = self.root / sub
            if not d.exists():
                continue
            names = ({n for s2, n in FILES.values() if s2 == sub} | {n for s2, n in EXTRA_SYNCED if s2 == sub}
                     | ({SESSION_FILE, VERSION_FILE} if sub == "." else set()))
            for name in sorted(names):
                stem, ext = os.path.splitext(name)
                pat = re.compile(re.escape(stem) + r"(-[A-Za-z0-9_.-]+| \(\d+\))" + re.escape(ext) + "$")
                out.extend((p, d / name) for p in sorted(d.iterdir()) if p.name != name and pat.match(p.name))
        return out

    def conflict_copies(self) -> list[Path]:
        return [c for c, _ in self.conflict_pairs()]

    def merge_conflict_copies(self) -> list[tuple[str, int]]:
        """Add each conflict copy's rows to its original and move the copy to
        .backup/. Every row of the original is kept (a correction row is
        never dropped). A row from the copy is skipped when the original
        already has the same row apart from its timestamps: the same record
        written on both laptops. Refused while another block is active."""
        if self.data_version() > schemas.DATA_SCHEMA_VERSION:
            raise InvalidRecord("read-only: the data folder is from a newer PredictBot; git pull before merging")
        if self.foreign_session() and not self.handover_confirmed:
            raise InvalidRecord("read-only: confirm the handover before merging")
        kinds = {name: kind for kind, (_, name) in FILES.items()}
        done = []
        for copy, original in self.conflict_pairs():
            if not copy.exists():
                continue
            if copy.suffix != ".csv":      # session / version state: the live file wins
                self._backup(copy, move=True)
                done.append((copy.name, 0))
                continue
            mine, theirs = self._rows(original) or ([], []), self._rows(copy) or ([], [])
            if original.name in kinds:
                unknown = [c for c in theirs[0] if c not in schemas.fields(kinds[original.name])]
                if unknown:
                    raise InvalidRecord(f"read-only: {copy.name} has columns this app does not know "
                                        f"({', '.join(unknown)}). Run git pull, then merge.")
            header = list(dict.fromkeys(mine[0] + theirs[0]))
            same = lambda r: tuple((c, r.get(c, "")) for c in header if not c.endswith("_utc"))
            seen = {same(r) for r in mine[1]}
            rows = list(mine[1])
            for r in theirs[1]:
                if same(r) not in seen:
                    seen.add(same(r))
                    rows.append(r)
            if original.exists():
                self._backup(original)
            self._write_rows(original, header, rows)
            self._backup(copy, move=True)
            done.append((copy.name, len(rows) - len(mine[1])))
        return done

    @staticmethod
    def _rows(p: Path):
        if not p.exists():
            return None
        with p.open(newline="", encoding="utf-8") as f:
            r = csv.DictReader(f)
            data = list(r)
            return (list(r.fieldnames or []), data)

    @staticmethod
    def _write_rows(p: Path, header: list, rows: list[dict]) -> None:
        tmp = p.with_name(p.name + ".tmp")
        with tmp.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in header})
        os.replace(tmp, p)

    def _backup(self, p: Path, move: bool = False) -> None:
        b = p.parent / ".backup"
        b.mkdir(exist_ok=True)
        target = b / f"{p.stem}.{_now().strftime('%Y%m%dT%H%M%S%f')}{p.suffix}"
        (shutil.move if move else shutil.copy2)(str(p), str(target))

    # ------------------------------------------- synced session (a warning)
    def session(self) -> dict:
        return _read_json(self.root / SESSION_FILE)

    def foreign_session(self) -> dict | None:
        """The session marker of another laptop that has not closed cleanly."""
        s = self.session()
        if s and s.get("host") != self.host and not s.get("released"):
            return s
        return None

    def claim_session(self, now: datetime | None = None) -> dict | None:
        """Mark this laptop as the one running the app. Returns the other
        laptop's marker if one was found; writes then stay blocked until the
        operator confirms the handover."""
        other = self.foreign_session()
        if other and not self.handover_confirmed:
            return other
        self._write_session(now)
        return None

    def heartbeat(self, now: datetime | None = None) -> bool:
        """Refresh this laptop's marker. If another laptop's live marker has
        replaced it, that laptop took over: stop writing and return False."""
        if self.foreign_session():
            self.handover_confirmed = False
            return False
        self._write_session(now)
        return True

    def _write_session(self, now: datetime | None) -> None:
        t = _stamp(now or _now())
        s = self.session()
        started = s.get("started_utc") if s.get("host") == self.host and not s.get("released") else t
        _write_atomic(self.root / SESSION_FILE, json.dumps({"host": self.host, "app_version": self.app_version,
                                                            "started_utc": started, "heartbeat_utc": t,
                                                            "released": False}, indent=1))

    def release_session(self, now: datetime | None = None) -> None:
        s = self.session()
        if s.get("host") == self.host:
            s.update(released=True, heartbeat_utc=_stamp(now or _now()))
            _write_atomic(self.root / SESSION_FILE, json.dumps(s, indent=1))

    def confirm_handover(self, now: datetime | None = None) -> None:
        """The operator closed the app on the other laptop and saw OneDrive
        finish syncing. Take the folder over."""
        self.handover_confirmed = True
        self._write_session(now)
        self.handover_confirmed = False

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
        self._check_writable()
        p = self.path(kind)
        self._upgrade_header(p, cols)
        new = not p.exists() or p.stat().st_size == 0
        with p.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            if new:
                w.writeheader()
            for r in records:
                w.writerow({c: r.get(c, "") for c in cols})
        return len(records)

    def _upgrade_header(self, p: Path, cols: list[str]) -> None:
        """Older files have fewer columns. Rewrite once with the full header,
        values unchanged; refuse if the file has columns this app lacks."""
        if not p.exists() or p.stat().st_size == 0:
            return
        with p.open(newline="", encoding="utf-8") as f:
            header = next(csv.reader(f), [])
        if header == cols:
            return
        unknown = [c for c in header if c not in cols]
        if unknown:
            raise InvalidRecord(f"read-only: {p.name} has columns this app does not know ({', '.join(unknown)}); "
                                "it was written by a newer PredictBot. Run git pull, then restart.")
        rows = self._rows(p)[1]
        self._backup(p)
        self._write_rows(p, cols, rows)

    def append_screening(self, rows: list[dict]) -> None:
        self._check_writable()
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
    def capture_files(self, extra_folders=(), since: datetime | None = None) -> list[tuple[Path, bytes]]:
        """Every bet9ja-*.json in captures/ (and any extra folders), each set of
        identical bytes read once. `since` skips files whose name dates them
        more than a day earlier (bet captures are always read)."""
        return self._files("bet9ja-*.json", extra_folders, since)

    def odds_walks(self, extra_folders=(), since: datetime | None = None) -> list[tuple[Path, bytes]]:
        """public-odds-walk-*.json files from the benchmark capture extension."""
        return self._files("public-odds-walk-*.json", extra_folders, since)

    def _files(self, pattern, extra_folders, since):
        cutoff = (since - timedelta(days=1)).strftime("%Y-%m-%d") if since else None
        seen, out = set(), []
        for folder in [self.root / "captures", *map(Path, extra_folders)]:
            if not folder.exists():
                continue
            for p in sorted(folder.rglob(pattern)):
                m = FILE_DATE.search(p.name)
                if cutoff and m and m.group(1) < cutoff and not p.name.startswith(("bet9ja-open-bets", "bet9ja-settled")):
                    continue
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
        self._check_writable()
        (self.root / "packs" / f"{pack['pack_id']}.json").write_text(json.dumps(pack, indent=1), encoding="utf-8")

    def load_pack(self, kind: str, pack_id: str | None = None) -> dict | None:
        prefix = "R-" if kind == "research" else "S-"
        files = sorted((self.root / "packs").glob(prefix + "*.json"))
        if pack_id:
            files = [p for p in files if p.stem == pack_id]
        return json.loads(files[-1].read_text(encoding="utf-8")) if files else None
