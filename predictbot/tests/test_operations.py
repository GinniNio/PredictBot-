"""Step 1 operations: settlement rules, Polymarket eligibility, rechecks,
pre-kickoff snapshots, forecast scoring, match review, coverage, and the
two-laptop setup (versions, conflicts, handover, idempotent imports)."""

import csv
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import odds_sources  # noqa: E402
import pcbf  # noqa: E402
import schemas  # noqa: E402
import workflow  # noqa: E402
from storage import DataFolder, InvalidRecord  # noqa: E402
from test_predictbot import FUTSAL, at, poly_soccer, walker  # noqa: E402


class Rules(unittest.TestCase):
    def test_min_odds(self):
        self.assertEqual(pcbf.min_odds(0.5), 2.06)
        self.assertEqual(pcbf.min_odds(1 / 3.275), 3.38)     # 3.37325 rounds up

    def test_settlement_checks(self):
        self.assertEqual(pcbf.settlement_check("pinnacle", "soccer", "1X2_REGULATION")[0], "ok")
        self.assertEqual(pcbf.settlement_check("oddsportal", "basketball", "MONEYLINE_INC_OT")[0], "ok")
        self.assertEqual(pcbf.settlement_check("pinnacle", "tennis", "MATCH_WINNER")[0], "unconfirmed")
        self.assertEqual(pcbf.settlement_check("polymarket", "soccer", "1X2_REGULATION")[0], "unconfirmed")
        rules = {"polymarket|soccer|1X2_REGULATION": "same as bet9ja", "pinnacle|tennis|MATCH_WINNER": "differs"}
        self.assertEqual(pcbf.settlement_check("polymarket", "soccer", "1X2_REGULATION", rules)[0], "ok")
        self.assertEqual(pcbf.settlement_check("Pinnacle (via chat)", "tennis", "MATCH_WINNER", rules)[0], "differs")

    def test_rule_that_differs_rejects(self):
        cand = pcbf.candidates_from_capture(walker("tennis", [("match_winner", [2.10, 1.73])], home="Medvedev, Daniil",
                                                   away="Djokovic, Novak"), "h")[0]
        out = pcbf.price_benchmark(pcbf.capture_records(cand), ["pinnacle", "https://pinnacle.com/x", "2026-10-05T10:20Z",
                                                                "1.70 / 2.20"], at("2026-10-05T10:25:00Z"),
                                   rules={"pinnacle|tennis|MATCH_WINNER": "differs"})
        self.assertEqual({r["tier"] for r in out}, {"REJECTED"})
        self.assertIn("settles tennis MATCH_WINNER differently", out[0]["rejection_reason"])

    def test_recheck(self):
        sel = {"selection_record_id": "sel_1", "tier": "PICK", "bookmaker_odds": "2.20", "fair_odds": "2.103",
               "fair_prob": "0.4755"}
        r = pcbf.recheck(sel, 2.10, at("2026-10-03T14:20:00Z"))
        self.assertEqual((r["initial_odds"], r["rechecked_odds"], r["edge_pct"], r["tier"], r["min_odds"]),
                         ("2.20", "2.10", "-0.0014", "WATCH", "2.17"))
        r = pcbf.recheck(sel, 2.25, at("2026-10-03T14:20:00Z"))
        self.assertEqual((r["edge_pct"], r["tier"]), ("0.0699", "PICK"))
        self.assertEqual(schemas.validate("recheck", r), [])

    def test_snapshot_labels(self):
        k = at("2026-10-05T18:00:00Z")
        self.assertEqual(pcbf.snapshot_label(at("2026-10-05T17:57:00Z"), k), ("closing price", "3"))
        self.assertEqual(pcbf.snapshot_label(at("2026-10-05T16:00:00Z"), k),
                         ("pre-kickoff snapshot, 120 min before kickoff", "120"))
        self.assertEqual(pcbf.snapshot_label(at("2026-10-05T18:01:00Z"), k), (None, "quote taken after kickoff"))


