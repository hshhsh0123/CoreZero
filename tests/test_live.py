"""장중 실시간 엔진 테스트. 가짜 시세·가짜 AI·가짜 시계로 하루를 돌려본다. 네트워크는 안 쓴다."""

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from trader import broker, league, live, quotes, triggers
from trader.config import LIVE, MARKETS, MAX_POSITIONS, MAX_WEIGHT, SLIPPAGE

KR = MARKETS["kr"]
T0 = datetime(2026, 9, 30, 1, 0, tzinfo=timezone.utc)  # 한국 10:00 (수요일)
CAP = KR["capital"]


def bars_for(n=40):
    """하루 ±1% 왔다 갔다 하는 일봉. 일변동성이 약 1%가 된다."""
    out, p = [], 100.0
    for i in range(n):
        p *= 1.01 if i % 2 == 0 else 0.99
        d = (date(2026, 8, 1) + timedelta(days=i)).isoformat()
        out.append({"date": d, "open": p, "high": p, "low": p, "close": p, "volume": 1e6})
    return out


class Market:
    """가짜 시장. prices만 바꿔주면 시세 스냅샷이 그 값으로 나온다."""

    def __init__(self, codes=("S1", "S2", "S3", "S4", "S5", "S6")):
        self.now = T0
        self.prev = {c: 100.0 for c in codes}
        self.prices = dict(self.prev)
        self.status = "OPEN"
        self.halted = set()
        self.bench_price = 100.0

    def quotes(self):
        out = {}
        for c, p in self.prices.items():
            out[c] = {"code": c, "name": f"종목{c}", "kind": "stock", "price": p, "pct": p / self.prev[c] - 1,
                      "volume": 1e6, "at": self.now, "status": self.status, "halted": c in self.halted}
        out["069500"] = {"code": "069500", "name": "KODEX 200", "kind": "etf", "price": self.bench_price,
                         "pct": self.bench_price / 100 - 1, "volume": 1e6, "at": self.now,
                         "status": self.status, "halted": False}
        return out


class FakeBrain:
    def __init__(self, verdict="review", actions=None, boom=False):
        self.verdict, self.actions, self.boom = verdict, actions or [], boom
        self.triage_calls, self.react_calls = [], []

    def triage(self, ctx, events):
        self.triage_calls.append(events)
        return {"verdict": self.verdict, "importance": 4, "reason": "테스트", "meta": {}}

    def react(self, ctx, events, triage=None):
        self.react_calls.append((ctx, events))
        if self.boom:
            raise RuntimeError("deepseek down")
        return {"assessment": "판단", "actions": self.actions, "watch": [], "cash_reason": "", "meta": {"model": "fake"}}


def act(code, weight, stop=None, take=None, reason="이유"):
    return {"code": code, "weight": weight, "reason": reason, "stop_pct": stop, "take_pct": take}


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(league, "STATE_DIR", tmp / "state"),
            mock.patch.object(league, "LOG_DIR", tmp / "logs"),
            mock.patch.object(live, "RUNTIME", tmp / "runtime"),
        ]
        for p in self.patches:
            p.start()
        self.market = Market()
        self.news_items = []
        self.daily_calls = []
        self.logs = []

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def seed(self, positions=None, plans=None, shortlist=("S2",), pending=None):
        st = league.new_state("kr")
        st.update(started="2026-09-29", last_decided="2026-09-29", decisions=1, pending=pending)
        for pl in st["players"].values():
            pl["history"].append({"date": "2026-09-29", "equity": float(CAP)})
        ai = st["players"]["ai"]
        for code, (shares, avg) in (positions or {}).items():
            ai["positions"][code] = {"shares": shares, "avg": avg, "name": f"종목{code}"}
            ai["cash"] -= shares * avg
            st["names"][code] = f"종목{code}"
        st["plans"] = plans or {}
        st["journal"] = [{"date": "2026-09-29", "targets": [],
                          "shortlist": [{"code": c, "name": f"종목{c}", "why": "눈여겨봄"} for c in shortlist]}]
        league.save_state("kr", st)

    def engine(self, brain=None, **kw):
        deps = {
            "quotes": self.market.quotes,
            "news": lambda codes: list(self.news_items),
            "bars": lambda codes: {c: bars_for() for c in codes},
            "daily": lambda market, now=None, log=print: self.daily_calls.append(now),
            "brain": brain or FakeBrain(),
            "sync_worker": True,
            "strict": True,
            **kw,
        }
        return live.LiveEngine("kr", log=self.logs.append, deps=deps)

    def run_ticks(self, eng, n, step=60):
        for _ in range(n):
            eng.tick(self.market.now)
            self.market.now += timedelta(seconds=step)


