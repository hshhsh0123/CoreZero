"""리그 하루치 진행: 밀린 주문 체결 → 확정된 날 평가 → 새 결정."""

import json
import random
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import brain, broker, data, features
from .config import MARKETS, MONKEY_EVERY, MONKEY_PICKS, NEWS_PER_STOCK, PLAYERS, SETTLE_MINUTES

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
LOG_DIR = ROOT / "logs"


def load_state(market):
    path = STATE_DIR / f"{market}.json"
    if path.exists():
        return json.loads(path.read_text())
    return {
        "market": market,
        "started": None,
        "last_decided": None,
        "decisions": 0,
        "pending": None,
        "players": {p: broker.new_player(MARKETS[market]["capital"]) for p in PLAYERS},
        "names": {},
        "journal": [],
        "live": None,
        "updated_at": None,
    }


def save_state(market, st):
    STATE_DIR.mkdir(exist_ok=True)
    (STATE_DIR / f"{market}.json").write_text(json.dumps(st, ensure_ascii=False, indent=1))


def _hm(s):
    h, m = s.split(":")
    return time(int(h), int(m))


def last_complete(market, bench_bars, now):
    """장이 끝나서 확정된 마지막 거래일. 장중이면 전날."""
    cfg = MARKETS[market]
    tz = ZoneInfo(cfg["tz"])
    local = now.astimezone(tz)
    settle = datetime.combine(local.date(), _hm(cfg["close"]), tz) + timedelta(minutes=SETTLE_MINUTES)
    dates = [b["date"] for b in bench_bars]
    if dates and dates[-1] >= local.date().isoformat() and local < settle:
        return dates[-2] if len(dates) > 1 else None
    return dates[-1] if dates else None


def _price_on(bars, day, field):
    for b in reversed(bars or []):
        if b["date"] == day:
            return b[field]
    return None


def _close_asof(bars, day):
    for b in reversed(bars or []):
        if b["date"] <= day:
            return b["close"]
    return None


def run(market, now=None, log=print):
    now = now or datetime.now(timezone.utc)
    cfg = MARKETS[market]
    st = load_state(market)
    bench = cfg["benchmark"]
    st["names"][bench["code"]] = bench["name"]

    bench_bars = data.bars(market, bench["code"])
    sessions = [b["date"] for b in bench_bars]
    asof = last_complete(market, bench_bars, now)
    cache = {bench["code"]: bench_bars}

    def get_bars(codes):
        missing = [c for c in codes if c not in cache]
        if missing:
            cache.update(data.bars_many(market, missing))
        return cache

    log(f"[{cfg['name']}] 확정된 마지막 거래일: {asof}")
    _fill_pending(st, market, sessions, get_bars, log)
    _mark(st, sessions, asof, get_bars)
    if asof and asof != st["last_decided"] and not st["pending"]:
        _decide(st, market, asof, now, sessions, get_bars, log)
        _fill_pending(st, market, sessions, get_bars, log)
    elif st["pending"]:
        log(f"  주문 대기 중 ({st['pending']['decided_on']} 결정분, 다음 장 시가에 체결)")
    else:
        log("  새로 확정된 거래일이 없어서 이번엔 쉰다")
    st["live"] = _live(st, get_bars, now)
    st["updated_at"] = now.isoformat()
    save_state(market, st)
    return st


def _fill_pending(st, market, sessions, get_bars, log):
    pending = st["pending"]
    if not pending:
        return
    later = [d for d in sessions if d > pending["decided_on"]]
    if not later:
        return
    day = later[0]
    codes = set()
    for targets in pending["targets"].values():
        codes |= set(targets or {})
    for pl in st["players"].values():
        codes |= set(pl["positions"])
    bars = get_bars(sorted(codes))
    opens = {c: _price_on(bars.get(c), day, "open") for c in codes}
    opens = {c: p for c, p in opens.items() if p}
    for name, targets in pending["targets"].items():
        if targets is None:
            continue
        fills = broker.rebalance(st["players"][name], targets, opens, st["names"], MARKETS[market], day)
        log(f"  {PLAYERS[name]}: {len(fills)}건 체결 ({day} 시가)")
    st["pending"] = None


def _mark(st, sessions, asof, get_bars):
    """확정된 거래일마다 종가 기준 평가금액을 남긴다."""
    if not st["started"] or not asof:
        return
    todo = [d for d in sessions if st["started"] < d <= asof]
    for pl in st["players"].values():
        have = {h["date"] for h in pl["history"]}
        days = [d for d in todo if d not in have]
        if not days:
            continue
        bars = get_bars(sorted(pl["positions"]))
        for d in days:
            prices = {c: _close_asof(bars.get(c), d) for c in pl["positions"]}
            prices = {c: p for c, p in prices.items() if p}
            pl["history"].append({"date": d, "equity": round(broker.equity(pl, prices), 4)})


