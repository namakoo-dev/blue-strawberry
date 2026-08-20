#!/usr/bin/env python3
"""Claude Code のセッション transcript (JSONL) を索引・検索・部分読み出しするローカル CLI。

背景: Claude Code は会話を ~/.claude/projects/<project>/<session-id>.jsonl
に永続化する。コンテキスト圧縮 (compact) 後もファイルは消えない ── 圧縮で「生きたコンテキ
スト」から落ちた過去の会話は、ここに残っている。使用者は人間ではなく AI エージェント自身。
出力はターミナルで読む前提 ── 機械可読フォーマット (JSON 等) より人間可読を優先する。

    python recall.py list [--project 部分一致]
    python recall.py search "query" [--project P] [--days N] [--max 20] [-i]
    python recall.py show SESSION_PREFIX [--grep q] [--head N | --tail N]
    python recall.py show SESSION_PREFIX --at "YYYY-MM-DD HH:MM" [--window 分]
    python recall.py origin MEMORY_NAME_PREFIX [--grep q | --tail N]
    python recall.py check [--quiet]
    python recall.py lineage QUERY [--deep]
    python recall.py suggest [--max 10]
    python recall.py stale [--top 10]
    python recall.py backlinks NODE名

全サブコマンドの前に置ける共通の上書き引数: --projects-dir / --memory-dir
(優先順位: CLI 引数 > 環境変数 RECALL_PROJECTS_DIR / RECALL_MEMORY_DIR > 自動検出。
projects-dir の既定値は ~/.claude/projects、その直下のプロジェクトディレクトリが
ちょうど1つならそれを自動採用する (0個/複数個なら一覧を出して要指定)。--projects-dir
には親 (プロジェクト群) だけでなく単一プロジェクトの dir を直接渡してもよい (直下に
*.jsonl があればそれ自体を1プロジェクトとして扱う)。memory-dir の
既定値は projects-dir 直下で memory/ サブディレクトリを持つプロジェクトがちょうど1つ
ならそこの memory/ を自動採用する (0個/複数個なら memory 層は未設定として扱う)。

--- transcript 形式 (実データを調べて分かったこと) ---
1 行 = 1 JSON オブジェクト。主な "type":
  - "user" / "assistant"     : message.content を持つ。content は str か、ブロックの list。
        list のブロック type: "text" (会話文)、"tool_use" (assistant のみ、name/input を持つ)、
        "tool_result" (user のみ、tool_use_id で対応する tool_use に紐付く)、"thinking"
        (assistant のみ)、"image"。
        圧縮直後の要約は type="user" だが record 直下に "isCompactSummary": true が付く
        (content は str で "This session is being continued from a previous conversation..."
        から始まる)。
  - "system" / subtype="compact_boundary" : 圧縮イベントそのもの。
        compactMetadata.preTokens / postTokens / trigger 等を持つ。★ これが圧縮の記録形式。
        record 直下の isCompactSummary な user 行 (上記) とセットで、圧縮の「境界」と
        「要約テキスト」が別行に分かれて残る。
  - その他 (会話テキストを持たない、search/show の対象外):
        "mode", "permission-mode", "file-history-snapshot", "file-history-delta",
        "attachment", "ai-title", "last-prompt", "queue-operation",
        "system" (turn_duration / away_summary / stop_hook_summary 等の他 subtype)
timestamp は user/assistant/system/attachment/file-history-delta 行の直下にある
(ISO8601, 例 "2026-08-02T10:10:43.550Z")。mode/permission-mode 等には無い。

--- 性能設計判断 ---
- search: 生の行 (bytes) に query が含まれない行は json.loads しない (プリフィルタ)。
  case-insensitive は行を lower() してから bytes 比較 (非 ASCII は lower() で変化しない
  ので日本語クエリでも安全)。162MB ファイル込みの全体検索を実測 (下記コミット/報告参照)。
- list: 162MB ファイルを全行 parse しない。開始情報は先頭 ~300 行だけ読んで打ち切り、
  終了 timestamp はファイル末尾 数百KB だけ読んで逆順に探す。圧縮イベント数だけは
  全行スキャンが要るが、生 bytes の部分文字列一致だけ (json.loads しない) で足りる。
- どのコマンドも全行を list にため込まない。list は各行を都度出力、search は都度出力、
  show は既定/--head/--tail は 1 行ずつストリームし (--tail は deque(maxlen=N) で
  末尾だけ保持)、--grep のみ前後文脈が要るため単一セッション分を list 化する。
- 壊れた JSON 行 (クラッシュしたセッションの尻切れ行等) は json.JSONDecodeError /
  UnicodeDecodeError / ValueError を捕まえてスキップし、処理を続ける。

--- 記憶グラフ (origin / check) ---
記憶は 3 層: ①MEMORY.md (索引) ②memory/*.md (131 ファイル、frontmatter+本文) ③transcript
(このツールの主対象)。origin は②→③の跳躍 (memory ノードから元セッションへ)、check は①⇄②
の整合番人。memory の実体は自動検出される (--memory-dir / RECALL_MEMORY_DIR で明示的に
上書き可能。既定は PROJECTS_DIR 直下で memory/ を持つプロジェクトがちょうど1つのときの
その memory/ ── 詳細は冒頭の共通引数の節を参照)。
- memory/*.md の形式: YAML 風 frontmatter (`---` で囲む) に `name:` (自由文言、ファイル名と
  一致するとは限らない ── 131 件中 86 件が不一致だった。実データで確認済み)、`description:`、
  `metadata:` ブロック配下にインデントされた `originSessionId:` (元セッションの UUID) を持つ。
  本文中の `[[名前]]` は他 memory ファイル (名前.md) への wikilink。フルの YAML パーサは
  使わず、行単位の正規表現で該当 3 フィールドだけ拾う (この形式専用、汎用 YAML ではない)。
- MEMORY.md の行形式: `- [表示名](ファイル名.md) — 一言`。
- check の dead link (WARN): [[x]] の x.md が memory dir に無い。★ skill 名への意図的な
  先行リンクが実在する (実データで tuning-audit 等 3 件確認) ので ERROR にはしない。
- check の orphan (WARN): 自分への inbound リンクも自分からの outbound リンクも無いノード。
- check の index 欠落/幽霊 (ERROR): memory/*.md ⇔ MEMORY.md の行の集合が食い違う場合。
  実データで 1 件の index 欠落を検出済み (feedback_pace_and_buildup.md)。

--- 系譜 (lineage) ---
memory 本文の行 `系譜: <node> (ラベル) → <node> (ラベル) → ...` が方向付き・時系列のエッジ
(実例: feedback_search_absorb_build_order.md 末尾)。1 行 = 1 鎖、矢印は "→" (全角)、
折り返されない前提。ノードは 2 種:
  - `[[memory-name]]`                          memory ノード (実在しなくてもエラーにしない)
  - `<session-prefix>@YYYY-MM-DD HH:MM`        transcript の瞬間アンカー (UTC、transcript の
                                                 timestamp と同じ書式の日時)
どちらも末尾に任意で ` (ラベル)` が付く。lineage は memory-name / session-prefix の前方一致
で「query を含む鎖」を全部集めて矢印順に表示する。memory ノードの時刻は対象ファイルの
frontmatter modified、アンカーの時刻は行に書かれている値をそのまま使う (検証はしない)。
--deep はアンカー ±2 分以内の text ブロックを transcript から引いて添える (無ければ
「(該当時刻の発言が見つからない)」)。check はこのアンカーの prefix が実在セッションに
一致するかだけ検査する (時刻の妥当性までは見ない ── 高くつく)。
MEMORY.md 末尾の "## 系譜" セクションは系譜の索引 (1 系譜行 = 1 行、`- <テーマ> → <記憶名>
(= その系譜行が書かれているファイル名) — <一言>`)。check はこの索引と実体 (各ファイルの
系譜: 行の本数) を突き合わせる (欠落/幽霊/本数不一致、いずれも ERROR)。

--- suggest (候補提案、読み取り専用) ---
「隠れた関係」の候補を出すだけで、ファイルは一切書き換えない。判断と接続は人がやる。
A. 語彙類似: frontmatter を除いた本文から token 化 (ASCII 語 3 文字以上／カタカナ 3+ 連／
   漢字 2+ 連は重複 2-gram 化) → TF-IDF (tf=1+log(n)、df が全体の 25% 超の頻出語は語彙から
   除外、idf=log(N/df)) → cosine。既にどちらか向きでも wikilink がある対と自己対は除外。
B. 三角閉包: [[link]] を無向グラフ化 (自己リンクは除外 ── 系譜行の最終ノードが自分自身を
   指す実例があり、これを弾かないと壊れる)。共通隣人 3 以上かつ未接続の対を提案。
どちらも読み取り専用・exit コードは常に 0 (判定ツールではなく提案ツールなので)。
"""
import argparse
import itertools
import json
import math
import os
import re
import sys
import time
from collections import Counter, deque
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def resolve_projects_dir(cli_value=None):
    """PROJECTS_DIR の解決順位: CLI 引数 > 環境変数 RECALL_PROJECTS_DIR > 自動検出。
    呼ぶ度に os.environ を読む (import 時に一度だけ評価しない) ── テストで
    monkeypatch.setenv してから直接呼べるようにするため。"""
    if cli_value:
        return Path(cli_value)
    env_value = os.environ.get("RECALL_PROJECTS_DIR")
    if env_value:
        return Path(env_value)
    return _autodetect_projects_dir()


