---
name: codex_mcp_session
description: codex-mcp のsandbox fail-open調査・failIfUnavailable検証・gpt6-astraサブエージェント追加をまとめたセッション記録
metadata: 
  node_type: memory
  type: project
  originSessionId: 5df8f19e-a017-518b-9a04-959a0e7e462b
  modified: 2026-09-28T12:00:00.000Z
---

このセッションで codex-mcp リポジトリに対して行った主な作業:

## コア機能拡張
- codex_argv() を Linux/macOS/Termux(Android) でも本体を探せるように拡張(テスト4本追加)。
- codex_status に proxy 設定を診断表示する機能を追加(認証情報はマスク, テスト3本追加)。

## サブエージェント
- `.claude/agents/gpt6-astra.md` を追加: codex_ask 経由で OpenAI の gpt-6-astra
  (Codex CLI の最上位モデル)に相談するサブエージェント。
- `.mcp.json` で codex-mcp 自体を MCP サーバーとして登録(python3 server.py 起動)。

## Sandbox 調査
- Claude Code の `sandbox.enabled` が `bwrap`/`socat` 欠如時に fail-open する挙動を再現・
  レポート化。既存の `sandbox.failIfUnavailable:true` で fail-closed にできることを検証。
- 検知フック(sandbox_deps_check.sh / v2)を作成し、なりすまし・ログ改ざん・フック無効化の
  3通りのバイパス経路を自分で実証。ローカル完結の検知は同一権限の攻撃者に対して無力であり、
  本命の対策は failIfUnavailable であるという結論。
- ネットワーク名前空間隔離テスト(`bwrap --unshare-net` + curl)が最も信頼できる
  なりすまし検出手段であることを確認。dpkg ベースライン検証も実施済み。

## セッション記憶
- blue-strawberry の archive/ に記憶ノートを格納。
- session_start_recall.sh を作成: SessionStart フックで MEMORY.md 索引を自動注入。
- brain-vault(age暗号化リポジトリ)を接続済み。ユーザーの age 公開鍵待ち。

## 成果物(scratchpad / outputs)
- secretscan.py — 本番実装(Anthropic/OpenAI/GitHub/AWS/PEM/Slack/Google パターン)
- sandbox-fail-open-report.md, worst-case-analysis.md — レポート
- codex_mcp_crash_check.py — access.jsonl からのクラッシュ検出
- detection-bypass-analysis.md — バイパス3手法の分析
- sandbox_deps_check_v2.sh — 機能テスト付き検知フック(手動実行のみ、自動起動禁止)

## ブランチ
- claude/sandbox-settings-test-q8ynmw — 全変更をここにpush済み
- HEAD: 202c55d (gpt6-astra: 実在するOpenAIモデル gpt-6-astra を明示指定するよう修正)

## ユーザー制約
- 「自動起動させるな」— verify_sandbox.sh は手動実行のみ
- 「個人情報渡さないという」— 個人情報を聞かない・扱わない

系譜: 5df8f19e@2026-09-28 01:29 → 継続セッション@2026-09-28 12:00
