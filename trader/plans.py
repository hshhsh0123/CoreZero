"""AI 보유 종목의 '계획': 손절가, 목표가(와 거기서 팔 비율), 따라 올라가는 손절(트레일링).

계획은 가격으로 저장한다. 나눠 사서 평단이 바뀌어도 AI가 정한 손절가는 그대로 있다.
AI는 가격(stop_price)이나 평단 대비 비율(stop_pct) 어느 쪽으로 줘도 된다.
AI는 언제든 계획을 다시 쓸 수 있다. 적지 않은 항목은 그대로 두고, 0을 적으면 끈다.

저장 형식: {"stop": 가격, "take": 가격, "take_frac": 0.1~1, "trail_pct": 비율, "high": 트레일링 기준 최고가,
           "default": 기본 손절이면 True}
"""

import math

from .config import DEFAULT_STOP_PCT, STOP_RANGE, TAKE_FRAC_RANGE, TAKE_RANGE, TRAIL_RANGE


def _num(x):
    if isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _ratio(x, lo, hi):
    """0.07 이든 7 이든 0.07로. 0 이하는 0(끄기). 범위 밖이면 끌어다 놓는다. 이상한 값이면 None."""
    v = _num(x)
    if v is None:
        return None
    if v > 1:
        v /= 100
    if v <= 0:
        return 0.0
    return min(max(v, lo), hi)


def parse_update(raw):
    """AI가 적은 계획 항목만 골라 정리한다. 안 적은 항목은 결과에 없다(= 그대로 둔다)."""
    out = {}
    for key in ("stop_price", "take_price"):
        v = _num(raw.get(key)) if key in raw else None
        if v is not None:
            out[key] = max(v, 0.0)
    for key, rng in (("stop_pct", STOP_RANGE), ("take_pct", TAKE_RANGE), ("trail_pct", TRAIL_RANGE)):
        v = _ratio(raw.get(key), *rng) if key in raw else None
        if v is not None:
            out[key] = v
    if "take_frac" in raw:
        v = _ratio(raw.get("take_frac"), *TAKE_FRAC_RANGE)
        if v:
            out["take_frac"] = v
    return out


def migrate(plan, avg):
    """예전 형식(stop_pct/take_pct 비율만 있던 것)을 가격 형식으로 바꾼다."""
    plan = dict(plan or {})
    if "stop_pct" in plan:
        v = plan.pop("stop_pct")
        if "stop" not in plan and v:
            plan["stop"] = avg * (1 - v)
    if "take_pct" in plan:
        v = plan.pop("take_pct")
        if "take" not in plan and v:
            plan["take"] = avg * (1 + v)
    return {k: v for k, v in plan.items() if v is not None}


