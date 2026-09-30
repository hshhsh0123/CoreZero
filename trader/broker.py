"""가짜 증권사. 목표 비중대로 정수 주식을 사고팔고 수수료·세금을 뗀다."""

import math

from .config import TRADE_THRESHOLD


def new_player(capital):
    return {"cash": float(capital), "positions": {}, "trades": [], "history": []}


def equity(player, prices):
    total = player["cash"]
    for code, pos in player["positions"].items():
        total += pos["shares"] * prices.get(code, pos["avg"])
    return total


def rebalance(player, targets, prices, names, cfg, day):
    """targets({code: weight})에 맞춰 prices(체결가)로 매매한다. 체결 내역을 돌려준다."""
    eq = equity(player, prices)
    fee, tax = cfg["fee"], cfg["sell_tax"]
    min_trade = eq * TRADE_THRESHOLD
    fills = []

    want = {}
    for code, w in targets.items():
        price = prices.get(code)
        if price:
            want[code] = math.floor(eq * w / price)

    # 먼저 팔아서 현금을 만든다
    for code, pos in list(player["positions"].items()):
        price = prices.get(code)
        if not price:
            continue  # 오늘 가격이 없으면 들고 간다
        target = want.get(code, 0)
        qty = pos["shares"] - target
        if qty <= 0 or (target > 0 and qty * price < min_trade):
            continue
        gross = qty * price
        cost = gross * (fee + tax)
        player["cash"] += gross - cost
        realized = (price - pos["avg"]) * qty - cost
        pos["shares"] -= qty
        if pos["shares"] == 0:
            del player["positions"][code]
        fills.append(_fill(day, "sell", code, names, qty, price, cost, realized))

    # 그다음 산다. 현금이 모자라면 살 수 있는 만큼만.
    for code, target in sorted(want.items(), key=lambda kv: -kv[1] * prices[kv[0]]):
        price = prices[code]
        held = player["positions"].get(code, {}).get("shares", 0)
        qty = target - held
        if qty <= 0 or qty * price < min_trade:
            continue
        qty = min(qty, math.floor(player["cash"] / (price * (1 + fee))))
        if qty <= 0:
            continue
        gross = qty * price
        cost = gross * fee
        player["cash"] -= gross + cost
        pos = player["positions"].setdefault(
            code, {"shares": 0, "avg": 0.0, "name": names.get(code, code)}
        )
        pos["avg"] = (pos["avg"] * pos["shares"] + gross + cost) / (pos["shares"] + qty)
        pos["shares"] += qty
        fills.append(_fill(day, "buy", code, names, qty, price, cost, None))

    player["trades"].extend(fills)
    return fills


def _fill(day, side, code, names, qty, price, cost, realized):
    t = {
        "date": day,
        "side": side,
        "code": code,
        "name": names.get(code, code),
        "shares": qty,
        "price": price,
        "cost": round(cost, 4),
    }
    if realized is not None:
        t["realized"] = round(realized, 4)
    return t