class PhaseTest(EngineCase):
    def test_kr_schedule(self):
        eng = self.engine()
        kst = lambda h, m, d=30: datetime(2026, 9, d, h, m, tzinfo=live.ZoneInfo("Asia/Seoul"))
        self.assertEqual(eng._phase(kst(8, 30))[0], "pre")
        self.assertEqual(eng._phase(kst(9, 0))[0], "open")
        self.assertEqual(eng._phase(kst(15, 30))[0], "open")
        self.assertEqual(eng._phase(kst(15, 40))[0], "settle")
        phase, wake = eng._phase(kst(16, 30))
        self.assertEqual((phase, wake.day, wake.hour, wake.minute), ("post", 1, 8, 59))
        saturday = datetime(2026, 10, 3, 11, 0, tzinfo=live.ZoneInfo("Asia/Seoul"))
        phase, wake = eng._phase(saturday)
        self.assertEqual((phase, wake.day, wake.weekday()), ("closed", 5, 0))  # 다음 월요일 08:59

    def test_us_follows_daylight_saving(self):
        eng = live.LiveEngine("us", log=lambda *_: None, deps={"strict": True})
        utc = lambda y, mo, d, h, m: datetime(y, mo, d, h, m, tzinfo=timezone.utc).astimezone(eng.tz)
        self.assertEqual(eng._phase(utc(2026, 9, 30, 13, 30))[0], "open")   # 뉴욕 09:30 (서머타임)
        self.assertEqual(eng._phase(utc(2027, 1, 13, 14, 30))[0], "open")   # 뉴욕 09:30 (표준시)
        self.assertEqual(eng._phase(utc(2027, 1, 13, 13, 30))[0], "pre")


