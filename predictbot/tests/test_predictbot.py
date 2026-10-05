import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pcbf          # noqa: E402
import schemas       # noqa: E402
import workflow      # noqa: E402
from storage import DataFolder, InvalidRecord  # noqa: E402

FIX = HERE / "fixtures"
FUTSAL = FIX / "bet9ja-allsports-walk-futsal-2026-10-03T13-58-01-082Z.json"
CRICKET = FIX / "bet9ja-allsports-walk-cricket-2026-10-03T13-53-02-264Z.json"
SOCCER = FIX / "bet9ja-soccer-all-soccer-2026-10-03T04-45-07Z.trimmed.json"
LEDGER4 = FIX / "pcbf-ledger-4rows.csv"


def at(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class RulebookMath(unittest.TestCase):
    def test_rulebook_example(self):
        self.assertEqual(pcbf.edge(3.40, [2.30, 3.40, 3.10], 2), (3.275, 0.0381))

    def test_two_way(self):
        self.assertEqual(pcbf.edge(3.25, [2.77, 1.42], 0), (3.1, 0.0483))

    def test_devig_sums_to_one(self):
        for odds in ([2.30, 3.40, 3.10], [1.9, 1.9], [1.17, 7.3, 12.25]):
            self.assertAlmostEqual(sum(pcbf.devig_prop(odds)), 1.0, places=9)
            self.assertAlmostEqual(sum(pcbf.devig_power(odds)), 1.0, places=6)

    def test_power_devig_does_not_normalise_below_100(self):
        # documented weakness of the rulebook block, flagged as a caution
        self.assertLess(sum(pcbf.devig_power([2.2, 3.9, 4.1])), 1.0)

    def test_clv(self):
        self.assertEqual(pcbf.clv(2.2, 2.292), -0.0401)
        self.assertEqual(pcbf.clv(3.0, 2.5), 0.2)

    def test_tier(self):
        self.assertEqual(pcbf.tier_for(0.03, []), "PICK")
        self.assertEqual(pcbf.tier_for(0.0299, []), "WATCH")
        self.assertEqual(pcbf.tier_for(-0.2, []), "WATCH")
        self.assertEqual(pcbf.tier_for(0.10, ["STALE: benchmark not from today"]), "WATCH")

    def test_prices(self):
        self.assertEqual(pcbf.parse_price("13/15"), 1 + 13 / 15)
        self.assertIsNone(pcbf.parse_price("1.00"))
        self.assertIsNone(pcbf.parse_price("abc"))
        self.assertEqual(pcbf.parse_prices("2.30 / 3.40 / 3.10", 3), [2.3, 3.4, 3.1])
        self.assertEqual(pcbf.parse_prices("2.30, 3.40, 3.10", 3), [2.3, 3.4, 3.1])
        self.assertEqual(pcbf.parse_prices("13/15 / 11/10", 2), [1 + 13 / 15, 2.1])
        self.assertEqual(pcbf.parse_prices("13/15/11/10", 2), [1 + 13 / 15, 2.1])
        self.assertIsNone(pcbf.parse_prices("2.30 / 3.40", 3))


class FourRowLedger(unittest.TestCase):
    """The PCBF Mini v1.3 ledger run through the new core, exact values."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.df = DataFolder(self.tmp)
        self.now = at("2026-10-05T09:00:00Z")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_exact_outputs(self):
        out = workflow.import_pcbf_ledger(self.df, LEDGER4, self.now)["imported"]
        got = {r["origin"].split()[-1]: r for r in out}
        self.assertEqual(len(got), 4)
        # 001 / 002: benchmark market never recorded -> cannot be verified
        for rid in ("SIM-20261003-001", "SIM-20261003-002"):
            self.assertEqual(got[rid]["tier"], "REJECTED")
            self.assertEqual(got[rid]["rejection_reason"], "benchmark market not recorded: edge cannot be recomputed")
        p3, p4 = got["SIM-20261003-003"], got["SIM-20261003-004"]
        self.assertEqual((p3["tier"], p3["fair_odds"], p3["edge_pct"], p3["market"], p3["selection_index"],
                          p3["benchmark_odds"], p3["benchmark_source"]),
                         ("PICK", "3.100", "0.0483", "MONEYLINE_INC_OT", "0", "2.770/1.420", "oddsportal"))
        self.assertEqual((p4["tier"], p4["fair_odds"], p4["edge_pct"], p4["market"], p4["selection_index"],
                          p4["benchmark_odds"]),
                         ("PICK", "4.367", "0.0305", "1X2_REGULATION", "2", "1.690/4.700/4.050"))
        # written to disk, and a second import adds nothing
        self.assertEqual(len(self.df.read("selection")), 4)
        self.assertEqual(workflow.import_pcbf_ledger(self.df, LEDGER4, self.now)["imported"], [])
        self.assertEqual(len(self.df.read("selection")), 4)


class WalkerCapture(unittest.TestCase):
    def test_model_ready(self):
        raw = FUTSAL.read_bytes()
        cands = pcbf.candidates_from_capture(json.loads(raw), pcbf.payload_hash(raw), FUTSAL.name)
        self.assertEqual(len(cands), 8)
        c = next(x for x in cands if x["home"] == "Inter FS")
        self.assertEqual(c["event_id"], "bet9ja:831277889")
        self.assertEqual(c["market"], "1X2_REGULATION")
        self.assertEqual(c["odds"], [2.2, 4.55, 2.35])
        self.assertEqual(c["kickoff_utc"], "2026-10-03T16:30Z")
        self.assertEqual(c["captured_at_utc"], "2026-10-03T13:57Z")
        self.assertEqual(c["country"], "spain")
        self.assertTrue(c["source_url"].startswith("https://sports.bet9ja.com/competition/futsal/spain/"))
        self.assertEqual(c["raw_payload_hash"], pcbf.payload_hash(raw))
        recs = pcbf.capture_records(c)
        self.assertEqual([r["selection"] for r in recs], ["Inter FS (home)", "Draw", "Murcia FS (away)"])
        self.assertEqual(len({r["capture_id"] for r in recs}), 3)
        self.assertEqual(recs, pcbf.capture_records(c))      # stable IDs
        for r in recs:
            self.assertEqual(schemas.validate("capture", r), [])

    def test_two_way_semantics(self):
        raw = CRICKET.read_bytes()
        cands = pcbf.candidates_from_capture(json.loads(raw), pcbf.payload_hash(raw))
        self.assertEqual({c["market"] for c in cands}, {"MATCH_WINNER"})
        self.assertEqual(cands[0]["outcomes"], ["United Arab Emirates", "Namibia"])
        # 'UAE competitions' are excluded; a UAE national team in an ICC event is not
        self.assertIsNone(pcbf.screen(cands[0], at("2026-10-03T13:00:00Z")))

    def test_failure_records(self):
        data = json.loads(FUTSAL.read_text())
        fx = data["results"][0]["fixtures"][0]
        fx["markets"][0]["selections"][0]["state"] = "locked"
        data["results"][1]["fixtures"][0]["markets"] = [{"market_key": "over_under", "selections": []}]
        cands = pcbf.candidates_from_capture(data, "h")
        reasons = [pcbf.screen(c, at("2026-10-03T10:00:00Z")) for c in cands]
        self.assertIn("locked price", reasons)
        self.assertTrue(any(r and r.startswith("unsupported or missing main market") for r in reasons))

    def test_soccer_session_and_screening(self):
        raw = SOCCER.read_bytes()
        cands = pcbf.candidates_from_capture(json.loads(raw), pcbf.payload_hash(raw))
        alb = next(c for c in cands if c["home"] == "Albacete")
        self.assertEqual(alb["kickoff_utc"], "2026-10-03T12:00Z")          # 13:00 WAT
        self.assertEqual(alb["odds"], [2.83, 3.4, 2.42])
        now = at("2026-10-03T05:00:00Z")
        srl = next(c for c in cands if "SRL" in c["away"])
        self.assertEqual(pcbf.screen(srl, now), "virtual / simulated event")
        self.assertIsNone(pcbf.screen(alb, now))
        self.assertEqual(pcbf.screen(alb, at("2026-10-03T12:00:00Z")), "already started")

    def test_merge_keeps_newest_and_unions(self):
        a = {"event_id": "e1", "captured_at_utc": "2026-10-03T04:44Z", "v": "old"}
        b = {"event_id": "e1", "captured_at_utc": "2026-10-03T04:52Z", "v": "new"}
        c = {"event_id": "e2", "captured_at_utc": "2026-10-03T04:44Z", "v": "only-old"}
        merged, dups = pcbf.merge_candidates([[a, c], [b]])
        self.assertEqual((merged["e1"]["v"], merged["e2"]["v"], dups), ("new", "only-old", 1))

    def test_screen_rules(self):
        base = {"problem": None, "competition": "Liga", "country": "spain", "home": "A", "away": "B",
                "kickoff_utc": "2026-10-04T10:00Z"}
        now = at("2026-10-03T10:00:00Z")
        self.assertIsNone(pcbf.screen(base, now))
        self.assertEqual(pcbf.screen({**base, "country": "turkey"}, now), "excluded country or competition")
        self.assertEqual(pcbf.screen({**base, "home": "Russia"}, now), "excluded country or competition")
        self.assertEqual(pcbf.screen({**base, "competition": "Premier League U21"}, now), "youth / reserve / amateur")
        self.assertEqual(pcbf.screen({**base, "home": "Real Madrid B"}, now), "youth / reserve / amateur")
        self.assertEqual(pcbf.screen({**base, "competition": "Zoom Soccer"}, now), "virtual / simulated event")
        self.assertEqual(pcbf.screen({**base, "kickoff_utc": ""}, now), "kickoff unknown")
        self.assertEqual(pcbf.screen({**base, "home": "Odds BK 2", "away": "Viking FK 2"}, now), "youth / reserve / amateur")
        self.assertEqual(pcbf.screen({**base, "kickoff_utc": "2026-10-03T10:45Z"}, now),
                         "kicks off within 60 min (too soon to research)")


def walker(sport, markets, home="A", away="B", fid="1", comp="League", url="https://sports.bet9ja.com/competition/x/germany/l/1"):
    return {"schema_version": "bet9ja-allsports-sport-walk.v2.1", "sport": sport, "captured_at_utc": "2026-10-05T08:50:00.000Z",
            "results": [{"competition_label": comp, "observed_source_url_raw": url, "captured_at_utc": "2026-10-05T08:50:00.000Z",
                         "fixtures": [{"fixture_id": fid, "participant_1": home, "participant_2": away,
                                       "kickoff_utc_derived": "2026-10-05T18:00:00.000Z",
                                       "markets": [{"market_key": k, "selections": [{"odds": o, "state": "open"} for o in odds]}
                                                   for k, odds in markets]}]}]}


class RealMarketKeys(unittest.TestCase):
    """Market keys seen in the 5 Oct 2026 captures."""
    now = at("2026-10-05T09:00:00Z")

    def one(self, data):
        c = pcbf.candidates_from_capture(data, "h")[0]
        return c, pcbf.screen(c, self.now)

    def test_hockey_match_winner_is_regulation_1x2(self):
        c, r = self.one(walker("ice_hockey", [("draw_no_bet", [1.5, 2.5]), ("match_winner", [2.1, 4.0, 2.9]),
                                              ("handicap_rt", [1.9, 1.9])]))
        self.assertEqual((c["market"], c["market_key"], r), ("1X2_REGULATION", "match_winner", None))

    def test_hockey_prefers_regulation_over_moneyline(self):
        c, _ = self.one(walker("ice_hockey", [("2_way", [1.8, 2.0]), ("match_winner", [2.1, 4.0, 2.9])]))
        self.assertEqual(c["market"], "1X2_REGULATION")

    def test_handball_3way_and_table_tennis(self):
        self.assertEqual(self.one(walker("handball", [("3way", [1.5, 9.0, 3.2]), ("handicap", [1.9, 1.9])]))[0]["market"],
                         "1X2_REGULATION")
        self.assertEqual(self.one(walker("table_tennis", [("2_way", [1.6, 2.2])]))[0]["market"], "MATCH_WINNER")

    def test_specials_excluded(self):
        _, r = self.one(walker("specials_combo", [("to_happen", [1.5, 2.5])]))
        self.assertEqual(r, "specials / bet-builder / zoom: excluded by rulebook")

    def test_doubles_initial_is_not_a_b_team(self):
        _, r = self.one(walker("tennis", [("match_winner", [1.7, 2.1])], home="Rossi M / Bianchi L",
                               away="Cossu M / de la Pena B"))
        self.assertIsNone(r)

    def test_same_game_from_two_extensions_kept_once(self):
        a = pcbf.candidates_from_capture(walker("soccer", [("1x2", [1.9, 3.1, 4.55])], home="Deportivo Riestra",
                                                away="Central Cordoba SdE", fid="845"), "h1")
        session = {"schema_version": "bet9ja-soccer-session.v1", "fixtures": [{
            "fixture_id": "bxf_abc", "participants": {"home": "Deportivo Riestra", "away": "Central Cordoba SdE"},
            "offered_odds": {"H": 1.9, "D": 3.1, "A": 4.55}, "kickoff_utc": "2026-10-05T18:00:00Z",
            "captured_at_utc": "2026-10-05T08:55:35Z", "market_family": "1X2", "status": "PRE_MATCH",
            "competition": "Primera LPF", "region": "Argentina"}]}
        b = pcbf.candidates_from_capture(session, "h2")
        merged, dups = pcbf.merge_candidates([b, a])
        self.assertEqual((len(merged), dups), (1, 1))
        self.assertEqual(list(merged), ["bet9ja:bxf_abc"])   # 08:55 session capture is newer than the 08:50 walk


class EndToEnd(unittest.TestCase):
    """Walker capture -> pack -> chat reply -> validated records -> bet ->
    settlement -> CLV and performance."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.df = DataFolder(self.tmp)
        shutil.copy(FUTSAL, self.tmp / "captures" / FUTSAL.name)
        self.now = at("2026-10-03T14:10:00Z")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def code_for(self, pack, home):
        return next(k for k, e in pack["entries"].items() if e["captures"][0]["event_name"].startswith(home))

    def test_loop(self):
        pack = workflow.create_pack(self.df, self.now)
        # 8 futsal games; Manzanares (14:30) is within 60 min of 14:10, so 7 go out
        self.assertEqual(len(pack["entries"]), 7)
        self.assertIn("Inter FS v Murcia FS", pack["text"])
        self.assertEqual(len(self.df.read("capture")), 21)   # 7 games x 3 outcomes
        inter, benfica = self.code_for(pack, "Inter FS"), self.code_for(pack, "SL Benfica")
        parrulo, petrarca = self.code_for(pack, "O Parrulo"), self.code_for(pack, "Petrarca")
        reply = "\n".join([
            f"Here you go ({pack['pack_id']}):",
            f"{inter} | pinnacle | https://www.pinnacle.com/x | 2026-10-03T14:05Z | 1.95 / 4.30 / 3.00 | no news",
            f"{benfica} | NONE | no market listed",
            f"{parrulo} | oddsportal | | 2026-10-03T14:00Z | 1.80 / 4.50 / 3.10 | -",
            f"{petrarca} | oddsportal | https://www.oddsportal.com/y | 2026-10-03T14:00Z | 5.0 / 6.0 | -",
            "F99 | pinnacle | https://x | 2026-10-03T14:00Z | 2 / 3 / 4 | -",
        ])
        out = workflow.apply_reply(self.df, reply, self.now)
        recs = out["records"]
        by = {}
        for r in recs:
            by.setdefault(r["event_name"].split(" v ")[0], []).append(r)
        inter_recs = by["Inter FS"]
        self.assertEqual([(r["tier"], r["fair_odds"], r["edge_pct"]) for r in inter_recs],
                         [("PICK", "2.103", "0.0459"), ("WATCH", "4.820", "-0.0561"), ("WATCH", "3.270", "-0.2812")])
        self.assertEqual(inter_recs[0]["stake_notional"], "25")
        self.assertEqual({r["tier"] for r in by["SL Benfica"]}, {"RESEARCH"})
        self.assertEqual({r["rejection_reason"] for r in by["O Parrulo Ferrol"]}, {"missing source URL"})
        self.assertTrue(all(r["tier"] == "REJECTED" for r in by["Petrarca Calcio A Cinque"]))
        self.assertEqual(out["unknown_codes"], ["F99"])
        self.assertEqual(len(self.df.read("selection")), 12)

        # the same benchmark again is a duplicate, not a second PICK
        again = workflow.apply_reply(self.df, reply, self.now)
        dup = [r for r in again["records"] if r["event_name"].startswith("Inter FS")]
        self.assertEqual({r["tier"] for r in dup}, {"REJECTED"})
        self.assertTrue(dup[0]["rejection_reason"].startswith("duplicate"))

        # a bet must point at a logged selection
        with self.assertRaises(InvalidRecord):
            workflow.record_bet(self.df, "sel_doesnotexist", "100", "2.20", "2026-10-03T14:20Z", "B9-1")
        rejected = next(r for r in recs if r["tier"] == "REJECTED")
        with self.assertRaises(InvalidRecord):
            workflow.record_bet(self.df, rejected["selection_record_id"], "100", "2.20", "2026-10-03T14:20Z", "B9-1")
        pick = inter_recs[0]
        workflow.record_bet(self.df, pick["selection_record_id"], "100", "2.20", "2026-10-03T14:20Z", "B9-1")
        self.assertEqual(len(self.df.read("bet")), 1)

        # settlement after the game, with closing prices
        later = at("2026-10-03T23:00:00Z")
        sp = workflow.create_settle_pack(self.df, later)
        code = next(k for k, e in sp["entries"].items() if pick["selection_record_id"] in e["selection_record_ids"])
        res = workflow.apply_settlement(
            self.df, f"{sp['pack_id']}\n{code} | 1 | pinnacle | https://www.pinnacle.com/x | 2026-10-03T16:25Z | 2.10 / 4.20 / 2.65",
            later)
        self.assertEqual(res["settled"], 3)
        st = {s["selection_record_id"]: s for s in self.df.read("settlement")}
        cl = {c["selection_record_id"]: c for c in self.df.read("closing")}
        self.assertEqual((st[pick["selection_record_id"]]["result"], st[pick["selection_record_id"]]["return_amount"]),
                         ("WIN", "55.00"))
        self.assertEqual((cl[pick["selection_record_id"]]["closing_fair_odds"], cl[pick["selection_record_id"]]["clv_pct"]),
                         ("2.292", "-0.0401"))

        perf = pcbf.performance(self.df.read("selection"), self.df.read("settlement"), self.df.read("closing"),
                                self.df.read("bet"))
        p = perf["by_tier"]["PICK"]
        self.assertEqual((p["n"], p["settled"], p["with_clv"], p["win_rate"]), (1, 1, 1, 1.0))
        self.assertAlmostEqual(p["mean_clv"], -0.0401)
        self.assertAlmostEqual(perf["pick_notional_roi"], 1.2)
        self.assertAlmostEqual(perf["bets"]["stake_weighted_roi"], 1.2)
        self.assertFalse(perf["gate"]["unlocked"])
        self.assertEqual(perf["by_tier"]["WATCH"]["settled"], 2)


class Batching(unittest.TestCase):
    def test_packs_take_next_batch(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            df = DataFolder(tmp)
            shutil.copy(FUTSAL, tmp / "captures" / FUTSAL.name)
            now = at("2026-10-03T14:10:00Z")
            p1 = workflow.create_pack(df, now, limit=5)
            p2 = workflow.create_pack(df, at("2026-10-03T14:11:00Z"), limit=5)
            ev = lambda p: {e["captures"][0]["event_id"] for e in p["entries"].values()}
            self.assertEqual((len(p1["entries"]), len(p2["entries"]), p2["remaining"]), (5, 2, 0))
            self.assertFalse(ev(p1) & ev(p2))
        finally:
            shutil.rmtree(tmp)


class Schemas(unittest.TestCase):
    def test_storage_refuses_invalid(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            df = DataFolder(tmp)
            with self.assertRaises(InvalidRecord):
                df.append("selection", [{"selection_record_id": "x", "tier": "PICK"}])
            self.assertEqual(df.read("selection"), [])
        finally:
            shutil.rmtree(tmp)

    def test_pick_requires_prices(self):
        rec = {f: "" for f in schemas.fields("selection")}
        rec.update(selection_record_id="s", capture_id="c", priced_at_utc="2026-10-03T10:00Z",
                   tier="PICK", validation_status="VALID")
        self.assertIn("PICK needs benchmark_odds", schemas.validate("selection", rec))


if __name__ == "__main__":
    unittest.main()
