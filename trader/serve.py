"""로컬 실시간 대시보드 서버. 표준 라이브러리만 쓴다. 기본은 이 컴퓨터에서만 열린다."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import report


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/data":
                body = json.dumps(report.collect(live_mode=True), ensure_ascii=False).encode("utf-8")
                return self._send(200, body, "application/json; charset=utf-8")
            if path in ("/", "/index.html"):
                return self._send(200, report.render(live_mode=True).encode("utf-8"), "text/html; charset=utf-8")
        except Exception as e:  # 파일을 쓰는 도중에 읽어도 서버가 죽지 않게
            return self._send(500, f"오류: {e}".encode("utf-8"), "text/plain; charset=utf-8")
        self._send(404, "없는 주소예요".encode("utf-8"), "text/plain; charset=utf-8")

    def log_message(self, *args):
        pass


def make_server(port, host="127.0.0.1"):
    return ThreadingHTTPServer((host, port), Handler)


def start_in_thread(port, host="127.0.0.1", log=print):
    server = make_server(port, host)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log(f"대시보드: http://{host}:{server.server_address[1]}")
    return server


def serve(port=8765, host="127.0.0.1", log=print):
    server = make_server(port, host)
    log(f"대시보드: http://{host}:{server.server_address[1]}  (끄려면 Ctrl+C)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
