"""Bet9ja ticket captures: legs are linked to logged selections as bets and
settled legs settle those selections, with nothing entered by hand."""

import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tickets  # noqa: E402
import workflow  # noqa: E402
from storage import DataFolder  # noqa: E402

NOW = datetime(2026, 10, 7, 23, 0, tzinfo=timezone.utc)


def sel(event, idx, name, market="1X2_REGULATION", sport="soccer", kick="2026-10-07T19:00Z",
        priced="2026-10-07T12:00Z", odds="2.00", tier="WATCH"):
    rid = f"sel_{event}_{idx}_{priced[11:13]}"
    return {"selection_record_id": rid, "capture_id": "cap_1", "priced_at_utc": priced,
            "benchmark_source": "oddsportal", "benchmark_timestamp_utc": priced, "benchmark_odds": "2/3/4",
            "fair_odds": "2.1", "edge_pct": "-0.02", "tier": tier, "validation_status": "valid",
            "rejection_reason": "", "selection_id": f"bet9ja:{event}:{market}:{idx}",
            "event_name": "Alpha FC v Beta United" if sport == "soccer" else "Cobras v Dingos", "sport": sport,
            "kickoff_utc": kick, "market": market, "selection": name, "selection_index": str(idx),
            "bookmaker_odds": odds, "stake_notional": "25" if tier == "PICK" else ""}


def leg(selection, market="1X2", fixture="Alpha FC - Beta United07 Oct 20:00", odds="2.10", **kw):
    return {"fixture_and_time_raw": fixture, "market_raw": market, "selection": selection, "odds": odds, **kw}


def capture(tks, settled=False, at="2026-10-07T16:00:00.000Z"):
    return (Path("x.json"), {"schema_version": "bet9ja-settled-bets.v1" if settled else "bet9ja-ticket-capture.v1",
                             "captured_at_utc": at, "tickets": tks})


def ticket(tid, legs, buckets, placed="07 Oct 2026 14:00", status=None):
    t = {"bet9ja_ticket_id": tid, "placed_at_raw": placed, "legs": legs, "stake_buckets": buckets,
         "total_stake": sum(float(b["total_stake"]) for b in buckets) if buckets else "100"}
    if status:
        t["ticket_status"] = status
    return t


SINGLES_AND_DOUBLE = [{"fold_size": 1, "unit_stake": "35.00", "total_stake": "70.00"},
                      {"fold_size": 2, "unit_stake": "35.00", "total_stake": "35.00"}]
DOUBLES = [{"fold_size": 2, "unit_stake": "10.00", "total_stake": "10.00"}]


class LegParsing(unittest.TestCase):
    def test_fixture_with_page_time(self):
        placed = tickets.placed_utc("07 Oct 2026 17:03")
        self.assertEqual(placed, datetime(2026, 10, 7, 16, 3, tzinfo=timezone.utc))
        home, away, kick = tickets.split_fixture("Chicago White Sox - Cleveland Guardians07 Oct 21:00", placed)
        self.assertEqual((home, away), ("Chicago White Sox", "Cleveland Guardians"))
        self.assertEqual(kick, datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc))
        self.assertEqual(tickets.split_fixture("Rogle BK - HC Davos", placed), ("Rogle BK", "HC Davos", None))

    def test_market_labels_become_capture_keys(self):
        self.assertEqual(tickets.market_key("1-2 (Inc. Extra Inning)"), "1-2_(inc__extra_inning)")
        self.assertEqual(tickets.market_key("1X2(market result: 29:25)"), "1x2")
        self.assertEqual(tickets.market_key("Moneyline(market result: 96:76)"), "2_way")
        self.assertEqual(tickets.market_key("1 - 2"), "1_-_2")

    def test_side_index(self):
        self.assertEqual(tickets.side_index("Draw", "A", "B", 3), 1)
        self.assertEqual(tickets.side_index("2", "A", "B", 2), 1)
        self.assertEqual(tickets.side_index("Beta United", "Alpha FC", "Beta United", 3), 2)
        self.assertIsNone(tickets.side_index("Over (18.5)", "A", "B", 2))

    def test_single_stake(self):
        self.assertEqual(str(tickets.single_stake({"buckets": SINGLES_AND_DOUBLE, "legs": [1, 2]})), "35.00")
        self.assertIsNone(tickets.single_stake({"buckets": DOUBLES, "legs": [1, 2]}))
        self.assertEqual(tickets.single_stake({"buckets": [], "legs": [1], "stake": 50}), 50)