def priced(event, probs, winner_idx, source="pinnacle", b9="1.60/2.30"):
    sels, sts = [], []
    for i, p in enumerate(probs):
        rid = f"{event}-{i}"
        sels.append({"selection_record_id": rid, "tier": "WATCH" if i else "PICK", "selection_id": f"bet9ja:{event}:MATCH_WINNER:{i}",
                     "priced_at_utc": "2026-10-05T10:00Z", "benchmark_source": source, "market": "MATCH_WINNER",
                     "selection_index": str(i), "fair_prob": f"{p:.4f}", "fair_odds": f"{1 / p:.3f}", "market_odds": b9,
                     "sport": "tennis"})
        sts.append({"selection_record_id": rid, "result": "WIN" if i == winner_idx else "LOSE"})
    return sels, sts


class ForecastScoring(unittest.TestCase):
    def test_brier_and_log_loss(self):
        s1, t1 = priced("1", [0.6, 0.4], 0)
        s2, t2 = priced("2", [0.5, 0.5], 1)
        m = pcbf.probability_metrics(s1 + s2, t1 + t2)
        b = m["benchmark"]
        self.assertEqual(b["events"], 2)
        self.assertAlmostEqual(b["brier"], (0.32 + 0.5) / 2)
        self.assertAlmostEqual(b["log_loss"], (0.510826 + 0.693147) / 2, places=5)
        self.assertEqual(m["bet9ja_same_events"]["events"], 2)
        self.assertEqual([c["n"] for c in m["calibration"]], [1, 2, 1])   # 0.4, 0.5 x2, 0.6

    def test_void_and_incomplete_events_left_out(self):
        s1, t1 = priced("1", [0.6, 0.4], 0)
        t1[0]["result"] = "VOID"
        s2, t2 = priced("2", [0.5, 0.5], 1)
        m = pcbf.probability_metrics(s1 + s2, t1 + t2[:1])
        self.assertEqual(m["benchmark"]["events"], 0)


class Matching(unittest.TestCase):
    def test_side_match(self):
        self.assertEqual(odds_sources.side_match("Olmos, Zoe", "Zoe Olmos"), "exact")
        self.assertEqual(odds_sources.side_match("Williams, Venus", "Serena Williams"), "partial")
        self.assertEqual(odds_sources.side_match("Leeds United", "Leeds"), "exact")
        self.assertEqual(odds_sources.side_match("Manchester City", "Manchester United"), "partial")
        self.assertIsNone(odds_sources.side_match("Real Madrid", "Real Sociedad"))   # 'real' alone proves nothing

    def soccer_quote(self, home, away, url=None):
        run = poly_soccer(home, away, [0.5, 0.25, 0.27], [9e4, 9e4, 9e4], "2026-10-05 18:00:00+00", "2026-10-05T13:56:00Z")
        if url:
            run["captures"][0]["source_url"] = url
        return odds_sources.quotes_from_walk(run)[0]

    def cand(self, home, away):
        return pcbf.candidates_from_capture(walker("soccer", [("1x2", [1.9, 3.6, 3.75])], home=home, away=away), "h")[0]

    def test_partial_names_wait_for_review_then_follow_the_decision(self):
        q = self.soccer_quote("Manchester United", "Leeds United")
        c = self.cand("Manchester City", "Leeds United")
        r = odds_sources.choose_quote(c, [q])
        self.assertEqual(r["status"], "review")
        self.assertIn("names only partly agree", r["review"][0]["why"])
        key = (c["event_id"], odds_sources.quote_key(q))
        self.assertEqual(odds_sources.choose_quote(c, [q], {key: "accept"})["status"], "matched")
        self.assertEqual(odds_sources.choose_quote(c, [q], {key: "reject"})["status"], "none")

    def test_two_events_matching_one_fixture_is_ambiguous(self):
        a = self.soccer_quote("Italy", "Turkiye")
        b = self.soccer_quote("Italy", "Turkiye", url="https://polymarket.com/sports/fifa-friendlies/other-2026-10-05")
        r = odds_sources.choose_quote(self.cand("Italy", "Turkiye"), [a, b])
        self.assertEqual((r["status"], r["review"][0]["why"]), ("review", "several different events match this fixture"))

    def test_higher_source_in_review_holds_the_fixture(self):
        poly = self.soccer_quote("Italy", "Turkiye")
        op = dict(poly, source="oddsportal", url="https://www.oddsportal.com/football/h2h/italy/turkiye/", start_utc="",
                  bookmaker_count=17)
        r = odds_sources.choose_quote(self.cand("Italy", "Turkiye"), [poly, op])
        self.assertEqual([m["quote"]["source"] for m in r["review"]], ["oddsportal"])
        key = (self.cand("Italy", "Turkiye")["event_id"], odds_sources.quote_key(op))
        self.assertEqual(odds_sources.choose_quote(self.cand("Italy", "Turkiye"), [poly, op], {key: "reject"})
                         ["match"]["quote"]["source"], "polymarket")

    def test_same_event_captured_twice_uses_newest(self):
        a = self.soccer_quote("Italy", "Turkiye")
        b = dict(a, captured_at_utc="2026-10-05T15:00Z")
        self.assertEqual(odds_sources.choose_quote(self.cand("Italy", "Turkiye"), [a, b])["match"]["quote"]["captured_at_utc"],
                         "2026-10-05T15:00Z")


