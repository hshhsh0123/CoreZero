"""state/*.json과 runtime/의 실시간 기록을 읽어서 대시보드 HTML 한 장(또는 JSON)을 만든다."""

import json
from datetime import datetime, timezone
from pathlib import Path

from . import league, live
from . import plans as planlib
from .config import (
    LIVE,
    MARKETS,
    MAX_POSITIONS,
    MAX_WEIGHT,
    MIN_WEIGHT,
    MODEL_DECIDE,
    MODEL_REACT,
    MODEL_TRIAGE,
    MONKEY_EVERY,
    MONKEY_PICKS,
    PLAYERS,
    SLIPPAGE,
)

TEMPLATE = Path(__file__).with_name("report_template.html")
SHELL = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
</head>
<body>
{body}
</body>
</html>
"""
FEED_LIMIT = 80
POINT_LIMIT = 400


# ---------------------------------------------------------------- 파일 읽기 (쓰는 도중인 파일도 견딘다)
def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_jsonl(path, tail=None):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return []
    if tail:
        lines = lines[-tail:]
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _downsample(rows, limit):
    if len(rows) <= limit:
        return rows
    step = len(rows) / limit
    out = [rows[int(i * step)] for i in range(limit)]
    out[-1] = rows[-1]
    return out


def _parse_iso(s):
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 데이터 모으기
def collect(runtime_dir=None, live_mode=False):
    rdir = Path(runtime_dir) if runtime_dir else live.RUNTIME
    markets = []
    for key, cfg in MARKETS.items():
        path = league.STATE_DIR / f"{key}.json"
        st = _read_json(path) if path.exists() else None
        if st:
            st.setdefault("plans", {})
            st.setdefault("reactions", [])
            markets.append(_market_view(key, cfg, st, rdir))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "live_mode": live_mode,
        "markets": markets,
        "rules": {
            "model": MODEL_DECIDE,
            "model_react": MODEL_REACT,
            "model_triage": MODEL_TRIAGE,
            "max_positions": MAX_POSITIONS,
            "min_weight": MIN_WEIGHT,
            "max_weight": MAX_WEIGHT,
            "monkey_picks": MONKEY_PICKS,
            "monkey_every": MONKEY_EVERY,
            "poll_seconds": LIVE["poll_seconds"],
            "news_seconds": LIVE["news_seconds"],
            "heartbeat_min": LIVE["heartbeat_min"],
            "max_reviews": LIVE["max_reviews_per_day"],
            "max_triages": LIVE["max_triages_per_day"],
            "news_confirm_min": LIVE["news_confirm_min"],
            "news_min_stop_gap": LIVE["news_min_stop_gap"],
        },
    }


def render(runtime_dir=None, live_mode=False, fragment=False):
    blob = json.dumps(collect(runtime_dir, live_mode), ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", blob)
    return page if fragment else SHELL.format(body=page)


def build(out, fragment=False, runtime_dir=None, live_mode=False):
    """fragment=True면 <html>/<head>/<body> 없이 본문만 쓴다 (아티팩트 게시용)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(runtime_dir, live_mode, fragment), encoding="utf-8")
    return out


# ---------------------------------------------------------------- 실시간 기록
def _runtime_view(key, st, rdir):
    """실시간 엔진이 남긴 기록. 하나도 없으면 None."""
    snap, feed = None, []
    intraday = {"date": None, "points": []}
    if rdir.is_dir():
        snap = _read_json(rdir / f"live_{key}.json")
        feed = list(reversed(_read_jsonl(rdir / f"feed_{key}.jsonl", tail=400)))[:FEED_LIMIT]
        files = sorted(rdir.glob(f"intraday_{key}_*.jsonl"))
        if files:
            rows = [r for r in _read_jsonl(files[-1]) if "t" in r]
            intraday = {"date": files[-1].stem.rsplit("_", 1)[-1], "points": _downsample(rows, POINT_LIMIT)}
    if not isinstance(snap, dict):
        snap = None
    if not feed:  # 실시간 기록이 없어도 state에 남은 장중 판단은 보여준다
        feed = [
            {"t": r.get("time"), "kind": "react", "text": "AI 판단: " + str(r.get("assessment", ""))[:140], "reaction": r}
            for r in reversed(st.get("reactions", [])[-20:])
            if r.get("time")
        ]
    if not (snap or feed or intraday["points"]):
        return None
    if snap:
        snap.pop("feed", None)
    return {"snapshot": snap, "feed": feed, "intraday": intraday}


def _fresher_snapshot(snap, st):
    """실시간 스냅샷이 state의 마지막 평가보다 새것이면 그 숫자를 쓴다."""
    if not snap or not snap.get("players"):
        return None
    at = _parse_iso(snap.get("updated_at"))
    last = _parse_iso((st.get("live") or {}).get("at"))
    if at is None:
        return None
    return snap if last is None or at > last else None


