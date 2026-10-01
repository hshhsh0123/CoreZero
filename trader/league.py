"""리그 하루치 진행: 밀린 주문 체결 → 확정된 날 평가 → 새 결정."""

import json
import math
import os
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import agenda, brain, broker, corp, data, features, quotes
from . import plans as planlib
from .config import (
    CALENDAR_DAYS,
    MARKETS,
    MONKEY_EVERY,
    MONKEY_PICKS,
    NEWS_PER_STOCK,
    PLAYERS,
    SETTLE_MINUTES,
    SLIPPAGE,
)

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
LOG_DIR = ROOT / "logs"


def atomic_write(path, text):
    """쓰다 만 파일을 다른 프로세스가 읽지 않게, 임시 파일에 쓰고 바꿔치기한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def new_state(market):
    return {
        "market": market,
        "started": None,
        "last_decided": None,
        "decisions": 0,
        "pending": None,
        "players": {p: broker.new_player(MARKETS[market]["capital"]) for p in PLAYERS},
        "names": {},
        "journal": [],
        "plans": {},
        "alerts": [],
        "reactions": [],
        "live": None,
        "updated_at": None,
    }


def load_state(market):
    path = STATE_DIR / f"{market}.json"
    st = json.loads(path.read_text(encoding="utf-8")) if path.exists() else new_state(market)
    st.setdefault("plans", {})
    st.setdefault("alerts", [])
    st.setdefault("reactions", [])
    return st


def save_state(market, st):
    atomic_write(STATE_DIR / f"{market}.json", json.dumps(st, ensure_ascii=False, indent=1))


def sync_plans(st, updates=None, prices=None, min_gap=0.0, sigmas=None):
    """AI 보유 종목의 계획을 맞춘다. 판 종목은 지우고, AI의 새 지시(updates)를 반영하고,
    손절도 트레일링도 없는 종목엔 기본 손절을 건다. 받아들이지 않은 지시에 대한 메모를 돌려준다.
    min_gap은 planlib.apply로 그대로 넘긴다 (계획을 지금 가격에 얼마나 붙일 수 있는지).
    sigmas는 {code: 하루 변동성}. 새로 거는 기본 손절의 폭을 정한다."""
    held = st["players"]["ai"]["positions"]
    plans = st.setdefault("plans", {})
    notes = []
    for code in [c for c in plans if c not in held]:
        del plans[code]
    for code, pos in held.items():
        plan = planlib.migrate(plans.get(code), pos["avg"])
        update = (updates or {}).get(code)
        if update:
            plan, skipped = planlib.apply(plan, update, pos["avg"], (prices or {}).get(code), min_gap)
            notes += [f"{pos['name']}: {n}" for n in skipped]
        plans[code] = planlib.ensure_default(plan, pos["avg"], planlib.default_pct((sigmas or {}).get(code)))
    return notes


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


def slippage(market):
    """종목별 체결 미끄러짐 (기본값과 호가 반 칸 중 큰 쪽)."""
    return broker.slippage_fn(market, SLIPPAGE[market], etfs={MARKETS[market]["benchmark"]["code"]})


def run(market, now=None, log=print):
    now = now or datetime.now(timezone.utc)
    cfg = MARKETS[market]
    st = load_state(market)
    bench = cfg["benchmark"]
    st["names"][bench["code"]] = bench["name"]

    bench_bars = data.bars(market, bench["code"])
    sessions = [b["date"] for b in bench_bars]
    asof = last_complete(market, bench_bars, now)
    today = now.astimezone(ZoneInfo(cfg["tz"])).date().isoformat()
    if asof == today and quotes.session_open(market, bench["code"]):
        # 수능일처럼 장이 늦게 끝나는 날: 시계로는 끝났어도 정규장이 아직 열려 있으면 오늘 봉은 미완성
        earlier = [d for d in sessions if d < today]
        asof = earlier[-1] if earlier else None
        log("  정규장이 아직 열려 있어서 오늘 봉은 확정 전으로 봐요")
    cache = {bench["code"]: bench_bars}

    def get_bars(codes):
        missing = [c for c in codes if c not in cache]
        if missing:
            cache.update(data.bars_many(market, missing))
        return cache

    log(f"[{cfg['name']}] 확정된 마지막 거래일: {asof}")
    for ev in corp.check_splits(st, get_bars(sorted(st.get("closes") or {})), asof or today):
        log(f"  {ev['name']}: 가격이 소급 수정돼서(분할·병합 등) 주식 수를 {ev['ratio']:g}배로 맞췄어요")
    _fill_pending(st, market, sessions, get_bars, log)
    _refresh_yields(st, market, now, log)
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


def _sigmas(codes, bars, before):
    """before 전날까지 일봉으로 구한 하루 변동성. 기본 손절 폭을 정하는 데 쓴다."""
    out = {}
    for code in codes:
        f = features.compute([b for b in bars.get(code) or [] if b["date"] < before])
        if f:
            out[code] = f["vol20"] / math.sqrt(252)
    return out


def _fill_pending(st, market, sessions, get_bars, log, extra_opens=None):
    """결정해 둔 주문을 다음 거래일 시가에 체결한다. 시가를 못 구한 종목은 이유를 남긴다.
    extra_opens는 일봉에 시가가 아직 없을 때 다른 데서 구한 시가 {code: 가격}."""
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
    opens.update({c: p for c, p in (extra_opens or {}).items() if p and not opens.get(c)})
    opens = {c: p for c, p in opens.items() if p}
    unfilled = []
    for name, targets in pending["targets"].items():
        if targets is None:
            continue
        pl = st["players"][name]
        targets = dict(targets)
        keep = (pending.get("keep") or {}).get(name) or []
        if keep:
            # AI가 안 적은 보유 종목은 주식 수 그대로 들고 간다
            eq = broker.equity(pl, opens)
            for code in keep:
                pos = pl["positions"].get(code)
                if pos and opens.get(code) and eq > 0:
                    targets[code] = pos["shares"] * opens[code] / eq
        missing = [c for c in targets if targets[c] > 0 and c not in opens and c not in pl["positions"]]
        unfilled += [(name, c) for c in missing]
        fills = broker.rebalance(pl, targets, opens, st["names"], MARKETS[market], day, slippage=slippage(market), tag="open")
        log(f"  {PLAYERS[name]}: {len(fills)}건 체결 ({day} 시가)")
    for name, code in unfilled:
        log(f"  ! {PLAYERS[name]}: {st['names'].get(code, code)} 시가를 못 받아서 못 샀어요")
    if unfilled:
        entry = next((j for j in reversed(st["journal"]) if j.get("date") == pending["decided_on"]), None)
        if entry is not None:
            entry["unfilled"] = [{"player": n, "code": c, "name": st["names"].get(c, c)} for n, c in unfilled]
    if pending["targets"].get("ai") is not None:
        held = list(st["players"]["ai"]["positions"])
        sync_plans(st, pending.get("plans"), sigmas=_sigmas(held, bars, day))
    st["pending"] = None


def _refresh_yields(st, market, now, log):
    """보유 종목의 배당수익률을 일주일에 한 번 새로 받는다. 배당은 이 값으로 매일 조금씩 쌓는다."""
    book = st.setdefault("div_yield", {})
    bench = MARKETS[market]["benchmark"]
    held = sorted({c for pl in st["players"].values() for c in pl["positions"]})
    week_ago = (now - timedelta(days=7)).date().isoformat()
    stale = [c for c in held if (book.get(c) or {}).get("date", "") < week_ago]
    if not stale:
        return
    got = {}
    try:
        if market == "kr":
            with ThreadPoolExecutor(max_workers=8) as ex:
                for code, info in zip(stale, ex.map(_kr_info_safe, stale)):
                    got[code] = (info or {}).get("div_yield")
        else:
            got = {c: v.get("div_yield") for c, v in data.us_info(stale).items()}
    except Exception as e:
        log(f"  ! 배당수익률을 못 받았어요: {e}")
    for code in stale:
        y = got.get(code)
        if y is None and code == bench["code"]:
            y = bench.get("div_yield")
        book[code] = {"y": y or 0.0, "date": now.date().isoformat()}


def _kr_info_safe(code):
    try:
        return data.kr_info(code)
    except Exception:
        return None


def _mark(st, sessions, asof, get_bars):
    """확정된 거래일마다 배당을 쌓고 종가 기준 평가금액을 남긴다. 다음 날 분할 확인용 종가도 적어 둔다."""
    if not st["started"] or not asof:
        return
    todo = [d for d in sessions if st["started"] < d <= asof]
    yields = {c: v.get("y") for c, v in (st.get("div_yield") or {}).items()}
    for pl in st["players"].values():
        have = {h["date"] for h in pl["history"]}
        days = [d for d in todo if d not in have]
        if not days:
            continue
        bars = get_bars(sorted(pl["positions"]))
        for d in days:
            prices = {c: _close_asof(bars.get(c), d) for c in pl["positions"]}
            prices = {c: p for c, p in prices.items() if p}
            div = corp.accrue_dividends(pl, prices, yields)
            row = {"date": d, "equity": round(broker.equity(pl, prices), 4)}
            if div:
                row["div"] = round(div, 4)
            pl["history"].append(row)
    held = sorted({c for pl in st["players"].values() for c in pl["positions"]})
    bars = get_bars(held)
    corp.record_closes(st, asof, {c: _price_on(bars.get(c), asof, "close") for c in held})


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
    _refresh_sectors(st, market, uni, held, log)
    bars = get_bars(sorted(set(info) | held))
    by_code = {}
    for code in sorted(set(info) | held):
        f = features.compute(features.upto(bars.get(code) or [], asof))
        if f:
            item = dict(info.get(code) or {"code": code, "name": st["names"].get(code, code)})
            item["f"] = f
            item["sector"] = st["sectors"].get(code) or item.get("sector") or ""
            by_code[code] = item

    ctx = _context(st, market, asof, by_code)
    symbols = {(u.get("symbol") or u["code"].split(".")[0]): u["name"] for u in uni}
    symbols.update({c.split(".")[0]: st["names"].get(c, c) for c in held})
    ctx["agenda"] = st["agenda"] = _agenda(market, asof, symbols, log)
    entry = {"date": asof, "decided_at": now.isoformat()}
    log_payload = {"date": asof, "market": market}
    ai_plans = {}
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
            d["disclosures"] = _recent_disclosures(market, code, cutoff)
            details.append(d)
        decided = brain.decide(ctx, details)
        ai_targets = decided["targets"]
        ai_plans = decided.get("plans") or {}
        ai_keep = decided.get("keep") or []
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
                    "plan": ai_plans.get(c) or None,
                }
                for c, w in sorted(ai_targets.items(), key=lambda kv: -kv[1])
            ],
            cash_reason=decided["cash_reason"],
            kept=[{"code": c, "name": st["names"].get(c, c)} for c in ai_keep],
            notes=decided.get("notes") or [],
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
        ai_targets, ai_keep = None, []
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
        "plans": ai_plans,
        "keep": {"ai": ai_keep},
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
    fmt = lambda x: brain.fmt_price(x, market)
    sectors = st.get("sectors") or {}
    holdings = [
        {
            "code": c,
            "name": pos["name"],
            "weight": pos["shares"] * prices[c] / eq,
            "pnl": prices[c] / pos["avg"] - 1,
            "sector": sectors.get(c, ""),
            "plan": planlib.describe(planlib.migrate(st["plans"].get(c), pos["avg"]), fmt) if st["plans"].get(c) else None,
        }
        for c, pos in ai["positions"].items()
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
        "sectors": {c: sectors.get(c, "") for c in set(by_code) | set(ai["positions"])},
        "last_view": last_view,
    }


def _refresh_sectors(st, market, uni, held, log):
    """종목별 업종 이름을 채운다. 업종은 잘 안 바뀌니까 한 번 받은 건 계속 쓴다."""
    book = st.setdefault("sectors", {})
    if market == "us":
        items = list(uni)
        missing = [c for c in held if c not in {u["code"] for u in uni}]
        if missing:
            try:
                items += list(data.us_info(missing).values())
            except Exception as e:
                log(f"  ! 업종을 못 받았어요: {e}")
        book.update(us_sector_groups(items))
        return
    missing = [c for c in sorted({u["code"] for u in uni} | set(held)) if c not in book]
    if not missing:
        return
    try:
        names = data.industries()
        with ThreadPoolExecutor(max_workers=8) as ex:
            for code, info in zip(missing, ex.map(_kr_info_safe, missing)):
                if info and info.get("industry"):
                    book[code] = names.get(info["industry"], f"업종 {info['industry']}")
    except Exception as e:
        log(f"  ! 업종을 못 받았어요: {e}")


def us_sector_groups(items):
    """미국 종목의 업종 묶음. 네이버 업종(TRBC 8자리)은 '반도체'와 '반도체 장비 및 테스트'처럼 잘게 나뉘어서
    앞 6자리(산업 그룹)로 묶는다. 이름은 그 묶음에 든 업종 이름들을 이어 붙인다."""
    groups = {}
    for u in items:
        if u.get("sector"):
            key = (u.get("sector_code") or "")[:6] or u["sector"]
            groups.setdefault(key, set()).add(u["sector"])
    out = {}
    for u in items:
        if u.get("sector"):
            key = (u.get("sector_code") or "")[:6] or u["sector"]
            out[u["code"]] = " / ".join(sorted(groups[key]))
    return out


def _recent_disclosures(market, code, cutoff):
    """한국 종목의 최근 거래소 공시 (결정 시점 이전 것만)."""
    if market != "kr":
        return []
    try:
        rows = data.disclosures(code, 5)
    except Exception:
        return []
    return [
        {"time": r["when"].strftime("%m-%d %H:%M"), "title": r["title"]}
        for r in rows
        if r.get("when") and (cutoff is None or r["when"] < cutoff)
    ][:4]


def _agenda(market, asof, symbols, log):
    """결정 다음 날부터 며칠 안의 일정 (FOMC, 옵션 만기, 미국 실적 발표)."""
    try:
        start = date.fromisoformat(asof) + timedelta(days=1)
        fomc = agenda.fomc_dates(STATE_DIR / "calendar.json", today=start)
        earnings = []
        if market == "us" and symbols:
            found = agenda.earnings_us(agenda.trading_days_ahead(start, 5), list(symbols))
            earnings = [(d, f"{symbols.get(sym, sym)}({sym})", when) for d, sym, when in found]
        return agenda.upcoming(market, start, CALENDAR_DAYS, fomc=fomc, earnings=earnings)
    except Exception as e:
        log(f"  ! 일정을 못 받았어요: {e}")
        return []


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
    (folder / f"{asof}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