def _decide(st, market, asof, now, sessions, get_bars, log):
    cfg = MARKETS[market]
    if st["started"] is None:
        st["started"] = asof
        for pl in st["players"].values():
            pl["history"].append({"date": asof, "equity": float(cfg["capital"])})

    log("  후보 종목 불러오는 중...")
    uni = data.universe(market)
    info = {u["code"]: u for u in uni}
    for u in uni:
        st["names"][u["code"]] = u["name"]
    held = set(st["players"]["ai"]["positions"])
    bars = get_bars(sorted(set(info) | held))
    by_code = {}
    for code in sorted(set(info) | held):
        f = features.compute(features.upto(bars.get(code) or [], asof))
        if f:
            item = dict(info.get(code) or {"code": code, "name": st["names"].get(code, code)})
            item["f"] = f
            by_code[code] = item

    ctx = _context(st, market, asof, by_code)
    entry = {"date": asof, "decided_at": now.isoformat()}
    log_payload = {"date": asof, "market": market}
    try:
        log(f"  AI 1단계: 후보 {len(info)}개 중에서 추리는 중...")
        scouted = brain.scout(ctx, [by_code[c] for c in info if c in by_code])
        why = {s["code"]: s["why"] for s in scouted["shortlist"]}
        detail_codes = list(dict.fromkeys([s["code"] for s in scouted["shortlist"]] + sorted(held)))
        cutoff = _news_cutoff(market, asof, sessions)
        log(f"  AI 2단계: {len(detail_codes)}개 종목 뉴스·지표 보는 중...")
        details = []
        for code in detail_codes:
            if code not in by_code:
                continue
            d = dict(by_code[code])
            d["why"] = why.get(code, "지금 들고 있는 종목")
            d["news"] = data.news(market, code, NEWS_PER_STOCK, before=cutoff)
            d["fund"] = data.fundamentals(market, code)
            details.append(d)
        decided = brain.decide(ctx, details)
        ai_targets = decided["targets"]
        entry.update(
            scout_view=scouted["market_view"],
            shortlist=[{**s, "name": st["names"].get(s["code"], s["code"])} for s in scouted["shortlist"]],
            market_view=decided["market_view"],
            targets=[
                {
                    "code": c,
                    "name": st["names"].get(c, c),
                    "weight": w,
                    "reason": decided["reasons"].get(c, ""),
                }
                for c, w in sorted(ai_targets.items(), key=lambda kv: -kv[1])
            ],
            cash_reason=decided["cash_reason"],
            model=decided["meta"].get("model"),
            usage={"scout": scouted["meta"].get("usage"), "decide": decided["meta"].get("usage")},
        )
        log_payload.update(
            scout={k: v for k, v in scouted.items()},
            decide={k: v for k, v in decided.items()},
            news_cutoff=cutoff.isoformat() if cutoff else None,
        )
        summary = ", ".join(f"{st['names'].get(c, c)} {w:.0%}" for c, w in ai_targets.items())
        log(f"  AI 결정: {summary or '전부 현금'}")
    except Exception as e:
        log(f"  ! AI 결정 실패, 이번엔 매매 안 함: {e}")
        ai_targets = None
        entry["error"] = str(e)

    monkey_targets = None
    pool = sorted(c for c in info if c in by_code)
    if st["decisions"] % MONKEY_EVERY == 0 and len(pool) >= MONKEY_PICKS:
        picks = random.Random(f"{market}-{asof}").sample(pool, MONKEY_PICKS)
        monkey_targets = {c: round(1 / MONKEY_PICKS, 4) for c in picks}
        entry["monkey"] = [st["names"].get(c, c) for c in picks]
        log(f"  원숭이 다트: {', '.join(entry['monkey'])}")

    hodl_targets = {cfg["benchmark"]["code"]: 1.0} if st["decisions"] == 0 else None

    st["pending"] = {
        "decided_on": asof,
        "targets": {"ai": ai_targets, "monkey": monkey_targets, "hodl": hodl_targets},
    }
    st["last_decided"] = asof
    st["decisions"] += 1
    st["journal"].append(entry)
    _write_log(market, asof, log_payload)


def _context(st, market, asof, by_code):
    cfg = MARKETS[market]
    ai = st["players"]["ai"]
    prices = {
        c: by_code[c]["f"]["close"] if c in by_code else pos["avg"]
        for c, pos in ai["positions"].items()
    }
    eq = broker.equity(ai, prices)
    holdings = [
        {
            "code": c,
            "name": pos["name"],
            "weight": pos["shares"] * prices[c] / eq,
            "pnl": prices[c] / pos["avg"] - 1,
        }
        for c, pos in ai["positions"].items()
    ]
    capital = cfg["capital"]
    standings = [
        (label, st["players"][p]["history"][-1]["equity"] / capital - 1)
        for p, label in PLAYERS.items()
    ]
    last_view = next((j["market_view"] for j in reversed(st["journal"]) if j.get("market_view")), None)
    return {
        "market": market,
        "market_name": cfg["name"],
        "currency": cfg["currency"],
        "fee": cfg["fee"],
        "sell_tax": cfg["sell_tax"],
        "bench_name": cfg["benchmark"]["name"],
        "asof": asof,
        "equity": eq,
        "cash": ai["cash"],
        "holdings": holdings,
        "standings": standings,
        "last_view": last_view,
    }


def _news_cutoff(market, asof, sessions):
    """다음 장이 이미 열렸으면 그 시가 이전 뉴스만 보게 한다 (미래 정보 차단)."""
    later = [d for d in sessions if d > asof]
    if not later:
        return None
    cfg = MARKETS[market]
    return datetime.combine(date.fromisoformat(later[0]), _hm(cfg["open"]), ZoneInfo(cfg["tz"]))


def _live(st, get_bars, now):
    """장중 포함 지금 가격으로 평가."""
    out = {}
    for name, pl in st["players"].items():
        bars = get_bars(sorted(pl["positions"]))
        prices = {c: bars[c][-1]["close"] for c in pl["positions"] if bars.get(c)}
        dates = {c: bars[c][-1]["date"] for c in pl["positions"] if bars.get(c)}
        out[name] = {
            "equity": round(broker.equity(pl, prices), 4),
            "prices": prices,
            "price_dates": dates,
        }
    return {"at": now.isoformat(), "players": out}


def _write_log(market, asof, payload):
    folder = LOG_DIR / market
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{asof}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1))
