"""DeepSeek 두뇌. 1단계에서 후보를 추리고, 2단계에서 뉴스·지표를 보고 최종 비중을 정한다."""

import json
import os
import re

from . import net
from .config import (
    DEFAULT_STOP_PCT,
    MAX_POSITIONS,
    MAX_WEIGHT,
    MIN_WEIGHT,
    MODEL_DECIDE,
    MODEL_REACT,
    MODEL_SCOUT,
    MODEL_TRIAGE,
    SHORTLIST_MAX,
    STOP_RANGE,
    TAKE_RANGE,
)
from .features import pct

API_URL = "https://api.deepseek.com/chat/completions"


def chat_json(model, system, user, max_tokens=16000):
    """JSON 모드로 한 번 묻는다. 파싱에 실패하면 한 번 더 묻는다."""
    headers = {}
    # 로컬이나 GitHub Actions에서는 키가 필요하다. 프록시가 키를 넣어주는 환경이면 없어도 된다.
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
    }
    last_err = None
    for _ in range(2):
        resp = net.post_json(API_URL, payload, headers=headers)
        msg = resp["choices"][0]["message"]
        try:
            parsed = _parse_json(msg.get("content") or "")
        except ValueError as e:
            last_err = e
            continue
        return parsed, {
            "model": resp.get("model", model),
            "usage": resp.get("usage", {}),
            "thinking": (msg.get("reasoning_content") or "")[:4000],
        }
    raise RuntimeError(f"DeepSeek 응답을 JSON으로 못 읽음: {last_err}")


def _parse_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(str(e)) from e


def system_prompt(ctx):
    rules = f"매수·매도 수수료 {ctx['fee'] * 100:.3f}%"
    if ctx["sell_tax"]:
        rules += f", 매도할 때 세금 {ctx['sell_tax'] * 100:.2f}%"
    return (
        f"너는 {ctx['market_name']} 주식 모의투자 리그에 참가한 AI 펀드매니저야. "
        f"상대는 둘이야: 랜덤으로 종목을 고르는 '원숭이', 그리고 {ctx['bench_name']}를 사서 "
        "가만히 들고 있는 '존버'. 목표는 앞으로의 수익률로 둘 다 이기는 거야. "
        "가짜 돈이지만 진짜 돈처럼 진지하게 굴려. "
        f"매매 비용({rules})이 드니까 이유 없이 자주 사고팔면 손해야. "
        "주어진 데이터와 뉴스만 근거로 판단하고, 모르는 건 모른다고 봐. "
        "모든 설명은 한국어로, 답은 반드시 JSON 객체 하나로만 해."
    )


def scout(ctx, candidates):
    """1단계: 시총 상위 후보 표를 보고 자세히 볼 종목을 추린다."""
    lines = [
        f"기준일: {ctx['asof']} (이날 종가까지의 데이터)",
        "",
        portfolio_text(ctx),
        "",
        f"[후보 종목: 시총 상위 {len(candidates)}개]",
        _table_header(ctx["market"]),
    ]
    lines += [_table_row(ctx["market"], c) for c in candidates]
    lines += [
        "",
        "[할 일]",
        f"다음 단계에서 뉴스와 지표를 자세히 볼 후보를 최대 {SHORTLIST_MAX}개 골라. "
        "지금 보유 중인 종목은 자동으로 포함되니까 안 넣어도 돼.",
        '형식: {"market_view": "시장 전체에 대한 한두 문장", '
        '"shortlist": [{"code": "종목코드", "why": "고른 이유 한 줄"}]}',
    ]
    result, meta = chat_json(MODEL_SCOUT, system_prompt(ctx), "\n".join(lines))
    allowed = {c["code"] for c in candidates}
    shortlist = []
    for item in result.get("shortlist") or []:
        code = str(item.get("code", "")).strip()
        if code in allowed and code not in [s["code"] for s in shortlist]:
            shortlist.append({"code": code, "why": str(item.get("why", ""))})
    return {
        "market_view": str(result.get("market_view", "")),
        "shortlist": shortlist[:SHORTLIST_MAX],
        "meta": meta,
    }


