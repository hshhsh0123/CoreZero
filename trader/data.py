"""네이버 증권에서 시세·시총 순위·뉴스·기본 지표를 가져온다. 전부 키 없이 된다."""

import html
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import net
from .config import HISTORY_DAYS, MARKETS

KST = ZoneInfo("Asia/Seoul")
CONTROL = re.compile(r"[\x00-\x1f\x7f\u200b-\u200f\u2028\u2029]")


def clean_title(t, limit=150):
    """바깥에서 온 제목을 AI 프롬프트에 넣기 전에 다듬는다. 제어 문자·줄바꿈을 없애고 길이를 자른다."""
    t = re.sub(r"\s+", " ", CONTROL.sub(" ", html.unescape(str(t or "")))).strip()
    return t if len(t) <= limit else t[: limit - 1] + "…"


def _pct(x):
    """'0.62%', '0.44', 0.77 같은 퍼센트 값을 소수로. 모르면 None."""
    try:
        v = float(str(x).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return v / 100 if v >= 0 else None


def universe(market):
    """시총 상위 종목 후보 목록. ETF는 뺀다."""
    out = []
    for board, n in MARKETS[market]["universe"]:
        picked = []
        for page in range(1, 4):
            if market == "kr":
                url = f"https://m.stock.naver.com/api/stocks/marketValue/{board}?page={page}&pageSize=100"
            else:
                url = f"https://api.stock.naver.com/stock/exchange/{board}/marketValue?page={page}&pageSize=100"
            stocks = net.get_json(url).get("stocks") or []
            for s in stocks:
                if s.get("stockEndType") != "stock":
                    continue
                picked.append(_universe_item(market, board, s))
                if len(picked) >= n:
                    break
            if len(picked) >= n or not stocks:
                break
        out.extend(picked)
    return out


def _universe_item(market, board, s):
    if market == "kr":
        return {
            "code": s["itemCode"],
            "name": s["stockName"],
            "board": board,
            "mcap": s.get("marketValueHangeul", ""),
        }
    industry = s.get("industryCodeType") or {}
    return {
        "code": s["reutersCode"],
        "symbol": s.get("symbolCode") or s["reutersCode"].split(".")[0],
        "name": s["stockName"],
        "name_en": s.get("stockNameEng", ""),
        "sector": industry.get("industryGroupKor", ""),
        "sector_code": str(industry.get("code") or ""),   # TRBC 8자리. 앞 6자리가 같으면 같은 업종 묶음
        "div_yield": _pct(s.get("dividendYield")),
        "board": board,
        "mcap": s.get("marketValueHangeul", ""),
    }


def industries():
    """한국 업종 번호 → 이름. (한 번에 100개까지만 줘서 쪽을 넘기며 받는다)"""
    out = {}
    for page in range(1, 6):
        groups = net.get_json(f"https://m.stock.naver.com/api/stocks/industry?page={page}&pageSize=100").get("groups") or []
        out.update({str(g["no"]): g["name"] for g in groups if g.get("no") is not None and g.get("name")})
        if len(groups) < 100:
            break
    return out


def kr_info(code):
    """한국 종목 하나의 업종 번호, 배당수익률, 오늘 시가. 모르는 값은 None."""
    d = net.get_json(f"https://m.stock.naver.com/api/stock/{code}/integration", timeout=10, retries=2)
    infos = {i.get("code"): i.get("value") for i in d.get("totalInfos") or []}
    etf = d.get("etfKeyIndicator") or {}
    y = _pct(infos.get("dividendYieldRatio"))
    if y is None and etf.get("dividendYieldTtm") is not None:
        y = _pct(etf["dividendYieldTtm"])
    try:
        open_ = float(str(infos.get("openPrice")).replace(",", ""))
    except (TypeError, ValueError):
        open_ = None
    return {"industry": str(d["industryCode"]) if d.get("industryCode") else None, "div_yield": y, "open": open_}


def us_info(codes, pages=3):
    """미국 종목들의 업종, 배당수익률, 오늘 시가. 거래소 시총 순위표를 훑어서 찾는다."""
    want, out = set(codes), {}
    for board in ("NASDAQ", "NYSE"):
        for page in range(1, pages + 1):
            if want <= set(out):
                return out
            try:
                stocks = net.get_json(
                    f"https://api.stock.naver.com/stock/exchange/{board}/marketValue?page={page}&pageSize=100"
                ).get("stocks") or []
            except Exception as e:
                print(f"  ! {board} {page}쪽 실패: {e}")
                break
            for s in stocks:
                if s.get("reutersCode") in want:
                    item = _universe_item("us", board, s)
                    open_ = s.get("openPriceRaw") or s.get("openPrice")
                    try:
                        item["open"] = float(str(open_).replace(",", "")) if open_ else None
                    except ValueError:
                        item["open"] = None
                    out[item["code"]] = item
            if not stocks:
                break
    return out


def disclosures(code, n=8):
    """한국 종목의 거래소 공시(투자경고, 공매도 과열, 거래정지, 배당락 등). 뉴스와 같은 모양으로 돌려준다."""
    rows = net.get_json(f"https://m.stock.naver.com/api/stock/{code}/disclosure?page=1&pageSize={n}")
    out = []
    for r in rows if isinstance(rows, list) else []:
        when = None
        try:
            when = datetime.fromisoformat(str(r.get("datetime"))).replace(tzinfo=KST)
        except ValueError:
            pass
        title = clean_title(r.get("title"))
        if title and r.get("disclosureId") is not None:
            out.append({"code": code, "key": f"dis:{r['disclosureId']}", "when": when, "title": title,
                        "source": "거래소 공시", "official": True})
    return out


def bars(market, code, days=HISTORY_DAYS):
    """일봉 목록 (오래된 것부터). 장중이면 오늘 봉은 아직 미완성일 수 있다."""
    if market == "kr":
        url = (
            "https://fchart.stock.naver.com/sise.nhn"
            f"?symbol={code}&timeframe=day&count={days}&requestType=0"
        )
        text = net.get(url).decode("euc-kr", errors="replace")
        rows = []
        for d, rest in re.findall(r'data="(\d{8})\|([^"]+)"', text):
            o, h, l, c, v = (float(x) for x in rest.split("|"))
            rows.append(_bar(d, o, h, l, c, v))
        return rows
    end = date.today() + timedelta(days=2)
    start = end - timedelta(days=int(days * 1.6))
    url = (
        f"https://api.stock.naver.com/chart/foreign/item/{code}/day"
        f"?startDateTime={start:%Y%m%d}0000&endDateTime={end:%Y%m%d}2359"
    )
    rows = [
        _bar(
            p["localDate"],
            p["openPrice"],
            p["highPrice"],
            p["lowPrice"],
            p["closePrice"],
            p.get("accumulatedTradingVolume") or 0,
        )
        for p in net.get_json(url)
    ]
    rows.sort(key=lambda b: b["date"])
    return rows[-days:]


def _bar(d, o, h, l, c, v):
    return {
        "date": f"{d[:4]}-{d[4:6]}-{d[6:8]}",
        "open": float(o),
        "high": float(h),
        "low": float(l),
        "close": float(c),
        "volume": float(v),
    }


def bars_many(market, codes, workers=8):
    """여러 종목 일봉을 병렬로. 실패한 종목은 빠진다."""

    def one(code):
        try:
            return code, bars(market, code)
        except Exception as e:  # 한두 종목 실패로 전체를 멈추지 않는다
            print(f"  ! {code} 시세 실패: {e}")
            return code, None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return {c: b for c, b in ex.map(one, codes) if b}


def news(market, code, n=5, before=None):
    """최근 뉴스 제목. before(aware datetime)가 있으면 그 이전 기사만."""
    try:
        if market == "kr":
            groups = net.get_json(
                f"https://m.stock.naver.com/api/news/stock/{code}?pageSize={n * 2}&page=1"
            )
            items = [
                (i.get("datetime", ""), i.get("title", ""), i.get("officeName", ""))
                for g in groups
                for i in g.get("items", [])
            ]
        else:
            raw = net.get_json(
                f"https://api.stock.naver.com/news/worldStock/{code}?pageSize={n * 2}&page=1"
            )
            items = [(i.get("dt", ""), i.get("tit", ""), i.get("ohnm", "")) for i in raw]
    except Exception as e:
        print(f"  ! {code} 뉴스 실패: {e}")
        return []
    out = []
    for stamp, title, source in items:
        when = _parse_stamp(stamp)
        if before and when and when >= before:
            continue
        out.append(
            {
                "time": when.strftime("%m-%d %H:%M") if when else "",
                "title": clean_title(title),
                "source": clean_title(source, 30),
            }
        )
        if len(out) >= n:
            break
    return out


def _parse_stamp(stamp):
    digits = re.sub(r"\D", "", stamp or "")
    if len(digits) < 12:
        return None
    if len(digits) >= 14:
        return datetime.strptime(digits[:14], "%Y%m%d%H%M%S").replace(tzinfo=KST)
    return datetime.strptime(digits[:12], "%Y%m%d%H%M").replace(tzinfo=KST)


def news_items(market, code, n=8):
    """기사 목록(최신순). key는 중복 제거용이다. code가 MAIN이면 한국 주요뉴스."""
    if market == "kr" and code != "MAIN":
        groups = net.get_json(f"https://m.stock.naver.com/api/news/stock/{code}?pageSize={n}&page=1")
        raw = [
            (f"{i.get('officeId')}:{i.get('articleId')}", i.get("datetime", ""), i.get("title", ""), i.get("officeName", ""))
            for g in groups
            for i in g.get("items", [])
        ]
    else:
        if market == "kr":
            url = f"https://m.stock.naver.com/api/news/list?category=mainnews&pageSize={n}&page=1"
        else:
            url = f"https://api.stock.naver.com/news/worldStock/{code}?pageSize={n}&page=1"
        raw = [
            (f"{i.get('oid')}:{i.get('aid')}", i.get("dt", ""), i.get("tit", ""), i.get("ohnm", ""))
            for i in net.get_json(url)
        ]
    items = [
        {"code": code, "key": key, "when": _parse_stamp(stamp), "title": clean_title(title), "source": clean_title(source, 30)}
        for key, stamp, title, source in raw
        if title
    ]
    items.sort(key=lambda it: it["when"] or datetime.min.replace(tzinfo=KST), reverse=True)
    return items


def news_many(market, codes, n=8, workers=8, disclosure_codes=()):
    """여러 피드를 병렬로. codes 순서를 유지한다. 실패한 피드는 빠진다.
    한국은 disclosure_codes 종목의 거래소 공시도 같이 가져온다 (보유 종목 피드보다 뒤에 붙는다)."""

    def one(code):
        try:
            return news_items(market, code, n)
        except Exception as e:
            print(f"  ! {code} 뉴스 실패: {e}")
            return []

    def dis(code):
        try:
            return disclosures(code)
        except Exception as e:
            print(f"  ! {code} 공시 실패: {e}")
            return []

    with ThreadPoolExecutor(max_workers=workers) as ex:
        out = [it for items in ex.map(one, codes) for it in items]
        if market == "kr":
            out += [it for items in ex.map(dis, disclosure_codes) for it in items]
    return out


FUNDAMENTAL_KEYS = {
    "per": "PER",
    "cnsPer": "추정PER",
    "pbr": "PBR",
    "dividendYieldRatio": "배당수익률",
    "highPriceOf52Weeks": "52주최고",
    "lowPriceOf52Weeks": "52주최저",
    "foreignRate": "외인소진율",
}


def fundamentals(market, code):
    """PER·PBR 같은 기본 지표. 지금은 한국 종목만 지원."""
    if market != "kr":
        return {}
    try:
        infos = net.get_json(f"https://m.stock.naver.com/api/stock/{code}/integration").get(
            "totalInfos"
        ) or []
    except Exception:
        return {}
    return {
        FUNDAMENTAL_KEYS[i["code"]]: i.get("value")
        for i in infos
        if i.get("code") in FUNDAMENTAL_KEYS and i.get("value") not in (None, "", "N/A")
    }
