"""state/*.json을 읽어서 대시보드 HTML 한 장을 만든다."""

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import (
    MARKETS,
    MAX_POSITIONS,
    MAX_WEIGHT,
    MIN_WEIGHT,
    MODEL_DECIDE,
    MONKEY_EVERY,
    MONKEY_PICKS,
    PLAYERS,
)
from .league import STATE_DIR

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


def build(out, fragment=False):
    """fragment=True면 <html>/<head>/<body> 없이 본문만 쓴다 (아티팩트 게시용)."""
    markets = []
    for key, cfg in MARKETS.items():
        path = STATE_DIR / f"{key}.json"
        if path.exists():
            markets.append(_market_view(key, cfg, json.loads(path.read_text())))
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "markets": markets,
        "rules": {
            "model": MODEL_DECIDE,
            "max_positions": MAX_POSITIONS,
            "min_weight": MIN_WEIGHT,
            "max_weight": MAX_WEIGHT,
            "monkey_picks": MONKEY_PICKS,
            "monkey_every": MONKEY_EVERY,
        },
    }
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.read_text().replace("/*__DATA__*/null", blob)
    if not fragment:
        page = SHELL.format(body=page)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    return out


def _market_view(key, cfg, st):
    cap = float(cfg["capital"])
    live = (st.get("live") or {}).get("players", {})
    players = []
    for pkey, label in PLAYERS.items():
        pl = st["players"][pkey]
        lv = live.get(pkey, {})
        prices, dates = lv.get("prices", {}), lv.get("price_dates", {})
        last_close = pl["history"][-1]["equity"] if pl["history"] else cap
        eq = lv.get("equity", last_close)
        positions = []
        for code, pos in pl["positions"].items():
            price = prices.get(code, pos["avg"])
            value = pos["shares"] * price
            positions.append(
                {
                    "code": code,
                    "name": pos["name"],
                    "shares": pos["shares"],
                    "avg": pos["avg"],
                    "price": price,
                    "value": value,
                    "weight": value / eq if eq else 0,
                    "pnl": price / pos["avg"] - 1 if pos["avg"] else 0,
                }
            )
        positions.sort(key=lambda p: -p["value"])
        players.append(
            {
                "key": pkey,
                "label": label,
                "equity": eq,
                "ret": eq / cap - 1,
                "today": eq / last_close - 1 if last_close else 0,
                "cash": pl["cash"],
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
        "universe": sum(n for _, n in cfg["universe"]),
        "started": st.get("started"),
        "last_decided": st.get("last_decided"),
        "updated_at": st.get("updated_at"),
        "sessions": max(len(players[0]["history"]) - 1, 0) if players else 0,
        "live": {"date": live_date, "provisional": provisional},
        "pending": pending
        and {
            "decided_on": pending["decided_on"],
            "who": [PLAYERS[k] for k, v in pending["targets"].items() if v is not None],
        },
        "players": players,
        "journal": list(reversed(st.get("journal", [])))[:30],
    }