def soccer_day(folder: Path, b9_captured="2026-10-05T13:30:00.000Z", home_odds=1.60):
    """One Bet9ja soccer fixture and one Polymarket walk of it, in captures/."""
    w = walker("soccer", [("1x2", [home_odds, 4.9, 6.8])], home="Italy", away="Turkiye", fid="77")
    w["results"][0]["captured_at_utc"] = b9_captured
    (folder / "captures" / "bet9ja-allsports-walk-soccer-2026-10-05.json").write_text(json.dumps(w))
    run = poly_soccer("Italy", "Türkiye", [0.705, 0.185, 0.115], [187595, 52110, 17948], "2026-10-05 18:00:00+00",
                      "2026-10-05T13:56:00Z")
    run["schema_version"] = "public-odds-capture-walk.v1"
    (folder / "captures" / "public-odds-walk-polymarket-2026-10-05T13-56.json").write_text(json.dumps(run))


class AutoBenchmark(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.df = DataFolder(self.tmp, host="HP")
        self.now = at("2026-10-05T14:00:00Z")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_price_coverage_idempotent_snapshot(self):
        soccer_day(self.tmp)
        status = workflow.day_status(self.df, self.now)
        self.assertEqual([r["state"] for r in status["rows"]], ["quote waiting"])
        r = workflow.auto_benchmark(self.df, self.now)
        self.assertEqual([x["tier"] for x in r["records"]], ["WATCH", "WATCH", "WATCH"])   # Polymarket soccer rule unconfirmed
        self.assertEqual(r["records"][0]["benchmark_file"], "public-odds-walk-polymarket-2026-10-05T13-56.json")
        self.assertEqual(workflow.unconfirmed_rules(self.df), {"polymarket|soccer|1X2_REGULATION": 3})
        cov = workflow.coverage(self.df, workflow.day_status(self.df, self.now), self.now)
        self.assertEqual({k: cov[0][k] for k in ("sport", "captured", "usable", "benchmarked", "unresolved", "shortlist")},
                         {"sport": "soccer", "captured": 1, "usable": 1, "benchmarked": 1, "unresolved": 0, "shortlist": 0})
        self.assertEqual(cov[0]["screened_out"], 0)
        # running Update again writes nothing
        again = workflow.auto_benchmark(self.df, self.now)
        self.assertEqual((again["records"], len(self.df.read("selection"))), ([], 3))
        # the walker captured the page again at 17:58; after kickoff it becomes the closing price
        run = poly_soccer("Italy", "Türkiye", [0.69, 0.19, 0.12], [9e5, 9e5, 9e5], "2026-10-05 18:00:00+00",
                          "2026-10-05T17:58:00Z")
        (self.tmp / "captures" / "public-odds-walk-polymarket-2026-10-05T17-58.json").write_text(json.dumps(run))
        later = workflow.auto_benchmark(self.df, at("2026-10-05T18:30:00Z"))
        self.assertEqual(len(later["closes"]), 3)
        self.assertEqual({c["snapshot_label"] for c in later["closes"]}, {"closing price"})
        self.assertEqual(len(workflow.auto_benchmark(self.df, at("2026-10-05T18:31:00Z"))["closes"]), 0)

    def test_old_captures_left_out_of_the_day(self):
        soccer_day(self.tmp)
        old = self.tmp / "captures" / "bet9ja-allsports-walk-soccer-2026-09-28T08-00-00-000Z.json"
        old.write_text((self.tmp / "captures" / "bet9ja-allsports-walk-soccer-2026-10-05.json").read_text()
                       .replace('"77"', '"78"').replace("2026-10-05T13:30", "2026-09-28T08:00"))
        self.assertEqual(len(workflow.load_candidates(self.df, self.now, since=at("2026-09-01T00:00:00Z"))["accepted"]), 1)
        self.assertEqual([c["event_id"] for c in workflow.load_candidates(self.df, self.now)["accepted"]], ["bet9ja:77"])
        self.assertEqual(len(self.df.capture_files(since=self.now)), 1)

    def test_confirmed_rule_gives_pm_paper(self):
        soccer_day(self.tmp)
        workflow.record_rule(self.df, "polymarket|soccer|1X2_REGULATION", "same as bet9ja", self.now,
                             "both settle on 90 minutes plus stoppage")
        r = workflow.auto_benchmark(self.df, self.now)
        home = r["records"][0]
        self.assertEqual((home["tier"], home["settlement_check"], home["price_basis"]),
                         ("PM_PAPER", "ok", "midpoint (bid/ask)"))
        self.assertEqual(workflow.coverage(self.df, workflow.day_status(self.df, self.now), self.now)[0]["shortlist"], 1)

    def test_evidence_review_can_allow_polymarket_picks(self):
        soccer_day(self.tmp)
        workflow.record_rule(self.df, "polymarket|soccer|1X2_REGULATION", "same as bet9ja", self.now)
        with self.assertRaises(InvalidRecord):
            workflow.record_evidence_review(self.df, "continue paper", "too short", self.now, True)
        workflow.record_evidence_review(self.df, "continue paper", "Polymarket soccer prices tracked Pinnacle closely "
                                        "over the review period; allowing them as PICKs.", self.now, True)
        self.assertEqual(workflow.auto_benchmark(self.df, self.now)["records"][0]["tier"], "PICK")

    def test_stale_polymarket_quote_cannot_be_pm_paper(self):
        soccer_day(self.tmp, b9_captured="2026-10-05T10:30:00.000Z")
        workflow.record_rule(self.df, "polymarket|soccer|1X2_REGULATION", "same as bet9ja", self.now)
        home = workflow.auto_benchmark(self.df, self.now)["records"][0]
        self.assertEqual(home["tier"], "WATCH")
        self.assertIn("more than 2h apart", home["caution_flags"])


class Storage(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def rule(self, key="pinnacle|tennis|MATCH_WINNER", t="2026-10-05T10:00Z"):
        return {"rule_key": key, "status": "same as bet9ja", "confirmed_at_utc": t}

    def test_old_file_gains_new_columns_values_kept(self):
        df = DataFolder(self.tmp, host="HP")
        old_cols = schemas.fields("closing")[:7]
        p = df.path("closing")
        with p.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(old_cols)
            w.writerow(["sel_old", "2026-10-03T16:25Z", "pinnacle", "2.1/4.2/2.65", "2.292", "-0.0401", "https://x"])
        df.append("closing", [{"selection_record_id": "sel_new", "captured_at_utc": "2026-10-05T17:58Z", "source": "polymarket",
                               "closing_odds": "1.4/5.2/8.3", "closing_fair_odds": "1.45", "clv_pct": "0.01",
                               "snapshot_label": "closing price"}])
        rows = df.read("closing")
        self.assertEqual([(r["selection_record_id"], r["clv_pct"], r["snapshot_label"]) for r in rows],
                         [("sel_old", "-0.0401", ""), ("sel_new", "0.01", "closing price")])
        self.assertEqual(len(list((p.parent / ".backup").iterdir())), 1)

    def test_newer_data_blocks_writes(self):
        df = DataFolder(self.tmp, host="HP")
        df.append("rule", [self.rule()])
        self.assertEqual(json.loads((self.tmp / "predictbot-data.json").read_text())["data_schema_version"],
                         schemas.DATA_SCHEMA_VERSION)
        (self.tmp / "predictbot-data.json").write_text(json.dumps({"data_schema_version": schemas.DATA_SCHEMA_VERSION + 1}))
        self.assertIn("written by a newer PredictBot", df.write_block())
        with self.assertRaises(InvalidRecord):
            df.append("rule", [self.rule(t="2026-10-05T11:00Z")])
        self.assertEqual(len(df.read("rule")), 1)

    def test_unknown_column_blocks_writes(self):
        df = DataFolder(self.tmp, host="HP")
        df.path("rule").write_text(",".join(schemas.fields("rule") + ["from_the_future"]) + "\n")
        with self.assertRaises(InvalidRecord) as ctx:
            df.append("rule", [self.rule()])
        self.assertIn("from_the_future", str(ctx.exception))

    def test_conflict_copy_blocks_then_merges_without_duplicates(self):
        df = DataFolder(self.tmp, host="HP")
        df.append("rule", [self.rule("a|x|M"), self.rule("b|x|M")])
        mine = df.path("rule")
        theirs = mine.with_name("settlement-rules-HUAWAI.csv")
        shutil.copy(mine, theirs)
        with theirs.open("a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["c|x|M", "differs", "2026-10-05T12:00Z", ""])
        self.assertIn("sync-conflict", df.write_block())
        with self.assertRaises(InvalidRecord):
            df.append("rule", [self.rule("d|x|M")])
        self.assertEqual(df.merge_conflict_copies(), [("settlement-rules-HUAWAI.csv", 1)])
        self.assertEqual([r["rule_key"] for r in df.read("rule")], ["a|x|M", "b|x|M", "c|x|M"])
        self.assertIsNone(df.write_block())
        self.assertFalse(theirs.exists())


def sync(src: Path, dst: Path):
    """What OneDrive does once both laptops are up to date."""
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


class MergeById(unittest.TestCase):
    def test_same_record_written_on_both_laptops_kept_once(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            df = DataFolder(tmp, host="HP")
            rec = {"selection_record_id": "sel_1", "settled_at_utc": "2026-10-05T22:00Z", "result": "WIN",
                   "return_amount": "55.00", "settlement_source": "chat"}
            df.append("settlement", [rec])
            theirs = df.path("settlement").with_name("settlements (1).csv")
            shutil.copy(df.path("settlement"), theirs)
            text = theirs.read_text().replace("2026-10-05T22:00Z", "2026-10-05T22:05Z")
            theirs.write_text(text + "sel_2,2026-10-05T22:05Z,LOSE,0.00,chat,\n")
            self.assertEqual(df.merge_conflict_copies(), [("settlements (1).csv", 1)])
            self.assertEqual([r["selection_record_id"] for r in df.read("settlement")], ["sel_1", "sel_2"])
        finally:
            shutil.rmtree(tmp)


class ReviewRegressions(unittest.TestCase):
    """Bugs found in review of the first Step 1 commit."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.df = DataFolder(self.tmp, host="HP")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_merge_keeps_correction_rows(self):
        base = {"selection_record_id": "sel_1", "settled_at_utc": "2026-10-05T22:00Z", "return_amount": "0.00",
                "settlement_source": "chat"}
        self.df.append("settlement", [dict(base, result="WIN"), dict(base, result="LOSE", note="correction")])
        copy = self.df.path("settlement").with_name("settlements-HUAWAI.csv")
        with copy.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=schemas.fields("settlement"))
            w.writeheader()
            w.writerow({**base, "result": "WIN", "settled_at_utc": "2026-10-05T22:07Z"})
        self.assertEqual(self.df.merge_conflict_copies(), [("settlements-HUAWAI.csv", 0)])
        self.assertEqual([r["result"] for r in self.df.read("settlement")], ["WIN", "LOSE"])

    def test_merge_refused_until_handover(self):
        (self.tmp / "predictbot-session.json").write_text(json.dumps({"host": "HUAWAI", "released": False}))
        (self.tmp / "rules" / "settlement-rules (1).csv").write_text("rule_key,status,confirmed_at_utc,note\n")
        with self.assertRaises(InvalidRecord):
            self.df.merge_conflict_copies()

    def test_merge_refuses_unknown_columns(self):
        self.df.append("rule", [{"rule_key": "a|x|M", "status": "differs", "confirmed_at_utc": "2026-10-05T10:00Z"}])
        (self.tmp / "rules" / "settlement-rules-HUAWAI.csv").write_text(
            "rule_key,status,confirmed_at_utc,note,future\nb|x|M,differs,2026-10-05T11:00Z,,1\n")
        with self.assertRaises(InvalidRecord):
            self.df.merge_conflict_copies()

    def test_snapshot_uses_the_benchmark_source(self):
        from test_predictbot import OddsPortal
        rows = [("Pinnacle", 1.50, 4.60, 6.90, 97.5)] + [("b%d" % i, 1.47, 4.5, 6.2, 94.0) for i in range(5)]
        page = OddsPortal.page(None, rows)
        (self.tmp / "captures" / "public-odds-walk-oddsportal-2026-10-05T16-47.json").write_text(json.dumps(page))
        w = walker("soccer", [("1x2", [1.6, 4.4, 6.5])], home="France", away="Belgium", fid="9")
        w["results"][0]["fixtures"][0]["kickoff_utc_derived"] = "2026-10-05T18:45:00.000Z"
        w["results"][0]["captured_at_utc"] = "2026-10-05T16:30:00.000Z"
        (self.tmp / "captures" / "bet9ja-allsports-walk-soccer-2026-10-05.json").write_text(json.dumps(w))
        first = workflow.auto_benchmark(self.df, at("2026-10-05T16:50:00Z"))["records"]
        self.assertEqual({r["benchmark_source"] for r in first}, {"pinnacle"})
        page["captures"][0]["captured_at_utc"] = "2026-10-05T18:42:00Z"
        page["captures"][0]["html"] = page["captures"][0]["html"].replace("<p>1.5</p>", "<p>1.55</p>", 1)
        (self.tmp / "captures" / "public-odds-walk-oddsportal-2026-10-05T18-42.json").write_text(json.dumps(page))
        closes = workflow.auto_benchmark(self.df, at("2026-10-05T19:00:00Z"))["closes"]
        self.assertEqual({(c["source"], c["snapshot_label"]) for c in closes}, {("pinnacle", "closing price")})

    def test_zero_price_snapshot_does_not_crash(self):
        soccer_day(self.tmp)
        workflow.auto_benchmark(self.df, at("2026-10-05T14:00:00Z"))
        run = poly_soccer("Italy", "Türkiye", [0.0, 0.19, 0.12], [9e5, 9e5, 9e5], "2026-10-05 18:00:00+00",
                          "2026-10-05T17:58:00Z")
        (self.tmp / "captures" / "public-odds-walk-polymarket-2026-10-05T17-58.json").write_text(json.dumps(run))
        self.assertEqual(workflow.auto_benchmark(self.df, at("2026-10-05T18:30:00Z"))["closes"], [])

    def test_unknown_month_leaves_kickoff_blank(self):
        from test_predictbot import OddsPortal
        page = OddsPortal.page(None, [("b%d" % i, 1.47, 4.5, 6.2, 94.0) for i in range(5)])
        page["captures"][0]["html"] = page["captures"][0]["html"].replace("05 Oct 2026", "05 Okt 2026")
        self.assertEqual(odds_sources.quotes_from_walk(page)[0]["start_utc"], "")

    def test_research_fixture_shows_as_unresolved(self):
        soccer_day(self.tmp)
        run = json.loads((self.tmp / "captures" / "public-odds-walk-polymarket-2026-10-05T13-56.json").read_text())
        run["captures"][0]["html"] = run["captures"][0]["html"].replace('\\"volume\\":\\"52110\\"', '\\"volume\\":\\"900\\"')
        (self.tmp / "captures" / "public-odds-walk-polymarket-2026-10-05T13-56.json").write_text(json.dumps(run))
        recs = workflow.auto_benchmark(self.df, at("2026-10-05T14:00:00Z"))["records"]
        self.assertEqual({r["tier"] for r in recs}, {"RESEARCH"})
        row = workflow.day_status(self.df, at("2026-10-05T14:00:00Z"))["rows"][0]
        self.assertEqual(row["state"], "unresolved")
        self.assertIn("thin market", row["reason"])

    def test_review_decision_survives_recapture(self):
        q = Matching.soccer_quote(None, "Manchester United", "Leeds United")
        c = Matching.cand(None, "Manchester City", "Leeds United")
        decisions = {(c["event_id"], odds_sources.event_key(q)): "reject"}
        again = dict(q, captured_at_utc="2026-10-05T17:00Z")
        self.assertEqual(odds_sources.choose_quote(c, [q, again], decisions)["status"], "none")


class TwoLaptops(unittest.TestCase):
    """Acceptance: start a session on one laptop, sync, continue on the other,
    and every record is still there exactly once."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.hp, self.huawai = self.base / "hp", self.base / "huawai"
        self.hp.mkdir()

    def tearDown(self):
        shutil.rmtree(self.base)

    def reply_for(self, pack, home, line):
        code = next(k for k, e in pack["entries"].items() if e["captures"][0]["event_name"].startswith(home))
        return f"{pack['pack_id']}\n{code} | {line}"

    def test_session_continues_on_the_other_laptop(self):
        now = at("2026-10-03T14:10:00Z")
        a = DataFolder(self.hp, host="HP")
        self.assertIsNone(a.claim_session(now))
        shutil.copy(FUTSAL, self.hp / "captures" / FUTSAL.name)
        pack = workflow.create_pack(a, now)
        reply = self.reply_for(pack, "Inter FS", "pinnacle | https://www.pinnacle.com/x | 2026-10-03T14:05Z | 1.95 / 4.30 / 3.00 | -")
        self.assertEqual(len(workflow.apply_reply(a, reply, now)["records"]), 3)
        a.release_session(now)

        sync(self.hp, self.huawai)
        b = DataFolder(self.huawai, host="HUAWAI")
        self.assertIsNone(b.claim_session(at("2026-10-03T15:00:00Z")))
        self.assertIsNone(b.write_block())
        # the same capture (now in OneDrive) and the same reply pasted again: nothing new
        self.assertEqual(b.import_captures(self.hp / "captures"), 0)
        self.assertEqual(workflow.apply_reply(b, reply, now)["records"], [])
        self.assertEqual(workflow.auto_benchmark(b, now)["records"], [])
        later = at("2026-10-03T23:00:00Z")
        sp = workflow.create_settle_pack(b, later)
        code = next(k for k in sp["entries"])
        self.assertEqual(workflow.apply_settlement(b, f"{sp['pack_id']}\n{code} | 1 | NONE", later)["settled"], 3)
        b.release_session(later)

        sync(self.huawai, self.hp)
        a2 = DataFolder(self.hp, host="HP")
        self.assertIsNone(a2.claim_session(later))
        for kind, key in (("selection", "selection_record_id"), ("capture", "capture_id"),
                          ("settlement", "selection_record_id")):
            ids = [r[key] for r in a2.read(kind)]
            self.assertEqual(len(ids), len(set(ids)), kind)
        self.assertEqual((len(a2.read("selection")), len(a2.read("capture")), len(a2.read("settlement"))), (3, 21, 3))
        self.assertEqual(len(list((self.hp / "packs").glob("*.json"))), 2)

    def test_other_laptop_still_open_needs_handover(self):
        now = at("2026-10-05T10:00:00Z")
        a = DataFolder(self.hp, host="HP")
        a.claim_session(now)
        sync(self.hp, self.huawai)                     # HP is still running
        b = DataFolder(self.huawai, host="HUAWAI")
        other = b.claim_session(now)
        self.assertEqual(other["host"], "HP")
        self.assertIn("did not close it here", b.write_block())
        with self.assertRaises(InvalidRecord):
            workflow.record_rule(b, "pinnacle|tennis|MATCH_WINNER", "same as bet9ja", now)
        b.confirm_handover(now)                        # operator closed HP and saw OneDrive up to date
        self.assertIsNone(b.write_block())
        workflow.record_rule(b, "pinnacle|tennis|MATCH_WINNER", "same as bet9ja", now)
        # if HP was in fact still running, its next heartbeat sees HUAWAI's marker and stops writing
        shutil.copy(self.huawai / "predictbot-session.json", self.hp / "predictbot-session.json")
        self.assertFalse(a.heartbeat(now))
        self.assertIn("HUAWAI", a.write_block())

    def test_simultaneous_start_shows_as_conflict(self):
        """Both laptops started while offline: OneDrive keeps both markers."""
        a = DataFolder(self.hp, host="HP")
        a.claim_session(at("2026-10-05T10:00:00Z"))
        (self.hp / "predictbot-session-HUAWAI.json").write_text(json.dumps({"host": "HUAWAI", "released": False}))
        self.assertIn("sync-conflict", a.write_block())


class SingleInstance(unittest.TestCase):
    def test_second_copy_cannot_take_the_port(self):
        import app
        first = app.open_server(0)
        try:
            self.assertIsNotNone(first)
            self.assertIsNone(app.open_server(first.server_address[1]))
        finally:
            first.server_close()


if __name__ == "__main__":
    unittest.main()
