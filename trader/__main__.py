"""사용법:
    python -m trader run [--market kr|us|all]   하루치 리그 진행 (장 마감 후 한 번)
    python -m trader live [--market kr|us|all]  장중 실시간 감시: 시세·뉴스를 계속 보다가 사건이 터지면 AI가 반응
    python -m trader report [--out 경로]          대시보드 HTML 만들기
"""

import argparse

from . import league
from .config import MARKETS


def main():
    ap = argparse.ArgumentParser(prog="trader")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="밀린 주문 체결, 평가, 새 결정")
    r.add_argument("--market", choices=[*MARKETS, "all"], default="all")
    lv = sub.add_parser("live", help="장중 실시간 감시 (끄기 전까지 계속 돈다)")
    lv.add_argument("--market", choices=[*MARKETS, "all"], default="all")
    lv.add_argument("--once", action="store_true", help="한 번만 확인하고 끝 (점검용)")
    p = sub.add_parser("report", help="대시보드 HTML 생성")
    p.add_argument("--out", default=str(league.ROOT / "report" / "index.html"))
    p.add_argument("--fragment", action="store_true", help="html/head/body 없이 본문만 (아티팩트용)")
    args = ap.parse_args()

    if args.cmd in ("run", "live"):
        markets = list(MARKETS) if args.market == "all" else [args.market]
        if args.cmd == "run":
            for m in markets:
                league.run(m)
        else:
            from . import live

            live.run_forever(markets, once=args.once)
    else:
        from . import report

        path = report.build(args.out, fragment=args.fragment)
        print(f"대시보드: {path}")


if __name__ == "__main__":
    main()
