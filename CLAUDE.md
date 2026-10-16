# CLAUDE.md — blue-strawberry

## 進め方
- 応答は日本語で行う。
- 依頼されたタスクは途中で止めず、実装 → テスト → コミット → プッシュまで完遂する。
- 確認が必要なのは、意図が読み取れない場合と取り消しにくい操作の前だけ。
- `git push --force` / `-f` は使わない（settings.json で deny 済み）。

## プロジェクト概要
Claude Code のセッション記録 (JSONL) を検索・索引する単一ファイルの CLI `recall.py` と、Markdown ノートによる「メモリグラフ」層。

- `recall.py` — 本体。**標準ライブラリのみ**（外部依存を追加しない）。Python 3.9+ 対応を保つ。
- `session_start_recall.sh` — SessionStart フックから呼ばれるスクリプト。
- `sample/projects/` — 動作確認用の合成データ。
- `tests/` — pytest のテスト。
- `archive/`, `docs/` — 過去資料と画像。

## テスト
```
python3 -m pip install pytest
python3 -m pytest -q
```
CI (`.github/workflows/ci.yml`) は ubuntu と windows の両方で `pytest -q` を実行する。パス区切りや改行コードなど Windows で壊れる書き方に注意する。

## 動作確認
```
python3 recall.py --projects-dir sample/projects list
python3 recall.py --projects-dir sample/projects check   # dead=0 orphan=0 missing=0 ghost=0 stale=0 になること
```