def _market_view(key, cfg, st, rdir):
    cap = float(cfg["capital"])
    rt = _runtime_view(key, st, rdir)
    snap = _fresher_snapshot((rt or {}).get("snapshot"), st)
    live_state = (st.get("live") or {}).get("players", {})
    plans = (snap or {}).get("plans") or st.get("plans") or {}
    alerts = (snap or {}).get("alerts") if snap and "alerts" in snap else st.get("alerts") or []
    players = []
    for pkey, label in PLAYERS.items():
        pl = st["players"][pkey]
        snap_pl = (snap["players"].get(pkey) or {}) if snap else {}
        if snap_pl:
            eq = snap_pl["equity"]
            prices = {p["code"]: p["price"] for p in snap_pl.get("positions", [])}
            dates = {}
        else:
            lv = live_state.get(pkey, {})
            prices, dates = lv.get("prices", {}), lv.get("price_dates", {})
            eq = lv.get("equity", pl["history"][-1]["equity"] if pl["history"] else cap)
        last_close = pl["history"][-1]["equity"] if pl["history"] else cap
        positions = []
        for code, pos in pl["positions"].items():
            price = prices.get(code, pos["avg"])
            value = pos["shares"] * price
            item = {
                "code": code,
                "name": pos["name"],
                "shares": pos["shares"],
                "avg": pos["avg"],
                "price": price,
                "value": value,
                "weight": value / eq if eq else 0,
                "pnl": price / pos["avg"] - 1 if pos["avg"] else 0,
            }
            plan = planlib.migrate(plans.get(code), pos["avg"]) if pkey == "ai" and plans.get(code) else None
            if plan:
                item["stop"] = plan.get("stop")
                item["trail_stop"] = planlib.trail_stop(plan)
                item["trail_pct"] = plan.get("trail_pct")
                item["high"] = plan.get("high")
                item["take"] = plan.get("take")
                item["take_frac"] = plan.get("take_frac") or (1.0 if plan.get("take") else None)
                item["plan_default"] = bool(plan.get("default"))
            positions.append(item)
        positions.sort(key=lambda p: -p["value"])
        players.append(
            {
                "key": pkey,
                "label": label,
                "equity": eq,
                "ret": eq / cap - 1,
                "today": eq / last_close - 1 if last_close else 0,
                "cash": snap_pl.get("cash", pl["cash"]) if snap_pl else pl["cash"],
                "history": [
                    {"date": h["date"], "equity": h["equity"], "ret": h["equity"] / cap - 1}
                    for h in pl["history"]
                ],
                "positions": positions,
                "trades": pl["trades"][-60:][::-1],
                "n_trades": len(pl["trades"]),
                "fees": sum(t["cost"] for t in pl["trades"]),
                "price_dates": dates,
            }
        )

    last_marked = max((h["date"] for p in players for h in p["history"]), default=None)
    if snap:
        live_date = snap.get("date")
        provisional = snap.get("phase") == "open" or bool(live_date and last_marked and live_date > last_marked)
    else:
        live_date = max((d for p in players for d in p["price_dates"].values()), default=None)
        provisional = bool(live_date and last_marked and live_date > last_marked)
    pending = st.get("pending")
    return {
        "key": key,
        "name": cfg["name"],
        "currency": cfg["currency"],
        "capital": cap,
        "benchmark": cfg["benchmark"]["name"],
        "fee": cfg["fee"],
        "sell_tax": cfg["sell_tax"],
        "slippage": SLIPPAGE[key],
        "tz": cfg["tz"],
        "open": cfg["open"],
        "close": cfg["close"],
        "universe": sum(n for _, n in cfg["universe"]),
        "started": st.get("started"),
        "last_decided": st.get("last_decided"),
        "updated_at": (snap or {}).get("updated_at") or st.get("updated_at"),
        "sessions": max(len(players[0]["history"]) - 1, 0) if players else 0,
        "live": {"date": live_date, "provisional": provisional},
        "pending": pending
        and {
            "decided_on": pending["decided_on"],
            "who": [PLAYERS[k] for k, v in pending["targets"].items() if v is not None],
        },
        "players": players,
        "alerts": [{**a, "name": st.get("names", {}).get(a.get("code"), a.get("code"))} for a in alerts if isinstance(a, dict)],
        "next_check_at": (snap or {}).get("next_check_at"),
        "followups": (snap.get("followups") or []) if snap and snap.get("phase") == "open" else [],
        "journal": list(reversed(st.get("journal", [])))[:30],
        "rt": rt,
    }
