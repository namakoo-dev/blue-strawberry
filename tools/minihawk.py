#!/usr/bin/env python3
"""minihawk — 自作の簡易 DAST スキャナ (標準ライブラリのみ)。

自分で起動したローカル Web アプリに対して、よくある問題を能動的に調べる教育用ツール。
HawkScan が使えない環境での代替。あくまで「自分の管理下のアプリ」に対してのみ使うこと。

使い方:
    python3 minihawk.py http://127.0.0.1:8000
    python3 minihawk.py http://127.0.0.1:8000 --paths / /search /login --json out.json

チェック項目:
  - セキュリティヘッダーの欠落 (CSP / X-Content-Type-Options / HSTS など)
  - サーバーのバージョン情報漏えい (Server / X-Powered-By ヘッダー)
  - Cookie の属性不足 (HttpOnly / Secure / SameSite)
  - 反射型 XSS (一意なマーカーがエスケープされず応答に出るか)
  - ディレクトリトラバーサル (../ で既知ファイルの中身が返るか)
  - 詳細なスタックトレース / エラー情報の漏えい
"""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

TIMEOUT = 8

SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}


class Finding:
    def __init__(self, severity, name, path, detail):
        self.severity = severity
        self.name = name
        self.path = path
        self.detail = detail

    def as_dict(self):
        return {"severity": self.severity, "name": self.name,
                "path": self.path, "detail": self.detail}


def fetch(url, timeout=TIMEOUT):
    """(status, headers(dict, 小文字キー), body(str)) を返す。失敗時は status=None。"""
    req = urllib.request.Request(url, headers={"User-Agent": "minihawk/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(200_000).decode("utf-8", errors="replace")
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, headers, body
    except urllib.error.HTTPError as e:
        body = e.read(200_000).decode("utf-8", errors="replace")
        headers = {k.lower(): v for k, v in (e.headers or {}).items()}
        return e.code, headers, body
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, {}, str(e)


def check_headers(url, path, headers, findings):
    want = {
        "content-security-policy": ("MEDIUM", "CSP ヘッダーが無い (XSS 緩和策の欠如)"),
        "x-content-type-options": ("LOW", "X-Content-Type-Options: nosniff が無い"),
        "x-frame-options": ("LOW", "X-Frame-Options が無い (クリックジャッキング)"),
    }
    for h, (sev, desc) in want.items():
        if h not in headers:
            findings.append(Finding(sev, "missing-security-header", path, desc))
    if url.startswith("https://") and "strict-transport-security" not in headers:
        findings.append(Finding("MEDIUM", "missing-hsts", path,
                                 "HTTPS だが HSTS ヘッダーが無い"))
    for leak in ("server", "x-powered-by"):
        val = headers.get(leak)
        # バージョン番号らしきものが含まれていれば漏えい扱い
        if val and any(c.isdigit() for c in val):
            findings.append(Finding("LOW", "version-disclosure", path,
                                     f"{leak}: {val} (バージョン情報の漏えい)"))


def check_cookies(path, headers, findings):
    raw = headers.get("set-cookie")
    if not raw:
        return
    low = raw.lower()
    name = raw.split("=", 1)[0]
    if "httponly" not in low:
        findings.append(Finding("MEDIUM", "cookie-no-httponly", path,
                                 f"Cookie {name} に HttpOnly が無い"))
    if "samesite" not in low:
        findings.append(Finding("LOW", "cookie-no-samesite", path,
                                 f"Cookie {name} に SameSite が無い"))


def check_error_leak(path, status, body, findings):
    markers = ("Traceback (most recent call last)", "SQLSTATE",
               "java.lang.", "at org.", "ORA-0", "stack trace")
    if status and status >= 500:
        hit = next((m for m in markers if m in body), None)
        if hit:
            findings.append(Finding("MEDIUM", "verbose-error", path,
                                     f"5xx 応答に詳細なエラー情報: '{hit}'"))


def probe_xss(base, path, findings):
    marker = "mh" + uuid.uuid4().hex[:10]
    payload = f"<mhx>{marker}</mhx>"
    url = build_url(base, path, {"q": payload})
    status, headers, body = fetch(url)
    if status is None:
        return
    ctype = headers.get("content-type", "")
    if "html" in ctype and payload in body:
        findings.append(Finding("HIGH", "reflected-xss", path,
                                 f"パラメータ q の入力が未エスケープで応答に反映 "
                                 f"(marker {marker})"))


def probe_traversal(base, path, findings):
    for attack in ("../../../../etc/passwd", "....//....//etc/passwd"):
        url = build_url(base, path, {"file": attack})
        status, _, body = fetch(url)
        if status == 200 and "root:x:0:0" in body:
            findings.append(Finding("HIGH", "path-traversal", path,
                                     f"パラメータ file で /etc/passwd を読取 (payload {attack})"))
            return


def build_url(base, path, params):
    u = urllib.parse.urljoin(base, path)
    if params:
        sep = "&" if "?" in u else "?"
        u = u + sep + urllib.parse.urlencode(params)
    return u


def scan(base, paths):
    findings = []
    reachable = False
    for path in paths:
        url = urllib.parse.urljoin(base, path)
        status, headers, body = fetch(url)
        if status is None:
            findings.append(Finding("INFO", "unreachable", path,
                                     f"到達不能: {body}"))
            continue
        reachable = True
        check_headers(base, path, headers, findings)
        check_cookies(path, headers, findings)
        check_error_leak(path, status, body, findings)
        probe_xss(base, path, findings)
        probe_traversal(base, path, findings)
    return findings, reachable


def main(argv=None):
    ap = argparse.ArgumentParser(description="minihawk — 簡易 DAST スキャナ")
    ap.add_argument("target", help="対象のベース URL (例 http://127.0.0.1:8000)")
    ap.add_argument("--paths", nargs="+", default=["/"],
                    help="走査するパス (既定: /)")
    ap.add_argument("--json", metavar="FILE", help="結果を JSON で保存")
    ap.add_argument("--fail-on", default="HIGH",
                    choices=["HIGH", "MEDIUM", "LOW", "NONE"],
                    help="この深刻度以上が有れば exit 1 (既定 HIGH)")
    args = ap.parse_args(argv)

    if not args.target.startswith(("http://", "https://")):
        ap.error("target は http:// か https:// で始めてください")

    findings, reachable = scan(args.target, args.paths)
    findings.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 9))

    print(f"=== minihawk scan: {args.target} ===")
    if not reachable:
        print("対象に 1 つも到達できませんでした。アプリは起動していますか？")
    counts = {}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
        print(f"[{f.severity:6}] {f.name:24} {f.path:14} {f.detail}")
    if not findings:
        print("検出項目なし。")
    summary = " ".join(f"{k}={counts.get(k,0)}"
                       for k in ("HIGH", "MEDIUM", "LOW", "INFO"))
    print(f"--- {summary} total={len(findings)}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"target": args.target,
                       "findings": [f.as_dict() for f in findings]},
                      fh, ensure_ascii=False, indent=2)
        print(f"JSON を書き出しました: {args.json}")

    if args.fail_on != "NONE":
        threshold = SEVERITY_ORDER[args.fail_on]
        if any(SEVERITY_ORDER.get(f.severity, 9) <= threshold for f in findings):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
