"""네트워크 없이 도는 테스트. python -m unittest discover tests"""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from trader import brain, broker, league
from trader.config import MARKETS, MAX_POSITIONS, MAX_WEIGHT

KR = MARKETS["kr"]


def make_bars(dates, start=100.0, step=1.0):
    out = []
    for i, d in enumerate(dates):
        c = start + step * i
        out.append({"date": d, "open": c - 0.5, "high": c + 1, "low": c - 1, "close": c, "volume": 1000.0})
    return out


def days(n, first=1):
    return [f"2026-08-{d:02d}" for d in range(first, first + n)]


class LastCompleteTest(unittest.TestCase):
    def test_intraday_bar_is_not_complete(self):
        bars = make_bars(["2026-09-29", "2026-09-30"])
        now = datetime(2026, 9, 30, 1, 0, tzinfo=timezone.utc)  # 한국 10:00
        self.assertEqual(league.last_complete("kr", bars, now), "2026-09-29")

    def test_bar_is_complete_after_close(self):
        bars = make_bars(["2026-09-29", "2026-09-30"])
        now = datetime(2026, 9, 30, 7, 0, tzinfo=timezone.utc)  # 한국 16:00
        self.assertEqual(league.last_complete("kr", bars, now), "2026-09-30")

    def test_us_evening_same_day(self):
        bars = make_bars(["2026-09-28", "2026-09-29"])
        now = datetime(2026, 9, 30, 0, 45, tzinfo=timezone.utc)  # 뉴욕 9/29 20:45
        self.assertEqual(league.last_complete("us", bars, now), "2026-09-29")


class BrokerTest(unittest.TestCase):
    def test_buy_then_sell_charges_costs(self):
        pl = broker.new_player(1_000_000)
        names = {"A": "에이"}
        broker.rebalance(pl, {"A": 0.5}, {"A": 1000.0}, names, KR, "d1")
        self.assertEqual(pl["positions"]["A"]["shares"], 500)
        self.assertAlmostEqual(pl["cash"], 1_000_000 - 500_000 * (1 + KR["fee"]))
        broker.rebalance(pl, {}, {"A": 1000.0}, names, KR, "d2")
        self.assertNotIn("A", pl["positions"])
        expected = 1_000_000 - 500_000 * (KR["fee"] * 2 + KR["sell_tax"])
        self.assertAlmostEqual(pl["cash"], expected)

    def test_never_overspends(self):
        pl = broker.new_player(100_000)
        broker.rebalance(pl, {"A": 1.0}, {"A": 333.0}, {}, KR, "d1")
        self.assertGreaterEqual(pl["cash"], 0)
        self.assertGreater(pl["positions"]["A"]["shares"], 0)

    def test_small_adjustments_are_skipped(self):
        pl = broker.new_player(1_000_000)
        broker.rebalance(pl, {"A": 0.5}, {"A": 1000.0}, {}, KR, "d1")
        fills = broker.rebalance(pl, {"A": 0.505}, {"A": 1000.0}, {}, KR, "d2")
        self.assertEqual(fills, [])


class CleanTargetsTest(unittest.TestCase):
    def test_rules(self):
        raw = [{"code": f"C{i}", "weight": 20, "reason": "r"} for i in range(12)]
        raw.append({"code": "NOPE", "weight": 0.2})
        raw.append({"code": "C0", "weight": 0.9})
        targets, reasons = brain.clean_targets(raw, {f"C{i}" for i in range(12)})
        self.assertNotIn("NOPE", targets)
        self.assertLessEqual(len(targets), MAX_POSITIONS)
        self.assertLessEqual(sum(targets.values()), 1.0 + 1e-9)
        self.assertTrue(all(w <= MAX_WEIGHT + 1e-9 for w in targets.values()))
        self.assertEqual(set(targets), set(reasons))


