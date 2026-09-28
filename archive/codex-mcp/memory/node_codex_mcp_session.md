---
name: codex_mcp_session
description: codex-mcp のsandbox fail-open調査・failIfUnavailable検証・gpt6-astraサブエージェント追加をまとめたセッション記録
metadata: 
  node_type: memory
  type: project
  originSessionId: 5df8f19e-a017-518b-9a04-959a0e7e462b
  modified: 2026-09-28T01:29:30.562Z
---

このセッションで codex-mcp リポジトリに対して行った主な作業:

- Claude Code の `sandbox.enabled` が `bwrap`/`socat` 欠如時に fail-open する挙動を再現・
  レポート化。既存の `sandbox.failIfUnavailable:true` で fail-closed にできることを検証。
- codex_argv() を Linux/macOS/Termux(Android) でも本体を探せるように拡張(テスト4本追加)。
- codex_status に proxy 設定を診断表示する機能を追加(認証情報はマスク)。
- `.claude/agents/gpt6-astra.md` を追加: codex_ask 経由で OpenAI の gpt-6-astra
  (Codex CLI の最上位モデル)に相談するサブエージェント。`.mcp.json` で codex-mcp を登録。
- 検知フック(sandbox_deps_check.sh / v2)を作成し、なりすまし・ログ改ざん・フック無効化の
  3通りのバイパス経路を自分で実証。ローカル完結の検知は同一権限の攻撃者に対して無力であり、
  本命の対策は failIfUnavailable であるという結論に至った。

系譜: 5df8f19e@2026-09-28 01:29 (セッション記録・更新分格納)
