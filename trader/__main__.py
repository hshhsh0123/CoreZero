"""사용법:
    python -m trader run [--market kr|us|all]   하루치 리그 진행
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
    p = sub.add_parser("report", help="대시보드 HTML 생성")
    p.add_argument("--out", default=str(league.ROOT / "report" / "index.html"))
    p.add_argument("--fragment", action="store_true", help="html/head/body 없이 본문만 (아티팩트용)")
    args = ap.parse_args()

    if args.cmd == "run":
        markets = list(MARKETS) if args.market == "all" else [args.market]
        for m in markets:
            league.run(m)
    else:
        from . import report

        path = report.build(args.out, fragment=args.fragment)
        print(f"대시보드: {path}")


if __name__ == "__main__":
    main()
