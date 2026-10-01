"""표준 라이브러리만 쓰는 HTTP 헬퍼. 환경변수의 HTTPS_PROXY를 자동으로 따른다."""

import json
import time
import urllib.error
import urllib.request

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
RETRY_STATUS = {429, 500, 502, 503, 504}


def _open(req, timeout, retries):
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_STATUS or attempt == retries - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == retries - 1:
                raise
        time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def get(url, timeout=20, retries=3):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return _open(req, timeout, retries)


def get_json(url, **kw):
    return json.loads(get(url, **kw))


def post_json(url, payload, headers=None, timeout=600, retries=2):
    body = json.dumps(payload).encode()
    hdrs = {"Content-Type": "application/json", "User-Agent": UA, **(headers or {})}
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    return json.loads(_open(req, timeout, retries))