def decide(ctx, details):
    """2단계: 후보별 지표·뉴스를 보고 목표 비중을 정한다."""
    lines = [
        f"기준일: {ctx['asof']} (이날 종가까지의 데이터)",
        "",
        portfolio_text(ctx),
        "",
        "[후보 상세]",
    ]
    for d in details:
        lines.append(_detail_block(ctx["market"], d))
    lines += [
        "[규칙]",
        "- 목표 포트폴리오를 비중으로 정해. 비중 합은 1 이하이고 나머지는 현금이야.",
        f"- 최대 {MAX_POSITIONS}종목, 종목당 비중은 {MIN_WEIGHT} 이상 {MAX_WEIGHT} 이하.",
        "- 위 [후보 상세]에 있는 코드만 쓸 수 있어.",
        "- 주문은 다음 거래일 시가에 체결돼. 계속 들고 갈 종목도 targets에 다시 넣어야 하고, "
        "빠진 보유 종목은 전부 팔아.",
        f"- 종목마다 손절 비율 stop_pct(평단보다 이만큼 떨어지면 장중에 자동으로 팔아. {STOP_RANGE[0]}~{STOP_RANGE[1]})를 "
        f"정해. 목표 수익 비율 take_pct({TAKE_RANGE[0]}~{TAKE_RANGE[1]})는 선택이야. "
        f"stop_pct를 안 정하면 {DEFAULT_STOP_PCT}가 기본으로 걸려.",
        "- 장중에는 시세와 뉴스를 계속 지켜보다가 큰 일이 생기면 너를 다시 불러서 비중을 고치게 해줄 거야.",
        "",
        '형식: {"market_view": "시장 전체에 대한 한두 문장", '
        '"targets": [{"code": "종목코드", "weight": 0.2, "reason": "왜 이 종목을 이 비중으로 드는지 한두 문장", '
        '"stop_pct": 0.08, "take_pct": 0.2}], '
        '"cash_reason": "현금 비중을 이렇게 둔 이유 한 줄"}',
    ]
    result, meta = chat_json(MODEL_DECIDE, system_prompt(ctx), "\n".join(lines))
    allowed = {d["code"] for d in details}
    targets, reasons = clean_targets(result.get("targets") or [], allowed)
    return {
        "market_view": str(result.get("market_view", "")),
        "targets": targets,
        "reasons": reasons,
        "plans": parse_plans(result.get("targets") or [], set(targets)),
        "cash_reason": str(result.get("cash_reason", "")),
        "meta": meta,
    }


def clean_targets(raw, allowed):
    """AI가 준 비중을 규칙에 맞게 다듬는다."""
    merged, reasons = {}, {}
    for t in raw:
        code = str(t.get("code", "")).strip()
        try:
            w = float(t.get("weight", 0))
        except (TypeError, ValueError):
            continue
        if w > 1.0:  # 20처럼 퍼센트로 준 경우
            w /= 100
        if code not in allowed or w < MIN_WEIGHT:
            continue
        merged[code] = min(w, MAX_WEIGHT)
        reasons[code] = str(t.get("reason", ""))
    top = sorted(merged.items(), key=lambda kv: -kv[1])[:MAX_POSITIONS]
    total = sum(w for _, w in top)
    scale = 1 / total if total > 1 else 1
    targets = {code: round(w * scale, 4) for code, w in top}
    return targets, {c: reasons[c] for c in targets}


def portfolio_text(ctx):
    cur = ctx["currency"]
    lines = [
        "[내 포트폴리오]",
        f"평가금액 {fmt_money(ctx['equity'], cur)}, 현금 {fmt_money(ctx['cash'], cur)} "
        f"({ctx['cash'] / ctx['equity'] * 100:.1f}%)",
    ]
    for h in ctx["holdings"]:
        lines.append(
            f"- {h['name']}({h['code']}): 비중 {h['weight'] * 100:.1f}%, "
            f"평단 대비 {pct(h['pnl'])}"
        )
    if not ctx["holdings"]:
        lines.append("- 보유 종목 없음")
    lines.append("")
    lines.append("[리그 현황: 시작 이후 수익률]")
    for name, r in ctx["standings"]:
        lines.append(f"- {name}: {pct(r, 2)}")
    if ctx.get("last_view"):
        lines.append("")
        lines.append(f"[지난번 네 판단] {ctx['last_view']}")
    return "\n".join(lines)