class RunFlowTest(unittest.TestCase):
    """가짜 시세와 가짜 AI로 이틀치 리그를 돌려본다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(league, "STATE_DIR", tmp / "state"),
            mock.patch.object(league, "LOG_DIR", tmp / "logs"),
        ]
        for p in self.patches:
            p.start()
        self.all_dates = days(30)
        self.visible = 25  # 지금까지 나온 봉 개수

        def fake_bars(market, code, days=130):
            return make_bars(self.all_dates[: self.visible], start=100 + len(code))

        uni = [{"code": f"S{i}", "name": f"종목{i}", "board": "KOSPI", "mcap": "1조"} for i in range(8)]
        self.patches += [
            mock.patch("trader.data.bars", side_effect=fake_bars),
            mock.patch("trader.data.universe", return_value=uni),
            mock.patch("trader.data.news", return_value=[]),
            mock.patch("trader.data.fundamentals", return_value={}),
            mock.patch(
                "trader.brain.scout",
                return_value={
                    "market_view": "v",
                    "shortlist": [{"code": "S1", "why": "w"}],
                    "meta": {"model": "fake", "usage": {}},
                },
            ),
            mock.patch(
                "trader.brain.decide",
                return_value={
                    "market_view": "좋아 보임",
                    "targets": {"S1": 0.3},
                    "reasons": {"S1": "이유"},
                    "cash_reason": "",
                    "meta": {"model": "fake", "usage": {}},
                },
            ),
        ]
        for p in self.patches[2:]:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_decide_fill_and_mark(self):
        log = lambda *_: None
        # 장 마감 후 실행: 마지막 봉(25번째)이 확정, 다음 장이 아직 없으니 주문 대기
        after_close = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)
        st = league.run("kr", now=after_close, log=log)
        self.assertEqual(st["started"], "2026-08-25")
        self.assertIsNotNone(st["pending"])
        self.assertEqual(st["players"]["ai"]["positions"], {})

        # 다음 날 장 마감 후: 시가에 체결되고, 그날 종가로 평가가 남고, 새 결정을 한다
        self.visible = 26
        st = league.run("kr", now=datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc), log=log)
        ai = st["players"]["ai"]
        self.assertIn("S1", ai["positions"])
        self.assertEqual(ai["trades"][0]["date"], "2026-08-26")
        self.assertIn("069500", st["players"]["hodl"]["positions"])
        self.assertEqual(len(st["players"]["monkey"]["positions"]), 5)
        self.assertEqual([h["date"] for h in ai["history"]], ["2026-08-25", "2026-08-26"])
        self.assertEqual(st["last_decided"], "2026-08-26")
        self.assertEqual(st["decisions"], 2)

        # 같은 날 한 번 더 돌려도 아무 일 없음
        again = league.run("kr", now=datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc), log=log)
        self.assertEqual(len(again["players"]["ai"]["trades"]), len(ai["trades"]))
        self.assertEqual(again["decisions"], 2)


class Utf8Test(unittest.TestCase):
    """한국어 윈도우(기본 cp949)에서도 리눅스가 만든 UTF-8 상태 파일을 읽고 쓸 수 있어야 한다."""

    def test_state_roundtrip_under_a_non_utf8_locale(self):
        import os
        import subprocess
        import sys

        root = Path(__file__).resolve().parent.parent
        code = f'''
import sys, tempfile
from pathlib import Path
sys.path.insert(0, {str(root)!r})
from trader import league
league.STATE_DIR = Path(tempfile.mkdtemp()) / "state"
st = league.new_state("kr")
st["journal"].append({{"reason": "한글과 이모지 🚀 그리고 …"}})
league.save_state("kr", st)
assert league.load_state("kr")["journal"][0]["reason"].endswith("…")
print("ok")
'''
        script = Path(tempfile.mkdtemp()) / "check.py"
        script.write_text(code, encoding="utf-8")  # 소스 파일은 로케일과 상관없이 UTF-8로 읽힌다
        env = {**os.environ, "LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0", "PYTHONIOENCODING": "utf-8"}
        out = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(out.stdout.strip(), "ok", out.stderr[-500:])


if __name__ == "__main__":
    unittest.main()