class DetectionTest(EngineCase):
    def test_fast_drop_wakes_ai_and_sells_at_live_price(self):
        self.seed({"S1": (100_000, 100.0)}, plans={"S1": {"stop_pct": 0.10, "take_pct": None}})
        brain = FakeBrain(actions=[act("S1", 0.0, reason="악재라 정리")])
        eng = self.engine(brain)
        self.run_ticks(eng, 20)                       # 20분 동안 잠잠
        self.assertEqual(brain.react_calls, [])
        self.market.prices["S1"] = 96.0               # 갑자기 -4%
        self.run_ticks(eng, 2)
        self.assertEqual(len(brain.react_calls), 1)
        kinds = {e["kind"] for e in brain.react_calls[0][1]}
        self.assertIn("fast_move", kinds)
        ai = eng.st["players"]["ai"]
        self.assertNotIn("S1", ai["positions"])
        sell = ai["trades"][-1]
        self.assertEqual((sell["side"], sell["tag"]), ("sell", "react"))
        self.assertAlmostEqual(sell["price"], 96.0 * (1 - SLIPPAGE["kr"]))
        self.assertNotIn("S1", eng.st["plans"])
        self.assertEqual(eng.st["reactions"][-1]["actions"][0]["reason"], "악재라 정리")
        saved = league.load_state("kr")
        self.assertEqual(len(saved["reactions"]), 1)

    def test_cooldown_stops_repeat_alerts(self):
        self.seed({"S1": (100_000, 100.0)})
        eng = self.engine(FakeBrain())
        self.run_ticks(eng, 20)
        self.market.prices["S1"] = 96.0
        self.run_ticks(eng, 1)
        first = len([e for e in eng.feed if e["kind"] == "fast_move"])
        self.market.prices["S1"] = 92.0
        self.run_ticks(eng, 3)
        self.assertEqual(len([e for e in eng.feed if e["kind"] == "fast_move"]), first)

    def test_quiet_market_makes_no_ai_calls_until_heartbeat(self):
        self.seed({"S1": (100_000, 100.0)})
        brain = FakeBrain()
        eng = self.engine(brain)
        self.run_ticks(eng, 60)
        self.assertEqual(brain.react_calls, [])
        self.run_ticks(eng, 40)                       # 90분 넘김
        self.assertEqual(len(brain.react_calls), 1)
        self.assertEqual(brain.react_calls[0][1][0]["kind"], "heartbeat")
        self.run_ticks(eng, 5)
        self.assertEqual(len(brain.react_calls), 1)

    def test_news_is_triaged_first(self):
        self.seed({"S1": (100_000, 100.0)})
        brain = FakeBrain(verdict="ignore")
        eng = self.engine(brain)
        when = T0 - timedelta(minutes=2)
        self.news_items = [{"code": "S1", "key": "a:1", "when": when, "title": "무난한 소식", "source": "연합"}]
        self.run_ticks(eng, 3)
        self.assertEqual(len(brain.triage_calls), 1)
        self.assertEqual(brain.react_calls, [])
        self.assertEqual(eng.counts["triages"], 1)
        # 같은 기사는 다시 안 물어본다
        self.run_ticks(eng, 5, step=130)
        self.assertEqual(len(brain.triage_calls), 1)

        brain.verdict = "review"
        self.news_items.append({"code": "S1", "key": "a:2", "when": self.market.now - timedelta(minutes=1),
                                "title": "계약 해지", "source": "한경"})
        self.run_ticks(eng, 3, step=130)
        self.assertEqual(len(brain.triage_calls), 2)
        self.assertEqual(len(brain.react_calls), 1)

    def test_old_news_is_ignored_on_startup(self):
        self.seed({"S1": (100_000, 100.0)})
        brain = FakeBrain()
        eng = self.engine(brain)
        self.news_items = [{"code": "S1", "key": "old:1", "when": T0 - timedelta(hours=3), "title": "옛 기사", "source": "x"}]
        self.run_ticks(eng, 3)
        self.assertEqual((brain.triage_calls, brain.react_calls), ([], []))

    def test_ai_failure_does_not_crash_or_loop(self):
        self.seed({"S1": (100_000, 100.0)})
        brain = FakeBrain(boom=True)
        eng = self.engine(brain)
        self.run_ticks(eng, 20)
        self.market.prices["S1"] = 96.0
        self.run_ticks(eng, 6)
        self.assertEqual(len(brain.react_calls), 1)
        self.assertTrue(any(e["kind"] == "error" for e in eng.feed))
        self.assertIn("S1", eng.st["players"]["ai"]["positions"])


class StopTest(EngineCase):
    def test_stop_needs_two_ticks_then_sells_without_ai(self):
        self.seed({"S1": (100_000, 100.0)}, plans={"S1": {"stop_pct": 0.08, "take_pct": None}})
        brain = FakeBrain()
        eng = self.engine(brain)
        self.run_ticks(eng, 3)
        self.market.prices["S1"] = 91.0
        self.run_ticks(eng, 1)
        self.assertIn("S1", eng.st["players"]["ai"]["positions"])   # 한 번만으로는 안 판다
        self.run_ticks(eng, 1)
        ai = eng.st["players"]["ai"]
        self.assertNotIn("S1", ai["positions"])
        self.assertEqual(ai["trades"][-1]["tag"], "stop")
        self.assertNotIn("S1", eng.st["plans"])
        self.assertEqual(brain.react_calls, [])                      # AI 호출 없이 집행

    def test_recovery_resets_confirmation(self):
        self.seed({"S1": (100_000, 100.0)}, plans={"S1": {"stop_pct": 0.08, "take_pct": None}})
        eng = self.engine()
        self.run_ticks(eng, 3)
        for price in (91.0, 95.0, 91.0):
            self.market.prices["S1"] = price
            self.run_ticks(eng, 1)
        self.assertIn("S1", eng.st["players"]["ai"]["positions"])

    def test_take_profit(self):
        self.seed({"S1": (100_000, 100.0)}, plans={"S1": {"stop_pct": 0.08, "take_pct": 0.10}})
        eng = self.engine()
        self.run_ticks(eng, 3)
        self.market.prices["S1"] = 112.0
        self.run_ticks(eng, 2)
        self.assertEqual(eng.st["players"]["ai"]["trades"][-1]["tag"], "take")

    def test_halted_stock_is_not_traded(self):
        self.seed({"S1": (100_000, 100.0)}, plans={"S1": {"stop_pct": 0.08, "take_pct": None}})
        eng = self.engine()
        self.run_ticks(eng, 3)
        self.market.prices["S1"] = 80.0
        self.market.halted.add("S1")
        self.run_ticks(eng, 4)
        self.assertIn("S1", eng.st["players"]["ai"]["positions"])


