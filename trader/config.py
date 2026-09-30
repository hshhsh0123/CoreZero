"""리그 설정. 숫자를 바꾸고 싶으면 여기만 고치면 된다."""

MARKETS = {
    "kr": {
        "name": "한국",
        "currency": "KRW",
        "tz": "Asia/Seoul",
        "open": "09:00",
        "close": "15:30",
        "capital": 100_000_000,
        # 수수료·세금은 대략적인 가정값이다. 증권사나 세법에 맞게 고쳐 써도 된다.
        "fee": 0.00015,
        "sell_tax": 0.0020,
        "benchmark": {"code": "069500", "name": "KODEX 200"},
        # (보드, 시총 상위 몇 개를 후보로 줄지)
        "universe": [("KOSPI", 80), ("KOSDAQ", 30)],
    },
    "us": {
        "name": "미국",
        "currency": "USD",
        "tz": "America/New_York",
        "open": "09:30",
        "close": "16:00",
        "capital": 100_000,
        "fee": 0.0007,
        "sell_tax": 0.0,
        "benchmark": {"code": "SPY", "name": "SPY (S&P 500)"},
        "universe": [("NASDAQ", 60), ("NYSE", 60)],
    },
}

PLAYERS = {
    "ai": "AI (DeepSeek)",
    "monkey": "원숭이",
    "hodl": "존버",
}

# 장 마감 후 이만큼 지나야 그날 봉을 확정된 걸로 본다.
SETTLE_MINUTES = 20

# DeepSeek 모델. 1단계(후보 추리기)와 2단계(최종 결정)를 따로 바꿀 수 있다.
MODEL_SCOUT = "deepseek-v4-pro"
MODEL_DECIDE = "deepseek-v4-pro"

SHORTLIST_MAX = 15      # 1단계에서 AI가 추릴 후보 수
MAX_POSITIONS = 10      # 최대 보유 종목 수
MAX_WEIGHT = 0.30       # 한 종목 최대 비중
MIN_WEIGHT = 0.03       # 이보다 작은 비중은 버린다
NEWS_PER_STOCK = 5

MONKEY_PICKS = 5        # 원숭이가 다트 던지는 종목 수
MONKEY_EVERY = 5        # 원숭이는 결정 5번에 한 번 포트폴리오를 갈아엎는다

# 평가금액 대비 이보다 작은 매매는 건너뛴다 (수수료 낭비 방지)
TRADE_THRESHOLD = 0.01

HISTORY_DAYS = 130      # 지표 계산용 일봉 개수
