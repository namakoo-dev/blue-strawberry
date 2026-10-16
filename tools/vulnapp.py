#!/usr/bin/env python3
"""minihawk の動作確認用の、わざと脆弱なテスト用 Web アプリ (標準ライブラリのみ)。
localhost 専用。本番で使うものではない。

脆弱性 (意図的):
  - 反射型 XSS (?q= をそのまま HTML に埋め込む)
  - パスの素朴な ?file= 読み込み (トラバーサル)
  - セキュリティヘッダー無し / Server バージョン表示 / HttpOnly 無し Cookie
  - ?boom=1 で詳細な 500 エラー
"""
import html  # noqa: F401 (あえて使わない、の対比用)
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs


class Handler(BaseHTTPRequestHandler):
    server_version = "VulnApp/1.2.3"
    sys_version = ""

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "boom" in params:
            raise RuntimeError("intentional boom for error-leak test")

        if "file" in params:
            name = params["file"][0]
            try:
                with open(name, "r", errors="replace") as fh:  # noqa: S???
                    data = fh.read(2000)
                self._respond(200, f"<pre>{data}</pre>")
            except OSError as e:
                self._respond(404, f"not found: {e}")
            return

        q = params.get("q", [""])[0]
        # わざと未エスケープで埋め込む
        self._respond(200, f"<html><body>検索語: {q}</body></html>",
                      set_cookie="session=abc123; Path=/")

    def _respond(self, status, body, set_cookie=None):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
