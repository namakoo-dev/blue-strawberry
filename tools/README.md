# minihawk — 簡易 DAST スキャナ

HawkScan 等が使えない環境向けの、標準ライブラリのみの軽量な動的セキュリティスキャナ。
**自分の管理下で起動しているローカル Web アプリに対してのみ**使うこと。

## 使い方
```
python3 tools/minihawk.py http://127.0.0.1:8000 --paths / /search /login
```
- `--json out.json` で結果を保存
- `--fail-on HIGH` (既定) で HIGH 以上が有れば exit 1 (CI 向け)

## 検査項目
反射型 XSS / ディレクトリトラバーサル / セキュリティヘッダー欠落 (CSP など) /
Cookie 属性不足 (HttpOnly・SameSite) / バージョン漏えい / 詳細エラー漏えい

## 動作確認 (発見→修正→再スキャン)
```
python3 tools/vulnapp.py 8071 &          # わざと脆弱なテストアプリ
python3 tools/minihawk.py http://127.0.0.1:8071 --paths / /search   # HIGH=2 等を検出
python3 tools/fixedapp.py 8072 &         # 修正版
python3 tools/minihawk.py http://127.0.0.1:8072 --paths / /search   # 検出 0
```

本格的な網羅性は無い教育・自己診断用。実運用は本物の HawkScan / OWASP ZAP を推奨。
