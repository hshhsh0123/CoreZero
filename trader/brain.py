"""DeepSeek 두뇌. 1단계에서 후보를 추리고, 2단계에서 뉴스·지표를 보고 최종 비중을 정한다.
장중에는 사건이 생길 때마다 불려서 비중과 계획(손절·목표·트레일링·알림)을 계속 고친다."""

import json
import os
import re

from . import net
from . import plans as planlib
from .config import (
    DEFAULT_STOP_PCT,
    MAX_ALERTS,
    MAX_POSITIONS,
    MAX_WEIGHT,
    MIN_WEIGHT,
    MODEL_DECIDE,
    MODEL_REACT,
    MODEL_SCOUT,
    MODEL_TRIAGE,
    NEXT_CHECK_RANGE,
    SHORTLIST_MAX,
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
    """2단계: 후보별 지표·뉴스를 보고 목표 비중과 종목별 계획을 정한다."""
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
        "- 한 번에 다 살 필요는 없어. 처음엔 목표보다 적게 담고, 장중에 상황을 보면서 더 사거나 줄여도 돼.",
        "- 종목마다 계획을 정해. 전부 비율은 소수로 적어 (예: 0.07은 7%).",
        "  · stop_pct: 평단보다 이만큼 떨어지면 장중에 자동으로 전량 매도",
        "  · take_pct: 평단보다 이만큼 오르면 take_frac만큼 자동으로 매도 (take_frac 0.5면 절반만 파는 분할 매도, 기본 1)",
        "  · trail_pct: 오른 뒤 최고가에서 이만큼 빠지면 전량 매도 (따라 올라가는 손절)",
        f"  · 손절도 트레일링도 안 정하면 {DEFAULT_STOP_PCT:.0%} 손절이 기본으로 걸려. 이미 들고 있는 종목은 안 적은 항목이 그대로 유지돼.",
        "- 장중에는 시세와 뉴스를 계속 지켜보다가 큰 일이 생기거나 네가 걸어둔 알림이 울리면 너를 다시 불러. "
        "그때마다 비중도 계획도 새로 고칠 수 있어.",
        "",
        '형식: {"market_view": "시장 전체에 대한 한두 문장", '
        '"targets": [{"code": "종목코드", "weight": 0.2, "reason": "왜 이 종목을 이 비중으로 드는지 한두 문장", '
        '"stop_pct": 0.08, "take_pct": 0.2, "take_frac": 0.5, "trail_pct": 0.1}], '
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
        if not isinstance(t, dict):
            continue
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


def parse_plans(raw, codes):
    """하루 결정에서 AI가 종목별로 적은 계획. {code: 계획 지시}"""
    out = {}
    for t in raw:
        if not isinstance(t, dict):
            continue
        code = str(t.get("code", "")).strip()
        if code in codes:
            update = planlib.parse_update(t)
            if update:
                out[code] = update
    return out


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
            f"평단 대비 {pct(h['pnl'])}" + (f", 지금 계획: {h['plan']}" if h.get("plan") else "")
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


def clean_actions(raw, allowed):
    """장중에 AI가 바꾸겠다는 종목들을 다듬는다. 비중(target_weight)을 빼면 계획만 바꾸는 지시다."""
    out, seen = [], set()
    for a in raw:
        if not isinstance(a, dict):
            continue
        code = str(a.get("code", "")).strip()
        if code not in allowed or code in seen:
            continue
        w = a.get("target_weight", a.get("weight"))
        if w is not None:
            w = planlib._num(w)
            if w is None:
                continue
            if w > 1.0:
                w /= 100
            if w < 0 or (0 < w < MIN_WEIGHT):
                continue
            w = min(w, MAX_WEIGHT)
        plan = planlib.parse_update(a)
        if w is None and not plan:
            continue
        seen.add(code)
        out.append({"code": code, "weight": w, "reason": str(a.get("reason", "")), "plan": plan})
    return out


def clean_alerts(raw, allowed, prices):
    """AI가 걸겠다는 가격 알림. 바로 울릴 알림은 빼고 메모를 남긴다. (알림 목록, 메모 목록)"""
    if not isinstance(raw, list):
        return [], []
    out, notes = [], []
    for a in raw:
        if not isinstance(a, dict):
            continue
        code = str(a.get("code", "")).strip()
        if code not in allowed:
            notes.append(f"{code}: 목록에 없는 종목이라 알림을 못 걸었어요")
            continue
        above, below = planlib._num(a.get("above")), planlib._num(a.get("below"))
        above = above if above and above > 0 else None
        below = below if below and below > 0 else None
        price = prices.get(code)
        if price and above and above <= price:
            notes.append(f"{code}: 위쪽 알림 {above:,.2f}가 이미 지금 가격 이하라 뺐어요")
            above = None
        if price and below and below >= price:
            notes.append(f"{code}: 아래쪽 알림 {below:,.2f}가 이미 지금 가격 이상이라 뺐어요")
            below = None
        if not above and not below:
            continue
        out.append({"code": code, "above": above, "below": below, "note": str(a.get("note", ""))[:200]})
        if len(out) >= MAX_ALERTS:
            break
    return out, notes


def system_prompt_live(ctx):
    return system_prompt(ctx) + (
        " 지금은 장이 열려 있고 시세와 뉴스를 실시간으로 지켜보는 중이야. 일이 생기거나 네가 부탁한 때가 되면 너를 불러. "
        "한 번 정한 계획을 고집할 필요 없어. 상황이 바뀌면 비중도 손절·목표도 바꾸고, 조금씩 나눠서 사고팔아도 돼. "
        "반대로 별일 아니면 아무것도 안 바꿔도 되고, 너무 자주 사고팔면 수수료와 체결 미끄러짐으로 손해야."
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


def _alert_line(a, market):
    parts = []
    if a.get("below"):
        parts.append(f"{fmt_price(a['below'], market)} 아래로 내려가면")
    if a.get("above"):
        parts.append(f"{fmt_price(a['above'], market)} 위로 올라가면")
    note = f" (메모: {a['note']})" if a.get("note") else ""
    return f"- {a.get('name') or a['code']}({a['code']}): {' 또는 '.join(parts)}{note}"


def react(ctx, events, triage=None):
    """장중에 사건을 보고 비중과 계획을 어떻게 바꿀지 정한다."""
    market = ctx["market"]
    cur = ctx["currency"]
    lo, hi = NEXT_CHECK_RANGE
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
        lines.append(
            f"- {h['name']}({h['code']}): 비중 {h['weight'] * 100:.1f}%, 현재가 {fmt_price(h['price'], market)}, "
            f"평단 {fmt_price(h['avg'], market)} 대비 {pct(h['pnl'])}, 오늘 {pct(h['day'])}, "
            f"15분 {pct(h['r15'])}, 1시간 {pct(h['r60'])}"
        )
        if h.get("plan_text"):
            lines.append(f"    계획: {h['plan_text']}")
        if h.get("thesis"):
            lines.append(f"    산 이유: {h['thesis']}")
    if not ctx["holdings"]:
        lines.append("- 보유 종목 없음")
    lines += ["", "[걸어둔 알림]"]
    lines += [_alert_line(a, market) for a in ctx.get("alerts") or []] or ["- 없음"]
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
        "[쓸 수 있는 도구] 비율은 전부 소수로 적어 (예: 0.07은 7%).",
        "1) 비중 바꾸기: target_weight에 새 목표 비중(전체 평가금액 대비)을 적어. 0이면 전량 매도. "
        "한 번에 다 사고팔 필요 없어. 조금 사고 상황을 보며 더 사거나(분할 매수), 일부만 팔아도 돼(분할 매도).",
        "2) 계획 고치기: target_weight를 빼고 아래 항목만 적으면 매매 없이 계획만 바뀌어. 안 적은 항목은 그대로 두고, 0을 적으면 꺼.",
        "   - stop_price(또는 평단 대비 stop_pct): 이 가격 아래로 내려가면 자동으로 전량 매도. 수익이 났으면 평단 위로 올려서 이익을 지킬 수도 있어.",
        "   - take_price(또는 평단 대비 take_pct)와 take_frac: 이 가격에 닿으면 take_frac만큼 자동으로 매도. 0.5면 절반만. 팔고 나면 너를 다시 불러서 남은 물량을 어떻게 할지 물어볼게.",
        "   - trail_pct: 오른 뒤 최고가에서 이만큼 빠지면 전량 매도 (따라 올라가는 손절).",
        f"   - 손절도 트레일링도 없으면 평단 대비 {DEFAULT_STOP_PCT:.0%} 손절이 자동으로 걸려.",
        "3) 알림 걸기: alerts에 가격을 적으면 그 가격 위로(above) 올라가거나 아래로(below) 내려가면 너를 다시 불러. "
        "note에 그때 하려는 일을 적어두면 같이 보여줄게. 예: 더 떨어지면 나눠서 더 살지 검토. "
        f"알림은 한 번 울리면 사라지고 최대 {MAX_ALERTS}개야. alerts를 적으면 지금 걸린 알림 전체가 그 목록으로 바뀌고, 안 적으면 그대로야.",
        f"4) 다음 점검: next_check_min({lo}~{hi})에 아무 일이 없어도 다시 볼 시간을 분으로 적어. 불안하면 짧게, 조용하면 길게.",
        "",
        "[규칙]",
        f"- 종목당 비중은 {MIN_WEIGHT} 이상 {MAX_WEIGHT} 이하, 최대 {MAX_POSITIONS}종목.",
        "- actions에는 바꿀 종목만 넣어. 안 넣은 보유 종목은 비중도 계획도 그대로야. 바꿀 게 없으면 \"actions\": [].",
        "- 코드는 [내 포트폴리오], [갈아탈 후보], [오늘 등락 상위·하위]에 나온 것만 쓸 수 있어.",
        f"- 오늘 이번 점검 뒤에 남은 점검은 {ctx['reviews_left']}번이야. 다 쓰면 손절·목표가·트레일링만 자동으로 돌아가고 알림은 무시돼.",
        "",
        '형식: {"assessment": "지금 상황에 대한 판단 두세 문장", '
        '"actions": [{"code": "종목코드", "target_weight": 새 비중(선택), "reason": "왜 바꾸는지 한두 문장", '
        '"stop_price": 손절가(선택), "take_price": 목표가(선택), "take_frac": 목표가에서 팔 비율(선택), "trail_pct": 트레일링 비율(선택)}], '
        '"alerts": [{"code": "종목코드", "below": 가격(선택), "above": 가격(선택), "note": "그때 하려는 일"}], '
        '"next_check_min": 분, "cash_reason": "현금 비중에 대한 한 줄"}',
    ]
    result, meta = chat_json(MODEL_REACT, system_prompt_live(ctx), "\n".join(lines))
    allowed = set(ctx["allowed"])
    alerts, alert_notes = (None, [])
    if "alerts" in result:
        alerts, alert_notes = clean_alerts(result.get("alerts"), allowed, ctx["ref_prices"])
    nc = planlib._num(result.get("next_check_min"))
    return {
        "assessment": str(result.get("assessment", "")),
        "actions": clean_actions(result.get("actions") or [], allowed),
        "alerts": alerts,
        "alert_notes": alert_notes,
        "next_check_min": int(min(max(nc, lo), hi)) if nc else None,
        "cash_reason": str(result.get("cash_reason", "")),
        "meta": meta,
    }
