"""DeepSeek 두뇌. 1단계에서 후보를 추리고, 2단계에서 뉴스·지표를 보고 최종 비중을 정한다."""

import json
import os
import re

from . import net
from .config import (
    MAX_POSITIONS,
    MAX_WEIGHT,
    MIN_WEIGHT,
    MODEL_DECIDE,
    MODEL_SCOUT,
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
        "",
        '형식: {"market_view": "시장 전체에 대한 한두 문장", '
        '"targets": [{"code": "종목코드", "weight": 0.2, "reason": "왜 이 종목을 이 비중으로 드는지 한두 문장"}], '
        '"cash_reason": "현금 비중을 이렇게 둔 이유 한 줄"}',
    ]
    result, meta = chat_json(MODEL_DECIDE, system_prompt(ctx), "\n".join(lines))
    allowed = {d["code"] for d in details}
    targets, reasons = clean_targets(result.get("targets") or [], allowed)
    return {
        "market_view": str(result.get("market_view", "")),
        "targets": targets,
        "reasons": reasons,
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
