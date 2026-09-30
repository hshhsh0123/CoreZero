"""대시보드 데이터 만들기와 로컬 서버 테스트. 네트워크는 안 쓴다(로컬 서버만)."""

import json
import re
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from trader import league, live, report, serve

T = "2026-09-30T01:40:00+00:00"


def write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


class ReportCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state_dir = self.root / "state"
        self.runtime = self.root / "runtime"
        self.state_dir.mkdir()
        self.patches = [
            mock.patch.object(league, "STATE_DIR", self.state_dir),
            mock.patch.object(live, "RUNTIME", self.runtime),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def make_state(self, market="kr", live_at=None):
        st = league.new_state(market)
        st.update(started="2026-09-29", last_decided="2026-09-29", decisions=1)
        for pl in st["players"].values():
            pl["history"].append({"date": "2026-09-29", "equity": 100_000_000.0})
        ai = st["players"]["ai"]
        ai["positions"]["A"] = {"shares": 100, "avg": 1000.0, "name": "에이"}
        ai["cash"] = 100_000_000.0 - 100_000
        st["plans"] = {"A": {"stop_pct": 0.1, "take_pct": 0.2}}
        if live_at:
            st["live"] = {"at": live_at, "players": {}}
        (self.state_dir / f"{market}.json").write_text(json.dumps(st), encoding="utf-8")
        return st

    def snapshot(self, market="kr", equity=101_000_000.0, price=1100.0, phase="open", updated_at=T):
        snap = {
            "market": market, "phase": phase, "updated_at": updated_at, "date": "2026-09-30", "poll_seconds": 20,
            "players": {
                "ai": {"equity": equity, "ret": 0.01, "day": 0.01, "cash": 99_900_000.0,
                       "positions": [{"code": "A", "name": "에이", "shares": 100, "avg": 1000.0, "price": price, "day": 0.02}]},
                "monkey": {"equity": 100_000_000.0, "ret": 0, "day": 0, "cash": 100_000_000.0, "positions": []},
                "hodl": {"equity": 100_000_000.0, "ret": 0, "day": 0, "cash": 100_000_000.0, "positions": []},
            },
            "feed": [{"t": T, "kind": "info", "text": "복사본"}],
        }
        self.runtime.mkdir(exist_ok=True)
        (self.runtime / f"live_{market}.json").write_text(json.dumps(snap), encoding="utf-8")


class CollectTest(ReportCase):
    def test_works_without_any_runtime_files(self):
        self.make_state()
        data = report.collect()
        m = data["markets"][0]
        self.assertIsNone(m["rt"])
        self.assertEqual(m["players"][0]["positions"][0]["stop"], 900.0)   # 손절 계획은 state만으로도 나온다

    def test_live_snapshot_overrides_numbers_when_fresher(self):
        self.make_state(live_at="2026-09-29T07:00:00+00:00")
        self.snapshot()
        m = report.collect()["markets"][0]
        ai = m["players"][0]
        self.assertAlmostEqual(ai["equity"], 101_000_000.0)
        self.assertAlmostEqual(ai["ret"], 0.01)
        self.assertAlmostEqual(ai["positions"][0]["price"], 1100.0)
        self.assertAlmostEqual(ai["positions"][0]["pnl"], 0.1)
        self.assertTrue(m["live"]["provisional"])
        self.assertNotIn("feed", m["rt"]["snapshot"])

    def test_old_snapshot_does_not_override_newer_state(self):
        self.make_state(live_at="2026-09-30T07:00:00+00:00")
        self.snapshot(updated_at=T)
        m = report.collect()["markets"][0]
        self.assertNotAlmostEqual(m["players"][0]["equity"], 101_000_000.0)

    def test_feed_and_intraday_are_read_and_downsampled(self):
        self.make_state()
        self.runtime.mkdir()
        write_jsonl(self.runtime / "feed_kr.jsonl", [{"t": f"2026-09-30T01:{i:02d}:00+00:00", "kind": "news", "text": f"기사{i}"} for i in range(50)])
        write_jsonl(self.runtime / "intraday_kr_2026-09-30.jsonl", [{"t": f"2026-09-30T01:00:{i % 60:02d}+00:00", "ai": 1.0 + i, "monkey": 1.0, "hodl": 1.0} for i in range(1000)])
        m = report.collect()["markets"][0]
        self.assertEqual(m["rt"]["feed"][0]["text"], "기사49")           # 최신이 앞
        self.assertLessEqual(len(m["rt"]["intraday"]["points"]), report.POINT_LIMIT)
        self.assertEqual(m["rt"]["intraday"]["points"][-1]["ai"], 1000.0)
        self.assertEqual(m["rt"]["intraday"]["date"], "2026-09-30")

    def test_garbage_files_are_survived(self):
        self.make_state()
        self.runtime.mkdir()
        (self.runtime / "live_kr.json").write_text("{잘린 파일", encoding="utf-8")
        (self.runtime / "feed_kr.jsonl").write_text('{"t": "x", "kind": "news", "text": "ok"}\n깨진 줄\n[1,2]\n', encoding="utf-8")
        (self.runtime / "intraday_kr_2026-09-30.jsonl").write_text("깨진\n", encoding="utf-8")
        m = report.collect()["markets"][0]
        self.assertEqual([f["text"] for f in m["rt"]["feed"]], ["ok"])
        self.assertIsNone(m["rt"]["snapshot"])

    def test_reactions_in_state_fill_the_feed_when_no_runtime(self):
        st = self.make_state()
        st["reactions"] = [{"time": T, "assessment": "지켜본다", "actions": [], "fills": []}]
        (self.state_dir / "kr.json").write_text(json.dumps(st), encoding="utf-8")
        m = report.collect()["markets"][0]
        self.assertEqual(m["rt"]["feed"][0]["kind"], "react")

    def test_render_is_valid_fragment_and_page(self):
        self.make_state()
        frag = report.render(fragment=True)
        self.assertTrue(frag.lstrip().startswith("<title>"))
        self.assertNotIn("<html", frag)
        self.assertIn("</script>", frag)
        page = report.render()
        self.assertTrue(page.startswith("<!doctype html>"))
        blob = re.search(r"let DATA = (\{.*?\});\n", page, re.S).group(1)
        self.assertEqual(json.loads(blob)["markets"][0]["key"], "kr")

    def test_untrusted_text_cannot_close_the_script_tag(self):
        self.make_state()
        self.runtime.mkdir()
        write_jsonl(self.runtime / "feed_kr.jsonl", [{"t": T, "kind": "news", "text": "</script><b>x</b>"}])
        page = report.render()
        self.assertEqual(page.count("</script>"), 1)


class ServeTest(ReportCase):
    def test_api_and_page(self):
        self.make_state()
        self.snapshot()
        server = serve.make_server(0)
        import threading

        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            with urllib.request.urlopen(base + "/api/data") as res:
                self.assertEqual(res.headers["Cache-Control"], "no-store")
                data = json.loads(res.read())
            self.assertTrue(data["live_mode"])
            self.assertEqual(data["markets"][0]["rt"]["snapshot"]["phase"], "open")
            with urllib.request.urlopen(base + "/") as res:
                self.assertIn('"live_mode": true', res.read().decode("utf-8"))
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(base + "/nope")
            self.assertEqual(ctx.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
