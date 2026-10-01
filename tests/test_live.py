"""장중 실시간 엔진 테스트. 가짜 시세·가짜 AI·가짜 시계로 하루를 돌려본다. 네트워크는 안 쓴다."""

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from trader import brain, broker, league, live, quotes, triggers
from trader import plans as planlib
from trader.config import DEFAULT_STOP_PCT, LIVE, MARKETS, MAX_ALERTS, MAX_POSITIONS, MAX_WEIGHT, SLIPPAGE

KR = MARKETS["kr"]
T0 = datetime(2026, 9, 30, 1, 0, tzinfo=timezone.utc)  # 한국 10:00 (수요일)
CAP = KR["capital"]
BUY_AVG = 100.0 * (1 + SLIPPAGE["kr"]) * (1 + KR["fee"])  # 100원에 샀을 때 평단 (미끄러짐+수수료)


def kst(h, m, d=30):
    return datetime(2026, 9, d, h, m, tzinfo=live.ZoneInfo("Asia/Seoul")).astimezone(timezone.utc)


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
    """react의 답은 reply(고정 dict) 또는 respond(ctx, events) 함수로 정한다."""

    def __init__(self, verdict="review", actions=None, boom=False, reply=None, respond=None):
        self.verdict, self.boom = verdict, boom
        self.reply = {"actions": actions or [], **(reply or {})}
        self.respond = respond
        self.triage_calls, self.react_calls, self.modes = [], [], []

    def triage(self, ctx, events):
        self.triage_calls.append(events)
        return {"verdict": self.verdict, "importance": 4, "reason": "테스트", "meta": {}}

    def react(self, ctx, events, triage=None, mode="trade"):
        self.react_calls.append((ctx, events))
        self.modes.append(mode)
        if self.boom:
            raise RuntimeError("deepseek down")
        out = {"assessment": "판단", "actions": [], "alerts": None, "alert_notes": [], "next_check_min": None,
               "cash_reason": "", "meta": {"model": "fake"}}
        out.update(self.respond(ctx, events) if self.respond else self.reply)
        return out


