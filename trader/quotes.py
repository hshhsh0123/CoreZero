"""장중 실시간 시세. 시총 상위 200종목을 게시판당 한 번의 호출로 통째로 받는다."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import net

BOARDS = {"kr": ("KOSPI", "KOSDAQ"), "us": ("NASDAQ", "NYSE")}


def _num(x):
    if x is None or x == "":
        return None
    try:
        return float(str(x).replace(",", ""))
    except ValueError:
        return None


def parse_time(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _halted(s):
    status = s.get("tradableStatus")
    if status is not None and status != "tradable":
        return True
    name = str((s.get("tradeStopType") or {}).get("name") or "").upper()
    return any(word in name for word in ("HALT", "SUSPEND", "STOP"))


def parse_item(market, s, code=None):
    """네이버 시세 항목 하나를 {price, pct(소수), at, status, ...} 로. 가격이 없으면 None."""
    code = code or (s.get("itemCode") if market == "kr" else s.get("reutersCode"))
    price = _num(s.get("closePriceRaw")) or _num(s.get("closePrice"))
    if not code or not price:
        return None
    pct = _num(s.get("fluctuationsRatioRaw"))
    if pct is None:
        pct = _num(s.get("fluctuationsRatio"))
    return {
        "code": code,
        "name": s.get("stockName") or code,
        "kind": s.get("stockEndType") or "stock",
        "price": price,
        "pct": (pct or 0.0) / 100.0,
        "volume": _num(s.get("accumulatedTradingVolumeRaw")) or _num(s.get("accumulatedTradingVolume")),
        "at": parse_time(s.get("localTradedAt")),
        "status": s.get("marketStatus"),
        "halted": _halted(s),
    }


def _board_url(market, board):
    if market == "kr":
        return f"https://m.stock.naver.com/api/stocks/marketValue/{board}?page=1&pageSize=100"
    return f"https://api.stock.naver.com/stock/exchange/{board}/marketValue?page=1&pageSize=100"


def _basic_url(market, code):
    if market == "kr":
        return f"https://m.stock.naver.com/api/stock/{code}/basic"
    return f"https://api.stock.naver.com/stock/{code}/basic"


def snapshot(market, extra=()):
    """{code: quote}. 게시판 호출이 전부 실패하면 예외. extra는 목록에 없을 때만 따로 받는다."""

    def board(b):
        try:
            stocks = net.get_json(_board_url(market, b), timeout=10, retries=2).get("stocks") or []
        except Exception as e:
            print(f"  ! {b} 시세 실패: {e}")
            return []
        return [parse_item(market, s) for s in stocks]

    def one(code):
        try:
            return parse_item(market, net.get_json(_basic_url(market, code), timeout=10, retries=2), code=code)
        except Exception as e:
            print(f"  ! {code} 시세 실패: {e}")
            return None

    with ThreadPoolExecutor(max_workers=4) as ex:
        out = {}
        for items in ex.map(board, BOARDS[market]):
            out.update({it["code"]: it for it in items if it})
        if not out:
            raise RuntimeError("시세를 하나도 못 받았어요")
        missing = [c for c in extra if c not in out]
        for it in ex.map(one, missing):
            if it:
                out[it["code"]] = it
    return out


class History:
    """종목별 (시각, 가격) 기록. 몇 분 전 대비 등락률을 물어볼 수 있다."""

    def __init__(self, keep_seconds=7200):
        self.keep = keep_seconds
        self.h = {}

    def add(self, now, quotes):
        t = now.timestamp()
        for code, q in quotes.items():
            d = self.h.setdefault(code, deque())
            if d and d[-1][0] >= t:
                continue
            d.append((t, q["price"]))
            while d and d[0][0] < t - self.keep:
                d.popleft()

    def price_ago(self, code, seconds, now, slack=60):
        """seconds 전(또는 그 직전) 가격. 기록이 거기까지 안 닿으면 None."""
        d = self.h.get(code)
        if not d:
            return None
        target = now.timestamp() - seconds
        if d[0][0] > target + slack:
            return None
        then = None
        for t, p in d:
            if t > target:
                break
            then = p
        return then if then is not None else d[0][1]

    def ret(self, code, seconds, now):
        d = self.h.get(code)
        then = self.price_ago(code, seconds, now)
        if not d or not then:
            return None
        return d[-1][1] / then - 1
