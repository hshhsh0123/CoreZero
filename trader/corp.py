"""주식 수가 바뀌는 일(액면분할·병합·무상증자)과 배당을 장부에 반영한다.

네이버 일봉은 분할이 생기면 과거 가격을 소급해서 고쳐 준다(수정주가). 그런데 우리 장부의 주식 수와 평단은
그대로라서, 그냥 두면 10:1 분할 날 평가금액이 90% 빠진 걸로 보이고 손절까지 나간다.
그래서 매일 장이 끝나면 보유 종목의 종가를 적어 두고, 다음에 같은 날짜의 가격이 달라져 있으면
그 비율만큼 주식 수를 늘리고(줄이고) 평단·손절가·알림 가격을 나눈다.

배당은 날짜별 배당락 정보를 모든 종목에 대해 구하기 어려워서, 연 배당수익률을 거래일마다
1/252씩 현금으로 넣는 근사를 쓴다. 배당락 날 주가가 빠지는 만큼을 길게 보면 채워 준다.
"""

import math
from fractions import Fraction

MIN_ADJUST = 0.03   # 이보다 작은 가격 수정은 무시한다 (현금배당 수정일 수 있어서)


def adjust_ratio(recorded, now, min_change=MIN_ADJUST):
    """같은 날짜의 종가가 recorded → now로 바뀌었으면 주식 수에 곱할 비율. 수정이 아니면 None.
    10:1, 5:2 같은 간단한 비율에 아주 가까우면 그 값으로 맞춘다 (등락률 반올림 때문에 9.9997 같은 값이 나와서)."""
    if not recorded or not now:
        return None
    r = recorded / now
    if abs(r - 1) < min_change:
        return None
    nice = Fraction(r).limit_denominator(20)
    return float(nice) if abs(float(nice) / r - 1) < 0.005 else r


def record_closes(st, day, prices):
    """보유 종목들의 day 종가를 적어 둔다. 다음 날 분할 여부를 이 값과 비교한다."""
    book = st.setdefault("closes", {})
    held = {c for pl in st["players"].values() for c in pl["positions"]}
    for code in held:
        if prices.get(code):
            book[code] = {"date": day, "close": prices[code]}
    for code in [c for c in book if c not in held]:
        del book[code]


def apply(st, code, ratio, day):
    """code를 ratio배로 쪼갠 걸로 장부를 고친다. 끝수 주식은 수정 가격으로 현금 정산한다."""
    rec = (st.get("closes") or {}).get(code)
    new_price = rec["close"] / ratio if rec else None
    touched = []
    for key, pl in st["players"].items():
        pos = pl["positions"].get(code)
        if not pos:
            continue
        exact = pos["shares"] * ratio
        shares = math.floor(exact + 1e-9)
        if new_price and exact - shares > 1e-9:
            pl["cash"] += (exact - shares) * new_price
        if shares <= 0:
            del pl["positions"][code]
        else:
            pos["shares"] = shares
            pos["avg"] = pos["avg"] / ratio
        touched.append(key)
    plan = (st.get("plans") or {}).get(code)
    if plan:
        for k in ("stop", "take", "high"):
            if plan.get(k):
                plan[k] = plan[k] / ratio
    for a in st.get("alerts") or []:
        if a.get("code") == code:
            for k in ("above", "below"):
                if a.get(k):
                    a[k] = a[k] / ratio
    if rec:
        rec["raw"] = rec["close"]   # 고치기 전 가격. 일봉이 아직 이 값이면 소급 수정 전이라 다시 고치지 않는다
        rec["close"] = new_price
    name = st.get("names", {}).get(code, code)
    event = {"date": day, "code": code, "name": name, "ratio": round(ratio, 6), "players": touched}
    st.setdefault("corp_actions", []).append(event)
    del st["corp_actions"][:-100]
    return event


def check_splits(st, bars, day, quotes=None):
    """보유 종목 중 가격이 소급 수정된 종목을 찾아 장부를 고친다. 고친 목록을 돌려준다.

    bars: {code: 일봉}. 적어 둔 날짜의 종가가 지금 일봉과 다르면 수정된 것이다.
    quotes: 장중 시세 {code: {price, pct}}. 일봉이 아직 안 고쳐졌어도 오늘 기준가(어제 종가를 분할에 맞게
    고친 값 = 지금가 / (1 + 등락률))가 적어 둔 어제 종가와 다르면 분할로 본다. 어제 종가가 적혀 있을 때만.
    """
    done = []
    for code, rec in list((st.get("closes") or {}).items()):
        rows = bars.get(code) or []
        same = next((b for b in reversed(rows) if b["date"] == rec["date"]), None)
        if same and rec.get("raw") and adjust_ratio(rec["raw"], same["close"]) is None:
            continue   # 시세로 먼저 고쳤고, 일봉은 아직 옛날 가격 그대로다
        ratio = adjust_ratio(rec["close"], same["close"]) if same else None
        if ratio is None and quotes and code in quotes:
            prev = [b for b in rows if b["date"] < day]
            q = quotes[code]
            if prev and prev[-1]["date"] == rec["date"] and q.get("price") and q.get("pct") is not None and q["pct"] > -1:
                ratio = adjust_ratio(rec["close"], q["price"] / (1 + q["pct"]))
        if ratio:
            done.append(apply(st, code, ratio, day))
    return done


def accrue_dividends(pl, prices, yields, days=1):
    """연 배당수익률을 거래일 1/252씩 현금으로 넣는다. 넣은 금액을 돌려준다."""
    amount = 0.0
    for code, pos in pl["positions"].items():
        y, p = yields.get(code), prices.get(code)
        if y and p:
            amount += pos["shares"] * p * y / 252 * days
    if amount:
        pl["cash"] += amount
        pl["dividends"] = pl.get("dividends", 0.0) + amount
    return amount