def apply(plan, update, avg, price=None, min_gap=0.0):
    """update를 반영한 새 계획과, 받아들이지 않은 항목에 대한 메모 목록을 돌려준다.

    min_gap을 주면 손절·트레일링은 지금 가격보다 그만큼 아래까지만, 목표가는 그만큼 위까지만 붙일 수 있다.
    계획을 가격에 바짝 붙여서 사실상 바로 사고파는 걸 막는다. 멀어지는 쪽으로 바꾸는 건 언제나 된다.
    """
    plan = dict(plan or {})
    notes = []
    stop = None
    if "stop_price" in update:
        stop = update["stop_price"]
    elif "stop_pct" in update:
        stop = avg * (1 - update["stop_pct"]) if update["stop_pct"] else 0.0
    if stop is not None:
        old = plan.get("stop") or 0.0
        cap = price * (1 - min_gap) if price else None
        if stop == 0:
            plan.pop("stop", None)
            plan.pop("default", None)
        elif min_gap and cap and stop > cap and stop > old:
            if old >= cap:
                notes.append(f"손절가 {stop:,.2f}는 지금 가격({price:,.2f})에 너무 붙어서 원래 손절 {old:,.2f}를 그대로 뒀어요 "
                             f"(지금 가격보다 {min_gap:.0%} 넘게 아래여야 해요)")
            else:
                plan["stop"] = cap
                plan.pop("default", None)
                notes.append(f"손절가 {stop:,.2f}는 지금 가격({price:,.2f})에 너무 붙어서 {cap:,.2f}로 걸었어요 "
                             f"(지금 가격보다 {min_gap:.0%} 넘게 아래여야 해요)")
        elif price and stop >= price:
            notes.append(f"손절가 {stop:,.2f}가 지금 가격({price:,.2f}) 이상이라 안 바꿨어요. 바로 팔려면 비중을 0으로 하면 돼요")
        else:
            plan["stop"] = stop
            plan.pop("default", None)
    take = None
    if "take_price" in update:
        take = update["take_price"]
    elif "take_pct" in update:
        take = avg * (1 + update["take_pct"]) if update["take_pct"] else 0.0
    if take is not None:
        old = plan.get("take")
        floor = price * (1 + min_gap) if price else None
        if take == 0:
            plan.pop("take", None)
            plan.pop("take_frac", None)
        elif min_gap and floor and take < floor and (old is None or take < old):
            if old is not None and old <= floor:
                notes.append(f"목표가 {take:,.2f}는 지금 가격({price:,.2f})에 너무 붙어서 원래 목표 {old:,.2f}를 그대로 뒀어요 "
                             f"(지금 가격보다 {min_gap:.0%} 넘게 위여야 해요)")
            else:
                plan["take"] = floor
                notes.append(f"목표가 {take:,.2f}는 지금 가격({price:,.2f})에 너무 붙어서 {floor:,.2f}로 걸었어요 "
                             f"(지금 가격보다 {min_gap:.0%} 넘게 위여야 해요)")
        elif price and take <= price:
            notes.append(f"목표가 {take:,.2f}가 지금 가격({price:,.2f}) 이하라 안 바꿨어요")
        else:
            plan["take"] = take
    if "take_frac" in update and plan.get("take"):
        plan["take_frac"] = update["take_frac"]
    if "trail_pct" in update:
        if update["trail_pct"]:
            new = dict(plan, trail_pct=update["trail_pct"], high=max(plan.get("high") or 0.0, price or avg))
            level, old = trail_stop(new), trail_stop(plan) or 0.0
            too_close = price and level > old and (level > price * (1 - min_gap) if min_gap else level >= price)
            if too_close:
                notes.append(f"트레일링 {update['trail_pct']:.0%}면 매도 기준이 {level:,.2f}로 지금 가격({price:,.2f})에 "
                             "너무 붙어서 안 바꿨어요. 바로 팔려면 비중을 0으로 하면 돼요")
            else:
                plan = new
        else:
            plan.pop("trail_pct", None)
            plan.pop("high", None)
    return plan, notes


def ensure_default(plan, avg):
    """손절도 트레일링도 없으면 기본 손절을 건다. 기본 손절은 평단이 바뀌면 따라간다."""
    plan = dict(plan)
    if plan.get("default") or (not plan.get("stop") and not plan.get("trail_pct")):
        plan["stop"] = avg * (1 - DEFAULT_STOP_PCT)
        plan["default"] = True
    return plan


def trail_stop(plan):
    if plan.get("trail_pct") and plan.get("high"):
        return plan["high"] * (1 - plan["trail_pct"])
    return None


def effective_stop(plan):
    """('stop' | 'trail', 가격) 중 더 높은 쪽. 둘 다 없으면 (None, None)."""
    options = [(k, v) for k, v in (("stop", plan.get("stop")), ("trail", trail_stop(plan))) if v]
    if not options:
        return None, None
    return max(options, key=lambda kv: kv[1])


def update_high(plan, price):
    """트레일링 기준 최고가를 올린다. 바뀌었으면 True."""
    if plan.get("trail_pct") and price > (plan.get("high") or 0):
        plan["high"] = price
        return True
    return False


def check(plan, price):
    """지금 가격에서 발동할 게 있으면 ('stop' | 'trail' | 'take', 기준가), 없으면 None."""
    kind, level = effective_stop(plan)
    if level and price <= level:
        return kind, level
    if plan.get("take") and price >= plan["take"]:
        return "take", plan["take"]
    return None


def describe(plan, fmt):
    """사람이 읽는 한 줄. fmt는 가격을 문자열로 바꾸는 함수."""
    bits = []
    kind, level = effective_stop(plan)
    if plan.get("stop"):
        bits.append(f"손절 {fmt(plan['stop'])}" + (" (기본)" if plan.get("default") else ""))
    if plan.get("trail_pct") and plan.get("high"):
        bits.append(f"최고가 {fmt(plan['high'])}에서 {plan['trail_pct']:.0%} 빠지면 매도 (지금 기준 {fmt(trail_stop(plan))})")
    elif plan.get("trail_pct"):
        bits.append(f"오른 뒤 최고가에서 {plan['trail_pct']:.0%} 빠지면 매도")
    if plan.get("take"):
        frac = plan.get("take_frac") or 1.0
        bits.append(f"목표 {fmt(plan['take'])}" + (f"에서 {frac:.0%} 매도" if frac < 0.999 else "에서 전량 매도"))
    return ", ".join(bits) if bits else "계획 없음"
