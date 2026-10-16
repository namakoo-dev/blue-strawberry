#!/bin/bash
# Claude Code クラウドセッション用: テストに必要な開発依存 (pytest) を入れる。
# recall.py 本体は標準ライブラリのみなので、ここで入れるのは pytest だけ。
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

if ! python3 -c 'import pytest' 2>/dev/null; then
  python3 -m pip install --quiet pytest
fi
