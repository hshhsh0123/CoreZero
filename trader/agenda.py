"""앞으로 며칠 안의 시장 일정: FOMC, 옵션 만기, 미국 실적 발표. AI가 모르고 실적 전날 몰빵하지 않게 보여준다.

FOMC는 연준 홈페이지, 미국 실적은 나스닥 실적 캘린더에서 받는다. 옵션 만기는 계산한다
(한국은 매달 둘째 목요일, 미국은 셋째 금요일. 휴장일로 밀리는 경우는 따지지 않는다).
"""

import json
import re
from datetime import date, datetime, timedelta

from . import net

FED_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"], 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})


def parse_fomc(html):
    """연준 일정 페이지에서 회의 날짜들. [(시작일, 마지막 날(결정 발표일)), ...]"""
    heads = [(m.start(), int(m.group(1))) for m in re.finditer(r"(\d{4}) FOMC Meetings", html)]
    out = []
    for i, (pos, year) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(html)
        blk = html[pos:end]
        cells = re.findall(r'fomc-meeting__(month|date)[^>]*>(.*?)</div>', blk, re.S)
        text = lambda h: re.sub(r"<[^>]+>|\s+", " ", h).replace("*", "").strip()
        month = None
        for kind, raw in cells:
            if kind == "month":
                month = text(raw)
                continue
            if not month:
                continue
            days = re.findall(r"\d{1,2}", text(raw))
            names = [MONTHS.get(x.strip()[:3]) for x in month.split("/")]
            if not days or not all(names):
                month = None
                continue
            try:
                first = date(year, names[0], int(days[0]))
                last_month = names[-1]
                last_year = year + 1 if last_month < names[0] else year
                last = date(last_year, last_month, int(days[-1]))
            except ValueError:
                month = None
                continue
            out.append((first, last))
            month = None
    return sorted(set(out))


def fomc_dates(cache_path=None, today=None):
    """FOMC 회의 날짜. 하루 한 번만 받고 cache_path에 남긴다. 실패하면 캐시(없으면 빈 목록)."""
    today = today or date.today()
    cache = {}
    if cache_path:
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
    if cache.get("fetched") != today.isoformat():
        try:
            meetings = parse_fomc(net.get(FED_URL, timeout=15, retries=2).decode("utf-8", "replace"))
            if meetings:
                cache = {"fetched": today.isoformat(), "fomc": [[a.isoformat(), b.isoformat()] for a, b in meetings]}
                if cache_path:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(json.dumps(cache), encoding="utf-8")
        except Exception as e:
            print(f"  ! FOMC 일정 실패: {e}")
    return [(date.fromisoformat(a), date.fromisoformat(b)) for a, b in cache.get("fomc", [])]


def nth_weekday(year, month, weekday, n):
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def options_expiry(market, year, month):
    """한국: 둘째 목요일. 미국: 셋째 금요일."""
    return nth_weekday(year, month, 3, 2) if market == "kr" else nth_weekday(year, month, 4, 3)


def earnings_us(days, symbols):
    """나스닥 실적 캘린더에서 symbols 중 days(날짜 목록)에 실적을 내는 종목. [(날짜, 심볼, 시간대)]"""
    want = {s.upper() for s in symbols}
    out = []
    for d in days:
        try:
            rows = ((net.get_json(f"https://api.nasdaq.com/api/calendar/earnings?date={d.isoformat()}",
                                  timeout=12, retries=1).get("data") or {}).get("rows") or [])
        except Exception as e:
            print(f"  ! {d} 실적 일정 실패: {e}")
            continue
        for r in rows:
            sym = str(r.get("symbol") or "").upper()
            if sym in want:
                when = {"time-pre-market": "장 전", "time-after-hours": "장 마감 뒤"}.get(r.get("time"), "시간 미정")
                out.append((d, sym, when))
    return out


def upcoming(market, today, days=7, fomc=(), earnings=()):
    """today부터 days일 안의 일정을 사람이 읽는 줄로. fomc는 [(시작, 끝)], earnings는 [(날짜, 이름, 시간대)]."""
    end = today + timedelta(days=days)
    events = []
    for first, last in fomc:
        if today <= last <= end:
            # 미국 시간 오후 2시 발표라 한국장에는 다음 날 반영된다
            note = "결정은 마지막 날 미국 오후 2시 (한국장엔 다음 날 반영)" if market == "kr" else "결정은 마지막 날 오후 2시"
            events.append((last, f"FOMC 회의 {first:%m/%d}~{last:%m/%d}, {note}"))
    for y, m in {(today.year, today.month), (end.year, end.month)}:
        d = options_expiry(market, y, m)
        if today <= d <= end:
            events.append((d, "옵션 만기일 (한국 선물옵션 동시 만기면 변동 커질 수 있음)" if market == "kr" else "월간 옵션 만기일"))
    for d, name, when in earnings:
        if today <= d <= end:
            events.append((d, f"{name} 실적 발표 ({when})"))
    return [f"{d:%m/%d}({'월화수목금토일'[d.weekday()]}) {text}" for d, text in sorted(events)]


def trading_days_ahead(today, n):
    out, d = [], today
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def now_date(tz):
    return datetime.now(tz).date()