class SyncTickets(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.df = DataFolder(self.tmp, host="HP")
        self.df.append("selection", [sel("e1", 0, "Alpha FC (home)"), sel("e1", 1, "Draw"),
                                     sel("e1", 2, "Beta United (away)"),
                                     sel("e2", 0, "Cobras", "MONEYLINE_INC_OT", "basketball", odds="1.50"),
                                     sel("e2", 1, "Dingos", "MONEYLINE_INC_OT", "basketball", odds="2.60")])

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def sync(self, *caps):
        return workflow.sync_tickets(self.df, NOW, captures=list(caps))

    def test_singles_become_bets_accumulators_do_not(self):
        legs = [leg("Alpha FC"), leg("2", "2 Way", "Cobras - Dingos07 Oct 19:30", "2.55")]
        r = self.sync(capture([ticket("T1", legs, SINGLES_AND_DOUBLE), ticket("T2", legs, DOUBLES)]))
        self.assertEqual(r["bets"], 2)
        bets = {b["selection_record_id"]: b for b in self.df.read("bet")}
        self.assertEqual(set(bets), {"sel_e1_0_12", "sel_e2_1_12"})
        b = bets["sel_e2_1_12"]
        self.assertEqual((b["stake"], b["bookmaker_odds"], b["ticket_reference"], b["placed_at_utc"]),
                         ("35.00", "2.55", "T1", "2026-10-07T13:00Z"))
        self.assertEqual(r["counts"]["accumulator only"], 2)
        self.assertEqual(self.sync(capture([ticket("T1", legs, SINGLES_AND_DOUBLE)]))["bets"], 0)  # idempotent

    def test_settled_legs_settle_the_whole_game(self):
        won = leg("Cobras", "2 Way", "Cobras - Dingos", leg_status="WON")
        lost = leg("Alpha FC", "1X2(market result: 1:1)", "Alpha FC - Beta United", leg_status="LOST",
                   market_result_raw="1:1")
        r = self.sync(capture([ticket("T3", [won, lost], DOUBLES, status="LOST")], settled=True))
        self.assertEqual(r["settled"], 5)
        res = {s["selection_record_id"]: s["result"] for s in self.df.read("settlement")}
        self.assertEqual(res, {"sel_e2_0_12": "WIN", "sel_e2_1_12": "LOSE", "sel_e1_0_12": "LOSE",
                               "sel_e1_1_12": "WIN", "sel_e1_2_12": "LOSE"})
        self.assertEqual(self.sync(capture([ticket("T3", [won, lost], DOUBLES, status="LOST")], settled=True))
                         ["settled"], 0)

    def test_inconsistent_or_missing_score_is_not_settled(self):
        no_score = leg("Alpha FC", "1X2", "Alpha FC - Beta United", leg_status="LOST")
        wrong = leg("Draw", "1X2", "Alpha FC - Beta United", leg_status="LOST", market_result_raw="2:2")
        r = self.sync(capture([ticket("T4", [no_score], [], status="LOST"),
                               ticket("T5", [wrong], [], status="LOST")], settled=True))
        self.assertEqual(r["settled"], 0)
        self.assertEqual(len(r["problems"]), 2)

    def test_other_markets_unlogged_games_and_late_pricing(self):
        legs = [leg("Over (2.5)", "Total Goals"), leg("Gamma", fixture="Gamma - Delta07 Oct 20:00")]
        r = self.sync(capture([ticket("T6", legs, SINGLES_AND_DOUBLE)]))
        self.assertEqual(r["counts"], {"other market": 1, "game not logged": 1})
        early = self.sync(capture([ticket("T7", [leg("Alpha FC")], [], placed="07 Oct 2026 12:30")]))
        self.assertEqual(early["counts"], {"bet placed before the app priced it": 1})
        self.assertEqual(self.df.read("bet"), [])

    def test_three_way_ticket_market_does_not_link_to_basketball_moneyline(self):
        r = self.sync(capture([ticket("T8", [leg("Cobras", "3way", "Cobras - Dingos07 Oct 19:30")], [])]))
        self.assertEqual(r["counts"], {"other market": 1})

    def test_report_only_does_not_write(self):
        r = workflow.sync_tickets(self.df, NOW, captures=[capture([ticket("T9", [leg("Alpha FC")], [])])],
                                  write=False)
        self.assertEqual(r["bets"], 1)
        self.assertEqual(self.df.read("bet"), [])


class Breakdowns(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.df = DataFolder(self.tmp, host="HP")
        self.df.append("selection", [sel("e1", 0, "Alpha FC (home)"), sel("e1", 1, "Draw"), sel("e1", 2, "Beta United (away)"),
                                     sel("e2", 0, "Cobras", "MONEYLINE_INC_OT", "basketball", odds="1.50"),
                                     sel("e2", 1, "Dingos", "MONEYLINE_INC_OT", "basketball", odds="2.60")])

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_week_start_is_monday(self):
        self.assertEqual(tickets.week_start(datetime(2026, 10, 7, 14, 0)), "2026-10-05")
        self.assertEqual(tickets.week_start(None), "unknown")

    def test_by_sport_and_week(self):
        soccer = leg("Alpha FC", fixture="Alpha FC - Beta United", leg_status="WON", odds="2.00")
        basket = leg("Cobras", "2 Way", "Cobras - Dingos", leg_status="LOST", odds="1.50")
        other = leg("Gamma", fixture="Gamma - Delta", leg_status="WON", odds="3.00")
        one = [{"fold_size": 1, "unit_stake": "100", "total_stake": "100"}]
        tks = tickets.all_tickets([capture([
            ticket("S1", [soccer], one, status="WON"),                                  # soccer single: +100
            ticket("M1", [soccer, basket], DOUBLES, placed="30 Sep 2026 10:00", status="LOST"),
            ticket("U1", [other], one, status="WON"),
            ticket("O1", [leg("Alpha FC", fixture="Alpha FC - Beta United")], one, status="OPEN")], settled=True)])
        b = workflow.betting_breakdown(self.df, tks)
        sport = {r["key"]: r for r in b["by_sport"]}
        self.assertEqual(sport["soccer"]["settled"], 1)
        self.assertEqual(str(sport["soccer"]["pnl"]), "100.00")
        self.assertEqual(sport["mixed"]["settled"], 1)
        self.assertEqual(sport["unknown"]["settled"], 1)
        week = {r["key"]: r for r in b["by_week"]}
        self.assertEqual(set(week), {"2026-10-05", "2026-09-28"})
        self.assertEqual(week["2026-09-28"]["settled"], 1)
        self.assertEqual(week["2026-10-05"]["open"], 1)

    def test_competition_names_one_sport_only(self):
        index = {("competition", "liiga"): {"ice_hockey"}, ("competition", "champions league"): {"soccer", "basketball"}}
        self.assertEqual(workflow.leg_sport(leg("A", fixture="A - B", competition_raw="Liiga"), index), "ice_hockey")
        self.assertIsNone(workflow.leg_sport(leg("A", fixture="A - B", competition_raw="Champions League"), index))
        self.assertEqual(workflow.leg_sport(leg("A", "1-2 (Inc. Extra Inning)", "A - B"), index), "baseball")


if __name__ == "__main__":
    unittest.main()
