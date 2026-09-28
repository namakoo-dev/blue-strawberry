#!/usr/bin/env bash
# Claude Code SessionStart フック: blue-strawberry の archive/ から、直近の記憶メモを
# 自動でセッション冒頭のコンテキストに注入する(手動で recall.py を叩かなくて済むように)。
#
# 前提: このリポジトリ(blue-strawberry)がローカルにclone済みであること。
# BLUE_STRAWBERRY_DIR で場所を上書きできる(既定: このスクリプト自身の場所)。
set -uo pipefail

DIR="${BLUE_STRAWBERRY_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
ARCHIVE="$DIR/archive"

if [ ! -d "$ARCHIVE" ]; then
  # 記憶が無くても失敗にしない(初回セッション等、archiveが空のことはある)
  echo '{}'
  exit 0
fi

OUT=$(python3 "$DIR/recall.py" --projects-dir "$ARCHIVE" check 2>&1)
CHECK_EXIT=$?

# 各プロジェクトの MEMORY.md 索引を集めて要約として渡す(生の全文は長すぎるので索引だけ)
SUMMARY=""
while IFS= read -r -d '' f; do
  proj=$(basename "$(dirname "$(dirname "$f")")")
  SUMMARY="${SUMMARY}### ${proj}
$(cat "$f")

"
done < <(find "$ARCHIVE" -path "*/memory/MEMORY.md" -print0 2>/dev/null)

if [ -z "$SUMMARY" ]; then
  echo '{}'
  exit 0
fi

python3 - "$SUMMARY" "$CHECK_EXIT" <<'PY'
import json, sys
summary, check_exit = sys.argv[1], sys.argv[2]
ctx = ("[blue-strawberry archive からの自動読み込み]\n"
       f"(整合性チェック exit={check_exit})\n\n" + summary)
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": ctx
    }
}))
PY
