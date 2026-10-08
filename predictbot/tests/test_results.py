"""Automatic results: Polymarket's resolved markets and OddsPortal result
pages read by the walker settle logged games without a chat."""

import io
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import results  # noqa: E402
import workflow  # noqa: E402
from storage import DataFolder  # noqa: E402
from test_tickets import sel  # noqa: E402

NOW = datetime(2026, 10, 7, 23, 0, tzinfo=timezone.utc)


def ml(outcomes, prices, resolved=True, question="A vs. B"):
    return {"question": question, "sportsMarketType": "moneyline", "outcomes": json.dumps(outcomes),
            "outcomePrices": json.dumps([str(p) for p in prices]), "closed": resolved,
            "umaResolutionStatus": "resolved" if resolved else None}


def soccer_event(slug, home, away, yes, resolved=True):
    qs = [f"Will {home} win on 2026-10-07?", f"Will {home} vs. {away} end in a draw?", f"Will {away} win on 2026-10-07?"]
    return {"slug": slug, "tags": [{"slug": "soccer"}], "markets": [
        ml(["Yes", "No"], [y, 1 - y], resolved, q) for q, y in zip(qs, yes)]}


class Parsers(unittest.TestCase):
    def test_final_result_text(self):
        r = results.parse_final("Final result \n5:4 OT\n (0:2, 3:2, 1:0, 1:0) \n1X2 Home/Away")
        self.assertEqual(r, {"score": (5, 4), "flag": "OT", "periods": [(0, 2), (3, 2), (1, 0), (1, 0)]})
        self.assertIsNone(results.parse_final('"final_result":"Final result","none"'))

    def test_regulation_time_for_1x2(self):
        # OT win is a regulation draw; penalties after 1:1; a plain soccer win
        self.assertEqual(results.oddsportal_result("Final result 5:4 OT (0:2, 3:2, 1:0, 1:0)", "ice_hockey", 3, False), (1, ""))
        self.assertEqual(results.oddsportal_result("Final result 4:3 pen. (1:0, 0:1, 0:0, 3:2)", "soccer", 3, False), (1, ""))
        self.assertEqual(results.oddsportal_result("Final result 2:0 (1:0, 1:0)", "soccer", 3, False), (0, ""))
        self.assertEqual(results.oddsportal_result("Final result 1:2 (1:0, 0:1, 0:1)", "ice_hockey", 3, False), (2, ""))

    def test_two_way_and_exceptions(self):
        self.assertEqual(results.oddsportal_result("Final result 5:4 OT (0:2, 3:2, 1:0, 1:0)", "ice_hockey", 2, False), (0, ""))
        self.assertEqual(results.oddsportal_result("Final result 2:1 (6:4, 3:6, 7:5)", "tennis", 2, True), (0, ""))
        for raw, variable in (("Final result 1:0 ret.", True), ("Final result 0:0 canc.", False),
                              ("Final result 2:2", False)):
            pos, why = results.oddsportal_result(raw, "tennis", 2, variable)
            self.assertIsNone(pos)
            self.assertIn("by hand", why)
        self.assertIsNone(results.oddsportal_result("Final result 3:2 OT (1:1, 1:1)", "ice_hockey", 3, False)[0])

    def test_polymarket_winner(self):
        self.assertEqual(results.polymarket_winner({"markets": [ml(["Lakers", "Kings"], [0, 1])]}), ("Kings", ""))
        self.assertEqual(results.polymarket_winner(soccer_event("s", "A FC", "B FC", [0, 1, 0])), ("Draw", ""))
        self.assertIsNone(results.polymarket_winner({"markets": [ml(["A", "B"], [0.5, 0.5])]})[0])
        self.assertEqual(results.polymarket_winner({"markets": [ml(["A", "B"], [0.995, 0.005], resolved=False)]}),
                         (None, "not resolved yet"))


