"""장중 실시간 모드.

시세(20초)와 뉴스(2분)는 싸니까 계속 확인하고, 사건이 터질 때만 AI를 깨운다.

  시세 스냅샷
    → AI가 정해둔 계획 확인: 손절, 따라 올라가는 손절, 목표가 분할 매도 (AI 호출 없이 바로 집행)
    → AI가 걸어둔 가격 알림, 급변, 뉴스 감지
    → (뉴스만이면 flash가 먼저 거름) → v4-pro가 비중과 계획을 다시 정함
    → AI 답이 돌아온 그 순간의 시세로 체결

뉴스만 보고는 사고팔지 않는다. 기사만 들어온 점검('뉴스 점검')에서는 손절·목표·알림만 고칠 수 있고,
AI가 원하면 15분 뒤에 그동안의 주가 반응을 보여주고 다시 물어본다. 매매는 그때 한다.

AI는 한 번 정한 걸 고집하지 않는다. 점검 때마다 비중을 조금씩 늘리거나 줄이고, 손절·목표를 옮기고,
다음에 불러줄 가격(알림)과 시간(다음 점검)을 스스로 정한다. 장이 열리면 오늘 계획을 한 번 점검한다.

AI 호출은 별도 스레드에서 돌아서, AI가 생각하는 동안에도 시세 확인과 손절은 멈추지 않는다.
장이 끝나면 기존 하루 정산(league.run)을 이어서 돌려 내일 주문까지 정한다.
"""

import json
import math
import os
import queue
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import brain, broker, data, features, league, quotes, triggers
from . import plans as planlib
from .config import (
    LIVE,
    MARKET_NEWS,
    MARKETS,
    MAX_POSITIONS,
    PLAYERS,
    SETTLE_MINUTES,
    SLIPPAGE,
    TRADE_THRESHOLD,
)

RUNTIME = league.ROOT / "runtime"
DEFAULT_SIGMA = 0.025  # 일봉이 모자란 종목에 쓰는 하루 변동성 (2.5%)
MIN_SIGMA = 0.008
MARKET_FEED_NAMES = {"MAIN": "시장 주요뉴스", ".IXIC": "나스닥 종합 뉴스"}


def _hm(s):
    h, m = s.split(":")
    return int(h), int(m)


def session_minutes(cfg):
    oh, om = _hm(cfg["open"])
    ch, cm = _hm(cfg["close"])
    return (ch * 60 + cm) - (oh * 60 + om)