def act(code, weight, reason="이유", **plan):
    """brain.clean_actions를 거친 모양 그대로."""
    return {"code": code, "weight": weight, "reason": reason, "plan": planlib.parse_update(plan)}


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(league, "STATE_DIR", tmp / "state"),
            mock.patch.object(league, "LOG_DIR", tmp / "logs"),
            mock.patch.object(live, "RUNTIME", tmp / "runtime"),
            mock.patch.dict(LIVE, {"open_review": False}),  # 장 시작 점검은 따로 테스트한다
            # 가짜 시장은 100원짜리라 호가 반 칸(0.5원)이 0.5%나 된다. 미끄러짐은 기본값만 쓰고 호가는 따로 테스트한다
            mock.patch.object(broker, "tick_size", lambda market, price, etf=False: 0.0),
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

    def seed(self, positions=None, plans=None, shortlist=("S2",), pending=None, alerts=None):
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
        st["alerts"] = alerts or []
        st["journal"] = [{"date": "2026-09-29", "targets": [],
                          "shortlist": [{"code": c, "name": f"종목{c}", "why": "눈여겨봄"} for c in shortlist]}]
        league.save_state("kr", st)

    def engine(self, brain=None, **kw):
        deps = {
            "quotes": self.market.quotes,
            "news": lambda codes: list(self.news_items),
            "bars": lambda codes: {c: bars_for() for c in codes},
            "daily": lambda market, now=None, log=print: self.daily_calls.append(now),
            "opens": lambda codes: {},
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

    def open_and_check_now(self, eng):
        """장을 열고(첫 확인), 다음 확인 때 바로 정기 점검이 돌게 한다."""
        self.run_ticks(eng, 1)
        eng.next_check_at = self.market.now

    @staticmethod
    def kinds(call):
        return [e["kind"] for e in call[1]]


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
        brain_ = FakeBrain(actions=[act("S1", 0.0, reason="악재라 정리")])
        eng = self.engine(brain_)
        self.run_ticks(eng, 20)                       # 20분 동안 잠잠
        self.assertEqual(brain_.react_calls, [])
        self.market.prices["S1"] = 96.0               # 갑자기 -4%
        self.run_ticks(eng, 2)
        self.assertEqual(len(brain_.react_calls), 1)
        kinds = {e["kind"] for e in brain_.react_calls[0][1]}
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

    def test_day_step_fires_once_per_level_even_if_price_wobbles_across_it(self):
        self.seed({"S1": (100_000, 100.0)})
        eng = self.engine(FakeBrain())
        steps = lambda: [e["text"] for e in eng.feed if e["kind"] == "day_step"]
        self.run_ticks(eng, 20)
        for price in (103.5, 102.0, 103.4, 102.5, 103.6):   # 단계(+3%) 경계에서 오르내림
            self.market.prices["S1"] = price
            self.run_ticks(eng, 1)
        self.assertEqual(len(steps()), 1)
        self.market.prices["S1"] = 106.5                     # 더 높은 단계는 새 소식
        self.run_ticks(eng, 1)
        for price in (96.5, 101.0, 96.8):                    # 반대쪽 단계도 처음 한 번만
            self.market.prices["S1"] = price
            self.run_ticks(eng, 1)
        self.assertEqual(len(steps()), 3)
        self.assertIn("상승 2단계", steps()[1])
        self.assertIn("하락 1단계", steps()[2])
        self.assertEqual(eng.day_levels["S1"], [2, -1])

    def test_day_step_reads_old_single_number_levels_after_restart(self):
        self.seed({"S1": (100_000, 100.0)})
        eng = self.engine(FakeBrain())
        self.run_ticks(eng, 20)
        eng.day_levels = {"S1": 1}                           # 고치기 전 엔진이 저장한 모양
        self.market.prices["S1"] = 103.5
        self.run_ticks(eng, 1)
        self.assertEqual([e for e in eng.feed if e["kind"] == "day_step"], [])
        self.market.prices["S1"] = 106.5
        self.run_ticks(eng, 1)
        self.assertEqual(len([e for e in eng.feed if e["kind"] == "day_step"]), 1)

    def test_quiet_market_makes_no_ai_calls_until_next_check(self):
        self.seed({"S1": (100_000, 100.0)})
        brain_ = FakeBrain()
        eng = self.engine(brain_)
        self.run_ticks(eng, 60)
        self.assertEqual(brain_.react_calls, [])
        self.run_ticks(eng, 40)                       # 기본 간격(90분) 넘김
        self.assertEqual(len(brain_.react_calls), 1)
        self.assertEqual(brain_.react_calls[0][1][0]["kind"], "heartbeat")
        self.run_ticks(eng, 5)
        self.assertEqual(len(brain_.react_calls), 1)

    def test_news_is_triaged_first(self):
        self.seed({"S1": (100_000, 100.0)})
        brain_ = FakeBrain(verdict="ignore")
        eng = self.engine(brain_)
        when = T0 - timedelta(minutes=2)
        self.news_items = [{"code": "S1", "key": "a:1", "when": when, "title": "무난한 소식", "source": "연합"}]
        self.run_ticks(eng, 3)
        self.assertEqual(len(brain_.triage_calls), 1)
        self.assertEqual(brain_.react_calls, [])
        self.assertEqual(eng.counts["triages"], 1)
        self.run_ticks(eng, 5, step=130)             # 같은 기사는 다시 안 물어본다
        self.assertEqual(len(brain_.triage_calls), 1)

        brain_.verdict = "review"
        self.news_items.append({"code": "S1", "key": "a:2", "when": self.market.now - timedelta(minutes=1),
                                "title": "계약 해지", "source": "한경"})
        self.run_ticks(eng, 3, step=130)
        self.assertEqual(len(brain_.triage_calls), 2)
        self.assertEqual(len(brain_.react_calls), 1)

    def test_old_news_is_ignored_on_startup(self):
        self.seed({"S1": (100_000, 100.0)})
        brain_ = FakeBrain()
        eng = self.engine(brain_)
        self.news_items = [{"code": "S1", "key": "old:1", "when": T0 - timedelta(hours=3), "title": "옛 기사", "source": "x"}]
        self.run_ticks(eng, 3)
        self.assertEqual((brain_.triage_calls, brain_.react_calls), ([], []))

    def test_ai_failure_retries_important_events_once(self):
        self.seed({"S1": (100_000, 100.0)})
        brain_ = FakeBrain(boom=True)
        eng = self.engine(brain_)
        self.run_ticks(eng, 20)
        self.market.prices["S1"] = 96.0
        self.run_ticks(eng, 15)
        self.assertEqual(len(brain_.react_calls), 2)    # 처음 한 번 + 다시 한 번, 그 뒤로 폭주 없음
        self.assertTrue(any(e["kind"] == "error" for e in eng.feed))
        self.assertIn("S1", eng.st["players"]["ai"]["positions"])


class StopTest(EngineCase):
    def test_stop_needs_two_ticks_then_sells_without_ai_and_asks_ai_after(self):
        self.seed({"S1": (100_000, 100.0)}, plans={"S1": {"stop_pct": 0.08, "take_pct": None}})
        brain_ = FakeBrain()
        eng = self.engine(brain_)
        self.run_ticks(eng, 3)
        self.market.prices["S1"] = 91.0
        self.run_ticks(eng, 1)
        self.assertIn("S1", eng.st["players"]["ai"]["positions"])   # 한 번만으로는 안 판다
        self.run_ticks(eng, 1)
        ai = eng.st["players"]["ai"]
        self.assertNotIn("S1", ai["positions"])                    # AI 답을 안 기다리고 바로 판다
        self.assertEqual(ai["trades"][-1]["tag"], "stop")
        self.assertNotIn("S1", eng.st["plans"])
        self.assertEqual(len([e for e in eng.feed if e["kind"] == "stop"]), 1)   # 피드에 한 번만
        self.run_ticks(eng, 6)
        # 판 뒤에는 AI가 남은 돈을 어떻게 할지 다시 본다
        self.assertTrue(any("stop" in self.kinds(c) for c in brain_.react_calls))

    def test_recovery_resets_confirmation(self):
        self.seed({"S1": (100_000, 100.0)}, plans={"S1": {"stop_pct": 0.08, "take_pct": None}})
        eng = self.engine()
        self.run_ticks(eng, 3)
        for price in (91.0, 95.0, 91.0):
            self.market.prices["S1"] = price
            self.run_ticks(eng, 1)
        self.assertIn("S1", eng.st["players"]["ai"]["positions"])

    def test_take_profit_all(self):
        self.seed({"S1": (100_000, 100.0)}, plans={"S1": {"stop_pct": 0.08, "take_pct": 0.10}})
        eng = self.engine()
        self.run_ticks(eng, 3)
        self.market.prices["S1"] = 112.0
        self.run_ticks(eng, 2)
        self.assertEqual(eng.st["players"]["ai"]["trades"][-1]["tag"], "take")
        self.assertNotIn("S1", eng.st["players"]["ai"]["positions"])

    def test_partial_take_sells_part_and_asks_ai_to_replan(self):
        self.seed({"S1": (100_000, 100.0)}, plans={"S1": {"stop": 90.0, "take": 110.0, "take_frac": 0.5}})

        def respond(ctx, events):
            if any(e["kind"] == "take" for e in events):  # 절반 판 뒤: 손절을 본전으로, 트레일링 켜기
                return {"actions": [act("S1", None, stop_price=100.0, trail_pct=0.05, reason="본전 지키기")]}
            return {}

        brain_ = FakeBrain(respond=respond)
        eng = self.engine(brain_)
        self.run_ticks(eng, 3)
        self.market.prices["S1"] = 111.0
        self.run_ticks(eng, 2)
        ai = eng.st["players"]["ai"]
        self.assertEqual(ai["positions"]["S1"]["shares"], 50_000)
        self.assertEqual((ai["trades"][-1]["tag"], ai["trades"][-1]["shares"]), ("take", 50_000))
        self.assertNotIn("take", eng.st["plans"]["S1"])   # 다음 목표는 AI가 다시 정하게 비운다
        self.run_ticks(eng, 6)
        self.assertTrue(any("take" in self.kinds(c) for c in brain_.react_calls))
        plan = eng.st["plans"]["S1"]
        self.assertEqual(plan["stop"], 100.0)
        self.assertEqual((plan["trail_pct"], plan["high"]), (0.05, 111.0))
        self.assertEqual(ai["positions"]["S1"]["shares"], 50_000)   # 111 > 105.45라 안 팔린다

    def test_trailing_stop_follows_the_high(self):
        self.seed({"S1": (100_000, 100.0)}, plans={"S1": {"trail_pct": 0.05, "high": 100.0}})
        eng = self.engine()
        self.run_ticks(eng, 2)
        self.assertNotIn("default", eng.st["plans"]["S1"])   # 트레일링만 있어도 기본 손절은 안 건다
        for p in (104.0, 110.0, 120.0):
            self.market.prices["S1"] = p
            self.run_ticks(eng, 1)
        self.assertEqual(league.load_state("kr")["plans"]["S1"]["high"], 120.0)   # 저장도 된다
        self.market.prices["S1"] = 115.0                 # 120의 -5%(114)보다 위: 안 판다
        self.run_ticks(eng, 3)
        self.assertIn("S1", eng.st["players"]["ai"]["positions"])
        self.market.prices["S1"] = 113.5
        self.run_ticks(eng, 2)
        ai = eng.st["players"]["ai"]
        self.assertNotIn("S1", ai["positions"])
        self.assertEqual(ai["trades"][-1]["tag"], "trail")

    def test_halted_stock_is_not_traded(self):
        self.seed({"S1": (100_000, 100.0)}, plans={"S1": {"stop_pct": 0.08, "take_pct": None}})
        eng = self.engine()
        self.run_ticks(eng, 3)
        self.market.prices["S1"] = 80.0
        self.market.halted.add("S1")
        self.run_ticks(eng, 4)
        self.assertIn("S1", eng.st["players"]["ai"]["positions"])


class ExecuteTest(EngineCase):
    def prepared(self, positions=None, shortlist=("S2",)):
        self.seed(positions or {"S1": (50_000, 100.0)}, shortlist=shortlist)
        eng = self.engine()
        self.run_ticks(eng, 2)
        return eng

    def result(self, actions, ref=None, **extra):
        ref = ref or {"S1": 100.0, "S2": 100.0, "S3": 100.0}
        return {"events": [{"text": "테스트"}], "actions": actions, "ref_prices": ref, "assessment": "a",
                "seconds": 1.0, **extra}

    def go(self, eng, res):
        eng._execute_react(res, eng.last_q, self.market.now, self.market.now.astimezone(eng.tz))

    def test_chase_guard_and_untradable(self):
        eng = self.prepared()
        self.market.prices["S2"] = 103.0            # AI가 본 100보다 3% 올랐다
        self.market.halted.add("S3")
        eng.last_q = self.market.quotes()
        self.go(eng, self.result([act("S2", 0.2, stop_pct=0.07), act("S3", 0.1, stop_pct=0.07)]))
        self.assertNotIn("S2", eng.st["players"]["ai"]["positions"])
        self.assertNotIn("S3", eng.st["players"]["ai"]["positions"])
        notes = eng.st["reactions"][-1]["notes"]
        self.assertTrue(any("추격" in n for n in notes) and any("거래할 수 없" in n for n in notes))

    def test_buy_sets_plan_and_default_stop(self):
        eng = self.prepared(shortlist=("S2", "S3"))
        self.go(eng, self.result([act("S2", 0.2, stop_pct=0.07, take_pct=0.15, take_frac=0.5), act("S3", 0.1)]))
        plans = eng.st["plans"]
        self.assertAlmostEqual(plans["S2"]["stop"], BUY_AVG * 0.93)
        self.assertAlmostEqual(plans["S2"]["take"], BUY_AVG * 1.15)
        self.assertEqual(plans["S2"]["take_frac"], 0.5)
        self.assertTrue(plans["S3"]["default"])                     # 안 정하면 기본 손절
        pct = planlib.default_pct(eng._sigma("S3"))                  # 조용한 종목이라 좁게 (하한 5%)
        self.assertEqual(pct, 0.05)
        self.assertAlmostEqual(plans["S3"]["stop"], BUY_AVG * (1 - pct))
        self.assertIn("S1", plans)

    def test_averaging_down_does_not_loosen_the_default_stop(self):
        eng = self.prepared({"S1": (50_000, 100.0), "S4": (50_000, 100.0)})
        league.sync_plans(eng.st, {"S4": {"stop_price": 91.0}})
        self.assertAlmostEqual(eng.st["plans"]["S1"]["stop"], 90.0)   # 예전부터 들고 있던 종목: 10%
        self.market.prices.update(S1=93.0, S4=93.0)
        eng.last_q = self.market.quotes()
        # 떨어진 김에 둘 다 비중을 늘린다 (물타기)
        self.go(eng, self.result([act("S1", 0.1), act("S4", 0.1)], ref={"S1": 93.0, "S4": 93.0}))
        ai = eng.st["players"]["ai"]
        self.assertLess(ai["positions"]["S1"]["avg"], 100.0)
        plans = eng.st["plans"]
        self.assertAlmostEqual(plans["S1"]["stop"], 90.0)            # 평단이 내려가도 손절가는 그대로
        self.assertEqual(plans["S4"]["stop"], 91.0)                  # AI가 정한 손절가도 그대로

    def test_plan_only_update_does_not_trade(self):
        eng = self.prepared()
        before = len(eng.st["players"]["ai"]["trades"])
        self.go(eng, self.result([act("S1", None, stop_price=97.0, take_price=120.0, take_frac=0.3)]))
        self.assertEqual(len(eng.st["players"]["ai"]["trades"]), before)
        plan = eng.st["plans"]["S1"]
        self.assertEqual((plan["stop"], plan["take"], plan["take_frac"]), (97.0, 120.0, 0.3))
        self.assertNotIn("default", plan)
        rec = eng.st["reactions"][-1]
        self.assertIsNone(rec["actions"][0]["to"])
        self.assertIn("계획만", [e for e in eng.feed if e["kind"] == "react"][-1]["text"])

    def test_bad_plan_values_are_refused_with_a_note(self):
        eng = self.prepared()
        self.go(eng, self.result([act("S1", None, stop_price=150.0), act("S2", None, stop_price=90.0)]))
        notes = eng.st["reactions"][-1]["notes"]
        self.assertTrue(any("지금 가격" in n for n in notes))       # 지금 가격보다 높은 손절
        self.assertTrue(any("들고 있지 않은" in n for n in notes))  # 안 가진 종목의 계획만 변경
        self.assertTrue(eng.st["plans"]["S1"]["default"])

    def test_never_exceeds_cash_or_limits(self):
        codes = [f"S{i}" for i in range(1, 15)]
        self.market = Market(codes)
        eng = self.prepared({"S1": (250_000, 100.0)}, shortlist=codes[1:])   # 이미 25% 보유
        actions = [act(c, 0.30, stop_pct=0.07) for c in codes[1:]]
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
        plan_before = dict(eng.st["plans"]["S4"])
        self.go(eng, self.result([act("S2", 0.1, stop_pct=0.07)], ref={"S2": 100.0}))
        self.assertEqual(eng.st["players"]["ai"]["positions"]["S4"], before)
        self.assertEqual(eng.st["plans"]["S4"], plan_before)

    def test_daily_trade_limit_blocks_new_buys_only(self):
        eng = self.prepared()
        eng.counts["trades"] = LIVE["max_live_trades_per_day"]
        self.go(eng, self.result([act("S2", 0.2, stop_pct=0.07), act("S1", 0.0)]))
        ai = eng.st["players"]["ai"]
        self.assertNotIn("S2", ai["positions"])
        self.assertNotIn("S1", ai["positions"])                     # 팔기는 허용


class RulesTest(EngineCase):
    """장중 매매 제한: 같은 날 되사기, 최소 보유, 추격, 가격 이탈, 늦은 답, 한도, 마감·장 시작 시간, 상·하한가."""

    def prepared(self, positions=None, shortlist=("S2", "S3"), ticks=2):
        self.seed(positions or {"S1": (100_000, 100.0)}, shortlist=shortlist)
        eng = self.engine()
        self.run_ticks(eng, ticks)
        return eng

    def result(self, actions, ref=None, **extra):
        ref = ref or {c: 100.0 for c in ("S1", "S2", "S3", "S4", "S5")}
        return {"events": [{"text": "테스트", "kind": "heartbeat"}], "actions": actions, "ref_prices": ref,
                "assessment": "a", "seconds": 1.0, "ctx_at": self.market.now.isoformat(), **extra}

    def go(self, eng, res):
        eng.last_q = self.market.quotes()
        eng._execute_react(res, eng.last_q, self.market.now, self.market.now.astimezone(eng.tz))
        return eng.st["reactions"][-1]["notes"]

    def held(self, eng):
        return {c: p["shares"] for c, p in eng.st["players"]["ai"]["positions"].items()}

    def test_answer_after_a_stop_does_not_buy_back(self):
        self.seed({"S1": (100_000, 100.0)}, plans={"S1": {"stop": 92.0}})
        eng = self.engine()
        self.run_ticks(eng, 3)
        asked_at = self.market.now.isoformat()                         # AI가 95원을 보면서 생각하기 시작
        self.market.prices["S1"] = 91.0
        self.run_ticks(eng, 2)                                        # 생각하는 동안 손절 (두 번 확인하고 1분 뒤)
        self.assertNotIn("S1", self.held(eng))
        notes = self.go(eng, self.result([act("S1", 0.15, reason="눌림목")], ref={"S1": 95.0}, ctx_at=asked_at))
        self.assertNotIn("S1", self.held(eng))
        self.assertTrue(any("생각하는 동안" in n for n in notes))
        notes = self.go(eng, self.result([act("S1", 0.15)], ref={"S1": 91.0}))   # 새로 물어봐도 오늘은 안 산다
        self.assertNotIn("S1", self.held(eng))
        self.assertTrue(any("오늘 판 종목" in n for n in notes))

    def test_fresh_buy_cannot_be_sold_by_the_ai_but_stop_still_works(self):
        eng = self.prepared()
        self.go(eng, self.result([act("S2", 0.1, stop_price=95.0)]))
        self.assertIn("S2", self.held(eng))
        self.run_ticks(eng, 10)
        notes = self.go(eng, self.result([act("S2", 0.0, reason="마음이 바뀜")]))
        self.assertIn("S2", self.held(eng))
        self.assertTrue(any("분밖에 안 돼서" in n for n in notes))
        self.market.prices["S2"] = 94.0
        self.run_ticks(eng, 2)
        self.assertNotIn("S2", self.held(eng))                        # 손절은 막지 않는다
        self.assertEqual(eng.st["players"]["ai"]["trades"][-1]["tag"], "stop")

    def test_ai_can_sell_after_the_minimum_hold(self):
        eng = self.prepared()
        self.go(eng, self.result([act("S2", 0.1)]))
        self.run_ticks(eng, 31)
        self.go(eng, self.result([act("S2", 0.0)]))
        self.assertNotIn("S2", self.held(eng))

    def test_only_holdings_and_researched_candidates_can_be_bought(self):
        eng = self.prepared()
        notes = self.go(eng, self.result([act("S5", 0.1, reason="오늘 제일 많이 오름")]))
        self.assertNotIn("S5", self.held(eng))
        self.assertTrue(any("추격매수" in n for n in notes))

    def test_price_drift_skips_trades_and_asks_again_for_sells(self):
        eng = self.prepared()
        self.market.prices.update(S1=96.5, S2=97.0)                   # AI가 볼 때는 둘 다 100
        notes = self.go(eng, self.result([act("S1", 0.0), act("S2", 0.1)]))
        self.assertIn("S1", self.held(eng))
        self.assertNotIn("S2", self.held(eng))
        self.assertTrue(any("매도는 다시 물어볼게요" in n for n in notes) and any("내려서 매수는 건너뜀" in n for n in notes))
        retry = [e for e in eng.events if e["kind"] == "retry"]
        self.assertEqual(len(retry), 1)
        self.go(eng, self.result([act("S1", 0.0)]))                   # 또 이탈해도 다시 묻는 건 하루 한 번
        self.assertEqual(len([e for e in eng.events if e["kind"] == "retry"]), 1)

    def test_late_answer_keeps_plans_but_skips_trades(self):
        eng = self.prepared()
        old = (self.market.now - timedelta(minutes=6)).isoformat()
        notes = self.go(eng, self.result([act("S1", 0.0, stop_price=96.0)], ctx_at=old))
        self.assertIn("S1", self.held(eng))
        self.assertEqual(eng.st["plans"]["S1"]["stop"], 96.0)
        self.assertTrue(any("늦게 와서" in n for n in notes))
        self.assertEqual([e["kind"] for e in eng.events], ["retry"])

    def test_intraday_buying_is_capped_at_30_percent_a_day(self):
        eng = self.prepared(positions={"S1": (10_000, 100.0)})
        self.go(eng, self.result([act("S2", 0.2)]))
        notes = self.go(eng, self.result([act("S3", 0.2)]))
        day = self.market.now.astimezone(eng.tz).date().isoformat()
        bought = eng._bought_today(day)
        self.assertLessEqual(bought, 0.30 * CAP * 1.001)
        self.assertGreater(bought, 0.29 * CAP)
        self.assertTrue(any("매수 한도" in n for n in notes))
        self.go(eng, self.result([act("S3", 0.25)]))
        self.assertAlmostEqual(eng._bought_today(day), bought)       # 다 쓰면 더는 안 산다

    def test_sector_cap_limits_buys(self):
        eng = self.prepared(positions={"S1": (300_000, 100.0)})       # 30%
        eng.st["sectors"] = {"S1": "반도체", "S2": "반도체", "S3": "은행"}
        notes = self.go(eng, self.result([act("S2", 0.2), act("S3", 0.1)]))
        ai = eng.st["players"]["ai"]
        eq = broker.equity(ai, {c: 100.0 for c in ai["positions"]})
        semis = sum(ai["positions"][c]["shares"] * 100.0 for c in ("S1", "S2")) / eq
        self.assertLessEqual(semis, 0.40 + 0.002)
        self.assertAlmostEqual(ai["positions"]["S3"]["shares"] * 100.0 / eq, 0.1, places=2)
        self.assertTrue(any("반도체" in n for n in notes))

    def test_daily_loss_limit_blocks_new_buys(self):
        eng = self.prepared()
        eng.st["players"]["ai"]["history"][-1]["equity"] = CAP * 1.05   # 어제보다 5% 잃은 상태
        notes = self.go(eng, self.result([act("S2", 0.1), act("S1", 0.05)]))
        self.assertNotIn("S2", self.held(eng))
        self.assertLess(self.held(eng)["S1"], 100_000)                 # 줄이는 건 된다
        self.assertTrue(any("손실 한도" in n for n in notes))

    def test_no_ai_trades_just_before_the_close(self):
        eng = self.prepared()
        self.market.now = kst(15, 22)
        notes = self.go(eng, self.result([act("S1", 0.0, stop_price=97.0)]))
        self.assertIn("S1", self.held(eng))
        self.assertEqual(eng.st["plans"]["S1"]["stop"], 97.0)
        self.assertTrue(any("마감 10분 전" in n for n in notes))

    def test_no_new_buys_in_the_first_15_minutes(self):
        self.seed({"S1": (100_000, 100.0)}, shortlist=("S2",))
        eng = self.engine()
        self.market.now = kst(8, 59)
        self.market.status = "CLOSE"
        self.run_ticks(eng, 1)
        self.market.status = "OPEN"
        self.run_ticks(eng, 5)                                        # 09:05
        notes = self.go(eng, self.result([act("S2", 0.1), act("S1", 0.05)]))
        self.assertNotIn("S2", self.held(eng))
        self.assertLess(self.held(eng)["S1"], 100_000)
        self.assertTrue(any("15분은 새로 안 사요" in n for n in notes))

    def test_limit_up_and_down(self):
        self.seed({"S1": (100_000, 100.0)}, plans={"S1": {"stop": 92.0}}, shortlist=("S2",))
        base = self.market.quotes
        limits = {}
        self.market.quotes = lambda: {c: {**v, "limit": limits.get(c)} for c, v in base().items()}
        eng = self.engine()
        self.run_ticks(eng, 2)
        limits["S2"] = "up"
        notes = self.go(eng, self.result([act("S2", 0.1)]))
        self.assertTrue(any("상한가" in n for n in notes))
        limits["S1"] = "down"
        self.market.prices["S1"] = 70.0
        self.run_ticks(eng, 3)
        self.assertIn("S1", self.held(eng))                           # 하한가에서는 손절이 체결되지 않는다
        limits.pop("S1")
        self.market.prices["S1"] = 72.0
        self.run_ticks(eng, 1)
        self.assertNotIn("S1", self.held(eng))                        # 풀리자마자 판다

    def test_stop_far_through_sells_on_the_first_tick(self):
        self.seed({"S1": (100_000, 100.0)}, plans={"S1": {"stop": 92.0}})
        eng = self.engine()
        self.run_ticks(eng, 2)
        self.market.prices["S1"] = 89.0                               # 92의 2% 아래보다 더 깊다
        self.run_ticks(eng, 1)
        self.assertNotIn("S1", self.held(eng))

    def test_overflowing_events_drop_news_first(self):
        eng = self.prepared()
        now, local = self.market.now, self.market.now.astimezone(eng.tz)
        eng.events = []
        eng._add_event(now, local, "stop", "손절", code="S1")
        for i in range(40):
            eng._add_event(now, local, "news", f"기사 {i}", code="S1", quiet=True)
        self.assertEqual(len(eng.events), 30)
        self.assertEqual(eng.events[0]["kind"], "stop")

    def test_thesis_is_a_buy_reason(self):
        eng = self.prepared()
        eng.st["reactions"] = [
            {"actions": [{"code": "S1", "from": 0.0, "to": 0.1, "reason": "실적이 좋아서 샀다"}]},
            {"actions": [{"code": "S1", "from": 0.1, "to": 0.05, "reason": "절반 익절"}]},
            {"actions": [{"code": "S1", "from": 0.05, "to": None, "reason": "손절만 올림"}]},
        ]
        self.assertEqual(eng._thesis("S1"), "실적이 좋아서 샀다")


class RestartAndSplitTest(EngineCase):
    def test_restart_picks_up_the_day(self):
        self.seed({"S1": (100_000, 100.0)})
        b1 = FakeBrain(reply={"recheck": ["S1"]})
        with mock.patch.dict(LIVE, {"open_review": True}):
            e1 = self.engine(b1)
            self.run_ticks(e1, 17)                                    # 장 시작 점검(09:15 이후 바로)
            self.market.prices["S1"] = 104.0                          # 오늘 +4%
            self.run_ticks(e1, 8)
            self.news_items = [{"code": "S1", "key": "n1", "when": self.market.now - timedelta(minutes=1),
                                "title": "A사 매각설", "source": "x"}]
            e1.last_news_at = None
            self.run_ticks(e1, 3)
            kinds_before = [self.kinds(c) for c in b1.react_calls]
            self.assertEqual(list(e1.followups), ["S1"])
            b2 = FakeBrain()
            e2 = self.engine(b2)                                      # 같은 날 다시 켰다
            self.run_ticks(e2, 3)
        self.assertEqual([k for k in kinds_before if "open" in k], [["open"]])
        self.assertEqual(b2.react_calls, [])                          # 장 시작 점검·오늘 등락을 다시 안 한다
        self.assertEqual(list(e2.followups), ["S1"])                  # 뉴스 다시 보기 예약도 이어진다
        self.assertIsNotNone(e2.hist.ret("S1", 900, self.market.now))   # 15분 시세 기록도 있다
        self.assertTrue(e2.open_reviewed)

    def split_engine(self, stored_date="2026-09-29"):
        self.seed({"S1": (10_000, 1000.0)}, plans={"S1": {"stop": 900.0}})
        st = league.load_state("kr")
        st["closes"] = {"S1": {"date": stored_date, "close": 1000.0}}
        league.save_state("kr", st)
        days = [(date(2026, 9, 29) - timedelta(days=39 - i)).isoformat() for i in range(40)]
        adjusted = [{"date": d, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1e6} for d in days]
        return self.engine(bars=lambda codes: {c: adjusted for c in codes})

    def test_split_is_applied_before_stops(self):
        eng = self.split_engine()                                     # 어제 1,000원 → 오늘 10:1 분할로 100원
        self.run_ticks(eng, 3)
        ai = eng.st["players"]["ai"]
        self.assertEqual(ai["positions"]["S1"]["shares"], 100_000)
        self.assertAlmostEqual(ai["positions"]["S1"]["avg"], 100.0)
        self.assertAlmostEqual(eng.st["plans"]["S1"]["stop"], 90.0)
        self.assertEqual(ai["trades"], [])                            # 900원 손절이 100원에 나가지 않는다
        self.assertTrue(any("분할" in e["text"] for e in eng.feed))

    def test_unexplained_jump_pauses_stops(self):
        eng = self.split_engine(stored_date="2026-09-01")             # 적어 둔 날짜가 오래돼서 분할을 확정할 수 없다
        eng.st["closes"]["S1"]["date"] = "2026-08-01"
        self.run_ticks(eng, 3)
        ai = eng.st["players"]["ai"]
        self.assertEqual(ai["trades"], [])
        self.assertEqual(len([e for e in eng.feed if "손절을 멈춰요" in e["text"]]), 1)

    def test_open_price_from_elsewhere_when_the_bar_is_late(self):
        pend = {"decided_on": "2026-09-29", "targets": {"ai": {"S2": 0.2, "S3": 0.1}, "monkey": None, "hodl": None}}
        self.seed(pending=pend, shortlist=("S2", "S3"))
        today = {"date": "2026-09-30", "open": 100.0, "high": 101, "low": 99, "close": 100.5, "volume": 1e6}
        opens = {"S3": 101.0}
        eng = self.engine(bars=lambda codes: {c: bars_for() + ([today] if c != "S3" else []) for c in codes},
                          opens=lambda codes: {c: opens.get(c) for c in codes})
        self.run_ticks(eng, 12)
        ai = eng.st["players"]["ai"]
        self.assertEqual(set(ai["positions"]), {"S2", "S3"})
        s3 = [t for t in ai["trades"] if t["code"] == "S3"][0]
        self.assertAlmostEqual(s3["price"], 101.0 * (1 + SLIPPAGE["kr"]))

    def test_unfilled_orders_are_reported(self):
        pend = {"decided_on": "2026-09-29", "targets": {"ai": {"S2": 0.2, "S3": 0.1}, "monkey": None, "hodl": None}}
        self.seed(pending=pend, shortlist=("S2", "S3"))
        st = league.load_state("kr")
        st["journal"].append({"date": "2026-09-29", "targets": []})
        league.save_state("kr", st)
        today = {"date": "2026-09-30", "open": 100.0, "high": 101, "low": 99, "close": 100.5, "volume": 1e6}
        eng = self.engine(bars=lambda codes: {c: bars_for() + ([today] if c != "S3" else []) for c in codes})
        self.run_ticks(eng, 12)
        self.assertEqual(set(eng.st["players"]["ai"]["positions"]), {"S2"})
        self.assertEqual([u["code"] for u in eng.st["journal"][-1]["unfilled"]], ["S3"])
        self.assertTrue(any("못 산 종목" in e["text"] for e in eng.feed))


class AlertAndPacingTest(EngineCase):
    def test_alert_wakes_ai_with_its_note_then_disappears(self):
        self.seed({"S1": (50_000, 100.0)})
        alerts = [{"code": "S2", "below": 95.0, "above": None, "note": "여기 오면 절반 더 살지 검토"}]
        calls = []

        def respond(ctx, events):
            calls.append(events)
            if len(calls) == 1:
                return {"alerts": alerts, "next_check_min": 120}
            return {"actions": [act("S2", 0.05, stop_pct=0.05, reason="알림대로 조금 담기")]}

        brain_ = FakeBrain(respond=respond)
        eng = self.engine(brain_)
        self.open_and_check_now(eng)
        self.run_ticks(eng, 2)                      # 점검하고, 다음 확인 때 답을 반영
        self.assertEqual([(a["code"], a["below"], a["note"], a["name"]) for a in eng.st["alerts"]],
                         [("S2", 95.0, "여기 오면 절반 더 살지 검토", "종목S2")])
        self.run_ticks(eng, 10)
        self.assertEqual(len(calls), 1)             # 알림 가격 전에는 조용
        self.market.prices["S2"] = 94.0
        self.run_ticks(eng, 1)
        self.assertEqual(len(calls), 2)
        ev = calls[1][0]
        self.assertEqual(ev["kind"], "alert")
        self.assertIn("절반 더 살지", ev["text"])
        self.assertEqual(eng.st["alerts"], [])      # 한 번 울리면 사라진다
        self.run_ticks(eng, 1)                      # 답을 반영: 알림대로 조금 담는다
        self.assertIn("S2", eng.st["players"]["ai"]["positions"])
        self.assertAlmostEqual(eng.st["plans"]["S2"]["stop"], eng.st["players"]["ai"]["positions"]["S2"]["avg"] * 0.95)

    def test_alerts_are_kept_when_ai_does_not_mention_them(self):
        alerts = [{"code": "S2", "below": 95.0, "above": None, "note": ""}]
        self.seed({"S1": (50_000, 100.0)}, alerts=alerts)
        brain_ = FakeBrain()
        eng = self.engine(brain_)
        self.open_and_check_now(eng)
        self.run_ticks(eng, 1)
        self.assertEqual(len(brain_.react_calls), 1)
        self.assertEqual(eng.st["alerts"], alerts)

    def test_ai_chooses_when_to_look_again(self):
        self.seed({"S1": (50_000, 100.0)})
        brain_ = FakeBrain(reply={"next_check_min": 20})
        eng = self.engine(brain_)
        self.open_and_check_now(eng)
        self.run_ticks(eng, 1)
        self.assertEqual(len(brain_.react_calls), 1)
        self.run_ticks(eng, 19)
        self.assertEqual(len(brain_.react_calls), 1)
        self.run_ticks(eng, 2)
        self.assertEqual(len(brain_.react_calls), 2)
        self.assertEqual(brain_.react_calls[1][1][0]["kind"], "heartbeat")

    def test_budget_is_shown_to_the_ai(self):
        self.seed({"S1": (50_000, 100.0)})
        brain_ = FakeBrain()
        eng = self.engine(brain_)
        self.open_and_check_now(eng)
        eng.counts["reviews"] = 5
        self.run_ticks(eng, 1)
        self.assertEqual(brain_.react_calls[0][0]["reviews_left"], LIVE["max_reviews_per_day"] - 6)

    def test_open_review_once_after_the_open_fill(self):
        pend = {"decided_on": "2026-09-29", "targets": {"ai": {"S2": 0.3}, "monkey": None, "hodl": None}}
        self.seed(pending=pend)
        state = {"bars": [], "day": 0}
        today_bar = {"date": "2026-09-30", "open": 100.0, "high": 101, "low": 99, "close": 100.5, "volume": 1e6}
        brain_ = FakeBrain()
        with mock.patch.dict(LIVE, {"open_review": True}):
            eng = self.engine(brain_, bars=lambda codes: {c: bars_for() + state["bars"] for c in codes})
            self.run_ticks(eng, 3)
            self.assertEqual(brain_.react_calls, [])         # 시가 체결 전에는 기다린다
            state["bars"] = [today_bar]
            self.run_ticks(eng, 3)
            self.assertEqual(len(brain_.react_calls), 1)
            self.assertEqual(brain_.react_calls[0][1][0]["kind"], "open")
            self.assertIn("S2", brain_.react_calls[0][0]["allowed"])
            self.run_ticks(eng, 30)
            self.assertEqual(len(brain_.react_calls), 1)     # 하루에 한 번


class SessionFlowTest(EngineCase):
    def test_pending_order_fills_at_open_with_slippage_and_full_plan(self):
        pend = {"decided_on": "2026-09-29", "targets": {"ai": {"S2": 0.3}, "monkey": None, "hodl": None},
                "plans": {"S2": planlib.parse_update({"stop_pct": 0.07, "take_pct": 0.2, "take_frac": 0.5, "trail_pct": 0.1})}}
        self.seed(pending=pend)
        today_bar = {"date": "2026-09-30", "open": 100.0, "high": 101, "low": 99, "close": 100.5, "volume": 1e6}
        eng = self.engine(bars=lambda codes: {c: bars_for() + [today_bar] for c in codes})
        self.run_ticks(eng, 2)
        ai = eng.st["players"]["ai"]
        self.assertIn("S2", ai["positions"])
        self.assertAlmostEqual(ai["trades"][0]["price"], 100.0 * (1 + SLIPPAGE["kr"]))
        self.assertEqual(ai["trades"][0]["tag"], "open")
        plan = eng.st["plans"]["S2"]
        self.assertAlmostEqual(plan["stop"], BUY_AVG * 0.93)
        self.assertAlmostEqual(plan["take"], BUY_AVG * 1.2)
        self.assertEqual((plan["take_frac"], plan["trail_pct"]), (0.5, 0.1))
        self.assertIsNone(eng.st["pending"])

    def test_old_style_pending_plans_still_work(self):
        pend = {"decided_on": "2026-09-29", "targets": {"ai": {"S2": 0.3}, "monkey": None, "hodl": None},
                "plans": {"S2": {"stop_pct": 0.07, "take_pct": None}}}
        self.seed(pending=pend)
        today_bar = {"date": "2026-09-30", "open": 100.0, "high": 101, "low": 99, "close": 100.5, "volume": 1e6}
        eng = self.engine(bars=lambda codes: {c: bars_for() + [today_bar] for c in codes})
        self.run_ticks(eng, 2)
        plan = eng.st["plans"]["S2"]
        self.assertAlmostEqual(plan["stop"], BUY_AVG * 0.93)
        self.assertNotIn("take", plan)

    def test_pending_waits_for_open_bar(self):
        pend = {"decided_on": "2026-09-29", "targets": {"ai": {"S2": 0.3}, "monkey": None, "hodl": None}}
        self.seed(pending=pend)
        eng = self.engine()                                            # 오늘 봉이 아직 없다
        self.run_ticks(eng, 2)
        self.assertIsNotNone(eng.st["pending"])
        self.assertEqual(eng.st["players"]["ai"]["positions"], {})

    def daily_ok(self, market, now=None, log=print):
        """진짜 정산처럼 오늘 결정을 남기는 가짜 하루 정산."""
        self.daily_calls.append(now)
        st = league.load_state(market)
        st["last_decided"] = now.astimezone(live.ZoneInfo("Asia/Seoul")).date().isoformat()
        league.save_state(market, st)

    def test_daily_settlement_runs_once_after_close(self):
        self.seed()
        eng = self.engine(daily=self.daily_ok)
        eng.tick(kst(15, 40))
        self.assertEqual(self.daily_calls, [])                         # 아직 정산 대기 시간
        eng.tick(kst(15, 55))
        eng.tick(kst(16, 5))
        eng.tick(kst(16, 30))
        self.assertEqual(len(self.daily_calls), 1)

    def test_daily_settlement_retries_while_todays_bar_is_missing(self):
        self.seed()
        eng = self.engine()                                           # 가짜 정산은 오늘 결정을 못 남긴다 (봉이 늦음)
        self.run_ticks(eng, 2)                                        # 오늘 장이 열린 걸 봤다
        self.market.status = "CLOSE"
        for m in (31, 32, 33):                                        # 세 번 연속 닫힘: 15:33 정규장 끝
            eng.tick(kst(15, m))
        for h, m in ((15, 55), (16, 0), (16, 6), (16, 17)):
            eng.tick(kst(h, m))
        self.assertEqual(len(self.daily_calls), 3)                    # 10분마다 다시
        self.assertIn("아직 안 올라와서", [e for e in eng.feed if e["kind"] == "info"][-1]["text"])
        eng.tick(kst(19, 40))                                         # 마감 4시간 뒤: 포기하고 알린다
        eng.tick(kst(20, 0))
        self.assertEqual(len(self.daily_calls), 4)
        self.assertTrue(any("끝내 안 올라와서" in e["text"] for e in eng.feed))

    def test_holiday_needs_hours_of_closed_quotes(self):
        self.seed()
        self.market.status = "CLOSE"
        calls = []
        eng = self.engine(quotes=lambda: calls.append(1) or self.market.quotes())
        self.market.now = kst(9, 5)
        self.run_ticks(eng, 10, step=600)                             # 10:45까지: 늦게 여는 날일 수도 있으니 기다린다
        self.assertFalse(eng.holiday)
        self.run_ticks(eng, 10, step=600)                             # 12시 넘음: 휴장일
        self.assertTrue(eng.holiday)
        n = len(calls)
        self.run_ticks(eng, 3)
        self.assertEqual(len(calls), n)

    def test_late_open_starts_the_quiet_period_when_the_market_opens(self):
        self.seed({"S1": (100_000, 100.0)}, shortlist=("S2",))
        self.market.status = "CLOSE"
        brain_ = FakeBrain(actions=[act("S2", 0.1, reason="살래")])
        with mock.patch.dict(LIVE, {"open_review": True}):
            eng = self.engine(brain_)
            self.market.now = kst(9, 0)
            self.run_ticks(eng, 6, step=600)                          # 10:00까지 닫혀 있다 (새해 첫날·수능일)
            self.market.status = "OPEN"
            self.run_ticks(eng, 10)
            self.assertEqual(eng.session_start, kst(10, 0))
            self.assertEqual(brain_.react_calls, [])                  # 장 시작 점검은 15분 뒤
            self.run_ticks(eng, 8)
        self.assertEqual(brain_.react_calls[0][1][0]["kind"], "open")
        self.assertIn("S2", eng.st["players"]["ai"]["positions"])     # 조용한 시간 뒤라 살 수 있다

    def test_moves_in_the_quiet_minutes_wait_for_the_open_review(self):
        self.seed({"S1": (100_000, 100.0)})
        brain_ = FakeBrain()
        with mock.patch.dict(LIVE, {"open_review": True}):
            eng = self.engine(brain_)
            self.market.now = kst(9, 0)
            self.run_ticks(eng, 3)
            self.market.prices["S1"] = 96.0                           # 장 시작 3분 만에 -4%
            self.run_ticks(eng, 8)
            self.assertEqual(brain_.react_calls, [])                  # 시끄러운 시간이라 모아만 둔다
            self.run_ticks(eng, 6)                                    # 09:15가 지나면 한 번에 본다
        self.assertEqual(len(brain_.react_calls), 1)
        kinds = self.kinds(brain_.react_calls[0])
        self.assertEqual(kinds[0], "open")                            # 급한 사건이 앞에 온다
        self.assertIn("fast_move", kinds)
        self.assertIn("day_step", kinds)

    def test_late_close_keeps_watching_until_the_index_closes(self):
        self.seed({"S1": (100_000, 100.0)})
        calls = []
        eng = self.engine(daily=self.daily_ok, quotes=lambda: calls.append(1) or self.market.quotes())
        self.market.now = kst(15, 20)
        self.run_ticks(eng, 60)                                       # 16:20까지 지수가 계속 열려 있다 (수능일)
        n = len(calls)
        self.assertGreater(n, 55)
        self.assertEqual(self.daily_calls, [])
        self.market.status = "CLOSE"
        self.run_ticks(eng, 4)                                        # 16:24 정규장 끝
        self.assertEqual(eng.session_end, kst(16, 22))
        for h, m in ((16, 30), (16, 41)):
            eng.tick(kst(h, m))
        self.assertEqual(self.daily_calls, [])                        # 끝나고 20분은 기다린다
        eng.tick(kst(16, 43))
        self.assertEqual(len(self.daily_calls), 1)

    def test_after_hours_stock_quotes_do_not_open_the_session(self):
        self.seed()
        eng = self.engine()
        q = self.market.quotes()
        q["069500"]["status"] = "CLOSE"                                # 넥스트레이드 시간외: 종목은 OPEN, ETF는 CLOSE
        self.assertFalse(eng._market_open(q))
        q["069500"]["status"] = "OPEN"
        self.assertTrue(eng._market_open(q))

    def test_snapshot_files_for_dashboard(self):
        self.seed({"S1": (50_000, 100.0)}, alerts=[{"code": "S2", "below": 90.0, "above": None, "note": ""}])
        eng = self.engine()
        self.run_ticks(eng, 3)
        snap = json.loads((live.RUNTIME / "live_kr.json").read_text(encoding="utf-8"))
        self.assertEqual(snap["phase"], "open")
        self.assertEqual(set(snap["players"]), {"ai", "monkey", "hodl"})
        self.assertEqual(snap["alerts"][0]["code"], "S2")
        self.assertTrue(snap["next_check_at"])
        self.assertTrue((live.RUNTIME / "intraday_kr_2026-09-30.jsonl").exists())
        self.assertTrue((live.RUNTIME / "feed_kr.jsonl").exists())


class NewsSafetyTest(EngineCase):
    """기사 하나만 보고는 사고팔지 않는다. 주가 반응을 확인한 뒤에야 매매한다."""

    def news(self, code, title, minutes_ago=1, source="한경", key=None):
        return {"code": code, "key": key or f"{code}:{title}", "when": self.market.now - timedelta(minutes=minutes_ago),
                "title": title, "source": source}

    def test_headline_alone_cannot_trade_then_price_reaction_decides(self):
        self.seed({"S1": (100_000, 100.0)})

        def respond(ctx, events):
            kinds = {e["kind"] for e in events}
            if kinds == {"news"}:            # 기사만 보고 전량 매도 + 손절을 바짝 붙이려 한다
                return {"actions": [act("S1", 0.0, stop_price=99.5, reason="매각설이 악재")]}
            if "news_followup" in kinds:     # 15분 뒤 주가도 빠졌으면 그때 판다
                return {"actions": [act("S1", 0.0, reason="주가도 같이 빠져서 정리")]}
            return {}

        brain_ = FakeBrain(respond=respond)
        eng = self.engine(brain_)
        self.news_items = [self.news("S1", "A사, 경영권 매각설")]
        self.run_ticks(eng, 2)                       # 뉴스 점검, 다음 확인 때 답을 반영
        ai = eng.st["players"]["ai"]
        self.assertEqual(brain_.modes, ["news"])
        self.assertEqual(ai["positions"]["S1"]["shares"], 100_000)   # 기사만으로는 안 판다
        self.assertEqual(ai["trades"], [])
        self.assertAlmostEqual(eng.st["plans"]["S1"]["stop"], 98.0)   # 99.5는 너무 붙어서 2% 아래로
        rec = eng.st["reactions"][-1]
        self.assertEqual((rec["mode"], rec["blocked"], [r["code"] for r in rec["recheck"]]), ("news", ["종목S1"], ["S1"]))
        self.assertTrue(any("기사만 보고는" in n for n in rec["notes"]))
        self.assertTrue(any("너무 붙어서" in n for n in rec["notes"]))
        self.assertIn("AI 뉴스 점검", [e for e in eng.feed if e["kind"] == "react"][-1]["text"])
        snap = json.loads((live.RUNTIME / "live_kr.json").read_text(encoding="utf-8"))
        self.assertEqual([f["code"] for f in snap["followups"]], ["S1"])

        self.market.prices["S1"] = 99.0              # 조금 밀린다 (급변도 손절도 아님)
        self.run_ticks(eng, 13)
        self.assertEqual(len(brain_.react_calls), 1)  # 15분이 되기 전에는 다시 안 부른다
        self.run_ticks(eng, 3)
        self.assertEqual(brain_.modes, ["news", "trade"])
        ev = brain_.react_calls[1][1][0]
        self.assertEqual((ev["kind"], ev["code"]), ("news_followup", "S1"))
        self.assertIn("기사 뒤로 주가 -1.0%", ev["text"])
        self.assertIn("같은 동안 시장 +0.0%", ev["text"])
        self.assertIn("매각설", ev["text"])
        self.assertNotIn("S1", ai["positions"])       # 주가 반응을 본 점검에서는 판다
        self.assertEqual(ai["trades"][-1]["tag"], "react")
        self.assertEqual(eng.followups, {})

    def test_ai_can_ask_to_recheck_without_trading(self):
        self.seed({"S1": (100_000, 100.0)})
        brain_ = FakeBrain(reply={"recheck": ["S1"], "next_check_min": 60})
        eng = self.engine(brain_)
        self.news_items = [self.news("S1", "A사 신사업 진출 검토")]
        self.run_ticks(eng, 2)
        self.assertEqual(list(eng.followups), ["S1"])
        self.assertEqual(eng.st["reactions"][-1]["blocked"], [])
        self.run_ticks(eng, 16)
        self.assertEqual(brain_.modes, ["news", "trade"])
        self.assertEqual(brain_.react_calls[1][1][0]["kind"], "news_followup")

    def test_price_move_brings_the_pending_news_check_forward(self):
        self.seed({"S1": (100_000, 100.0)})
        brain_ = FakeBrain(reply={"recheck": ["S1"]})
        eng = self.engine(brain_)
        self.run_ticks(eng, 16)
        self.news_items = [self.news("S1", "A사 매각설")]
        eng.last_news_at = None
        self.run_ticks(eng, 7)
        self.assertEqual((brain_.modes, list(eng.followups)), (["news"], ["S1"]))
        self.market.prices["S1"] = 96.0              # 확인 시간 전에 주가가 먼저 크게 움직였다
        self.run_ticks(eng, 1)
        self.assertEqual(brain_.modes, ["news", "trade"])
        kinds = self.kinds(brain_.react_calls[1])
        self.assertIn("fast_move", kinds)
        self.assertIn("news_followup", kinds)        # 그 뉴스를 같이 보여준다
        self.assertEqual(eng.followups, {})
        self.run_ticks(eng, 15)
        self.assertEqual(brain_.modes, ["news", "trade"])   # 15분이 돼도 또 부르지 않는다

    def test_price_event_brings_its_own_news_into_a_trade_review(self):
        self.seed({"S1": (100_000, 100.0), "S4": (50_000, 100.0)})
        brain_ = FakeBrain()
        eng = self.engine(brain_)
        self.run_ticks(eng, 20)
        self.assertEqual(brain_.react_calls, [])
        self.market.prices["S1"] = 96.0              # 주가가 먼저 움직였다
        self.news_items = [self.news("S1", "A사 실적 쇼크"), self.news("S4", "B사 합병설")]
        eng.last_news_at = None
        self.run_ticks(eng, 1)
        self.assertEqual(brain_.modes, ["trade"])
        batch = brain_.react_calls[0][1]
        self.assertEqual({e["code"] for e in batch}, {"S1"})
        self.assertIn("news", self.kinds(brain_.react_calls[0]))   # 주가가 확인해준 기사는 같이 본다
        self.assertEqual([(e["kind"], e["code"]) for e in eng.events], [("news", "S4")])
        self.run_ticks(eng, 1)                       # 남은 기사는 따로 뉴스 점검
        self.assertEqual(brain_.modes, ["trade", "news"])
        self.assertEqual([e["code"] for e in brain_.react_calls[1][1]], ["S4"])

    def test_market_news_is_rechecked_on_the_index(self):
        self.seed({"S1": (100_000, 100.0)})

        def respond(ctx, events):
            if {e["kind"] for e in events} == {"news"}:
                return {"actions": [act("S1", 0.0, reason="시장이 무너질 것 같아")]}
            return {}

        brain_ = FakeBrain(respond=respond)
        eng = self.engine(brain_)
        self.news_items = [self.news("MAIN", "美 연준, 긴급 금리 인상 가능성")]
        self.run_ticks(eng, 2)
        self.assertIn("S1", eng.st["players"]["ai"]["positions"])
        self.assertEqual(list(eng.followups), ["069500"])
        self.assertEqual(eng.followups["069500"]["name"], "KODEX 200")
        news_ev = brain_.react_calls[0][1][0]
        self.assertTrue(news_ev["data"]["rumor"])
        self.assertNotIn("articles_1h", news_ev["data"])   # 시장 피드는 기사마다 다른 이야기라 안 센다
        self.market.bench_price = 99.7
        self.run_ticks(eng, 16)
        ev = brain_.react_calls[1][1][0]
        self.assertEqual((ev["kind"], ev["code"]), ("news_followup", "069500"))
        self.assertIn("KODEX 200 뉴스 확인", ev["text"])
        self.assertIn("-0.3%", ev["text"])
        self.assertNotIn("같은 동안 시장", ev["text"])
        self.assertIn("S1", eng.st["players"]["ai"]["positions"])   # 지수가 안 무너졌으니 그대로

    def test_news_hints_count_sources_and_price_since_the_article(self):
        self.seed({"S1": (100_000, 100.0)})
        brain_ = FakeBrain(verdict="ignore")
        eng = self.engine(brain_)
        self.run_ticks(eng, 6)
        self.market.prices["S1"] = 99.0
        self.news_items = [
            self.news("S1", "A사, 단독 공급 계약 체결", minutes_ago=3, source="한경", key="k1"),
            self.news("S1", "A사 공급 계약 기대", minutes_ago=30, source="연합", key="k2"),
            self.news("S1", "A사 옛날 이야기", minutes_ago=70, source="매경", key="k3"),
        ]
        eng.last_news_at = None
        self.run_ticks(eng, 1)
        ev = brain_.triage_calls[0][0]
        d = ev["data"]
        self.assertEqual((d["articles_1h"], d["sources_1h"], d["rumor"]), (2, 2, True))
        self.assertEqual([n["title"] for n in d["earlier"]], ["A사 공급 계약 기대"])
        self.assertAlmostEqual(d["moved"], -0.01)
        self.assertEqual(d["price"], 100.0)
        text = brain.events_text([ev])
        self.assertIn("기사 2건, 매체 2곳", text)
        self.assertIn("추측·단독성", text)
        self.assertIn("기사 뒤로 주가 -1.0%", text)
        self.assertIn("앞서 [", text)
        self.assertIn("[코드 S1]", text)


    def test_spent_budget_skips_news_too_and_says_so_once(self):
        self.seed({"S1": (100_000, 100.0)})
        brain_ = FakeBrain()
        eng = self.engine(brain_)
        self.run_ticks(eng, 1)
        eng.counts["reviews"] = LIVE["max_reviews_per_day"]
        for i in range(3):
            self.news_items.append(self.news("S1", f"기사 {i}"))
            eng.last_news_at = None
            self.run_ticks(eng, 2)
        self.assertEqual((brain_.triage_calls, brain_.react_calls), ([], []))
        self.assertEqual(len([e for e in eng.feed if "다 써서" in e["text"]]), 1)


class NewsBudgetTest(EngineCase):
    def test_news_reviews_have_their_own_cap(self):
        self.seed({"S1": (100_000, 100.0)})
        brain_ = FakeBrain()
        eng = self.engine(brain_)
        self.run_ticks(eng, 1)
        for i in range(LIVE["max_news_reviews_per_day"] + 2):
            self.news_items.append({"code": "S1", "key": f"k{i}", "when": self.market.now - timedelta(minutes=1),
                                    "title": f"A사 수주 {i}", "source": "x"})
            eng.last_news_at = None
            self.run_ticks(eng, 2)
        self.assertEqual(brain_.modes.count("news"), LIVE["max_news_reviews_per_day"])
        self.assertEqual(eng.counts["news_reviews"], LIVE["max_news_reviews_per_day"])
        self.assertEqual(len([e for e in eng.feed if "뉴스 점검" in e["text"] and "다 써서" in e["text"]]), 1)
        self.market.prices["S1"] = 95.0                              # 가격 사건은 여전히 AI를 부른다
        self.run_ticks(eng, 20)
        self.assertIn("trade", brain_.modes)
        trade = brain_.react_calls[brain_.modes.index("trade")][1]
        self.assertIn("news", [e["kind"] for e in trade])            # 못 본 기사는 그 종목이 움직일 때 같이 본다

    def test_news_is_screened_while_routine_price_events_wait(self):
        self.seed({"S1": (100_000, 100.0), "S4": (100_000, 100.0)}, plans={"S1": {"stop": 80.0}})
        brain_ = FakeBrain()
        eng = self.engine(brain_)
        self.run_ticks(eng, 20)
        eng.counts["reviews"] = 15                                    # 점검이 빠듯해서 가격 사건은 모아서 본다
        self.market.prices["S1"] = 96.0
        self.run_ticks(eng, 1)
        self.market.prices["S1"] = 93.0
        self.run_ticks(eng, 2)                                        # 급하지 않은 가격 사건이 기다리는 중
        self.news_items = [{"code": "S4", "key": "d1", "when": self.market.now - timedelta(minutes=1),
                            "title": "B사 투자경고종목 지정", "source": "거래소 공시", "official": True}]
        eng.last_news_at = None
        self.run_ticks(eng, 3)
        self.assertEqual(brain_.modes, ["trade", "news"])             # 기사는 기다리지 않고 따로 본다
        self.assertEqual([e["code"] for e in brain_.react_calls[1][1]], ["S4"])
        self.assertTrue(any(e["kind"] != "news" for e in eng.events))  # 가격 사건은 그대로 기다린다

    def test_scheduled_check_is_not_blocked_by_waiting_news(self):
        self.seed({"S1": (100_000, 100.0)})
        brain_ = FakeBrain()
        eng = self.engine(brain_)
        self.run_ticks(eng, 1)
        eng.counts["news_reviews"] = LIVE["max_news_reviews_per_day"]
        self.news_items = [{"code": "S1", "key": "w1", "when": self.market.now - timedelta(minutes=1), "title": "A사 소식", "source": "x"}]
        eng.last_news_at = None
        self.run_ticks(eng, 2)                                        # 기사는 몫을 다 써서 기다리는 중
        eng.next_check_at = self.market.now
        self.run_ticks(eng, 2)
        self.assertEqual(self.kinds(brain_.react_calls[0])[0], "heartbeat")   # AI가 정한 점검은 그대로 돈다

    def test_stale_unseen_news_is_dropped(self):
        self.seed({"S1": (100_000, 100.0)})
        brain_ = FakeBrain()
        eng = self.engine(brain_)
        self.run_ticks(eng, 1)
        eng.counts["news_reviews"] = LIVE["max_news_reviews_per_day"]
        self.news_items = [{"code": "S1", "key": "o1", "when": self.market.now - timedelta(minutes=1), "title": "A사 소식", "source": "x"}]
        eng.last_news_at = None
        self.run_ticks(eng, 2)
        self.assertEqual([e["kind"] for e in eng.events], ["news"])  # 몫을 다 써도 바로 버리지 않는다
        self.run_ticks(eng, 61)
        self.assertEqual(eng.events, [])                              # 한 시간이 지나면 버린다

    def test_low_importance_news_is_not_escalated(self):
        self.seed({"S1": (100_000, 100.0)})
        brain_ = FakeBrain()
        brain_.triage = lambda ctx, events: {"verdict": "review", "importance": 3, "reason": "애매", "meta": {}}
        eng = self.engine(brain_)
        self.news_items = [{"code": "S1", "key": "p1", "when": self.market.now - timedelta(minutes=1),
                            "title": "A사 80주년 프로모션", "source": "x"}]
        self.run_ticks(eng, 3)
        self.assertEqual(brain_.react_calls, [])
        self.assertTrue(any(e["kind"] == "triage" for e in eng.feed))


class PacingTest(EngineCase):
    def test_routine_price_events_wait_when_the_budget_is_thin(self):
        self.seed({"S1": (100_000, 100.0)}, plans={"S1": {"stop": 80.0}})
        brain_ = FakeBrain()
        eng = self.engine(brain_)
        self.run_ticks(eng, 20)
        eng.counts["reviews"] = 15                                    # 남은 점검 5번, 장은 5시간 넘게 남음
        self.market.prices["S1"] = 96.0                               # 급변·오늘 등락 (급하지 않은 사건)
        self.run_ticks(eng, 1)
        self.assertEqual(len(brain_.react_calls), 1)                  # 처음 한 번은 바로
        self.market.prices["S1"] = 92.0
        self.run_ticks(eng, 20)
        self.assertEqual(len(brain_.react_calls), 1)                  # 그다음은 모았다가 나중에
        self.run_ticks(eng, 40)
        self.assertEqual(len(brain_.react_calls), 2)
        self.assertGreaterEqual(len(brain_.react_calls[1][1]), 2)    # 쌓인 사건을 한 번에 본다

    def test_urgent_events_skip_the_pacing(self):
        alerts = [{"code": "S1", "below": 93.0, "above": None, "note": "더 빠지면 줄이기"}]
        self.seed({"S1": (100_000, 100.0)}, plans={"S1": {"stop": 80.0}}, alerts=alerts)
        brain_ = FakeBrain()
        eng = self.engine(brain_)
        self.run_ticks(eng, 20)
        eng.counts["reviews"] = 15
        self.market.prices["S1"] = 96.0
        self.run_ticks(eng, 1)
        self.market.prices["S1"] = 92.5                               # AI가 걸어둔 알림이 울린다
        self.run_ticks(eng, 6)
        self.assertEqual(len(brain_.react_calls), 2)
        self.assertIn("alert", self.kinds(brain_.react_calls[1]))


class NewsPromptTest(EngineCase):
    def ctx(self):
        self.seed({"S1": (100_000, 100.0)}, alerts=[{"code": "S1", "below": 99.0, "above": None, "note": ""}])
        eng = self.engine()
        self.run_ticks(eng, 1)
        now = self.market.now
        return eng._build_ctx(eng.last_q, now, now.astimezone(eng.tz))

    def ask(self, mode, answer):
        prompts = []

        def fake_chat(model, system, user, max_tokens=16000, **kw):
            prompts.append(user)
            return answer, {"model": "fake"}

        with mock.patch.object(brain, "chat_json", fake_chat):
            events = [{"kind": "news", "code": "S1", "text": "종목S1: 매각설", "data": {}, "local": "10:00"}]
            out = brain.react(self.ctx(), events, mode=mode)
        return prompts[0], out

    def test_news_mode_prompt_and_parsing(self):
        answer = {"assessment": "a", "actions": [{"code": "S1", "target_weight": 0, "stop_price": 97}],
                  "recheck": ["S1", "ZZ", "069500", "S1"],
                  "alerts": [{"code": "S1", "below": 99.5}, {"code": "S1", "below": 95}, {"code": "S1", "below": 99.0}]}
        prompt, out = self.ask("news", answer)
        self.assertIn("뉴스 점검", prompt)
        self.assertIn('"recheck"', prompt)
        self.assertNotIn('"target_weight"', prompt)
        self.assertEqual(out["recheck"], ["S1", "069500"])
        self.assertEqual([a["below"] for a in out["alerts"]], [95.0, 99.0])   # 99는 원래 걸려 있던 알림이라 둔다
        self.assertTrue(any("너무 붙어서" in n for n in out["alert_notes"]))

    def test_trade_mode_prompt(self):
        prompt, out = self.ask("trade", {"assessment": "a", "actions": [], "recheck": ["S1"],
                                         "alerts": [{"code": "S1", "below": 99.5}]})
        self.assertNotIn("뉴스 점검", prompt)
        self.assertIn('"target_weight"', prompt)
        self.assertIn("소문일 수 있어", prompt)
        self.assertNotIn("recheck", out)
        self.assertEqual([a["below"] for a in out["alerts"]], [99.5])          # 매매 점검에서는 간격 제한 없음

    def test_empty_alert_list_keeps_alerts_and_clearing_is_explicit(self):
        prompt, out = self.ask("trade", {"assessment": "기존 알림으로 대응", "actions": [], "alerts": []})
        self.assertIsNone(out["alerts"])               # 빈 목록은 '새 알림 없음'이지 '다 지워'가 아니다
        self.assertIn("clear_alerts", prompt)
        for odd in ("없음", None, {}, [{}], [{"code": "S1", "below": 101}]):   # 마지막은 지금 가격(100) 위라 못 건다
            self.assertIsNone(self.ask("trade", {"assessment": "a", "actions": [], "alerts": odd})[1]["alerts"])
        _, out = self.ask("trade", {"assessment": "a", "actions": [], "alerts": [], "clear_alerts": True})
        self.assertEqual(out["alerts"], [])
        self.assertIn("걸려 있던 알림 1개를 AI가 모두 지웠어요", out["alert_notes"])   # 지운 건 기록에 남긴다
        _, out = self.ask("trade", {"assessment": "a", "actions": [], "alerts": {"code": "S1", "below": 97}})
        self.assertEqual([a["below"] for a in out["alerts"]], [97.0])          # 목록 괄호를 빼먹어도 알림 하나로 읽는다


class PromptRulesTest(EngineCase):
    def fake_chat(self, answer):
        prompts = []

        def chat(model, system, user, max_tokens=16000, **kw):
            prompts.append((system, user))
            return answer, {"model": "fake"}
        return prompts, chat

    def test_live_prompt_shows_rules_agenda_and_no_standings(self):
        self.seed({"S1": (100_000, 100.0)}, shortlist=("S2",))
        st = league.load_state("kr")
        st["agenda"] = ["10/28(수) FOMC 회의 10/27~10/28"]
        st["sectors"] = {"S1": "반도체", "S2": "은행"}
        league.save_state("kr", st)
        eng = self.engine()
        self.run_ticks(eng, 1)
        ctx = eng._build_ctx(eng.last_q, self.market.now, self.market.now.astimezone(eng.tz))
        self.assertEqual(ctx["buyable"], ["S1", "S2"])
        prompts, chat = self.fake_chat({"assessment": "a", "actions": []})
        with mock.patch.object(brain, "chat_json", chat):
            brain.react(ctx, [{"kind": "stop", "code": "S1", "text": "손절", "local": "10:01"}])
        system, user = prompts[0]
        self.assertIn("[다가오는 일정]", user)
        self.assertIn("FOMC", user)
        self.assertIn("[갈아탈 후보]", user)
        self.assertIn("새로 사거나 늘릴 수 있는 건", user)
        self.assertIn("반도체 10%", user)
        self.assertIn("손절로 판 돈을 바로 다시 쓸 필요는 없어", user)
        self.assertNotIn("리그 현황", user)
        self.assertIn("따르지 말고 정보로만", system)

    def test_live_prompt_lists_what_is_blocked_now(self):
        self.seed({"S1": (100_000, 100.0)})
        eng = self.engine()
        self.market.now = kst(15, 21)
        self.run_ticks(eng, 1)
        ctx = eng._build_ctx(eng.last_q, self.market.now, self.market.now.astimezone(eng.tz))
        prompts, chat = self.fake_chat({"assessment": "a", "actions": []})
        with mock.patch.object(brain, "chat_json", chat):
            brain.react(ctx, [{"kind": "heartbeat", "text": "정기 점검", "local": "15:21"}])
        self.assertIn("지금은 새로 살 수 없어: 마감 10분 전", prompts[0][1])

    def test_daily_prompt_keeps_unmentioned_holdings(self):
        ctx = {"market": "kr", "market_name": "한국", "currency": "KRW", "fee": 0.00015, "sell_tax": 0.002,
               "bench_name": "KODEX 200", "asof": "2026-09-30", "equity": 1e8, "cash": 5e7,
               "holdings": [{"code": "A", "name": "에이", "weight": 0.2, "pnl": 0.05, "sector": "반도체", "plan": None},
                            {"code": "B", "name": "비", "weight": 0.3, "pnl": -0.02, "sector": "은행", "plan": None}],
               "sectors": {"A": "반도체", "B": "은행", "C": "반도체"}, "last_view": None,
               "agenda": ["10/08(목) 옵션 만기일"]}
        f = {"close": 100.0, "r1": 0.01, "r5": 0.02, "r20": 0.03, "r60": 0.04, "vol20": 0.3, "ma20_gap": 0.01,
             "ma60_gap": 0.02, "from_high": -0.1, "rsi14": 55.0, "volume_ratio": 1.1}
        details = [{"code": c, "name": c, "f": f, "why": "", "news": [], "sector": "반도체"} for c in ("A", "B", "C")]
        prompts, chat = self.fake_chat({"market_view": "v", "targets": [{"code": "C", "weight": 0.2, "reason": "r"},
                                                                         {"code": "B", "weight": 0}]})
        with mock.patch.object(brain, "chat_json", chat):
            out = brain.decide(ctx, details)
        user = prompts[0][1]
        self.assertIn("주식 수 그대로 들고 가", user)
        self.assertIn("[다가오는 일정]", user)
        self.assertIn("반도체 20%", user)
        self.assertNotIn("리그 현황", user)
        self.assertEqual(out["keep"], ["A"])
        self.assertEqual(out["targets"], {"C": 0.2})


class PlanUnitTest(unittest.TestCase):
    def test_parse_update(self):
        self.assertEqual(planlib.parse_update({"stop_pct": 7, "take_pct": "0.2"}), {"stop_pct": 0.07, "take_pct": 0.2})
        self.assertEqual(planlib.parse_update({"trail_pct": 0, "take_price": 0}), {"trail_pct": 0.0, "take_price": 0.0})
        self.assertEqual(planlib.parse_update({"stop_price": "손절가", "take_frac": 0, "x": 1}), {})
        self.assertEqual(planlib.parse_update({"stop_pct": 0.9})["stop_pct"], 0.30)   # 범위로 끌어온다
        self.assertEqual(planlib.parse_update({}), {})

    def test_apply_and_turn_off(self):
        plan, notes = planlib.apply({}, {"stop_pct": 0.1, "take_price": 130, "take_frac": 0.5, "trail_pct": 0.08}, avg=100, price=110)
        self.assertEqual((plan["stop"], plan["take"], plan["take_frac"], plan["trail_pct"], plan["high"]), (90, 130, 0.5, 0.08, 110))
        self.assertEqual(notes, [])
        plan, _ = planlib.apply(plan, {"take_price": 0.0, "trail_pct": 0.0}, avg=100, price=110)
        self.assertNotIn("take", plan)
        self.assertNotIn("take_frac", plan)
        self.assertNotIn("trail_pct", plan)
        _, notes = planlib.apply(plan, {"stop_price": 111, "take_price": 105}, avg=100, price=110)
        self.assertEqual(len(notes), 2)

    def test_min_gap_keeps_plans_off_the_price(self):
        plan, notes = planlib.apply({}, {"stop_price": 99.5}, avg=100, price=100, min_gap=0.02)
        self.assertEqual((plan["stop"], len(notes)), (98.0, 1))
        plan, notes = planlib.apply({"stop": 98.5}, {"stop_price": 99.5}, avg=100, price=100, min_gap=0.02)
        self.assertEqual(plan["stop"], 98.5)                               # 이미 더 바짝 있던 손절은 안 내린다
        plan, notes = planlib.apply({"stop": 99.0}, {"stop_price": 98.8}, avg=100, price=99.5, min_gap=0.02)
        self.assertEqual((plan["stop"], notes), (98.8, []))                # 멀어지는 쪽은 언제나 된다
        plan, _ = planlib.apply({}, {"take_price": 101.0}, avg=100, price=100, min_gap=0.02)
        self.assertEqual(plan["take"], 102.0)
        plan, _ = planlib.apply({"take": 101.5}, {"take_price": 101.0}, avg=100, price=100, min_gap=0.02)
        self.assertEqual(plan["take"], 101.5)
        plan, notes = planlib.apply({}, {"trail_pct": 0.02}, avg=100, price=100, min_gap=0.02)
        self.assertEqual((plan["trail_pct"], notes), (0.02, []))           # 최고가 100에서 2% = 98, 딱 경계
        plan, notes = planlib.apply({"trail_pct": 0.1, "high": 110.0}, {"trail_pct": 0.05}, avg=100, price=100)
        self.assertEqual((plan["trail_pct"], len(notes)), (0.1, 1))        # 104.5면 바로 팔리니까 안 바꾼다
        plan, notes = planlib.apply({"trail_pct": 0.05, "high": 110.0}, {"trail_pct": 0.1}, avg=100, price=100)
        self.assertEqual((plan["trail_pct"], notes), (0.1, []))            # 넓히는 건 된다
        plan, notes = planlib.apply({"trail_pct": 0.02, "high": 110.0}, {"trail_pct": 0.05}, avg=100, price=100)
        self.assertEqual((plan["trail_pct"], notes), (0.05, []))           # 아직 가격 위여도 넓히는 쪽이면 받는다

    def test_check_picks_the_higher_stop(self):
        plan = {"stop": 90.0, "trail_pct": 0.05, "high": 120.0}
        self.assertEqual(planlib.effective_stop(plan), ("trail", 114.0))
        self.assertEqual(planlib.check(plan, 113.0), ("trail", 114.0))
        self.assertIsNone(planlib.check(plan, 115.0))
        self.assertEqual(planlib.check({"stop": 90.0, "take": 110.0}, 110.0), ("take", 110.0))

    def test_migrate_and_default(self):
        self.assertEqual(planlib.migrate({"stop_pct": 0.1, "take_pct": None, "default": True}, 200), {"stop": 180.0, "default": True})
        plan = planlib.ensure_default({"stop": 180.0, "default": True}, avg=150)
        self.assertEqual(plan["stop"], 180.0)          # 물타기로 평단이 내려가도 손절가는 안 내려간다
        plan = planlib.ensure_default({"stop": 90.0, "default": True, "default_pct": 0.1}, avg=120)
        self.assertAlmostEqual(plan["stop"], 108.0)    # 더 사서 평단이 오르면 따라 오른다
        self.assertEqual(planlib.ensure_default({"stop": 170.0}, avg=150)["stop"], 170.0)
        self.assertNotIn("stop", planlib.ensure_default({"trail_pct": 0.1, "high": 150}, avg=150))
        new = planlib.ensure_default({}, avg=100, pct=0.07)
        self.assertEqual((new["stop"], new["default_pct"]), (93.0, 0.07))
        old = planlib.ensure_default({"stop": 90.0, "default": True}, avg=100, pct=0.05)
        self.assertEqual((old["stop"], old["default_pct"]), (90.0, 0.10))   # 예전에 걸린 건 폭을 안 바꾼다

    def test_default_pct_follows_volatility(self):
        self.assertEqual(planlib.default_pct(None), DEFAULT_STOP_PCT)
        self.assertEqual(planlib.default_pct(0.01), 0.05)            # 하한
        self.assertAlmostEqual(planlib.default_pct(0.03), 0.105)
        self.assertEqual(planlib.default_pct(0.08), 0.15)            # 상한


class BrainParsingTest(unittest.TestCase):
    def test_clean_actions(self):
        out, notes = brain.clean_actions([
            {"code": "A", "target_weight": 20, "reason": "r"},
            {"code": "B", "stop_price": 90},
            {"code": "C"},                              # 아무것도 안 바꿈
            {"code": "D", "target_weight": 0.01},       # 최소 비중의 절반도 안 됨: 0
            {"code": "E", "target_weight": 0.02},       # 절반은 넘음: 최소 비중으로
            {"code": "F", "target_weight": "많이"},      # 못 알아봄: 비중은 그대로
            {"code": "Z", "target_weight": 0.1},        # 목록에 없는 코드
            "garbage",
        ], {"A", "B", "C", "D", "E", "F"})
        self.assertEqual([(a["code"], a["weight"]) for a in out], [("A", 0.2), ("B", None), ("D", 0.0), ("E", 0.03)])
        self.assertEqual(out[1]["plan"], {"stop_price": 90.0})
        self.assertEqual(len(notes), 4)                 # D, E, F, Z는 조용히 버리지 않고 이유를 남긴다

    def test_clean_alerts(self):
        raw = [
            {"code": "A", "below": 95, "note": "더 살지"},
            {"code": "A", "above": 99},                 # 이미 지금 가격(100) 위: 바로 울리니 뺀다
            {"code": "Q", "below": 1},                  # 목록에 없는 종목
        ] + [{"code": "B", "above": 200 + i} for i in range(20)]
        alerts, notes = brain.clean_alerts(raw, {"A", "B"}, {"A": 100.0, "B": 100.0})
        self.assertEqual(alerts[0], {"code": "A", "above": None, "below": 95.0, "note": "더 살지"})
        self.assertEqual(len(alerts), MAX_ALERTS)
        self.assertTrue(any("이미" in n for n in notes) and any("Q" in n for n in notes))
        self.assertEqual(brain.clean_alerts(None, {"A"}, {}), ([], []))


    def test_clean_recheck(self):
        self.assertEqual(brain.clean_recheck(["A", "A", "Z", {"code": "B"}, 3], {"A", "B"}), ["A", "B"])
        self.assertEqual(brain.clean_recheck("A", {"A"}), ["A"])
        self.assertEqual(brain.clean_recheck(None, {"A"}), [])

    def test_market_feed_codes_are_not_shown_as_tradable(self):
        text = brain.events_text([{"kind": "news", "code": "MAIN", "text": "시장 주요뉴스: 금리", "data": {"rumor": True}}])
        self.assertNotIn("[코드", text)
        self.assertIn("추측·단독성", text)


class TriggerUnitTest(unittest.TestCase):
    def test_thresholds(self):
        thr = triggers.fast_threshold(0.03, 15, 390, 3.0, 0.012)
        self.assertAlmostEqual(thr, 3 * 0.03 * (15 / 390) ** 0.5)
        self.assertEqual(triggers.fast_threshold(0.005, 15, 390, 3.0, 0.012), 0.012)
        self.assertEqual((triggers.day_level(0.061, 0.03), triggers.day_level(-0.09, 0.04), triggers.day_level(0.01, 0.03)), (2, -2, 0))

    def test_rumor_words(self):
        for t in ("A사, B사 인수설 솔솔", "[단독] A사 매각 추진", "A사 유상증자 검토", "A사 상장폐지 가능성?", "업계 관측"):
            self.assertTrue(triggers.looks_like_rumor(t), t)
        for t in ("A사 3분기 영업이익 10조 발표", "A사, B사와 공급 계약 체결", "A사 인수 완료", None):
            self.assertFalse(triggers.looks_like_rumor(t), t)

    def test_history_return_needs_enough_data(self):
        h = quotes.History()
        for i in range(5):
            h.add(T0 + timedelta(minutes=i), {"A": {"price": 100.0 + i}})
        now = T0 + timedelta(minutes=4)
        self.assertIsNone(h.ret("A", 900, now))
        self.assertAlmostEqual(h.ret("A", 120, now), 104 / 102 - 1)


if __name__ == "__main__":
    unittest.main()