def _table_header(market):
    cols = "코드 | 종목명 | "
    if market == "us":
        cols += "섹터 | "
    return cols + "시총 | 종가 | 1일 | 5일 | 20일 | 60일 | 변동성(연) | 20일선대비 | 고점대비 | RSI14 | 거래량비(5/20)"


def _table_row(market, c):
    f = c["f"]
    cells = [c["code"], c["name"]]
    if market == "us":
        cells.append(c.get("sector") or "-")
    cells += [
        c.get("mcap") or "-",
        fmt_price(f["close"], market),
        pct(f["r1"]),
        pct(f["r5"]),
        pct(f["r20"]),
        pct(f["r60"]),
        f"{f['vol20'] * 100:.0f}%",
        pct(f["ma20_gap"]),
        pct(f["from_high"]),
        "-" if f["rsi14"] is None else f"{f['rsi14']:.0f}",
        "-" if f["volume_ratio"] is None else f"{f['volume_ratio']:.2f}",
    ]
    return " | ".join(cells)


def _detail_block(market, d):
    f = d["f"]
    head = f"### {d['name']} ({d['code']})"
    extra = [x for x in (d.get("sector"), d.get("mcap") and f"시총 {d['mcap']}") if x]
    if extra:
        head += " · " + " · ".join(extra)
    out = [
        head,
        f"지표: 종가 {fmt_price(f['close'], market)}, 수익률 1일 {pct(f['r1'])} / 5일 {pct(f['r5'])} / "
        f"20일 {pct(f['r20'])} / 60일 {pct(f['r60'])}, 연변동성 {f['vol20'] * 100:.0f}%, "
        f"20일선 대비 {pct(f['ma20_gap'])}, 60일선 대비 {pct(f['ma60_gap'])}, "
        f"고점 대비 {pct(f['from_high'])}, RSI14 "
        + ("-" if f["rsi14"] is None else f"{f['rsi14']:.0f}")
        + ", 거래량비 "
        + ("-" if f["volume_ratio"] is None else f"{f['volume_ratio']:.2f}"),
    ]
    if d.get("fund"):
        out.append("기본지표: " + ", ".join(f"{k} {v}" for k, v in d["fund"].items()))
    if d.get("why"):
        out.append(f"1단계에서 고른 이유: {d['why']}")
    if d.get("news"):
        out.append("최근 뉴스:")
        out += [f"- [{n['time']} {n['source']}] {n['title']}" for n in d["news"]]
    else:
        out.append("최근 뉴스: 없음")
    out.append("")
    return "\n".join(out)


def fmt_price(x, market):
    return f"{x:,.0f}" if market == "kr" else f"{x:,.2f}"


def fmt_money(x, currency):
    return f"{x:,.0f}원" if currency == "KRW" else f"${x:,.2f}"


# ---------------------------------------------------------------- 실시간(장중) 두뇌