class ExecuteTest(EngineCase):
    def prepared(self, positions=None):
        self.seed(positions or {"S1": (50_000, 100.0)})
        eng = self.engine()
        self.run_ticks(eng, 2)
        return eng

    def result(self, actions, ref=None):
        ref = ref or {"S1": 100.0, "S2": 100.0, "S3": 100.0}
        return {"events": [{"text": "테스트"}], "actions": actions, "ref_prices": ref, "assessment": "a", "seconds": 1.0}

    def go(self, eng, res):
        eng._execute_react(res, eng.last_q, self.market.now, self.market.now.astimezone(eng.tz))

    def test_chase_guard_and_untradable(self):
        eng = self.prepared()
        self.market.prices["S2"] = 103.0            # AI가 본 100보다 3% 올랐다
        self.market.halted.add("S3")
        eng.last_q = self.market.quotes()
        self.go(eng, self.result([act("S2", 0.2, stop=0.07), act("S3", 0.1, stop=0.07)]))
        self.assertNotIn("S2", eng.st["players"]["ai"]["positions"])
        self.assertNotIn("S3", eng.st["players"]["ai"]["positions"])
        notes = eng.st["reactions"][-1]["notes"]
        self.assertTrue(any("추격" in n for n in notes) and any("거래할 수 없" in n for n in notes))

    def test_buy_sets_plan_and_default_stop(self):
        eng = self.prepared()
        self.go(eng, self.result([act("S2", 0.2, stop=0.07, take=0.15), act("S3", 0.1)]))
        plans = eng.st["plans"]
        self.assertEqual((plans["S2"]["stop_pct"], plans["S2"]["take_pct"]), (0.07, 0.15))
        self.assertEqual(plans["S3"]["stop_pct"], 0.10)            # 안 정하면 기본 손절
        self.assertIn("S1", plans)

    def test_never_exceeds_cash_or_limits(self):
        codes = [f"S{i}" for i in range(1, 15)]
        self.market = Market(codes)
        eng = self.prepared({"S1": (250_000, 100.0)})               # 이미 25% 보유
        actions = [act(c, 0.30, stop=0.07) for c in codes[1:]]
        self.go(eng, self.result(actions, ref={c: 100.0 for c in codes}))
        ai = eng.st["players"]["ai"]
        self.assertGreaterEqual(ai["cash"], 0)
        self.assertLessEqual(len(ai["positions"]), MAX_POSITIONS)
        eq = broker.equity(ai, {c: 100.0 for c in codes})
        for c, p in ai["positions"].items():
            self.assertLessEqual(p["shares"] * 100.0 / eq, MAX_WEIGHT + 0.01)

    def test_untouched_holdings_are_left_alone(self):
        eng = self.prepared({"S1": (50_000, 100.0), "S4": (30_000, 100.0)})
        before = dict(eng.st["players"]["ai"]["positions"]["S4"])
        self.go(eng, self.result([act("S2", 0.1, stop=0.07)], ref={"S2": 100.0}))
        self.assertEqual(eng.st["players"]["ai"]["positions"]["S4"], before)

    def test_daily_trade_limit_blocks_new_buys_only(self):
        eng = self.prepared()
        eng.counts["trades"] = LIVE["max_live_trades_per_day"]
        self.go(eng, self.result([act("S2", 0.2, stop=0.07), act("S1", 0.0)]))
        ai = eng.st["players"]["ai"]
        self.assertNotIn("S2", ai["positions"])
        self.assertNotIn("S1", ai["positions"])                     # 팔기는 허용