def _autodetect_projects_dir():
    """--projects-dir / RECALL_PROJECTS_DIR が無いときの既定値探索。
    ~/.claude/projects 自体が無ければ「データが見つからない」で exit 2。
    直下のディレクトリがちょうど1つならそれを自動採用 (= DEFAULT_PROJECTS_DIR を返す)。
    0個/複数個ならどれを見るか曖昧なので、一覧を出して使用例つきで exit 2。"""
    base = DEFAULT_PROJECTS_DIR
    if not base.exists():
        sys.stderr.write(f"Claude Code のデータが見つからない: {base}\n")
        sys.exit(2)
    subdirs = sorted(d for d in base.iterdir() if d.is_dir())
    if len(subdirs) == 1:
        return base
    sys.stderr.write(f"複数のプロジェクトディレクトリが見つかった ({len(subdirs)} 件)。"
                      "--projects-dir で指定せよ:\n")
    for d in subdirs:
        sys.stderr.write(f"  {d}\n")
    example = subdirs[0] if subdirs else base
    sys.stderr.write(f'例: python recall.py --projects-dir "{example}" list\n')
    sys.exit(2)


def resolve_memory_dir(cli_value=None):
    """MEMORY_DIR_OVERRIDE の解決順位: CLI 引数 > 環境変数 RECALL_MEMORY_DIR > None。
    None は「memory_dir() が PROJECTS_DIR から派生させる」の意味。"""
    if cli_value:
        return Path(cli_value)
    env_value = os.environ.get("RECALL_MEMORY_DIR")
    if env_value:
        return Path(env_value)
    return None


# モジュール読み込み時の暫定値。実際の値は main() が argparse 後に
# resolve_projects_dir(args.projects_dir) で確定させる (下記)。ここで resolve_projects_dir()
# を呼ばないのは、自動検出 (曖昧/データ無し) が exit(2) や案内メッセージという副作用を
# 持つため ── import しただけで (--projects-dir を渡すつもりの起動でも) それが走っては
# ならない。CLI 引数 / 環境変数 を経由しない限りは常に DEFAULT_PROJECTS_DIR で十分。
PROJECTS_DIR = DEFAULT_PROJECTS_DIR
MEMORY_DIR_OVERRIDE = None

COMPACT_MARK = b'"subtype":"compact_boundary"'
# ★ 実データは区切り文字にスペースを入れない JSON ("key":"val") で書かれている
#   (json.dumps の既定はスペース入り "key": "val" なので合成データを作る時は要注意)。
#   count_compact_events はこのスペース無し形への部分文字列一致に依存する。


# ---------- 共通ヘルパ ----------

def safe_json_loads(line):
    """壊れた行は None を返す (JSONDecodeError / UnicodeDecodeError / ValueError を握る)。"""
    if not line or not line.strip():
        return None
    try:
        return json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None


def iter_sessions(project_filter=None):
    """PROJECTS_DIR/<project>/*.jsonl を Path で yield する (トップレベルのみ、非再帰)。

    ★ PROJECTS_DIR 直下に *.jsonl が直に転がっている場合は、PROJECTS_DIR 自身を
    1 プロジェクトとして扱う。--projects-dir に (親ではなく) 単一プロジェクトの dir を
    直接渡された救済 -- 複数プロジェクト検出時のエラーメッセージが例示する使い方
    (`--projects-dir "<個別プロジェクトdir>"`) がそのまま動くようにするため。
    通常の「PROJECTS_DIR は親、直下の各 dir がプロジェクト」という動作は変えない
    (直下に *.jsonl が無ければ今まで通りサブディレクトリを走査する)。"""
    if not PROJECTS_DIR.exists():
        return
    # ★ 隠しファイルは除く: 実測 (2026-08-20) で親 dir 直下の .recall-usage.jsonl（想起ログ）を
    #   「セッション」と誤認し、親を単一プロジェクト扱いして本物のセッションを全滅させた。
    #   セッション transcript が . 始まりになることは無い。
    direct = sorted(p for p in PROJECTS_DIR.glob("*.jsonl") if not p.name.startswith("."))
    if direct:
        if project_filter and project_filter.lower() not in PROJECTS_DIR.name.lower():
            return
        for p in direct:
            yield p
        return
    for proj_dir in sorted(PROJECTS_DIR.iterdir()):
        if not proj_dir.is_dir():
            continue
        if project_filter and project_filter.lower() not in proj_dir.name.lower():
            continue
        for p in sorted(proj_dir.glob("*.jsonl")):
            yield p


def warn_if_no_sessions_anywhere():
    """iter_sessions(None) (フィルタ無し) が 1 件も無い時だけ、探した場所を明示して stderr に
    WARN する。呼び出し側の絞り込み結果が 0 件なのとは区別する ── フィルタ一致ゼロは正常
    (そのプロジェクトが無いだけ) だが、フィルタ前から 0 件は --projects-dir の指す場所が
    そもそも違う (親と子を取り違えた等) 事故の典型で、黙って通すと番人が空データを
    「セッションが存在しない」と誤報し続ける (実測: dead=46)。"""
    if next(iter_sessions(None), None) is None:
        sys.stderr.write(f"WARN セッションが 1 件も見つからない (探索場所: {PROJECTS_DIR})\n")
        return True
    return False


def resolve_session(prefix):
    """session-id の先頭一致で 1 件に絞る。0 件/複数件は None を返し呼び出し側でエラー表示。"""
    matches = [p for p in iter_sessions(None) if p.stem.lower().startswith(prefix.lower())]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        sys.stderr.write("複数のセッションにマッチ:\n")
        for m in matches:
            sys.stderr.write(f"  {m.stem[:8]}  ({m.parent.name})\n")
        return None
    return None


def format_size(n):
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.0f}{unit}" if unit == "B" else f"{f:.1f}{unit}"
        f /= 1024
    return f"{f:.1f}TB"


def fmt_ts(ts):
    if not ts:
        return "?"
    return ts.replace("T", " ").rstrip("Z")[:16]


# ---------- list ----------

