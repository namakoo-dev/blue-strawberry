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

# 各プロジェクトの MEMORY.md 索引と、そのプロジェクト単位の整合性サマリ(check の graph: 行)を
# 集めて渡す。生の全文は長すぎるので索引だけ。check はプロジェクトごとに --memory-dir を明示する
# (archive/ 直下にプロジェクトが複数あると memory の自動検出が「未設定」扱いになるため)。
# check は WARN があっても exit 0 なので、終了コードではなく graph: 行そのものを載せる。
SUMMARY=""
while IFS= read -r -d '' f; do
  mem=$(dirname "$f")
  projdir=$(dirname "$mem")
  proj=$(basename "$projdir")
  graph=$(python3 "$DIR/recall.py" --projects-dir "$projdir" --memory-dir "$mem" check 2>/dev/null \
          | grep '^graph:' || echo 'graph: (check 失敗)')
  SUMMARY="${SUMMARY}### ${proj}
${graph}
$(cat "$f")

"
done < <(find "$ARCHIVE" -path "*/memory/MEMORY.md" -print0 2>/dev/null | sort -z)

if [ -z "$SUMMARY" ]; then
  echo '{}'
  exit 0
fi

python3 - "$SUMMARY" <<'PY'
import json, sys
summary = sys.argv[1]
ctx = ("[blue-strawberry archive からの自動読み込み]\n"
       "(archive/ には transcript を置かないので、graph: 行の dead は系譜アンカーの"
       "参照先セッションが手元に無いことを示すだけで、記憶の破損ではない)\n\n" + summary)
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": ctx
    }
}))
PY