class SessionFlowTest(EngineCase):
    def test_pending_order_fills_at_open_with_slippage(self):
        pend = {"decided_on": "2026-09-29", "targets": {"ai": {"S2": 0.3}, "monkey": None, "hodl": None},
                "plans": {"S2": {"stop_pct": 0.07, "take_pct": None}}}
        self.seed(pending=pend)
        today_bar = {"date": "2026-09-30", "open": 100.0, "high": 101, "low": 99, "close": 100.5, "volume": 1e6}
        eng = self.engine(bars=lambda codes: {c: bars_for() + [today_bar] for c in codes})
        self.run_ticks(eng, 2)
        ai = eng.st["players"]["ai"]
        self.assertIn("S2", ai["positions"])
        self.assertAlmostEqual(ai["trades"][0]["price"], 100.0 * (1 + SLIPPAGE["kr"]))
        self.assertEqual(ai["trades"][0]["tag"], "open")
        self.assertEqual(eng.st["plans"]["S2"]["stop_pct"], 0.07)
        self.assertIsNone(eng.st["pending"])

    def test_pending_waits_for_open_bar(self):
        pend = {"decided_on": "2026-09-29", "targets": {"ai": {"S2": 0.3}, "monkey": None, "hodl": None}}
        self.seed(pending=pend)
        eng = self.engine()                                            # 오늘 봉이 아직 없다
        self.run_ticks(eng, 2)
        self.assertIsNotNone(eng.st["pending"])
        self.assertEqual(eng.st["players"]["ai"]["positions"], {})

    def test_daily_settlement_runs_once_after_close(self):
        self.seed()
        eng = self.engine()
        kst = live.ZoneInfo("Asia/Seoul")
        at = lambda h, m: datetime(2026, 9, 30, h, m, tzinfo=kst).astimezone(timezone.utc)
        eng.tick(at(15, 40))
        self.assertEqual(self.daily_calls, [])                         # 아직 정산 대기 시간
        eng.tick(at(15, 55))
        eng.tick(at(16, 5))
        self.assertEqual(len(self.daily_calls), 1)

    def test_holiday_is_detected_from_quotes(self):
        self.seed()
        self.market.status = "CLOSE"
        calls = []
        eng = self.engine(quotes=lambda: calls.append(1) or self.market.quotes())
        self.market.now = T0 - timedelta(hours=1) + timedelta(minutes=5)   # 09:05
        self.run_ticks(eng, 8)
        self.assertTrue(eng.holiday)
        n = len(calls)
        self.run_ticks(eng, 3)
        self.assertEqual(len(calls), n)

    def test_snapshot_files_for_dashboard(self):
        self.seed({"S1": (50_000, 100.0)})
        eng = self.engine()
        self.run_ticks(eng, 3)
        snap = json.loads((live.RUNTIME / "live_kr.json").read_text())
        self.assertEqual(snap["phase"], "open")
        self.assertEqual(set(snap["players"]), {"ai", "monkey", "hodl"})
        self.assertTrue((live.RUNTIME / f"intraday_kr_2026-09-30.jsonl").exists())
        self.assertTrue((live.RUNTIME / "feed_kr.jsonl").exists())


class TriggerUnitTest(unittest.TestCase):
    def test_thresholds(self):
        thr = triggers.fast_threshold(0.03, 15, 390, 3.0, 0.012)
        self.assertAlmostEqual(thr, 3 * 0.03 * (15 / 390) ** 0.5)
        self.assertEqual(triggers.fast_threshold(0.005, 15, 390, 3.0, 0.012), 0.012)
        self.assertEqual((triggers.day_level(0.061, 0.03), triggers.day_level(-0.09, 0.04), triggers.day_level(0.01, 0.03)), (2, -2, 0))

    def test_history_return_needs_enough_data(self):
        h = quotes.History()
        for i in range(5):
            h.add(T0 + timedelta(minutes=i), {"A": {"price": 100.0 + i}})
        now = T0 + timedelta(minutes=4)
        self.assertIsNone(h.ret("A", 900, now))
        self.assertAlmostEqual(h.ret("A", 120, now), 104 / 102 - 1)


if __name__ == "__main__":
    unittest.main()
