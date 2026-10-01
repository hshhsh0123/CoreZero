"""일봉에서 AI한테 보여줄 간단한 지표를 뽑는다."""

import math


def upto(bars, day):
    """day(포함)까지의 봉만."""
    return [b for b in bars if b["date"] <= day]


def compute(bars):
    """bars는 이미 결정 기준일까지 잘린 상태여야 한다."""
    closes = [b["close"] for b in bars]
    vols = [b["volume"] for b in bars]
    if len(closes) < 21:
        return None
    last = closes[-1]

    def ret(n):
        return last / closes[-1 - n] - 1 if len(closes) > n and closes[-1 - n] else None

    daily = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 20, len(closes))]
    mean = sum(daily) / len(daily)
    vol20 = math.sqrt(sum((r - mean) ** 2 for r in daily) / (len(daily) - 1)) * math.sqrt(252)
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / len(closes[-60:])
    vol5 = sum(vols[-5:]) / 5
    vol20avg = sum(vols[-20:]) / 20
    return {
        "close": last,
        "r1": ret(1),
        "r5": ret(5),
        "r20": ret(20),
        "r60": ret(60),
        "vol20": vol20,
        "ma20_gap": last / ma20 - 1,
        "ma60_gap": last / ma60 - 1,
        "from_high": last / max(closes) - 1,
        "rsi14": _rsi(closes, 14),
        "volume_ratio": vol5 / vol20avg if vol20avg else None,
    }


def _rsi(closes, n):
    if len(closes) <= n:
        return None
    gains = losses = 0.0
    for i in range(len(closes) - n, len(closes)):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100 - 100 / (1 + rs)


def pct(x, digits=1):
    return "-" if x is None else f"{x * 100:+.{digits}f}%"
