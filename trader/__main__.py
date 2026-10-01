"""사용법:
    python -m trader run [--market kr|us|all]   하루치 리그 진행 (장 마감 후 한 번)
    python -m trader live [--market kr|us|all]  장중 실시간 감시: 시세·뉴스를 계속 보다가 사건이 터지면 AI가 반응
    python -m trader serve [--port 8765]        실시간 대시보드만 띄우기 (엔진은 따로 돌고 있을 때)
    python -m trader report [--out 경로]          대시보드 HTML 만들기
"""

import argparse
import sys

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
    lv.add_argument("--serve", type=int, metavar="PORT", help="이 포트로 실시간 대시보드도 같이 띄운다")
    lv.add_argument("--host", default="127.0.0.1", help="대시보드 주소 (기본은 이 컴퓨터에서만)")
    sv = sub.add_parser("serve", help="실시간 대시보드 서버")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--host", default="127.0.0.1", help="기본은 이 컴퓨터에서만 열린다")
    p = sub.add_parser("report", help="대시보드 HTML 생성")
    p.add_argument("--out", default=str(league.ROOT / "report" / "index.html"))
    p.add_argument("--fragment", action="store_true", help="html/head/body 없이 본문만 (아티팩트용)")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):  # 콘솔이 못 찍는 글자(이모지 등)가 뉴스 제목에 있어도 멈추지 않게
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    if args.cmd == "run":
        for m in list(MARKETS) if args.market == "all" else [args.market]:
            league.run(m)
    elif args.cmd == "live":
        from . import live

        if args.serve:
            from . import serve

            serve.start_in_thread(args.serve, args.host)
        live.run_forever(list(MARKETS) if args.market == "all" else [args.market], once=args.once)
    elif args.cmd == "serve":
        from . import serve

        serve.serve(args.port, args.host)
    else:
        from . import report

        path = report.build(args.out, fragment=args.fragment)
        print(f"대시보드: {path}")


if __name__ == "__main__":
    main()
