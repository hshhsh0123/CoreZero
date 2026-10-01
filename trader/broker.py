"""가짜 증권사. 목표 비중대로 정수 주식을 사고팔고 수수료·세금·미끄러짐을 뗀다."""

import math

from .config import TRADE_THRESHOLD


# 한국 주식 호가 단위 (2023년 1월부터 코스피·코스닥 공통): (이 가격 미만이면, 한 칸)
KR_TICKS = ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500))


def tick_size(market, price, etf=False):
    """호가 한 칸. 미국은 1센트, 한국 ETF는 2,000원 미만 1원·이상 5원."""
    if market != "kr":
        return 0.01
    if etf:
        return 1 if price < 2_000 else 5
    return next((tick for limit, tick in KR_TICKS if price < limit), 1_000)


def slippage_fn(market, base, etfs=()):
    """종목별 미끄러짐: 기본값과 호가 반 칸 중 큰 쪽. 싼 주식일수록 한 칸이 비싸서 더 밀린다."""
    etfs = set(etfs)

    def fn(code, price):
        return max(base, 0.5 * tick_size(market, price, code in etfs) / price) if price else base
    return fn


def _slip(slippage, code, price):
    return slippage(code, price) if callable(slippage) else slippage


def new_player(capital):
    return {"cash": float(capital), "positions": {}, "trades": [], "history": []}


def equity(player, prices):
    total = player["cash"]
    for code, pos in player["positions"].items():
        total += pos["shares"] * prices.get(code, pos["avg"])
    return total


def rebalance(player, targets, prices, names, cfg, day, slippage=0.0, at=None, tag=None):
    """targets({code: weight})에 맞춰 prices로 매매한다. 체결 내역을 돌려준다.

    slippage는 사면 그만큼 비싸게, 팔면 그만큼 싸게 체결되는 비율이다. (code, price)를 받는 함수여도 된다.
    at(체결 시각)과 tag(체결 이유)는 기록용이다.
    """
    eq = equity(player, prices)
    min_trade = eq * TRADE_THRESHOLD
    fills = []

    want = {}
    for code, w in targets.items():
        price = prices.get(code)
        if price:
            want[code] = math.floor(eq * w / price + 1e-9)

    # 먼저 팔아서 현금을 만든다
    for code, pos in list(player["positions"].items()):
        price = prices.get(code)
        if not price:
            continue  # 오늘 가격이 없으면 들고 간다
        target = want.get(code, 0)
        qty = pos["shares"] - target
        if qty <= 0 or (target > 0 and qty * price < min_trade):
            continue
        fills.append(_sell(player, code, qty, price, names, cfg, day, slippage, at, tag))

    # 그다음 산다. 현금이 모자라면 살 수 있는 만큼만.
    for code, target in sorted(want.items(), key=lambda kv: -kv[1] * prices[kv[0]]):
        price = prices[code]
        held = player["positions"].get(code, {}).get("shares", 0)
        qty = target - held
        if qty <= 0 or qty * price < min_trade:
            continue
        exec_price = price * (1 + _slip(slippage, code, price))
        qty = min(qty, math.floor(player["cash"] / (exec_price * (1 + cfg["fee"]))))
        if qty <= 0:
            continue
        fills.append(_buy(player, code, qty, exec_price, names, cfg, day, at, tag))

    return fills


def exit_position(player, code, price, names, cfg, day, slippage=0.0, at=None, tag=None):
    """한 종목을 전량 판다. 없으면 None."""
    pos = player["positions"].get(code)
    if not pos or not price:
        return None
    return _sell(player, code, pos["shares"], price, names, cfg, day, slippage, at, tag)


def reduce_position(player, code, qty, price, names, cfg, day, slippage=0.0, at=None, tag=None):
    """한 종목을 qty주만 판다 (분할 매도). 가진 것보다 많으면 전량. 없으면 None."""
    pos = player["positions"].get(code)
    if not pos or not price or qty <= 0:
        return None
    return _sell(player, code, min(int(qty), pos["shares"]), price, names, cfg, day, slippage, at, tag)


def _sell(player, code, qty, price, names, cfg, day, slippage, at, tag):
    pos = player["positions"][code]
    exec_price = price * (1 - _slip(slippage, code, price))
    gross = qty * exec_price
    cost = gross * (cfg["fee"] + cfg["sell_tax"])
    player["cash"] += gross - cost
    realized = (exec_price - pos["avg"]) * qty - cost
    pos["shares"] -= qty
    if pos["shares"] == 0:
        del player["positions"][code]
    fill = _fill(day, "sell", code, names, qty, exec_price, cost, realized, at, tag)
    player["trades"].append(fill)
    return fill


def _buy(player, code, qty, exec_price, names, cfg, day, at, tag):
    gross = qty * exec_price
    cost = gross * cfg["fee"]
    player["cash"] -= gross + cost
    pos = player["positions"].setdefault(
        code, {"shares": 0, "avg": 0.0, "name": names.get(code, code)}
    )
    pos["avg"] = (pos["avg"] * pos["shares"] + gross + cost) / (pos["shares"] + qty)
    pos["shares"] += qty
    fill = _fill(day, "buy", code, names, qty, exec_price, cost, None, at, tag)
    player["trades"].append(fill)
    return fill


def _fill(day, side, code, names, qty, price, cost, realized, at, tag):
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
    if at:
        t["at"] = at
    if tag:
        t["tag"] = tag
    return t