def get_start_info(path, max_lines=300):
    """先頭を舐めて最初の timestamp と最初のユーザ発言テキストを取る。見つかり次第打ち切り。"""
    start_ts = None
    first_user = None
    with open(path, "rb") as f:
        for i, line in enumerate(f):
            if i >= max_lines:
                break
            rec = safe_json_loads(line)
            if rec is None:
                continue
            if start_ts is None and isinstance(rec.get("timestamp"), str):
                start_ts = rec["timestamp"]
            if first_user is None and rec.get("type") == "user":
                msg = rec.get("message", {})
                content = msg.get("content")
                text = None
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "text":
                            text = b.get("text")
                            break
                if text and text.strip():
                    first_user = text.strip()
            if start_ts is not None and first_user is not None:
                break
    return start_ts, first_user


def get_end_ts(path):
    """ファイル末尾だけ読んで一番後ろの timestamp を探す。見つからなければ chunk を広げる。"""
    fsize = path.stat().st_size
    for chunk in (65536, 524288, 4 * 1024 * 1024, fsize):
        chunk = min(chunk, fsize)
        with open(path, "rb") as f:
            if fsize > chunk:
                f.seek(fsize - chunk)
                f.readline()  # 先頭の半端な行を捨てる
            data = f.read()
        lines = data.split(b"\n")
        for line in reversed(lines):
            rec = safe_json_loads(line)
            if rec is not None and isinstance(rec.get("timestamp"), str):
                return rec["timestamp"]
        if chunk >= fsize:
            break
    return None


def count_compact_events(path):
    """生 bytes の部分文字列一致だけで数える (json.loads しない、行またぎもしない)。"""
    n = 0
    with open(path, "rb") as f:
        for line in f:
            if COMPACT_MARK in line:
                n += 1
    return n


def cmd_list(args):
    warn_if_no_sessions_anywhere()
    sessions = sorted(iter_sessions(args.project), key=lambda p: p.stat().st_mtime, reverse=True)
    if not sessions:
        print("該当セッションなし")
        return
    for path in sessions:
        start_ts, first_user = get_start_info(path)
        end_ts = get_end_ts(path)
        n_compact = count_compact_events(path)
        size = format_size(path.stat().st_size)
        proj = path.parent.name
        head = (first_user or "").replace("\n", " ")[:60]
        print(f"{path.stem[:8]} | {proj} | {fmt_ts(start_ts)} 〜 {fmt_ts(end_ts)} | "
              f"{size} | compact={n_compact} | {head}")


# ---------- search ----------

def iter_text_blocks(rec):
    """検索対象のテキストブロックだけ (role, text) で yield する。
    tool_use の input / tool_result の出力は対象外。ユーザ発言(str/text ブロック)と
    assistant の text ブロックのみ。圧縮要約 (isCompactSummary) は role="summary"。"""
    t = rec.get("type")
    msg = rec.get("message", {})
    content = msg.get("content")
    if t == "user":
        role = "summary" if rec.get("isCompactSummary") else "user"
        if isinstance(content, str):
            if content.strip():
                yield role, content
        elif isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    txt = b.get("text", "")
                    if txt.strip():
                        yield role, txt
    elif t == "assistant":
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    txt = b.get("text", "")
                    if txt.strip():
                        yield "assistant", txt
        elif isinstance(content, str) and content.strip():
            yield "assistant", content


