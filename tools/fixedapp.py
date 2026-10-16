#!/usr/bin/env python3
"""vulnapp.py の脆弱性を修正した版。minihawk の再スキャンで HIGH/MEDIUM が消えることを示す。"""
import html
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

SAFE_DIR = os.path.abspath(os.path.dirname(__file__))


class Handler(BaseHTTPRequestHandler):
    server_version = "app"      # バージョンを出さない
    sys_version = ""

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "file" in params:
            # ベースディレクトリ内に限定してトラバーサルを防ぐ
            name = os.path.basename(params["file"][0])
            target = os.path.join(SAFE_DIR, name)
            if os.path.isfile(target):
                with open(target, "r", errors="replace") as fh:
                    self._respond(200, "<pre>%s</pre>" % html.escape(fh.read(2000)))
            else:
                self._respond(404, "not found")
            return

        q = params.get("q", [""])[0]
        self._respond(200,
                      "<html><body>検索語: %s</body></html>" % html.escape(q),
                      set_cookie="session=abc123; Path=/; HttpOnly; SameSite=Lax")

    def _respond(self, status, body, set_cookie=None):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Security-Policy", "default-src 'self'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
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
