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

# ---- 실시간(live) 모드: python -m trader live ----
MODEL_TRIAGE = "deepseek-flash"   # 새 소식이 중요한지 빠르게 거르는 모델
MODEL_REACT = "deepseek-v4-pro"   # 장중에 포트폴리오를 고치는 모델

# 체결가에 불리한 방향으로 붙이는 미끄러짐. 호가창을 못 보니까 대략적인 가정값이다.
SLIPPAGE = {"kr": 0.0005, "us": 0.0003}

DEFAULT_STOP_PCT = 0.10          # 손절도 트레일링도 없는 종목에 거는 기본 손절 (평단 대비)
STOP_RANGE = (0.02, 0.30)        # AI가 비율로 줄 때 허용 범위
TAKE_RANGE = (0.03, 1.00)
TRAIL_RANGE = (0.02, 0.30)       # 따라 올라가는 손절: 최고가 대비 이만큼 빠지면 판다
TAKE_FRAC_RANGE = (0.1, 1.0)     # 목표가에서 팔 비율 (0.5면 절반만 파는 분할 매도)
MAX_ALERTS = 10                  # AI가 걸어둘 수 있는 가격 알림 수
NEXT_CHECK_RANGE = (15, 120)     # AI가 정하는 다음 정기 점검까지 시간(분)

LIVE = {
    "poll_seconds": 20,          # 시세 확인 주기
    "news_seconds": 120,         # 뉴스 확인 주기
    "news_fresh_min": 20,        # 이보다 오래된 기사는 새 소식으로 안 친다
    "news_extra_movers": 5,      # 뉴스도 같이 확인할 등락 상위 종목 수
    "history_min": 120,          # 메모리에 들고 있는 시세 기록 길이(분)
    "fast_window_min": 15,       # 급변 판단 창(분)
    "fast_z": 3.0,               # 급변 기준: 그 창에서 평소 변동폭의 몇 배
    "fast_min_move": 0.012,      # 급변 최소 폭 (종목)
    "index_min_move": 0.005,     # 급변 최소 폭 (벤치마크)
    "day_step_min": 0.03,        # '오늘 이만큼 움직였다' 알림 단계 최소 폭 (종목)
    "index_day_step": 0.01,      # 같은 알림 단계 (벤치마크)
    "review_cooldown_min": 5,    # AI가 매매를 판단하는 사이의 최소 간격
    "stock_cooldown_min": 15,    # 같은 종목·같은 종류 알림을 다시 보내기까지 최소 간격
    "heartbeat_min": 90,         # AI가 다음 점검 시간을 안 정했을 때 기본 간격
    "open_review": True,         # 장이 열리고 시가 체결이 끝나면 AI가 오늘 계획을 한 번 점검
    "job_gap_seconds": 60,       # AI 호출 사이의 최소 간격 (실패해도 폭주 방지)
    "max_reviews_per_day": 20,
    "max_triages_per_day": 80,
    "max_live_trades_per_day": 30,
    "stop_confirm_ticks": 2,     # 손절·목표가를 이 횟수 연속 넘겨야 집행 (튀는 시세 방지)
    "chase_limit": 0.02,         # AI가 보던 가격보다 이만큼 더 오른 종목은 추격매수 안 함
    "stale_seconds": 900,        # 마지막 체결이 이보다 오래된 종목은 거래 안 함
    # 뉴스만으로는 사고팔지 않는다. 기사만 들어온 점검에서는 계획(손절·알림)만 고치고,
    # AI가 원하면 이만큼 뒤에 주가 반응을 보여주고 다시 판단하게 한다.
    "news_confirm_min": 15,
    "news_min_stop_gap": 0.02,   # 뉴스 점검에서 조이는 손절은 지금 가격보다 최소 이만큼 아래
    "news_memory_min": 120,      # 매체 수를 셀 때 기억하는 기사 기간(분)
}

# 시장 전체 분위기를 볼 뉴스 피드 (MAIN은 한국 주요뉴스, .IXIC는 나스닥 종합)
MARKET_NEWS = {"kr": ["MAIN"], "us": [".IXIC"]}