def _frac(x, lo, hi):
    """0.07 이든 7 이든 0.07로. 범위 밖이면 끌어다 놓고, 이상한 값이면 None."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v > 1:
        v /= 100
    if v <= 0:
        return None
    return min(max(v, lo), hi)


def parse_plans(raw, codes):
    """AI가 종목별로 적어준 손절·목표 비율. {code: {"stop_pct", "take_pct"}}"""
    plans = {}
    for t in raw:
        code = str(t.get("code", "")).strip()
        if code not in codes:
            continue
        stop = _frac(t.get("stop_pct"), *STOP_RANGE)
        take = _frac(t.get("take_pct"), *TAKE_RANGE)
        if stop or take:
            plans[code] = {"stop_pct": stop, "take_pct": take}
    return plans


def clean_actions(raw, allowed):
    """장중에 AI가 바꾸겠다는 종목들을 규칙에 맞게 다듬는다. 안 맞으면 그 항목만 버린다."""
    out, seen = [], set()
    for a in raw:
        code = str(a.get("code", "")).strip()
        if code not in allowed or code in seen:
            continue
        try:
            w = float(a.get("target_weight", a.get("weight", 0)))
        except (TypeError, ValueError):
            continue
        if w > 1.0:
            w /= 100
        if w < 0 or (0 < w < MIN_WEIGHT):
            continue
        seen.add(code)
        out.append(
            {
                "code": code,
                "weight": min(w, MAX_WEIGHT),
                "reason": str(a.get("reason", "")),
                "stop_pct": _frac(a.get("stop_pct"), *STOP_RANGE),
                "take_pct": _frac(a.get("take_pct"), *TAKE_RANGE),
            }
        )
    return out


def system_prompt_live(ctx):
    return system_prompt(ctx) + (
        " 지금은 장이 열려 있고 시세와 뉴스를 실시간으로 지켜보는 중이야. 새 사건이 생겼을 때만 너를 불러. "
        "대부분의 소식은 가만히 있는 게 정답이니까, 별일 아니면 아무것도 안 바꿔도 돼. "
        "너무 자주 사고팔면 수수료와 체결 미끄러짐으로 손해야."
    )


def events_text(events):
    lines = []
    for e in events:
        stamp = e.get("at", "")[11:16] if e.get("at") else ""
        lines.append(f"- [{e.get('local', stamp)}] {e['text']}")
        for n in (e.get("data") or {}).get("items", [])[:4]:
            lines.append(f"    · [{n['time']} {n['source']}] {n['title']}")
    return "\n".join(lines) if lines else "- 없음"


def triage(ctx, events):
    """새 소식만 있을 때 flash가 먼저 거른다. 판단이 필요하면 verdict가 review."""
    lines = [
        f"현재 시각 {ctx['now_local']} ({ctx['market_name']} 장중, 마감까지 {ctx['minutes_left']}분)",
        "",
        "[내 보유 종목]",
    ]
    lines += [
        f"- {h['name']}({h['code']}): 비중 {h['weight'] * 100:.1f}%, 평단 대비 {pct(h['pnl'])}, 오늘 {pct(h['day'])}"
        for h in ctx["holdings"]
    ] or ["- 없음"]
    lines += [
        "",
        "[새로 들어온 소식]",
        events_text(events),
        "",
        "이 소식 때문에 지금 포트폴리오를 바꿔야 할지 판단해. 이미 주가에 반영됐거나 나랑 상관없는 소식이면 ignore야. "
        "보유 종목에 직접 영향이 있거나, 새로 살 만한 큰 기회일 때만 review.",
        '형식: {"verdict": "review" 또는 "ignore", "importance": 1부터 5까지 정수, "reason": "한 줄"}',
    ]
    result, meta = chat_json(MODEL_TRIAGE, system_prompt_live(ctx), "\n".join(lines), max_tokens=4000)
    try:
        importance = int(result.get("importance"))
    except (TypeError, ValueError):
        importance = None
    return {
        "verdict": "review" if str(result.get("verdict", "")).lower().startswith("review") else "ignore",
        "importance": importance,
        "reason": str(result.get("reason", "")),
        "meta": meta,
    }


def react(ctx, events, triage=None):
    """장중에 사건을 보고 포트폴리오를 어떻게 바꿀지 정한다."""
    market = ctx["market"]
    cur = ctx["currency"]
    lines = [
        f"현재 시각 {ctx['now_local']} ({ctx['market_name']} 장중, 마감까지 {ctx['minutes_left']}분)",
        "",
        "[지금 벌어진 일]",
        events_text(events),
    ]
    if triage:
        lines.append(f"(빠른 심사에서 검토가 필요하다고 함: {triage.get('reason', '')})")
    lines += [
        "",
        "[내 포트폴리오]",
        f"평가금액 {fmt_money(ctx['equity'], cur)}, 현금 {fmt_money(ctx['cash'], cur)} ({ctx['cash'] / ctx['equity'] * 100:.1f}%)",
    ]
    for h in ctx["holdings"]:
        stop = f", 손절가 {fmt_price(h['stop'], market)}" if h.get("stop") else ""
        take = f", 목표가 {fmt_price(h['take'], market)}" if h.get("take") else ""
        thesis = f"\n    산 이유: {h['thesis']}" if h.get("thesis") else ""
        lines.append(
            f"- {h['name']}({h['code']}): 비중 {h['weight'] * 100:.1f}%, 현재가 {fmt_price(h['price'], market)}, "
            f"평단 대비 {pct(h['pnl'])}, 오늘 {pct(h['day'])}, 15분 {pct(h['r15'])}, 1시간 {pct(h['r60'])}{stop}{take}{thesis}"
        )
    if not ctx["holdings"]:
        lines.append("- 보유 종목 없음")
    b = ctx["bench"]
    lines += [
        "",
        f"[시장] {b['name']}: 오늘 {pct(b['day'])}, 15분 {pct(b['r15'])}, 1시간 {pct(b['r60'])}",
        "[오늘 등락 상위] " + (", ".join(f"{m['name']}({m['code']}) {pct(m['day'])}" for m in ctx["movers_up"]) or "-"),
        "[오늘 등락 하위] " + (", ".join(f"{m['name']}({m['code']}) {pct(m['day'])}" for m in ctx["movers_down"]) or "-"),
    ]
    if ctx["candidates"]:
        lines.append("[갈아탈 후보: 어제 종가 때 눈여겨본 종목]")
        for c in ctx["candidates"]:
            lines.append(f"- {c['name']}({c['code']}): 현재가 {fmt_price(c['price'], market)}, 오늘 {pct(c['day'])}. {c.get('why', '')}")
    lines += [
        "",
        "[리그 현황: 시작 이후 수익률] " + ", ".join(f"{n} {pct(r, 2)}" for n, r in ctx["standings"]),
        f"[오늘 내 매매] {ctx['trades_today']}건, 수수료·세금 {fmt_money(ctx['fees_today'], cur)}. "
        f"체결가에는 미끄러짐 {ctx['slippage'] * 100:.2f}%가 불리한 쪽으로 붙어.",
        "",
        "[규칙]",
        "- actions에는 바꾸고 싶은 종목만 넣어. 안 넣은 보유 종목은 그대로 들고 가. 바꿀 게 없으면 \"actions\": [].",
        "- target_weight는 그 종목의 새 목표 비중(전체 평가금액 대비)이야. 0이면 전량 매도.",
        f"- 종목당 비중은 {MIN_WEIGHT} 이상 {MAX_WEIGHT} 이하, 최대 {MAX_POSITIONS}종목.",
        f"- 새로 사거나 비중을 늘릴 때는 stop_pct(평단보다 이만큼 떨어지면 자동으로 팔아. {STOP_RANGE[0]}~{STOP_RANGE[1]})를 "
        f"꼭 정해. take_pct(목표 수익률)는 선택이야. 안 정하면 손절 {DEFAULT_STOP_PCT}가 기본으로 걸려.",
        "- 코드는 [내 포트폴리오], [갈아탈 후보], [오늘 등락 상위·하위]에 나온 것만 쓸 수 있어.",
        "- 이미 손절가가 걸려 있는 종목은 그 가격에 닿으면 자동으로 팔려. 그 전에 네가 먼저 팔 이유가 있을 때만 팔아.",
        "",
        '형식: {"assessment": "지금 상황에 대한 판단 두세 문장", '
        '"actions": [{"code": "종목코드", "target_weight": 0.12, "reason": "왜 이렇게 바꾸는지 한두 문장", '
        '"stop_pct": 0.07, "take_pct": 0.15}], '
        '"watch": ["더 지켜볼 종목코드"], "cash_reason": "현금 비중에 대한 한 줄"}',
    ]
    result, meta = chat_json(MODEL_REACT, system_prompt_live(ctx), "\n".join(lines))
    return {
        "assessment": str(result.get("assessment", "")),
        "actions": clean_actions(result.get("actions") or [], set(ctx["allowed"])),
        "watch": [str(c) for c in (result.get("watch") or [])][:6],
        "cash_reason": str(result.get("cash_reason", "")),
        "meta": meta,
    }