def context_snippet(text, query, width=120):
    low = text.lower()
    qlow = query.lower()
    idx = low.find(qlow)
    if idx == -1:
        idx, matchlen = 0, 0
    else:
        matchlen = len(query)
    start = max(0, idx - width // 2)
    end = min(len(text), idx + matchlen + width // 2)
    snippet = " ".join(text[start:end].split())
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + snippet + suffix


def cmd_search(args):
    query = args.query
    qlow = query.lower()
    qbytes_low = query.encode("utf-8").lower()

    warn_if_no_sessions_anywhere()
    sessions = list(iter_sessions(args.project))
    if args.days:
        cutoff = time.time() - args.days * 86400
        sessions = [p for p in sessions if p.stat().st_mtime >= cutoff]
    sessions.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    found = 0
    for path in sessions:
        if found >= args.max:
            break
        with open(path, "rb") as f:
            for line in f:
                if found >= args.max:
                    break
                if qbytes_low not in line.lower():
                    continue  # プリフィルタ: json.loads しない
                rec = safe_json_loads(line)
                if rec is None:
                    continue
                for role, text in iter_text_blocks(rec):
                    if qlow not in text.lower():
                        continue
                    ts = rec.get("timestamp", "?")
                    snippet = context_snippet(text, query)
                    print(f"{path.stem[:8]} | {fmt_ts(ts)} | {role} | {snippet}")
                    found += 1
                    if found >= args.max:
                        break
    if found == 0:
        print("マッチなし")


# ---------- show ----------

def build_turns(path):
    """会話テキストを時系列で (role, ts, text) の dict として yield する。
    tool_use / tool_result は「[tool: 名前]」1 行に畳む。thinking も 1 行に畳む。"""
    tool_names = {}
    with open(path, "rb") as f:
        for line in f:
            rec = safe_json_loads(line)
            if rec is None:
                continue
            t = rec.get("type")
            ts = rec.get("timestamp", "?")

            if t == "system" and rec.get("subtype") == "compact_boundary":
                meta = rec.get("compactMetadata", {}) or {}
                pre, post = meta.get("preTokens"), meta.get("postTokens")
                yield {"role": "compact", "ts": ts, "text": f"[圧縮: {pre} -> {post} tokens]"}
                continue

            msg = rec.get("message", {})
            content = msg.get("content")

            if t == "assistant":
                if isinstance(content, list):
                    for b in content:
                        if not isinstance(b, dict):
                            continue
                        bt = b.get("type")
                        if bt == "text":
                            txt = b.get("text", "")
                            if txt.strip():
                                yield {"role": "assistant", "ts": ts, "text": txt}
                        elif bt == "tool_use":
                            name = b.get("name", "?")
                            tool_names[b.get("id")] = name
                            yield {"role": "assistant", "ts": ts, "text": f"[tool: {name}]"}
                        elif bt == "thinking":
                            th = b.get("thinking", "")
                            if th.strip():
                                yield {"role": "assistant", "ts": ts,
                                       "text": f"[thinking: {len(th)}文字]"}
                elif isinstance(content, str) and content.strip():
                    yield {"role": "assistant", "ts": ts, "text": content}

            elif t == "user":
                role = "summary" if rec.get("isCompactSummary") else "user"
                if isinstance(content, str):
                    if content.strip():
                        yield {"role": role, "ts": ts, "text": content}
                elif isinstance(content, list):
                    for b in content:
                        if not isinstance(b, dict):
                            continue
                        bt = b.get("type")
                        if bt == "text":
                            txt = b.get("text", "")
                            if txt.strip():
                                yield {"role": role, "ts": ts, "text": txt}
                        elif bt == "tool_result":
                            name = tool_names.get(b.get("tool_use_id"), "?")
                            yield {"role": "user", "ts": ts, "text": f"[tool result: {name}]"}
                        elif bt == "image":
                            yield {"role": role, "ts": ts, "text": "[image]"}


def print_turn(t, marker=False):
    tag = ">>> " if marker else ""
    print(f"{tag}[{fmt_ts(t['ts'])}] {t['role']}")
    print(t["text"])
    print()


def render_session(path, grep=None, head=None, tail=None):
    """1 セッションの会話を時系列で表示する本体。cmd_show と cmd_origin の共通経路。"""
    if grep:
        turns = list(build_turns(path))
        qlow = grep.lower()
        matched = [i for i, t in enumerate(turns) if qlow in t["text"].lower()]
        if not matched:
            print(f'"{grep}" にマッチなし ({path.stem[:8]})')
            return
        shown = set()
        for i in matched:
            for j in range(max(0, i - 3), min(len(turns), i + 4)):
                shown.add(j)
        for i in sorted(shown):
            print_turn(turns[i], marker=(i in matched))
        return

    if tail:
        buf = deque(maxlen=tail)
        for t in build_turns(path):
            buf.append(t)
        for t in buf:
            print_turn(t)
        return

    # --head N、または指定なし (全件ストリーム)
    count = 0
    for t in build_turns(path):
        print_turn(t)
        count += 1
        if head and count >= head:
            break


def render_session_at(path, anchor_dt, window_minutes):
    """build_turns() の各ターンを anchor_dt ± window_minutes 分だけ表示する。"""
    count = 0
    for t in build_turns(path):
        if not ts_within_window(t["ts"], anchor_dt, window_minutes):
            continue
        print_turn(t)
        count += 1
    if count == 0:
        print(f"{anchor_dt.strftime('%Y-%m-%d %H:%M')} 前後 {window_minutes} 分にマッチなし")


def cmd_show(args):
    path = resolve_session(args.session_prefix)
    if path is None:
        warn_if_no_sessions_anywhere()
        sys.stderr.write(f"セッションが見つからない: {args.session_prefix}\n")
        sys.exit(1)
    at = getattr(args, "at", None)
    if at:
        if args.grep or args.head or args.tail:
            sys.stderr.write("--at は --grep/--head/--tail と併用できない\n")
            sys.exit(1)
        try:
            anchor_dt = datetime.strptime(at, "%Y-%m-%d %H:%M")
        except ValueError:
            sys.stderr.write(f"--at の日時形式が不正 (YYYY-MM-DD HH:MM で指定): {at}\n")
            sys.exit(1)
        window = getattr(args, "window", 2)
        render_session_at(path, anchor_dt, window)
        return
    render_session(path, grep=args.grep, head=args.head, tail=args.tail)


# ---------- origin (memory ノード → 元セッション) ----------

MEMORY_INDEX_NAME = "MEMORY.md"


def memory_dir():
    """memory/*.md の場所。MEMORY_DIR_OVERRIDE があればそれを返す。無ければ PROJECTS_DIR を
    基点にするので monkeypatch でテスト差し替え可能 (既存テストはこの動的参照に依存する)。"""
    if MEMORY_DIR_OVERRIDE is not None:
        return MEMORY_DIR_OVERRIDE
    return _autodetect_memory_dir()


def _autodetect_memory_dir():
    """--memory-dir / RECALL_MEMORY_DIR が無いときの既定値探索。PROJECTS_DIR 直下の
    プロジェクトディレクトリのうち memory/ サブディレクトリを持つものを探す。ちょうど1件
    ならそこの memory/ を採用。0件/複数件なら「見つからない」として exists()=False な
    パスを返す (呼び出し側の mdir.exists() チェックでグレースフルデグレードに落ちる、
    例外は投げない)。"""
    if not PROJECTS_DIR.exists():
        return PROJECTS_DIR / "memory"
    candidates = [d for d in sorted(PROJECTS_DIR.iterdir())
                  if d.is_dir() and (d / "memory").is_dir()]
    if len(candidates) == 1:
        return candidates[0] / "memory"
    return PROJECTS_DIR / "memory"


def require_memory_dir():
    """memory 層コマンド (check/lineage/suggest/backlinks/origin/stale) の入口ガード。
    memory dir が存在しなければ案内して exit 2 する。transcript 層 (list/search/show) は
    memory_dir() に一切触れないので、この関数は呼ばない。"""
    mdir = memory_dir()
    if not mdir.exists():
        sys.stderr.write("memory 層は未設定。使うには memory/ の規約 (README 参照) が必要\n")
        sys.exit(2)
    return mdir


def iter_memory_files():
    """memory/*.md を MEMORY.md を除いて yield する。"""
    mdir = memory_dir()
    if not mdir.exists():
        return
    for p in sorted(mdir.glob("*.md")):
        if p.name != MEMORY_INDEX_NAME:
            yield p


_FM_BLOCK_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
_FM_NAME_RE = re.compile(r"^name:\s*(.*)$", re.MULTILINE)
_FM_DESC_RE = re.compile(r"^description:\s*(.*)$", re.MULTILINE)
_FM_ORIGIN_RE = re.compile(r"^\s*originSessionId:\s*(\S+)", re.MULTILINE)
_FM_MODIFIED_RE = re.compile(r"^\s*modified:\s*(\S+)", re.MULTILINE)


def parse_frontmatter(text):
    """memory/*.md 専用の軽量パーサ (汎用 YAML ではない、この形式の 4 フィールドだけ拾う)。"""
    m = _FM_BLOCK_RE.match(text)
    if not m:
        return {}
    block = m.group(1)
    fm = {}
    nm = _FM_NAME_RE.search(block)
    if nm:
        fm["name"] = nm.group(1).strip()
    dm = _FM_DESC_RE.search(block)
    if dm:
        fm["description"] = dm.group(1).strip()
    om = _FM_ORIGIN_RE.search(block)
    if om:
        fm["originSessionId"] = om.group(1).strip()
    mm = _FM_MODIFIED_RE.search(block)
    if mm:
        fm["modified"] = mm.group(1).strip()
    return fm


def find_memory(prefix):
    """ファイル名 or frontmatter name の前方一致 (大小無視) で候補を集める。"""
    prefix_low = prefix.lower()
    matches = []
    for p in iter_memory_files():
        if p.stem.lower().startswith(prefix_low):
            matches.append(p)
            continue
        fm = parse_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        if fm.get("name", "").lower().startswith(prefix_low):
            matches.append(p)
    return matches


def cmd_origin(args):
    require_memory_dir()
    matches = find_memory(args.prefix)
    if len(matches) == 0:
        sys.stderr.write(f"memory が見つからない: {args.prefix}\n")
        sys.exit(1)
    if len(matches) > 1:
        sys.stderr.write("複数の memory にマッチ:\n")
        for m in matches:
            sys.stderr.write(f"  {m.stem}\n")
        sys.exit(1)

    mpath = matches[0]
    fm = parse_frontmatter(mpath.read_text(encoding="utf-8", errors="replace"))
    origin_id = fm.get("originSessionId")
    if not origin_id:
        sys.stderr.write(f"{mpath.stem}: originSessionId が無い (frontmatter を確認)\n")
        sys.exit(1)

    session_path = None
    for sp in iter_sessions(None):
        if sp.stem == origin_id:
            session_path = sp
            break
    if session_path is None:
        sys.stderr.write(
            f"{mpath.stem}: originSessionId={origin_id} に対応する transcript が見つからない "
            "(プロジェクトが移動/削除された可能性)\n")
        sys.exit(1)

    start_ts, _ = get_start_info(session_path)
    end_ts = get_end_ts(session_path)
    print(f"=== {mpath.stem} ===")
    if fm.get("description"):
        print(fm["description"])
    print(f"session: {origin_id[:8]} ({session_path.parent.name})  "
          f"{fmt_ts(start_ts)} 〜 {fmt_ts(end_ts)}")
    print()

    if args.grep:
        render_session(session_path, grep=args.grep)
    else:
        render_session(session_path, tail=args.tail or 30)


# ---------- check (memory グラフの整合番人) ----------

_LINK_RE = re.compile(r"\[\[([^\]|]+)\]\]")
_INDEX_LINE_RE = re.compile(r"^-\s*\[[^\]]*\]\(([^)]+\.md)\)", re.MULTILINE)


def _strip_md(name):
    return name[:-3] if name.endswith(".md") else name


def parse_links(text):
    """本文中の [[名前]] wikilink の集合 (.md 拡張子は剥がす、重複は 1 本のエッジに畳む)。"""
    return {_strip_md(m.group(1).strip()) for m in _LINK_RE.finditer(text)}


# ---------- 系譜 (lineage) の解析 ----------

_LINEAGE_LINE_RE = re.compile(r"^系譜:\s*(.+)$", re.MULTILINE)
# ★ "→" で split しない ── ラベル (...) の中に "→" が出てくる実例がある
#   (例: 明文化「探す→取り込む→無ければ自作」)。ノード形そのものを直接 finditer で拾う。
_NODE_TOKEN_RE = re.compile(
    r"\[\[(?P<mem_name>[^\]]+)\]\](?:\s*\((?P<mem_label>[^)]*)\))?"
    r"|"
    r"(?P<a_prefix>[0-9a-fA-F]{4,})@(?P<a_date>\d{4}-\d{2}-\d{2})\s+(?P<a_time>\d{2}:\d{2})"
    r"(?:\s*\((?P<a_label>[^)]*)\))?")


def parse_lineage_line(line):
    """1 本の系譜行をノード dict の list へ (矢印では split せず、ノード形を直接拾う)。
    memory ノード: {type:'memory', name, label}
    アンカーノード: {type:'anchor', prefix, date, time, label}"""
    nodes = []
    for m in _NODE_TOKEN_RE.finditer(line):
        if m.group("mem_name"):
            nodes.append({"type": "memory", "name": m.group("mem_name"),
                          "label": m.group("mem_label"), "raw": m.group(0)})
        else:
            nodes.append({"type": "anchor", "prefix": m.group("a_prefix"),
                          "date": m.group("a_date"), "time": m.group("a_time"),
                          "label": m.group("a_label"), "raw": m.group(0)})
    return nodes


def iter_lineage_chains():
    """全 memory ファイルの系譜行を (source_stem, nodes) で yield する。"""
    for p in iter_memory_files():
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in _LINEAGE_LINE_RE.finditer(text):
            nodes = parse_lineage_line(m.group(1))
            if nodes:
                yield p.stem, nodes


# MEMORY.md 側の "## 系譜" セクション: 1 行 = "- <テーマ> → <記憶名> — <一言>"。
# <一言> 側にも "→" が出てくる実例があるので、<記憶名> は「単語文字のみ + 直後の空白+—」で
# 固定して非貪欲マッチさせる (系譜: 行の parser と同じ理由の対策)。
_LINEAGE_SECTION_START_RE = re.compile(r"^## 系譜\b.*$", re.MULTILINE)
_LINEAGE_INDEX_LINE_RE = re.compile(r"^-\s.*?→\s*([A-Za-z0-9_]+)\s+—", re.MULTILINE)


def extract_lineage_index_section(index_text):
    """MEMORY.md 本文から "## 系譜" セクションの中身だけ切り出す。無ければ空文字列。"""
    m = _LINEAGE_SECTION_START_RE.search(index_text)
    if not m:
        return ""
    start = m.end()
    next_m = re.search(r"^##\s", index_text[start:], re.MULTILINE)
    end = start + next_m.start() if next_m else len(index_text)
    return index_text[start:end]


def count_lineage_index_lines(index_text):
    """"## 系譜" セクションの行を記憶名ごとに数える (Counter)。"""
    section = extract_lineage_index_section(index_text)
    counts = Counter()
    for m in _LINEAGE_INDEX_LINE_RE.finditer(section):
        counts[m.group(1)] += 1
    return counts


# ---------- 系譜アンカー実在検証 (check 専用) ----------
# 実例 (2026-08-13): セッションが fork (文脈切れ継続/compact) すると transcript ファイルが
# 変わるのに、会話文脈が覚えている session ID は古いままなので、古い前綴りでアンカーを書いて
# しまう事故が起きた (7 本のアンカーが旧 prefix のまま、実体は別ファイルにしかなかった)。
# iter_lineage_chains の "系譜:" 行限定パーサとは独立に、memory ディレクトリの全 *.md を
# 生テキストのまま正規表現で走査する (アンカーが 系譜: 行の外に出ても拾える形にしておく)。
_ANCHOR_RE = re.compile(r"([0-9a-f]{8})@(\d{4}-\d{2}-\d{2} \d{2}:\d{2})")


def iter_memory_anchors():
    """memory ディレクトリの全 *.md から系譜アンカー <8桁hex>@<日時> を yield する
    (src_stem, prefix, ts_str)。"""
    mdir = memory_dir()
    if not mdir.exists():
        return
    for p in sorted(mdir.glob("*.md")):
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in _ANCHOR_RE.finditer(text):
            yield p.stem, m.group(1), m.group(2)


def anchor_cache_path():
    """アンカー timestamp キャッシュ (jsonl ごとの分単位 timestamp 集合) の置き場所。
    memory dir の外に置く (memory/ に置くとグラフ番人や git スナップショットを汚す)。
    既定は PROJECTS_DIR 直下 -- --projects-dir / RECALL_PROJECTS_DIR の差し替えにそのまま
    追従する。環境変数 RECALL_ANCHOR_CACHE で明示的に上書き可能。"""
    env_value = os.environ.get("RECALL_ANCHOR_CACHE")
    if env_value:
        return Path(env_value)
    return PROJECTS_DIR / ".recall-anchor-cache.json"


def _load_anchor_cache():
    path = anchor_cache_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, OSError):
        return {}


def _save_anchor_cache(cache):
    try:
        anchor_cache_path().write_text(
            json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # キャッシュ書き込み失敗は check の判定結果には影響させない


_TIMESTAMP_KEY = b'"timestamp":"'


def extract_minute_timestamps(path):
    """1 jsonl ファイルの全行を生 bytes プリフィルタで走査し、`"timestamp":"..."` を拾って
    分単位 ("YYYY-MM-DD HH:MM") の集合にして返す (json.loads しない -- search と同じ思想)。"""
    minutes = set()
    with open(path, "rb") as f:
        for line in f:
            idx = line.find(_TIMESTAMP_KEY)
            if idx == -1:
                continue
            start = idx + len(_TIMESTAMP_KEY)
            end = line.find(b'"', start)
            if end == -1:
                continue
            raw = line[start:end]
            if len(raw) < 16 or raw[10:11] != b"T":
                continue
            minutes.add((raw[:10] + b" " + raw[11:16]).decode("ascii", errors="replace"))
    return minutes


def get_cached_minutes(path, cache):
    """(path, size, mtime) をキーに分単位 timestamp 集合をキャッシュから引く。無い/古ければ
    ファイルを再走査して cache (in-memory dict、呼び出し元が最後に永続化する) に書き戻す。"""
    st = path.stat()
    key = str(path.resolve())
    entry = cache.get(key)
    if entry and entry.get("size") == st.st_size and entry.get("mtime") == st.st_mtime:
        return set(entry["minutes"])
    minutes = extract_minute_timestamps(path)
    cache[key] = {"size": st.st_size, "mtime": st.st_mtime, "minutes": sorted(minutes)}
    return minutes


def _find_anchor_hint(sessions, candidates, exclude, cache):
    """stale anchor の救済ヒント: candidates (分単位 timestamp 集合) と交わる別セッションを
    1 つ探し "<前綴り8桁>@<一致時刻>" を返す (無ければ None、"可能なら" の best-effort)。"""
    for sp in sessions:
        if sp in exclude:
            continue
        hit = get_cached_minutes(sp, cache) & candidates
        if hit:
            return f"{sp.stem[:8]}@{sorted(hit)[0]}"
    return None


def check_anchor_staleness():
    """系譜アンカー <8桁prefix>@<日時> の実在検証。
    戻り値: (missing, stale)
      missing: list[(src, anchor_str)]         -- 前綴りに一致する .jsonl が無い (WARN 相当)
      stale:   list[(src, anchor_str, hint)]    -- ファイルはあるが ±3分以内の timestamp が
               1 つも無い (ERROR 相当、fork でファイルが変わった可能性)。hint は None のことも
               ある。"""
    sessions = list(iter_sessions(None))
    anchors = list(iter_memory_anchors())
    if not sessions and anchors:
        # セッションが 1 件も見つからないと、以下のループは anchors 全件を無条件で
        # missing (WARN dead link) に落とす -- 実体が本当に消えたのではなく、
        # --projects-dir の指す場所を親子で取り違えただけの可能性が高い。番人が
        # 「記憶が腐っている」と誤報するのを防ぐため、原因の見分け方を先に警告しておく。
        sys.stderr.write(f"WARN セッションが 1 件も見つからない (探索場所: {PROJECTS_DIR})。"
                          f"以下の系譜アンカー {len(anchors)} 件はこのせいで全て dead 判定に"
                          f"なっている可能性がある\n")
    cache = _load_anchor_cache()
    missing, stale = [], []
    for src, prefix, ts_str in anchors:
        anchor_str = f"{prefix}@{ts_str}"
        prefix_low = prefix.lower()
        matches = [sp for sp in sessions if sp.stem.lower().startswith(prefix_low)]
        if not matches:
            missing.append((src, anchor_str))
            continue
        anchor_dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M")
        candidates = {(anchor_dt + timedelta(minutes=d)).strftime("%Y-%m-%d %H:%M")
                      for d in range(-3, 4)}
        if any(get_cached_minutes(sp, cache) & candidates for sp in matches):
            continue
        hint = _find_anchor_hint(sessions, candidates, set(matches), cache)
        stale.append((src, anchor_str, hint))
    _save_anchor_cache(cache)
    return missing, stale


def cmd_check(args):
    mdir = require_memory_dir()

    node_files = list(iter_memory_files())
    node_stems = {p.stem for p in node_files}

    outbound = {}
    for p in node_files:
        text = p.read_text(encoding="utf-8", errors="replace")
        outbound[p.stem] = parse_links(text)

    inbound = {stem: set() for stem in node_stems}
    dead_links = []
    edge_count = 0
    for src, targets in outbound.items():
        for tgt in targets:
            edge_count += 1
            if tgt in node_stems:
                inbound[tgt].add(src)
            else:
                dead_links.append((src, tgt, "memory"))

    orphans = sorted(s for s in node_stems if not inbound.get(s) and not outbound.get(s))

    # 系譜アンカー <prefix>@<ts> の実在検証。前綴りに一致する .jsonl が無ければ dead link 扱い
    # (WARN、アーカイブ済みの可能性)。ファイルはあるが ±3分以内の timestamp が無ければ
    # stale anchor (ERROR、fork でファイルが変わった可能性)。
    missing_anchors, stale_anchors = check_anchor_staleness()
    for src, anchor_str in missing_anchors:
        dead_links.append((src, anchor_str, "anchor"))

    index_path = mdir / MEMORY_INDEX_NAME
    index_text = index_path.read_text(encoding="utf-8", errors="replace") \
        if index_path.exists() else ""
    index_stems = {_strip_md(m.group(1)) for m in _INDEX_LINE_RE.finditer(index_text)}

    missing_from_index = sorted(node_stems - index_stems)   # ERROR
    ghost_in_index = sorted(index_stems - node_stems)       # ERROR

    # 系譜索引 (MEMORY.md の "## 系譜" セクション) と 実体 (各ファイルの 系譜: 行) の同期検査
    lineage_entity_counts = Counter(src for src, _ in iter_lineage_chains())
    lineage_index_counts = count_lineage_index_lines(index_text)
    n_lineage = sum(lineage_entity_counts.values())

    lineage_missing_from_index = sorted(          # ERROR: 実体はあるが索引に無い
        set(lineage_entity_counts) - set(lineage_index_counts))
    lineage_ghost_in_index = sorted(               # ERROR: 索引はあるが実体 (系譜: 行) が無い
        set(lineage_index_counts) - set(lineage_entity_counts))
    lineage_count_mismatch = sorted(               # ERROR: 両方あるが本数が違う
        stem for stem in set(lineage_entity_counts) & set(lineage_index_counts)
        if lineage_entity_counts[stem] != lineage_index_counts[stem])

    n_dead, n_orphan = len(dead_links), len(orphans)
    n_missing, n_ghost = len(missing_from_index), len(ghost_in_index)
    n_lin_missing = len(lineage_missing_from_index)
    n_lin_ghost = len(lineage_ghost_in_index)
    n_lin_mismatch = len(lineage_count_mismatch)
    n_stale = len(stale_anchors)
    total_issues = (n_dead + n_orphan + n_missing + n_ghost
                     + n_lin_missing + n_lin_ghost + n_lin_mismatch + n_stale)
    has_error = (n_missing + n_ghost + n_lin_missing + n_lin_ghost
                 + n_lin_mismatch + n_stale) > 0

    if args.quiet and total_issues == 0:
        return  # 問題ゼロ: 何も出力しない (exit コードは呼び出し元の既定 0 のまま)

    if dead_links:
        print(f"WARN dead link ({n_dead} 件):")
        for src, tgt, kind in sorted(dead_links, key=lambda x: (x[0], x[1])):
            if kind == "anchor":
                print(f"  {tgt} in {src}.md -- セッションが存在しない")
            else:
                print(f"  [[{tgt}]] in {src}.md -- 存在しない (skill 名等への意図的先行リンクの"
                      f"可能性あり)")
    if orphans:
        print(f"WARN orphan ({n_orphan} 件):")
        for stem in orphans:
            print(f"  {stem}.md -- リンク無し(孤立)")
    n_index_lines = len(index_text.splitlines())
    if n_index_lines > 250:
        print(f"WARN 索引が {n_index_lines} 行 (階層化トリガー 300 行に接近)")
    if missing_from_index:
        print(f"ERROR index 欠落 ({n_missing} 件):")
        for stem in missing_from_index:
            print(f"  {stem}.md -- MEMORY.md に行が無い")
    if ghost_in_index:
        print(f"ERROR index 幽霊 ({n_ghost} 件):")
        for stem in ghost_in_index:
            print(f"  {stem}.md -- MEMORY.md にあるがファイルが無い")
    if lineage_missing_from_index:
        print(f"ERROR 系譜索引 欠落 ({n_lin_missing} 件):")
        for stem in lineage_missing_from_index:
            n = lineage_entity_counts[stem]
            print(f"  {stem}.md -- 系譜 {n} 本あるが MEMORY.md の ## 系譜 に無い")
    if lineage_ghost_in_index:
        print(f"ERROR 系譜索引 幽霊 ({n_lin_ghost} 件):")
        for stem in lineage_ghost_in_index:
            n = lineage_index_counts[stem]
            print(f"  {stem}.md -- ## 系譜 に {n} 行あるが本体に系譜行が無い")
    if lineage_count_mismatch:
        print(f"ERROR 系譜索引 本数不一致 ({n_lin_mismatch} 件):")
        for stem in lineage_count_mismatch:
            e, idx = lineage_entity_counts[stem], lineage_index_counts[stem]
            print(f"  {stem}.md -- 実体 {e} 本 / 索引 {idx} 行")
    if stale_anchors:
        print(f"ERROR 系譜アンカー 陳腐化 ({n_stale} 件):")
        for src, anchor_str, hint in sorted(stale_anchors, key=lambda x: (x[0], x[1])):
            msg = (f"  {anchor_str} in {src}.md -- ±3分以内の timestamp が無い "
                   f"(fork でファイルが変わった可能性。他セッションを検索して直せ)")
            if hint:
                msg += f"  候補: {hint}"
            print(msg)

    print(f"graph: nodes={len(node_stems)} edges={edge_count} dead={n_dead} "
          f"orphan={n_orphan} missing={n_missing} ghost={n_ghost} lineage={n_lineage} "
          f"stale={n_stale}")

    if has_error:
        sys.exit(1)


# ---------- stale (30日棚卸しの候補列挙) ----------

def cmd_stale(args):
    """全 memory ノードを「土台なのに古い」順にランキングする。
    score = 被リンク数(in-degree) × 経過日数。modified が無い/パース不能なノードは除外し
    stderr に WARN を出す (check と違い判定ツールではないので exit code は変えない)。"""
    require_memory_dir()
    node_files = list(iter_memory_files())
    node_stems = {p.stem for p in node_files}

    inbound = {stem: set() for stem in node_stems}
    for p in node_files:
        text = p.read_text(encoding="utf-8", errors="replace")
        for tgt in parse_links(text):
            if tgt in node_stems:
                inbound[tgt].add(p.stem)

    now = datetime.now()
    ranked = []
    for p in node_files:
        text = p.read_text(encoding="utf-8", errors="replace")
        fm = parse_frontmatter(text)
        modified = fm.get("modified")
        if not modified:
            sys.stderr.write(f"WARN {p.stem}.md: modified が無い (除外)\n")
            continue
        try:
            mod_dt = datetime.strptime(modified[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            sys.stderr.write(f"WARN {p.stem}.md: modified が無い (除外)\n")
            continue
        days = (now - mod_dt).days
        n_in = len(inbound.get(p.stem, set()))
        score = n_in * days
        desc = fm.get("description", "")[:50]
        ranked.append((score, p.stem, n_in, mod_dt, days, desc))

    if not ranked:
        print("対象ノードなし")
        return

    ranked.sort(key=lambda r: r[0], reverse=True)
    for score, stem, n_in, mod_dt, days, desc in ranked[:args.top]:
        print(f"{stem} | 被リンク {n_in} | 最終更新 {mod_dt.strftime('%Y-%m-%d')} "
              f"({days}日前) | {desc}")


# ---------- backlinks (除染用の逆リンク一覧) ----------

def cmd_backlinks(args):
    """node 名を wikilink [[node名]] (エイリアス含む) で参照している全 memory ファイルを
    列挙する。既存の _LINK_RE / _strip_md を再利用する。"""
    require_memory_dir()
    target = _strip_md(args.node)
    hits = []
    for p in iter_memory_files():
        text = p.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            for m in _LINK_RE.finditer(line):
                if _strip_md(m.group(1).strip()) != target:
                    continue
                snippet = line[max(0, m.start() - 80):min(len(line), m.end() + 80)].strip()
                mark = " (系譜)" if line.strip().startswith("系譜:") else ""
                hits.append(f"{p.stem}.md: {snippet}{mark}")

    if not hits:
        print("被リンクなし")
        return
    for h in hits:
        print(h)


# ---------- lineage ----------

def chain_matches_query(nodes, query_low):
    for n in nodes:
        if n["type"] == "memory" and n["name"].lower().startswith(query_low):
            return True
        if n["type"] == "anchor" and n["prefix"].lower().startswith(query_low):
            return True
    return False


def node_display_time(node):
    """memory ノードは対象ファイルの frontmatter modified、アンカーは行に書かれた値。"""
    if node["type"] == "anchor":
        return f"{node['date']} {node['time']}"
    if node["type"] == "memory":
        target = memory_dir() / f"{node['name']}.md"
        if target.exists():
            fm = parse_frontmatter(target.read_text(encoding="utf-8", errors="replace"))
            modified = fm.get("modified")
            if modified:
                return fmt_ts(modified)
        return "?"
    return "?"


def ts_within_window(ts_str, anchor_dt, window_minutes):
    """timestamp 文字列 (ISO8601, 先頭16文字が "YYYY-MM-DDTHH:MM") が anchor_dt から
    ±window_minutes 分以内かを返す。パース不能なら False。
    lineage --deep (find_anchor_quotes) と show --at の共通判定ロジック。"""
    if not isinstance(ts_str, str) or len(ts_str) < 16:
        return False
    try:
        rec_dt = datetime.strptime(ts_str[:16], "%Y-%m-%dT%H:%M")
    except ValueError:
        return False
    return abs(rec_dt - anchor_dt) <= timedelta(minutes=window_minutes)


def find_anchor_quotes(node, window_minutes=2, limit=3):
    """アンカー ±window 分以内の text ブロックを (role, snippet) で最大 limit 件返す。
    近い時刻順。該当セッションが見つからない/パースできない場合は空 list。"""
    sess = resolve_session(node["prefix"])
    if sess is None:
        return []
    try:
        anchor_dt = datetime.strptime(f"{node['date']} {node['time']}", "%Y-%m-%d %H:%M")
    except ValueError:
        return []
    hits = []
    with open(sess, "rb") as f:
        for line in f:
            rec = safe_json_loads(line)
            if rec is None:
                continue
            ts = rec.get("timestamp")
            if not ts_within_window(ts, anchor_dt, window_minutes):
                continue
            rec_dt = datetime.strptime(ts[:16], "%Y-%m-%dT%H:%M")
            for role, text in iter_text_blocks(rec):
                snippet = " ".join(text.split())
                hits.append((abs(rec_dt - anchor_dt), role, snippet))
    hits.sort(key=lambda h: h[0])
    return [(role, snippet) for _, role, snippet in hits[:limit]]


def render_lineage_chain(nodes, deep=False):
    for n in nodes:
        ts = node_display_time(n)
        label = f" ({n['label']})" if n.get("label") else ""
        if n["type"] == "memory":
            exists = (memory_dir() / f"{n['name']}.md").exists()
            name_disp = f"[[{n['name']}]]" + ("" if exists else " (未作成)")
        elif n["type"] == "anchor":
            name_disp = f"{n['prefix']}@{n['date']} {n['time']}"
        else:
            name_disp = n["raw"]
        print(f"  [{ts}] {name_disp}{label}")
        if deep and n["type"] == "anchor":
            quotes = find_anchor_quotes(n)
            if not quotes:
                print("      (該当時刻の発言が見つからない)")
            else:
                for role, snippet in quotes:
                    print(f"      > {role}: {snippet}")


def cmd_lineage(args):
    require_memory_dir()
    query_low = args.query.lower()
    chains = [(src, nodes) for src, nodes in iter_lineage_chains()
              if chain_matches_query(nodes, query_low)]
    if not chains:
        print("系譜なし")
        sys.exit(1)
    for i, (src, nodes) in enumerate(chains):
        if i > 0:
            print("---")
        print(f"[{src}.md]")
        render_lineage_chain(nodes, deep=args.deep)


# ---------- suggest (候補提案、読み取り専用) ----------

_ASCII_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]{2,}")
_KATAKANA_RUN_RE = re.compile(r"[゠-ヿー]{3,}")
_KANJI_RUN_RE = re.compile(r"[一-鿿]{2,}")


def strip_frontmatter_body(text):
    m = _FM_BLOCK_RE.match(text)
    return text[m.end():] if m else text


def extract_tokens(body_text):
    """ASCII 語 (3 文字以上、小文字化) ・カタカナ 3+ 連 (そのまま) ・漢字 2+ 連 (重複 2-gram
    化) を token 化する。ちゃんとした形態素解析ではない ── 表面類似の候補出しが目的。"""
    tokens = []
    for m in _ASCII_TOKEN_RE.finditer(body_text):
        tokens.append(m.group(0).lower())
    for m in _KATAKANA_RUN_RE.finditer(body_text):
        tokens.append(m.group(0))
    for m in _KANJI_RUN_RE.finditer(body_text):
        run = m.group(0)
        tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


def build_tfidf_vectors(node_files):
    """stem -> {term: tfidf} の dict。df が全ノード数の 25% を超える頻出語は語彙から除外。
    tf = 1 + log(生カウント)、idf = log(N / df)。"""
    doc_terms = {p.stem: Counter(extract_tokens(strip_frontmatter_body(
        p.read_text(encoding="utf-8", errors="replace")))) for p in node_files}
    n_docs = len(doc_terms)
    if n_docs == 0:
        return {}
    df = Counter()
    for terms in doc_terms.values():
        for term in terms:
            df[term] += 1
    threshold = 0.25 * n_docs
    vocab = {t for t, d in df.items() if d <= threshold}
    vectors = {}
    for stem, terms in doc_terms.items():
        vec = {}
        for term, n in terms.items():
            if term not in vocab:
                continue
            tf = 1.0 + math.log(n)
            idf = math.log(n_docs / df[term])
            vec[term] = tf * idf
        vectors[stem] = vec
    return vectors


def cosine_sim_with_terms(vec_a, vec_b):
    """cosine 類似度と、寄与 (積) 降順の共通語 list を返す (寄与上位語の表示に使う)。"""
    common = set(vec_a) & set(vec_b)
    if not common:
        return 0.0, []
    norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
    norm_b = math.sqrt(sum(v * v for v in vec_b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0, []
    dot = sum(vec_a[t] * vec_b[t] for t in common)
    sim = dot / (norm_a * norm_b)
    contrib = sorted(common, key=lambda t: vec_a[t] * vec_b[t], reverse=True)
    return sim, contrib


def build_undirected_graph(node_files):
    """[[link]] を無向グラフへ (stem -> set(neighbor_stem))。自己リンクは除外する ──
    系譜行の最終ノードが自分自身を指す実例があり (例: feedback_search_absorb_build_order)、
    弾かないと「自分の隣人」が紛れ込んで三角閉包の判定が壊れる。"""
    neighbors = {p.stem: set() for p in node_files}
    for p in node_files:
        text = p.read_text(encoding="utf-8", errors="replace")
        for tgt in parse_links(text):
            if tgt == p.stem:
                continue
            if tgt in neighbors:
                neighbors[p.stem].add(tgt)
                neighbors[tgt].add(p.stem)
    return neighbors


def suggest_lexical(node_files, graph, max_n):
    """A. 語彙類似: 未接続対を cosine 類似度降順で返す [(sim, a, b, top4語), ...]。"""
    vectors = build_tfidf_vectors(node_files)
    stems = sorted(vectors.keys())
    results = []
    for a, b in itertools.combinations(stems, 2):
        if b in graph.get(a, set()):
            continue  # 既にどちらか向きでもリンク済み
        sim, contrib = cosine_sim_with_terms(vectors[a], vectors[b])
        if sim <= 0:
            continue
        results.append((sim, a, b, contrib[:4]))
    results.sort(key=lambda r: r[0], reverse=True)
    return results[:max_n]


def suggest_triangles(graph, max_n):
    """B. 三角閉包: 共通隣人 3+ かつ未接続の対を隣人数降順で返す [(n, a, b, 隣人4件), ...]。"""
    stems = sorted(graph.keys())
    results = []
    for a, b in itertools.combinations(stems, 2):
        if b in graph[a]:
            continue  # 既に直接リンクされている対は候補にしない
        common = graph[a] & graph[b]
        if len(common) < 3:
            continue
        results.append((len(common), a, b, sorted(common)[:4]))
    results.sort(key=lambda r: r[0], reverse=True)
    return results[:max_n]


def cmd_suggest(args):
    require_memory_dir()
    node_files = list(iter_memory_files())
    graph = build_undirected_graph(node_files)

    lexical = suggest_lexical(node_files, graph, args.max)
    print("=== A. 語彙類似 (未接続対) ===")
    if not lexical:
        print("候補なし")
    for sim, a, b, contrib in lexical:
        terms = "・".join(contrib) if contrib else "?"
        print(f"  {sim:.3f}  {a} <-> {b}  [{terms}]")

    print()
    triangles = suggest_triangles(graph, args.max)
    print("=== B. 三角閉包 (共通隣人 3+ の未接続対) ===")
    if not triangles:
        print("候補なし")
    for n_common, a, b, common in triangles:
        neigh = "・".join(common) if common else "?"
        print(f"  隣人{n_common}  {a} <-> {b}  [{neigh}]")

    print()
    print("候補は表面類似を含む。繋ぐ判断は本文を読んでから"
          "(ハブ肥大・偶然の語彙一致に注意)")


# ---------- main ----------

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(
        description="Claude Code セッション transcript の索引・検索・部分読み出し")
    ap.add_argument("--projects-dir", default=None,
                     help="PROJECTS_DIR の上書き (既定: ~/.claude/projects、"
                          "環境変数 RECALL_PROJECTS_DIR も可)")
    ap.add_argument("--memory-dir", default=None,
                     help="memory dir の上書き (既定: PROJECTS_DIR 直下で memory/ を持つ"
                          "プロジェクトがちょうど1つのときのその memory/ を自動採用、"
                          "環境変数 RECALL_MEMORY_DIR も可)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="セッション一覧")
    p_list.add_argument("--project", help="プロジェクト名の部分一致")
    p_list.set_defaults(func=cmd_list)

    p_search = sub.add_parser("search", help="全 transcript のテキストを検索")
    p_search.add_argument("query")
    p_search.add_argument("--project", help="プロジェクト名の部分一致")
    p_search.add_argument("--days", type=int, default=None, help="直近 N 日のセッションのみ")
    p_search.add_argument("--max", type=int, default=20, help="出力件数の上限 (既定 20)")
    p_search.add_argument("-i", action="store_true",
                           help="大小無視で検索する (既定で大小無視のため実質 no-op)")
    p_search.set_defaults(func=cmd_search)

    p_show = sub.add_parser("show", help="1 セッションの会話を時系列で表示")
    p_show.add_argument("session_prefix", help="session-id の先頭 (8 桁など)")
    p_show.add_argument("--grep", help="マッチ前後 3 メッセージだけ表示")
    g = p_show.add_mutually_exclusive_group()
    g.add_argument("--head", type=int, default=None, help="先頭 N メッセージだけ表示")
    g.add_argument("--tail", type=int, default=None, help="末尾 N メッセージだけ表示")
    g.add_argument("--at", default=None,
                    help='指定時刻 (\"YYYY-MM-DD HH:MM\") ± window 分だけ表示。'
                         "--grep/--head/--tail と併用不可")
    p_show.add_argument("--window", type=int, default=2, help="--at の前後何分か (既定 2)")
    p_show.set_defaults(func=cmd_show)

    p_origin = sub.add_parser("origin", help="memory ノードから元セッションへ跳躍する")
    p_origin.add_argument("prefix", help="memory ファイル名 or frontmatter name の前方一致")
    og = p_origin.add_mutually_exclusive_group()
    og.add_argument("--grep", help="マッチ前後 3 メッセージだけ表示")
    og.add_argument("--tail", type=int, default=None, help="末尾 N メッセージ (既定 30)")
    p_origin.set_defaults(func=cmd_origin)

    p_check = sub.add_parser("check", help="memory グラフ (MEMORY.md ⇔ memory/*.md) の整合検査")
    p_check.add_argument("--quiet", action="store_true",
                          help="問題ゼロなら何も出力しない (SessionStart 番人向け)")
    p_check.set_defaults(func=cmd_check)

    p_lineage = sub.add_parser("lineage", help="系譜 (方向付き時系列エッジ) の鎖を表示")
    p_lineage.add_argument("query", help="memory 名 or session prefix の前方一致")
    p_lineage.add_argument("--deep", action="store_true",
                            help="アンカー ±2 分の発言を transcript から引用する")
    p_lineage.set_defaults(func=cmd_lineage)

    p_suggest = sub.add_parser(
        "suggest", help="記憶グラフの隠れた関係の候補提案 (読み取り専用、書き込まない)")
    p_suggest.add_argument("--max", type=int, default=10, help="各走査の出力件数上限 (既定 10)")
    p_suggest.set_defaults(func=cmd_suggest)

    p_stale = sub.add_parser("stale", help="30日棚卸し候補: 被リンク数×経過日数でランキング")
    p_stale.add_argument("--top", type=int, default=10, help="表示件数の上限 (既定 10)")
    p_stale.set_defaults(func=cmd_stale)

    p_backlinks = sub.add_parser("backlinks", help="node を wikilink で参照している全ファイル")
    p_backlinks.add_argument("node", help="対象ノード名 (.md 拡張子は無くても可)")
    p_backlinks.set_defaults(func=cmd_backlinks)

    args = ap.parse_args()
    global PROJECTS_DIR, MEMORY_DIR_OVERRIDE
    PROJECTS_DIR = resolve_projects_dir(args.projects_dir)
    MEMORY_DIR_OVERRIDE = resolve_memory_dir(args.memory_dir)
    args.func(args)


if __name__ == "__main__":
    main()