class LiveEngine:
    def __init__(self, market, log=print, deps=None):
        deps = deps or {}
        self.market = market
        self.cfg = MARKETS[market]
        self.tz = ZoneInfo(self.cfg["tz"])
        self.log = log
        bench = self.cfg["benchmark"]["code"]
        self._quotes = deps.get("quotes") or (lambda: quotes.snapshot(market, extra=[bench]))
        self._news = deps.get("news") or (lambda codes: data.news_many(market, codes))
        self._bars = deps.get("bars") or (lambda codes: data.bars_many(market, codes))
        self.ai = deps.get("brain") or brain
        self._daily_run = deps.get("daily") or league.run
        self.sync_worker = deps.get("sync_worker", False)
        self.strict = deps.get("strict", False)
        self.st = league.load_state(market)
        league.sync_plans(self.st)  # 손절 계획이 없던 보유 종목에는 기본 손절을 건다
        self.results = queue.Queue()
        self.job_running = False
        self.seen = self._load_seen()
        self.feed = []
        self.daily_done = None
        self.daily_retry_at = None
        self.started_at = datetime.now(timezone.utc)
        self._reset_day(None)

    # ------------------------------------------------------------------ 파일
    def _file(self, name, ext="json"):
        return RUNTIME / f"{name}_{self.market}.{ext}"

    def _read_json(self, path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _load_seen(self):
        d = self._read_json(self._file("seen"))
        return triggers.Seen(d if isinstance(d, list) else [])

    def _save_seen(self):
        league.atomic_write(self._file("seen"), json.dumps(self.seen.dump()))

    def _load_counts(self, today):
        snap = self._read_json(self._file("live")) or {}
        if today and snap.get("date") == today and isinstance(snap.get("counts"), dict):
            return {"reviews": 0, "triages": 0, "trades": 0, **snap["counts"]}
        return {"reviews": 0, "triages": 0, "trades": 0}

    def _feed(self, kind, text, now=None, **extra):
        now = now or datetime.now(timezone.utc)
        rec = {"t": now.isoformat(), "kind": kind, "text": text, **extra}
        self.feed.append(rec)
        del self.feed[:-200]
        RUNTIME.mkdir(parents=True, exist_ok=True)
        with open(self._file("feed", "jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.log(f"  [{kind}] {text}")

    def _safe(self, name, fn, *args):
        """한 부품이 오류를 내도 나머지(특히 손절)는 계속 돌아가게 한다."""
        try:
            return fn(*args)
        except Exception as e:
            if self.strict:
                raise
            self.log(f"  ! {name} 오류: {type(e).__name__}: {e}")
            if os.environ.get("TRADER_DEBUG"):
                traceback.print_exc()
            return None

    # ------------------------------------------------------------------ 하루 단위 초기화
    def _reset_day(self, today):
        self.today = today
        self.hist = quotes.History(keep_seconds=LIVE["history_min"] * 60)
        self.cool = triggers.Cooldown()
        self.events = []
        self.day_levels = {}
        self.breach = {}
        self.daily = {}
        self.counts = self._load_counts(today)
        self.last_react_at = None
        self.last_job_at = None
        self.last_news_at = None
        self.next_check_at = None
        self.open_reviewed = False
        self.news_log = {}       # code -> {기사키: (시각, 매체, 제목)} 매체 수 세기용
        self.followups = {}      # code -> 뉴스 뒤 주가 반응을 다시 볼 예약
        self.budget_noted = False
        self.opened_at = None
        self.holiday = False
        self.closed_ticks = 0
        self.quote_fail = 0
        self.last_minute = None
        self.last_q = {}

    # ------------------------------------------------------------------ 일정
    def _times(self, local):
        oh, om = _hm(self.cfg["open"])
        ch, cm = _hm(self.cfg["close"])
        return (
            local.replace(hour=oh, minute=om, second=0, microsecond=0),
            local.replace(hour=ch, minute=cm, second=0, microsecond=0),
        )

    def _next_open(self, local):
        day = local
        for _ in range(8):
            day = day + timedelta(days=1)
            if day.weekday() < 5:
                return self._times(day)[0] - timedelta(minutes=1)
        return local + timedelta(hours=12)

    def _phase(self, local):
        """(단계, 다음에 깰 시각). 단계는 pre / open / settle / post / closed."""
        open_t, close_t = self._times(local)
        if local.weekday() >= 5:
            return "closed", self._next_open(local)
        if local < open_t - timedelta(minutes=1):
            return "pre", open_t - timedelta(minutes=1)
        if local <= close_t + timedelta(seconds=30):
            return "open", None
        settle_t = close_t + timedelta(minutes=SETTLE_MINUTES + 1)
        if local < settle_t:
            return "settle", settle_t
        return "post", self._next_open(local)

    def tick(self, now):
        """한 번 확인하고, 다음 확인까지 몇 초 쉴지 돌려준다."""
        local = now.astimezone(self.tz)
        phase, wake = self._phase(local)
        if phase == "open":
            return self._tick_open(now, local)
        today = local.date().isoformat()
        if phase == "post" and self.daily_done != today and (
            self.daily_retry_at is None or now >= self.daily_retry_at
        ):
            self._run_daily(now, local)
        self._write_status(now, phase, wake)
        secs = (wake - local).total_seconds() if wake else 300.0
        return min(max(secs, 5.0), 300.0)

    def _run_daily(self, now, local):
        self.log(f"[{self.market}] 장이 끝나서 하루 정산과 내일 주문 결정을 시작해요")
        league.save_state(self.market, self.st)
        try:
            self._daily_run(self.market, now=now, log=self.log)
        except Exception as e:
            if self.strict:
                raise
            self.daily_retry_at = now + timedelta(minutes=10)
            self._feed("error", f"하루 정산 실패, 10분 뒤 다시 해요: {e}", now)
            self.st = league.load_state(self.market)
            return
        self.st = league.load_state(self.market)
        self.daily_done = local.date().isoformat()
        self.daily_retry_at = None
        self._feed("daily", "하루 정산과 내일 주문 결정을 마쳤어요", now)

    # ------------------------------------------------------------------ 장중 한 번
    def _tick_open(self, now, local):
        today = local.date().isoformat()
        if today != self.today:
            self._reset_day(today)
        poll = float(LIVE["poll_seconds"])
        if self.holiday:
            self._write_status(now, "holiday", None)
            return 300.0
        try:
            q = self._quotes()
        except Exception as e:
            if self.strict:
                raise
            self.quote_fail += 1
            self.log(f"  ! 시세 실패 ({self.quote_fail}번째): {e}")
            self._write_status(now, "quote_error", None, error=str(e))
            return poll
        self.quote_fail = 0
        if not self._market_open(q):
            self.closed_ticks += 1
            open_t, _ = self._times(local)
            if self.closed_ticks >= 5 and local >= open_t + timedelta(minutes=3):
                self.holiday = True
                self._feed("info", "스케줄상 장중인데 시세가 계속 '장 마감'이라 오늘은 쉬는 날로 봐요", now)
            self._write_status(now, "quotes_closed", None)
            return poll
        self.closed_ticks = 0
        if self.opened_at is None:
            self.opened_at = now
            self.next_check_at = now + timedelta(minutes=LIVE["heartbeat_min"])
            self._feed("info", f"{self.cfg['name']}장 감시 시작 (시세 {LIVE['poll_seconds']}초마다, 뉴스 {LIVE['news_seconds']}초마다)", now)
        self.last_q = q
        self.hist.add(now, q)
        for name, fn in (
            ("일봉", lambda: self._load_daily()),
            ("시가 체결", lambda: self._fill_pending(now, local)),
            ("장 시작 점검", lambda: self._open_review(now, local)),
            ("AI 결과 반영", lambda: self._apply_results(q, now, local)),
            ("손절·목표", lambda: self._enforce_plans(q, now, local)),
            ("알림", lambda: self._check_alerts(q, now, local)),
            ("뉴스 반응 확인", lambda: self._check_followups(q, now, local)),
            ("급변 감지", lambda: self._detect(q, now, local)),
            ("뉴스", lambda: self._poll_news(q, now, local)),
            ("AI 호출", lambda: self._maybe_review(q, now, local)),
            ("기록", lambda: self._record(q, now, local)),
        ):
            self._safe(name, fn)
        return poll

    def _market_open(self, q):
        stocks = [v for v in q.values() if v["kind"] == "stock"]
        if not stocks:
            return False
        return sum(1 for v in stocks if v["status"] == "OPEN") / len(stocks) >= 0.5

    def _tradable(self, qq, now):
        if qq.get("status") != "OPEN" or qq.get("halted"):
            return False
        at = qq.get("at")
        return not (at and (now - at).total_seconds() > LIVE["stale_seconds"])

    # ------------------------------------------------------------------ 준비
    def _shortlist(self):
        for j in reversed(self.st["journal"]):
            if j.get("shortlist"):
                return j["shortlist"]
        return []

    def _watch_codes(self):
        codes = set(self.st["players"]["ai"]["positions"]) | {self.cfg["benchmark"]["code"]}
        return codes | {s["code"] for s in self._shortlist()}

    def _load_daily(self):
        """감시 종목의 평소 변동성을 일봉에서 구한다. 급변 기준이 종목마다 달라지게."""
        missing = sorted(c for c in self._watch_codes() if c not in self.daily)
        if not missing:
            return
        try:
            bars = self._bars(missing)
        except Exception as e:
            if self.strict:
                raise
            self.log(f"  ! 일봉 실패: {e}")
            bars = {}
        for code in missing:
            prev = [b for b in (bars.get(code) or []) if b["date"] < self.today]
            f = features.compute(prev)
            sigma = f["vol20"] / math.sqrt(252) if f else DEFAULT_SIGMA
            self.daily[code] = {"sigma": max(sigma, MIN_SIGMA)}

    def _fill_pending(self, now, local):
        """어제 종가로 정해둔 주문을 오늘 시가에 체결한다."""
        pend = self.st.get("pending")
        today = local.date().isoformat()
        if not pend or pend["decided_on"] >= today:
            return
        codes = set()
        for t in pend["targets"].values():
            codes |= set(t or {})
        for pl in self.st["players"].values():
            codes |= set(pl["positions"])
        bars = self._bars(sorted(codes))
        missing = [c for c in codes if league._price_on(bars.get(c), today, "open") is None]
        waited = (now - self.opened_at).total_seconds() / 60 if self.opened_at else 0
        if missing and waited < 10:
            self.log(f"  시가 봉을 기다리는 중 ({len(missing)}종목)")
            return
        league._fill_pending(self.st, self.market, [today], lambda _codes: bars, self.log)
        league.save_state(self.market, self.st)
        self._feed("fill", "어제 종가 때 정한 주문을 오늘 시가에 체결했어요", now)

    # ------------------------------------------------------------------ 계획 집행 (AI 호출 없이 즉시)
    def _fmt(self, x):
        return brain.fmt_price(x, self.market)

    def _enforce_plans(self, q, now, local):
        """손절, 따라 올라가는 손절, 목표가 분할 매도. 팔고 나면 AI를 불러 남은 계획을 다시 정하게 한다."""
        ai = self.st["players"]["ai"]
        plans = self.st["plans"]
        day = local.date().isoformat()
        changed = False
        for code, pos in list(ai["positions"].items()):
            plan, qq = plans.get(code), q.get(code)
            if not plan or not qq or not self._tradable(qq, now):
                continue
            price = qq["price"]
            changed |= planlib.update_high(plan, price)
            hit = planlib.check(plan, price)
            if not hit:
                self.breach.pop(code, None)
                continue
            kind, level = hit
            prev_kind, n = self.breach.get(code, (kind, 0))
            n = n + 1 if prev_kind == kind else 1
            self.breach[code] = (kind, n)
            if n < LIVE["stop_confirm_ticks"]:
                continue
            self.breach.pop(code, None)
            avg, name, shares = pos["avg"], pos["name"], pos["shares"]
            qty = shares
            if kind == "take":
                frac = plan.get("take_frac") or 1.0
                qty = shares if frac >= 0.999 else max(1, math.floor(shares * frac))
                eq = broker.equity(ai, {c: q[c]["price"] for c in ai["positions"] if c in q})
                if (shares - qty) * price < eq * TRADE_THRESHOLD:
                    qty = shares  # 남는 게 너무 적으면 다 판다
            fill = broker.reduce_position(
                ai, code, qty, price, self.st["names"], self.cfg, day,
                slippage=SLIPPAGE[self.market], at=now.isoformat(), tag=kind,
            )
            if not fill:
                continue
            changed = True
            self.counts["trades"] += 1
            pnl = f"평단 대비 {price / avg - 1:+.1%}"
            left = ai["positions"].get(code, {}).get("shares", 0)
            if kind == "stop":
                text = f"{name} 손절: {self._fmt(level)} 아래로 내려가서 전량 매도 ({pnl})"
            elif kind == "trail":
                text = (f"{name} 따라 올라가는 손절: 최고가 {self._fmt(plan['high'])}에서 "
                        f"{plan['trail_pct']:.0%} 빠져서 전량 매도 ({pnl})")
            elif left:
                text = f"{name} 목표가 {self._fmt(level)} 도달: {fill['shares']:,}주 팔고 {left:,}주 남음 ({pnl}). 남은 물량 계획을 다시 정해줘"
                plan.pop("take", None)  # 다음 목표는 AI가 다시 정한다
                plan.pop("take_frac", None)
            else:
                text = f"{name} 목표가 {self._fmt(level)} 도달: 전량 매도 ({pnl})"
            self._feed(kind, text, now, code=code, fill=fill)
            self._add_event(now, local, kind, text, code=code, name=name, quiet=True)
        if changed:
            league.sync_plans(self.st)
            league.save_state(self.market, self.st)

    def _check_alerts(self, q, now, local):
        """AI가 걸어둔 가격 알림. 울리면 AI를 부르고 그 알림은 지운다."""
        alerts = self.st.get("alerts") or []
        keep, fired = [], []
        for a in alerts:
            qq = q.get(a["code"])
            if not qq:
                keep.append(a)
                continue
            price = qq["price"]
            if a.get("above") and price >= a["above"]:
                fired.append((a, f"{self._fmt(a['above'])} 위로 올라옴"))
            elif a.get("below") and price <= a["below"]:
                fired.append((a, f"{self._fmt(a['below'])} 아래로 내려옴"))
            else:
                keep.append(a)
        if not fired:
            return
        self.st["alerts"] = keep
        for a, what in fired:
            name = self.st["names"].get(a["code"]) or q[a["code"]]["name"]
            note = f" 네가 남긴 메모: {a['note']}" if a.get("note") else ""
            self._add_event(
                now, local, "alert", f"{name} 알림: {what} (지금 {self._fmt(q[a['code']]['price'])}).{note}",
                code=a["code"], name=name,
            )
        league.save_state(self.market, self.st)

    def _open_review(self, now, local):
        """장이 열리고 시가 체결까지 끝나면, 오늘 계획을 한 번 점검하게 한다."""
        if self.open_reviewed or not LIVE.get("open_review", True):
            return
        pend = self.st.get("pending")
        if pend and pend["decided_on"] < local.date().isoformat():
            return  # 시가 체결을 기다리는 중
        self.open_reviewed = True
        self._add_event(
            now, local, "open",
            "장이 열렸어. 시가를 보고 오늘 계획(비중, 손절·목표, 알림, 다음 점검)을 정해줘", quiet=True,
        )

    # ------------------------------------------------------------------ 감지
    def _add_event(self, now, local, kind, text, code=None, name=None, data=None, quiet=False):
        self.events.append(
            {"kind": kind, "code": code, "name": name, "text": text, "data": data or {},
             "at": now.isoformat(), "local": local.strftime("%H:%M")}
        )
        del self.events[:-30]
        if not quiet:
            self._feed(kind, text, now, code=code)

    def _sigma(self, code):
        return self.daily.get(code, {}).get("sigma", DEFAULT_SIGMA)

    def _detect(self, q, now, local):
        sm = session_minutes(self.cfg)
        window = LIVE["fast_window_min"]
        bench = self.cfg["benchmark"]["code"]
        for code in list(self.st["players"]["ai"]["positions"]) + [bench]:
            qq = q.get(code)
            if not qq:
                continue
            is_bench = code == bench
            sigma = self._sigma(code)
            name = qq["name"] if is_bench else self.st["names"].get(code, qq["name"])
            # 1) 최근 window분 급변: 평소 그 시간 변동폭의 z배 이상
            r = self.hist.ret(code, window * 60, now)
            if r is not None:
                thr = triggers.fast_threshold(
                    sigma, window, sm, LIVE["fast_z"],
                    LIVE["index_min_move"] if is_bench else LIVE["fast_min_move"],
                )
                if abs(r) >= thr and self.cool.ok(("fast", code), now, LIVE["stock_cooldown_min"]):
                    self.cool.mark(("fast", code), now)
                    times = abs(r) / triggers.normal_move(sigma, window, sm)
                    self._add_event(
                        now, local, "index_move" if is_bench else "fast_move",
                        f"{name} 최근 {window}분 {r:+.1%} (평소 그 시간 변동폭의 {times:.1f}배)",
                        code=code, name=name, data={"r": r, "thr": thr},
                    )
            # 2) 오늘 등락이 새 단계에 들어섰을 때 (한 번만)
            step = max(LIVE["index_day_step"] if is_bench else LIVE["day_step_min"], 1.5 * sigma)
            lvl = triggers.day_level(qq["pct"], step)
            prev = self.day_levels.get(code, 0)
            if lvl != 0 and (abs(lvl) > abs(prev) or lvl * prev < 0):
                self.day_levels[code] = lvl
                self._add_event(
                    now, local, "day_step", f"{name} 오늘 {qq['pct']:+.1%} ({'상승' if lvl > 0 else '하락'} {abs(lvl)}단계)",
                    code=code, name=name, data={"pct": qq["pct"]},
                )
            else:
                self.day_levels[code] = lvl

    def _news_codes(self, q):
        held = list(self.st["players"]["ai"]["positions"])
        cand = [s["code"] for s in self._shortlist() if s["code"] not in held]
        stocks = [v for v in q.values() if v["kind"] == "stock" and v["code"] not in held and v["code"] not in cand]
        movers = [v["code"] for v in sorted(stocks, key=lambda v: -abs(v["pct"]))[: LIVE["news_extra_movers"]]]
        return held + cand + movers + MARKET_NEWS[self.market]

    def _remember_news(self, items, now):
        """최근 기사를 종목별로 기억한다. 같은 이야기를 몇 곳이 썼는지 세는 데 쓴다."""
        keep = LIVE["news_memory_min"] * 60
        for it in items:
            when = it.get("when")
            if when and -300 <= (now - when).total_seconds() <= keep:
                self.news_log.setdefault(it["code"], {})[it["key"]] = (when, it.get("source") or "", it["title"])
        for code in list(self.news_log):
            log = {k: v for k, v in self.news_log[code].items() if (now - v[0]).total_seconds() <= keep}
            if log:
                self.news_log[code] = log
            else:
                del self.news_log[code]

    def _credibility(self, code, its, now):
        """기사를 얼마나 믿을지 AI가 판단할 힌트. 최근 1시간 기사·매체 수, 추측성 제목, 앞서 나온 기사."""
        out = {"rumor": any(triggers.looks_like_rumor(it["title"]) for it in its)}
        if code in MARKET_NEWS[self.market]:
            return out  # 시장 전체 피드는 기사마다 다른 이야기라 세어봐야 의미가 없다
        fresh = {it["key"] for it in its}
        recent = sorted(
            ((k, v) for k, v in self.news_log.get(code, {}).items() if (now - v[0]).total_seconds() <= 3600),
            key=lambda kv: kv[1][0], reverse=True,
        )
        out["articles_1h"] = len(recent)
        out["sources_1h"] = len({v[1] for _, v in recent if v[1]})
        out["earlier"] = [
            {"time": v[0].astimezone(self.tz).strftime("%H:%M"), "source": v[1], "title": v[2]}
            for k, v in recent if k not in fresh
        ][:3]
        return out

    def _ref_prices(self, code, when, now, q):
        """기사가 나온 무렵의 (가격, 벤치마크 가격, 그 시각). 기록이 거기까지 안 닿으면 지금 값."""
        bench = self.cfg["benchmark"]["code"]
        ago = (now - when).total_seconds()
        if ago > 0:
            p = self.hist.price_ago(code, ago, now)
            if p:
                return p, self.hist.price_ago(bench, ago, now), when
        return (q.get(code) or {}).get("price"), (q.get(bench) or {}).get("price"), now

    def _poll_news(self, q, now, local):
        if self.last_news_at and (now - self.last_news_at).total_seconds() < LIVE["news_seconds"]:
            return
        self.last_news_at = now
        items = self._news(self._news_codes(q))
        self._remember_news(items, now)
        fresh = triggers.fresh_news(items, self.seen, now, LIVE["news_fresh_min"])
        self._save_seen()
        by_code = {}
        for it in fresh:
            by_code.setdefault(it["code"], []).append(it)
        for code, its in by_code.items():
            name = MARKET_FEED_NAMES.get(code) or self.st["names"].get(code) or (q.get(code) or {}).get("name") or code
            shown = [
                {"time": it["when"].astimezone(self.tz).strftime("%H:%M"), "source": it["source"], "title": it["title"]}
                for it in its
            ]
            # 나중에 '기사 뒤로 주가가 어떻게 움직였나'를 볼 기준값
            pc = self._price_code(code)
            p0, b0, t0 = self._ref_prices(pc, min(it["when"] for it in its), now, q)
            data = {"items": shown, "price": p0, "bench": b0, "since": t0.astimezone(self.tz).strftime("%H:%M"),
                    **self._credibility(code, its, now)}
            cur = (q.get(pc) or {}).get("price")
            if t0 < now and p0 and cur:
                data["moved"] = cur / p0 - 1
            self._add_event(
                now, local, "news", f"{name}: {its[0]['title']}" + (f" 외 {len(its) - 1}건" if len(its) > 1 else ""),
                code=code, name=name, data=data,
            )

    def _price_code(self, code):
        """시장 전체 뉴스는 벤치마크 가격으로 반응을 본다."""
        return self.cfg["benchmark"]["code"] if code in MARKET_NEWS[self.market] else code

    def _check_followups(self, q, now, local):
        """뉴스 점검 때 '주가 반응을 보고 다시 보자'고 한 종목. 시간이 되면 반응을 보여주고 다시 부른다."""
        for code in [c for c, f in self.followups.items() if now >= f["due"]]:
            self._fire_followup(code, q, now, local)

    def _fire_followup(self, code, q, now, local):
        bench = self.cfg["benchmark"]["code"]
        f = self.followups.pop(code)
        p1, b1 = (q.get(code) or {}).get("price"), (q.get(bench) or {}).get("price")
        if not p1 or not f.get("price"):
            return
        r = p1 / f["price"] - 1
        rb = b1 / f["bench"] - 1 if b1 and f.get("bench") else None
        vs = f", 같은 동안 시장 {rb:+.1%}" if rb is not None and code != bench else ""
        self._add_event(
            now, local, "news_followup",
            f"{f['name']} 뉴스 확인: {f['since']} 기사 뒤로 주가 {r:+.1%} "
            f"({self._fmt(f['price'])} → {self._fmt(p1)}){vs}. 기사: {f['title']}",
            code=code, name=f["name"], data={"r": r, "rb": rb},
        )

    def _schedule_followups(self, res, want, blocked, q, now):
        """뉴스 점검 뒤 주가 반응을 다시 볼 예약. 매매를 막은 종목은 AI가 안 적었어도 다시 본다.

        막은 종목에 이번 기사가 없으면(예: 시장 뉴스를 보고 보유 종목을 팔려던 경우) 이번 기사 전부를 다시 본다.
        """
        news = {self._price_code(e["code"]): e for e in res["events"] if e["kind"] == "news" and e.get("code")}
        want = {self._price_code(c) for c in want}
        if blocked:
            hit = {self._price_code(c) for c in blocked} & set(news)
            want |= hit or set(news)
        bench = self.cfg["benchmark"]
        out = []
        for code in sorted(want & set(news)):
            if code in self.followups:
                continue
            d = news[code].get("data") or {}
            price = d.get("price") or (q.get(code) or {}).get("price")
            if not price:
                continue
            items = d.get("items") or []
            name = bench["name"] if code == bench["code"] else self.st["names"].get(code) or news[code]["name"]
            self.followups[code] = {
                "due": now + timedelta(minutes=LIVE["news_confirm_min"]), "price": price, "bench": d.get("bench"),
                "since": d.get("since") or now.astimezone(self.tz).strftime("%H:%M"),
                "title": items[0]["title"] if items else news[code]["text"], "name": name,
            }
            out.append({"code": code, "name": name, "due": self.followups[code]["due"].isoformat()})
        return out

    # ------------------------------------------------------------------ AI 호출
    def _minutes_left(self, local):
        return int((self._times(local)[1] - local).total_seconds() / 60)

    def _maybe_review(self, q, now, local):
        if self.job_running:
            return
        if self.last_job_at and (now - self.last_job_at).total_seconds() < LIVE["job_gap_seconds"]:
            return
        if not self.events:
            due = self.next_check_at and now >= self.next_check_at
            if not due or self._minutes_left(local) < 15:
                return
            self._add_event(now, local, "heartbeat", "네가 정한 다음 점검 시간이 됐어 (그동안 특별한 일은 없었어)", quiet=True)
        if self.counts["reviews"] >= LIVE["max_reviews_per_day"]:
            self.events.clear()
            if not self.budget_noted:
                self.budget_noted = True
                self._feed("info", f"오늘 AI 점검 {LIVE['max_reviews_per_day']}번을 다 써서 이후 사건은 넘어가요", now)
            return
        # 가격·계획에서 생긴 사건이 먼저다. 기사만 있으면 '뉴스 점검'(매매 없음)으로 따로 본다.
        priced = [e for e in self.events if e["kind"] != "news"]
        if priced:
            if self.last_react_at and (now - self.last_react_at) < timedelta(minutes=LIVE["review_cooldown_min"]):
                return
            mode = "trade"
            codes = {e.get("code") for e in priced if e.get("code")}
            # 확인을 기다리던 뉴스가 있는 종목이 먼저 움직였으면 기다리지 않고 그 뉴스도 같이 보여준다
            for code in [c for c in self.followups if c in codes]:
                self._fire_followup(code, q, now, local)
            priced = [e for e in self.events if e["kind"] != "news"]
            related = [e for e in self.events if e["kind"] == "news" and e.get("code") in codes]
            batch = (priced + related)[:12]  # 같은 종목 기사는 주가가 확인해준 뉴스로 같이 보여준다
            self.events = [e for e in self.events if not any(e is b for b in batch)]
        else:
            if self.counts["triages"] >= LIVE["max_triages_per_day"]:
                self.events.clear()
                return
            mode = "news"
            batch, self.events = self.events[:10], self.events[10:]
            self.counts["triages"] += 1
        extra = {self._price_code(e["code"]) for e in batch if e.get("code")}
        ctx = self._build_ctx(q, now, local, extra_codes=extra)
        self.last_job_at = now
        self.job_running = True
        job = {"events": batch, "ctx": ctx, "mode": mode}
        if self.sync_worker:
            self._work(job)
        else:
            threading.Thread(target=self._work, args=(job,), daemon=True).start()

    def _work(self, job):
        events, ctx, mode = job["events"], job["ctx"], job.get("mode", "trade")
        out = {"events": events, "ref_prices": ctx["ref_prices"], "mode": mode}
        t0 = time.time()
        try:
            triage = None
            if mode == "news":
                triage = self.ai.triage(ctx, events)
                out["triage"] = {k: triage.get(k) for k in ("verdict", "importance", "reason")}
                if triage["verdict"] != "review":
                    out["ignored"] = True
                    return
            out = {**self.ai.react(ctx, events, triage=triage, mode=mode), **out}  # 모드 같은 엔진 값이 이긴다
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"
        finally:
            out["seconds"] = round(time.time() - t0, 1)
            self.results.put(out)

    def _thesis(self, code):
        for r in reversed(self.st["reactions"]):
            for a in r.get("actions", []):
                if a["code"] == code and a.get("reason"):
                    return a["reason"]
        for j in reversed(self.st["journal"]):
            for t in j.get("targets") or []:
                if t["code"] == code and t.get("reason"):
                    return t["reason"]
        return None

    def _build_ctx(self, q, now, local, extra_codes=()):
        cfg, st = self.cfg, self.st
        ai = st["players"]["ai"]
        prices = {c: (q[c]["price"] if c in q else p["avg"]) for c, p in ai["positions"].items()}
        eq = broker.equity(ai, prices)
        holdings = []
        for code, pos in ai["positions"].items():
            plan, qq = st["plans"].get(code) or {}, q.get(code) or {}
            holdings.append({
                "code": code, "name": pos["name"], "shares": pos["shares"], "avg": pos["avg"],
                "price": prices[code], "weight": pos["shares"] * prices[code] / eq,
                "pnl": prices[code] / pos["avg"] - 1, "day": qq.get("pct"),
                "r15": self.hist.ret(code, 900, now), "r60": self.hist.ret(code, 3600, now),
                "plan_text": planlib.describe(plan, self._fmt) if plan else None,
                "thesis": self._thesis(code),
            })
        bcode = cfg["benchmark"]["code"]
        bench = {
            "name": cfg["benchmark"]["name"], "day": (q.get(bcode) or {}).get("pct"),
            "r15": self.hist.ret(bcode, 900, now), "r60": self.hist.ret(bcode, 3600, now),
        }
        ranked = sorted((v for v in q.values() if v["kind"] == "stock"), key=lambda v: v["pct"])

        def mover(v):
            return {"code": v["code"], "name": v["name"], "day": v["pct"], "price": v["price"]}

        movers_down = [mover(v) for v in ranked[:5] if v["pct"] < 0]
        movers_up = [mover(v) for v in reversed(ranked[-5:]) if v["pct"] > 0]
        candidates = [
            {"code": s["code"], "name": s.get("name") or q[s["code"]]["name"], "price": q[s["code"]]["price"],
             "day": q[s["code"]]["pct"], "why": s.get("why", "")}
            for s in self._shortlist()
            if s["code"] not in ai["positions"] and s["code"] in q
        ]
        cap = float(cfg["capital"])
        standings = []
        for key, label in PLAYERS.items():
            pl = st["players"][key]
            live_eq = broker.equity(pl, {c: q[c]["price"] for c in pl["positions"] if c in q})
            standings.append((label, live_eq / cap - 1))
        today = local.date().isoformat()
        todays = [t for t in ai["trades"] if t.get("date") == today]
        alerts = [{**a, "name": st["names"].get(a["code"]) or (q.get(a["code"]) or {}).get("name")}
                  for a in st.get("alerts") or []]
        allowed = (set(ai["positions"]) | {c["code"] for c in candidates}
                   | {m["code"] for m in movers_up + movers_down} | {a["code"] for a in alerts}
                   | {c for c in extra_codes if c in q and c != cfg["benchmark"]["code"]})
        ref = {c: q[c]["price"] for c in allowed if c in q}
        ref.update(prices)
        return {
            "market": self.market, "market_name": cfg["name"], "currency": cfg["currency"],
            "fee": cfg["fee"], "sell_tax": cfg["sell_tax"], "slippage": SLIPPAGE[self.market],
            "bench_name": cfg["benchmark"]["name"], "bench_code": cfg["benchmark"]["code"],
            "now_local": local.strftime("%H:%M"), "minutes_left": self._minutes_left(local),
            "equity": eq, "cash": ai["cash"], "holdings": holdings, "bench": bench,
            "movers_up": movers_up, "movers_down": movers_down, "candidates": candidates,
            "standings": standings, "trades_today": len(todays), "fees_today": sum(t["cost"] for t in todays),
            "alerts": alerts, "reviews_left": max(0, LIVE["max_reviews_per_day"] - self.counts["reviews"] - 1),
            "allowed": sorted(allowed), "ref_prices": ref,
        }

    # ------------------------------------------------------------------ AI 결과 반영
    def _apply_results(self, q, now, local):
        while True:
            try:
                res = self.results.get_nowait()
            except queue.Empty:
                return
            self.job_running = False
            if res.get("error"):
                self.last_react_at = now
                self.next_check_at = max(self.next_check_at or now, now + timedelta(minutes=15))
                self._feed("error", f"AI 호출 실패: {res['error']}", now)
                # 중요한 사건은 한 번 더 기회를 준다 (뉴스는 버린다)
                retry = [e for e in res["events"] if e["kind"] != "news" and not e.get("retried")]
                for e in retry:
                    e["retried"] = True
                self.events = (retry + self.events)[-30:]
            elif res.get("ignored"):
                tri = res["triage"]
                self._feed("triage", f"새 소식을 훑어봤는데 매매할 일은 아니래요: {tri.get('reason', '')}", now, triage=tri)
            else:
                self.counts["reviews"] += 1
                self.last_react_at = now
                minutes = res.get("next_check_min") or LIVE["heartbeat_min"]
                self.next_check_at = now + timedelta(minutes=minutes)
                self._execute_react(res, q, now, local)

    def _execute_react(self, res, q, now, local):
        ai = self.st["players"]["ai"]
        names = self.st["names"]
        day = local.date().isoformat()
        prices = {}
        for code in set(ai["positions"]) | {a["code"] for a in res["actions"]}:
            if code in q:
                prices[code] = q[code]["price"]
            elif code in ai["positions"]:
                prices[code] = ai["positions"][code]["avg"]
        eq = broker.equity(ai, prices)
        cur_w = {c: p["shares"] * prices[c] / eq for c, p in ai["positions"].items()}
        targets, touched, notes = dict(cur_w), {}, []
        plan_updates, plan_only, blocked = {}, {}, {}
        limit_hit = self.counts["trades"] >= LIVE["max_live_trades_per_day"]
        news_mode = res.get("mode") == "news"
        for a in res["actions"]:
            code, w = a["code"], a["weight"]
            qq = q.get(code)
            name = names.get(code) or (qq or {}).get("name") or code
            if news_mode and w is not None:  # 기사만 보고는 안 사고판다. 같이 적은 계획은 받는다
                if abs(w - cur_w.get(code, 0)) > 0.005:
                    blocked[code] = name
                if not a["plan"] or code not in ai["positions"]:
                    continue
                w = None
            if w is None:  # 매매 없이 계획만 고치기
                if code in ai["positions"]:
                    plan_updates[code] = a["plan"]
                    plan_only[code] = a
                else:
                    notes.append(f"{name}: 들고 있지 않은 종목이라 계획만 바꿀 수는 없어서 건너뜀")
                continue
            if not qq or not self._tradable(qq, now):
                notes.append(f"{name}: 지금은 거래할 수 없어서 건너뜀")
                continue
            buying = w > cur_w.get(code, 0) + 0.005
            ref = res["ref_prices"].get(code)
            if buying and limit_hit:
                notes.append(f"{name}: 오늘 매매 한도({LIVE['max_live_trades_per_day']}건)를 넘어서 매수는 건너뜀")
                continue
            if buying and ref and qq["price"] / ref - 1 > LIVE["chase_limit"]:
                notes.append(f"{name}: AI가 보던 가격보다 {qq['price'] / ref - 1:+.1%} 올라서 추격매수는 건너뜀")
                continue
            touched[code] = a
            targets[code] = w
            names.setdefault(code, name)
        targets = {c: w for c, w in targets.items() if w > 0}
        # 최대 종목 수: 새로 사려던 것 중 비중이 작은 것부터 포기
        while len(targets) > MAX_POSITIONS:
            new = [c for c in targets if c in touched and c not in ai["positions"]]
            if not new:
                break
            drop = min(new, key=lambda c: targets[c])
            notes.append(f"{names.get(drop, drop)}: 최대 {MAX_POSITIONS}종목을 넘어서 건너뜀")
            del targets[drop]
            touched.pop(drop, None)
        # 현금이 모자라면 늘리려던 만큼만 줄여서 맞춘다
        over = sum(targets.values()) - 1.0
        if over > 1e-9:
            inc = {c: targets[c] - cur_w.get(c, 0) for c in touched if c in targets and targets[c] > cur_w.get(c, 0)}
            total_inc = sum(inc.values())
            factor = max(0.0, 1 - over / total_inc) if total_inc > 0 else 0.0
            for c, d in inc.items():
                targets[c] = cur_w.get(c, 0) + d * factor
            targets = {c: w for c, w in targets.items() if w > 1e-9}
        fills = [] if news_mode else broker.rebalance(
            ai, targets, prices, names, self.cfg, day,
            slippage=SLIPPAGE[self.market], at=now.isoformat(), tag="react",
        )
        self.counts["trades"] += len(fills)
        for c, a in touched.items():
            if a["plan"]:
                plan_updates[c] = a["plan"]
        live_prices = {c: q[c]["price"] for c in ai["positions"] if c in q}
        gap = LIVE["news_min_stop_gap"] if news_mode else 0.0
        notes += league.sync_plans(self.st, plan_updates, live_prices, min_gap=gap)
        recheck = []
        if news_mode:
            recheck = self._schedule_followups(res, res.get("recheck") or [], blocked, q, now)
            if blocked:
                notes.insert(0, "기사만 보고는 안 사고팔아서 " + ", ".join(blocked.values())
                             + (f" 매매는 미뤘어요. {LIVE['news_confirm_min']}분 뒤 주가 반응을 보고 다시 판단해요"
                                if recheck else " 매매는 안 했어요"))
        if res.get("alerts") is not None:
            self.st["alerts"] = [
                {**a, "name": names.get(a["code"]) or (q.get(a["code"]) or {}).get("name") or a["code"]}
                for a in res["alerts"]
            ]
        notes += res.get("alert_notes") or []
        plans_now = self.st["plans"]
        actions = [
            {"code": c, "name": names.get(c, c), "from": round(cur_w.get(c, 0), 4), "to": round(targets.get(c, 0), 4),
             "reason": a["reason"], "plan": plans_now.get(c)}
            for c, a in touched.items()
        ] + [
            {"code": c, "name": names.get(c, c), "from": round(cur_w.get(c, 0), 4), "to": None,
             "reason": a["reason"], "plan": plans_now.get(c)}
            for c, a in plan_only.items()
        ]
        rec = {
            "time": now.isoformat(), "date": day, "mode": res.get("mode", "trade"),
            "trigger": [e["text"] for e in res["events"]][:6],
            "assessment": res.get("assessment", ""),
            "cash_reason": res.get("cash_reason", ""),
            "actions": actions,
            "alerts": self.st["alerts"] if res.get("alerts") is not None else None,
            "next_check_min": res.get("next_check_min"),
            "fills": fills, "notes": notes, "seconds": res.get("seconds"),
            "model": (res.get("meta") or {}).get("model"), "triage": res.get("triage"),
            "recheck": recheck, "blocked": list(blocked.values()),
        }
        self.st["reactions"].append(rec)
        del self.st["reactions"][:-200]
        league.save_state(self.market, self.st)
        if fills:
            summary = ", ".join(f"{f['name']} {'매수' if f['side'] == 'buy' else '매도'} {f['shares']:,}주" for f in fills)
        elif blocked and recheck:
            summary = "매매는 주가 반응을 본 뒤로 미뤘어요" + (" (계획은 고침)" if plan_updates else "")
        elif plan_updates:
            summary = "매매 없이 계획만 고쳤어요"
        else:
            summary = "지켜보기로 했어요 (매매 없음)"
        extra = []
        if res.get("alerts") is not None:
            extra.append(f"알림 {len(res['alerts'])}개")
        if recheck:
            extra.append(f"{', '.join(r['name'] for r in recheck)} {LIVE['news_confirm_min']}분 뒤 주가 반응 확인")
        extra.append(f"다음 점검 {res.get('next_check_min') or LIVE['heartbeat_min']}분 뒤")
        label = "AI 뉴스 점검" if news_mode else "AI 판단"
        self._feed(
            "react", f"{label} ({res.get('seconds')}초): {summary}. {res.get('assessment', '')} ({', '.join(extra)})",
            now, reaction=rec,
        )
        for n in notes:
            self._feed("info", n, now)

    # ------------------------------------------------------------------ 기록 (대시보드가 읽는다)
    def _snapshot_base(self, now, phase):
        return {
            "market": self.market, "phase": phase, "updated_at": now.isoformat(), "date": self.today,
            "counts": self.counts, "engine": {"pid": os.getpid(), "started_at": self.started_at.isoformat()},
        }

    def _write_status(self, now, phase, wake, **extra):
        snap = self._read_json(self._file("live")) or {}
        snap.update(self._snapshot_base(now, phase))
        snap.update({"next_wake": wake.isoformat() if wake else None, **extra})
        snap["feed"] = self.feed[-40:] or snap.get("feed", [])
        league.atomic_write(self._file("live"), json.dumps(snap, ensure_ascii=False))

    def _record(self, q, now, local):
        cap = float(self.cfg["capital"])
        players = {}
        for key, pl in self.st["players"].items():
            prices = {c: q[c]["price"] for c in pl["positions"] if c in q}
            eq = broker.equity(pl, prices)
            last_close = pl["history"][-1]["equity"] if pl["history"] else cap
            players[key] = {
                "equity": round(eq, 2), "ret": eq / cap - 1, "day": eq / last_close - 1, "cash": round(pl["cash"], 2),
                "positions": [
                    {"code": c, "name": p["name"], "shares": p["shares"], "avg": p["avg"],
                     "price": prices.get(c, p["avg"]), "day": (q.get(c) or {}).get("pct")}
                    for c, p in pl["positions"].items()
                ],
            }
        bcode = self.cfg["benchmark"]["code"]
        b = q.get(bcode) or {}
        ranked = sorted((v for v in q.values() if v["kind"] == "stock"), key=lambda v: v["pct"])
        snap = self._snapshot_base(now, "open")
        snap.update({
            "poll_seconds": LIVE["poll_seconds"], "players": players, "plans": self.st["plans"],
            "bench": {"code": bcode, "name": self.cfg["benchmark"]["name"], "price": b.get("price"), "day": b.get("pct"),
                      "at": b["at"].isoformat() if b.get("at") else None},
            "quote_age": (now - b["at"]).total_seconds() if b.get("at") else None,
            "movers_up": [{"code": v["code"], "name": v["name"], "day": v["pct"]} for v in reversed(ranked[-5:])],
            "movers_down": [{"code": v["code"], "name": v["name"], "day": v["pct"]} for v in ranked[:5]],
            "job_running": self.job_running, "queued_events": len(self.events),
            "last_react_at": self.last_react_at.isoformat() if self.last_react_at else None,
            "next_check_at": self.next_check_at.isoformat() if self.next_check_at else None,
            "alerts": self.st.get("alerts") or [],
            "followups": [{"code": c, "name": f["name"], "due": f["due"].isoformat(), "title": f["title"]}
                          for c, f in sorted(self.followups.items(), key=lambda kv: kv[1]["due"])],
            "heartbeat_min": LIVE["heartbeat_min"], "feed": self.feed[-40:], "next_wake": None,
        })
        league.atomic_write(self._file("live"), json.dumps(snap, ensure_ascii=False))
        stamp = (local.hour, local.minute)
        if stamp != self.last_minute:
            self.last_minute = stamp
            row = {"t": now.isoformat(), **{k: v["equity"] for k, v in players.items()}}
            RUNTIME.mkdir(parents=True, exist_ok=True)
            path = RUNTIME / f"intraday_{self.market}_{self.today}.jsonl"
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")


def run_forever(markets, log=print, once=False, deps=None):
    engines = [LiveEngine(m, log, (deps or {}).get(m)) for m in markets]
    try:
        while True:
            t0 = time.time()
            now = datetime.now(timezone.utc)
            waits = []
            for e in engines:
                try:
                    waits.append(e.tick(now))
                except Exception as ex:
                    log(f"  ! [{e.market}] 예상 못 한 오류: {type(ex).__name__}: {ex}")
                    if os.environ.get("TRADER_DEBUG"):
                        traceback.print_exc()
                    waits.append(30.0)
            if once:
                return
            time.sleep(max(1.0, min(waits) - (time.time() - t0)))
    except KeyboardInterrupt:
        log("\n멈춥니다. 상태를 저장했어요.")
        for e in engines:
            league.save_state(e.market, e.st)