class SettleFromResults(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.df = DataFolder(self.tmp, host="HP")
        self.df.append("selection", [sel("e1", 0, "Alpha FC (home)"), sel("e1", 1, "Draw"),
                                     sel("e1", 2, "Beta United (away)"),
                                     sel("e2", 0, "Cobras", "MONEYLINE_INC_OT", "basketball", odds="1.50"),
                                     sel("e2", 1, "Dingos", "MONEYLINE_INC_OT", "basketball", odds="2.60")])
        start = "2026-10-07T19:00Z"
        # Polymarket lists the basketball game away side first
        self.quotes = [
            {"source": "polymarket", "url": "https://polymarket.com/sports/x/soc-alp-bet-2026-10-07", "sport": "soccer",
             "start_utc": start, "captured_at_utc": "2026-10-07T12:00Z", "outcomes": ["Alpha FC", "Draw", "Beta United"],
             "probs": [0.4, 0.3, 0.3]},
            {"source": "polymarket", "url": "https://polymarket.com/sports/x/bk-din-cob-2026-10-07", "sport": "basketball",
             "start_utc": start, "captured_at_utc": "2026-10-07T12:00Z", "outcomes": ["Dingos", "Cobras"],
             "probs": [0.4, 0.6]}]
        self.calls = []

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def opener(self, events):
        def fake(url):
            self.calls.append(url)
            return io.BytesIO(json.dumps(events).encode())
        return fake

    def run_results(self, events, quotes=None):
        with mock.patch.object(workflow, "_result_quotes", lambda df, extra, now: quotes or self.quotes):
            return workflow.settle_from_results(self.df, NOW, opener=self.opener(events))

    def results_by_selection(self):
        return {s["selection_record_id"]: (s["result"], s["settlement_source"]) for s in self.df.read("settlement")}

    def test_polymarket_settles_both_games_in_bet9ja_order(self):
        r = self.run_results([soccer_event("soc-alp-bet-2026-10-07", "Alpha FC", "Beta United", [0, 0, 1]),
                              {"slug": "bk-din-cob-2026-10-07", "tags": [{"slug": "basketball"}],
                               "markets": [ml(["Dingos", "Cobras"], [0, 1])]}])
        self.assertEqual((r["games"], r["settled"]), (2, 5))
        got = {k: v[0] for k, v in self.results_by_selection().items()}
        self.assertEqual(got, {"sel_e1_0_12": "LOSE", "sel_e1_1_12": "LOSE", "sel_e1_2_12": "WIN",
                               "sel_e2_0_12": "WIN", "sel_e2_1_12": "LOSE"})
        self.assertEqual(len(self.calls), 1)                         # both slugs in one request
        self.assertTrue(list((self.tmp / "captures").glob("polymarket-results-*.json")))
        again = self.run_results([])
        self.assertEqual(again["settled"], 0)

    def test_offline_fetch_waits_before_retrying(self):
        import feeds
        def down(url):
            self.calls.append(url)
            raise OSError("network unreachable")
        groups = workflow.due_results(self.df, NOW)
        self.assertTrue(groups)
        slugs = ["soc-alp-bet-2026-10-07"]
        self.assertEqual(feeds.fetch_polymarket_results(self.df, NOW, slugs, opener=down)["status"], "failed")
        self.assertEqual(feeds.fetch_polymarket_results(self.df, NOW, slugs, opener=down)["status"], "recent")
        self.assertEqual(len(self.calls), 1)                         # no second wait on a dead network
        later = NOW + feeds.RETRY_AFTER
        self.assertEqual(feeds.fetch_polymarket_results(self.df, later, slugs, opener=self.opener([]))["status"], "fetched")

    def test_unresolved_and_50_50_wait(self):
        r = self.run_results([soccer_event("soc-alp-bet-2026-10-07", "Alpha FC", "Beta United", [0.9, 0.05, 0.05], False),
                              {"slug": "bk-din-cob-2026-10-07", "tags": [{"slug": "basketball"}],
                               "markets": [ml(["Dingos", "Cobras"], [0.5, 0.5])]}])
        self.assertEqual(r["settled"], 0)
        self.assertEqual(sorted(w[3] for w in r["waiting"]),
                         ["Polymarket: not resolved yet", "Polymarket: resolved 50-50 (cancelled or tied): settle by hand"])

    def walked(self, url, raw, kickoff="07 Oct 2026, 21:00"):
        run = {"source_key": "oddsportal", "mode": "results", "browser_utc_offset_minutes": 120,
               "captures": [{"role": "result", "capture_status": "RESULT_OK", "source_url": url,
                             "captured_at_utc": "2026-10-07T22:30:00Z", "final_result_raw": raw, "kickoff_raw": kickoff}]}
        self.df.save_capture("public-odds-walk-oddsportal-results-2026-10-07T22-30-00-000Z.json", json.dumps(run).encode())

    def test_oddsportal_result_page_settles_regulation_time(self):
        op = "https://www.oddsportal.com/football/h2h/alpha-AAAAAAAA/beta-BBBBBBBB/"
        quotes = [{"source": "oddsportal", "url": op, "page_url": op + "#Xy12", "sport": "soccer",
                   "start_utc": "2026-10-07T19:00Z", "captured_at_utc": "2026-10-07T12:00Z",
                   "outcomes": ["Alpha", "Draw", "Beta United"], "probs": [0.4, 0.3, 0.3]}]
        with mock.patch.object(workflow, "_result_quotes", lambda df, extra, now: quotes):
            targets = workflow.result_targets(self.df, NOW)
        self.assertEqual([t["url"] for t in targets], [op + "#Xy12"])   # the walker gets the match page with its hash
        self.walked(op + "#Xy12", "Final result 2:2 ET (1:0, 0:1, 1:1)")
        r = self.run_results([], quotes)
        self.assertEqual(r["games"], 1)
        got = {k: v for k, v in self.results_by_selection().items()}
        self.assertEqual(got["sel_e1_1_12"][0], "WIN")                    # 1:1 after 90 minutes: draw
        self.assertTrue(got["sel_e1_1_12"][1].startswith("oddsportal result"))

    def test_oddsportal_page_for_another_date_is_not_used(self):
        op = "https://www.oddsportal.com/football/h2h/alpha-AAAAAAAA/beta-BBBBBBBB/"
        quotes = [{"source": "oddsportal", "url": op, "sport": "soccer", "start_utc": "2026-10-07T19:00Z",
                   "captured_at_utc": "2026-10-07T12:00Z", "outcomes": ["Alpha", "Draw", "Beta United"],
                   "probs": [0.4, 0.3, 0.3]}]
        self.walked(op, "Final result 1:0 (1:0, 0:0)", kickoff="14 Oct 2026, 21:00")
        r = self.run_results([], quotes)
        self.assertEqual(r["settled"], 0)
        self.assertIn("different match date", r["waiting"][0][3])

    def test_sources_that_disagree_settle_nothing(self):
        op = "https://www.oddsportal.com/football/h2h/alpha-AAAAAAAA/beta-BBBBBBBB/"
        quotes = [self.quotes[0], {"source": "oddsportal", "url": op, "sport": "soccer", "start_utc": "2026-10-07T19:00Z",
                                   "captured_at_utc": "2026-10-07T12:00Z", "outcomes": ["Alpha", "Draw", "Beta United"],
                                   "probs": [0.4, 0.3, 0.3]}]
        self.walked(op + "#Xy12", "Final result 1:0 (1:0, 0:0)")
        r = self.run_results([soccer_event("soc-alp-bet-2026-10-07", "Alpha FC", "Beta United", [0, 1, 0])], quotes)
        self.assertEqual(r["settled"], 0)
        self.assertIn("sources disagree", r["problems"][0][1])

    def test_tennis_from_polymarket_needs_the_rule_confirmed(self):
        self.df.append("selection", [sel("e3", 0, "Ann", "MATCH_WINNER", "tennis"), sel("e3", 1, "Bea", "MATCH_WINNER", "tennis")])
        quotes = [{"source": "polymarket", "url": "https://polymarket.com/sports/wta/ten-ann-bea", "sport": "tennis",
                   "start_utc": "2026-10-07T19:00Z", "captured_at_utc": "2026-10-07T12:00Z",
                   "outcomes": ["Cobras", "Dingos"], "probs": [0.5, 0.5]}]
        r = self.run_results([{"slug": "ten-ann-bea", "tags": [{"slug": "tennis"}],
                               "markets": [ml(["Cobras", "Dingos"], [1, 0])]}], quotes)
        self.assertEqual(r["settled"], 0)
        self.assertIn("settlement rule unconfirmed", " ".join(w[3] for w in r["waiting"]))
        self.assertEqual(self.calls, [])                            # not even fetched
        self.df.append("rule", [{"rule_key": "polymarket|tennis|MATCH_WINNER", "status": "same as bet9ja",
                                 "confirmed_at_utc": "2026-10-07T22:00Z", "note": ""}])
        r = self.run_results([{"slug": "ten-ann-bea", "tags": [{"slug": "tennis"}],
                               "markets": [ml(["Cobras", "Dingos"], [1, 0])]}], quotes)
        self.assertEqual(r["games"], 1)


if __name__ == "__main__":
    unittest.main()
