"""'AI를 깨울 만한 일인가'를 정하는 순수 함수들. 네트워크도 AI도 안 쓴다."""

import math
import re


def fast_threshold(sigma_daily, window_min, session_minutes, z, min_move):
    """window_min 동안 이만큼 움직이면 급변. 평소(일변동성) 그 시간 폭의 z배, 그리고 최소 min_move."""
    return max(min_move, z * sigma_daily * math.sqrt(window_min / session_minutes))


def normal_move(sigma_daily, window_min, session_minutes):
    """평소 window_min 동안의 변동폭(1 표준편차)."""
    return sigma_daily * math.sqrt(window_min / session_minutes)


def day_level(pct, step):
    """오늘 등락률을 step 단위 단계로. +6%, step 4% 이면 1, -9% 이면 -2."""
    level = int(abs(pct) / step)
    return level if pct >= 0 else -level


class Cooldown:
    def __init__(self):
        self.t = {}

    def ok(self, key, now, minutes):
        last = self.t.get(key)
        return last is None or (now - last).total_seconds() >= minutes * 60

    def mark(self, key, now):
        self.t[key] = now


def fresh_news(items, seen, now, fresh_min):
    """seen에 없고 fresh_min분 안에 나온 기사만 골라 최신순으로 돌려준다.

    본 기사는 전부 seen(set)에 넣는다. 오래된 기사는 조용히 본 걸로 친다.
    같은 기사가 여러 피드에 있으면 items에서 먼저 나온 쪽(보유 종목 피드)이 가져간다.
    """
    out = []
    ordered = sorted(
        enumerate(items),
        key=lambda pair: (-(pair[1]["when"].timestamp() if pair[1]["when"] else 0), pair[0]),
    )
    for _, it in ordered:
        if it["key"] in seen:
            continue
        seen.add(it["key"])
        when = it["when"]
        if not when:
            continue
        age = (now - when).total_seconds()
        if -300 <= age <= fresh_min * 60:
            out.append(it)
    return out


RUMOR = re.compile(
    r"(매각|인수|합병|퇴진|사임|교체|부도|파산|폐지|분할|철수|감산|철회|결별|위기|해지|취소|중단|이탈|협상|출시|진출)설"
    r"|루머|찌라시|카더라|소식통|단독|관측|가능성|할 듯|될 듯|검토|\?"
)


def looks_like_rumor(title):
    """확정되지 않은 이야기처럼 보이는 제목인지 (소문, 단독, 추측 표현). 힌트일 뿐 판정은 AI가 한다."""
    return bool(RUMOR.search(title or ""))


class Seen:
    """이미 본 기사 키. 넣은 순서를 기억해서 오래된 것부터 잘라낼 수 있다."""

    def __init__(self, keys=()):
        self.d = dict.fromkeys(keys)

    def __contains__(self, key):
        return key in self.d

    def add(self, key):
        self.d[key] = None

    def dump(self, limit=1500):
        return list(self.d)[-limit:]
