"""recall.py の pytest テスト。合成 JSONL fixture (正常行/tool ブロック/壊れ行/summary 行)
で list / search / show の主要経路を検証する。実データには触れない。"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

RECALL_PATH = Path(__file__).resolve().parent.parent / "recall.py"
SAMPLE_PROJECTS_DIR = Path(__file__).resolve().parent.parent / "sample" / "projects"
spec = importlib.util.spec_from_file_location("recall", RECALL_PATH)
recall = importlib.util.module_from_spec(spec)
sys.modules["recall"] = recall
spec.loader.exec_module(recall)


def _line(d):
    # 実データはスペース無しの区切り文字で書かれている (","":" )。COMPACT_MARK の
    # 部分文字列一致はこの形に依存するので fixture もそれに合わせる。
    return json.dumps(d, ensure_ascii=False, separators=(",", ":")) + "\n"


def make_session(tmp_path, project, session_id, lines):
    proj_dir = tmp_path / project
    proj_dir.mkdir(parents=True, exist_ok=True)
    fp = proj_dir / f"{session_id}.jsonl"
    fp.write_text("".join(lines), encoding="utf-8")
    return fp


@pytest.fixture
def fake_projects(tmp_path, monkeypatch):
    """1 セッション分の合成 transcript を作り、recall.PROJECTS_DIR をそこへ差し替える。"""
    lines = [
        # 会話に無関係な行 (timestamp なし) -- スキップされるべき
        _line({"type": "mode", "mode": "normal", "sessionId": "s1"}),
        # 最初のユーザ発言
        _line({
            "type": "user", "timestamp": "2026-01-01T00:00:00.000Z",
            "message": {"role": "user", "content": "hello recall world"},
        }),
        # assistant: text + tool_use
        _line({
            "type": "assistant", "timestamp": "2026-01-01T00:00:05.000Z",
            "message": {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "let me think about qa-dashboard"},
                {"type": "text", "text": "answering about qa-dashboard now"},
                {"type": "tool_use", "id": "tid1", "name": "Bash",
                 "input": {"command": "ls qa-dashboard"}},
            ]},
        }),
        # tool_result (出力は検索対象外のはず)
        _line({
            "type": "user", "timestamp": "2026-01-01T00:00:06.000Z",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tid1",
                 "content": "SECRET_TOOL_OUTPUT_TOKEN"},
            ]},
        }),
        # 壊れた行 (尻切れ JSON) -- スキップされてクラッシュしないこと
        '{"type": "user", "message": {\n',
        # 圧縮境界
        _line({
            "type": "system", "subtype": "compact_boundary",
            "timestamp": "2026-01-01T00:00:07.000Z",
            "compactMetadata": {"trigger": "manual", "preTokens": 5000, "postTokens": 500},
        }),
        # 圧縮要約 (isCompactSummary)
        _line({
            "type": "user", "isCompactSummary": True,
            "timestamp": "2026-01-01T00:00:08.000Z",
            "message": {"role": "user",
                        "content": "This session is being continued from a previous one."},
        }),
        # 最後の発言 (末尾 timestamp 検出用)
        _line({
            "type": "assistant", "timestamp": "2026-01-01T00:00:09.000Z",
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": "final word about qa-dashboard"}]},
        }),
    ]
    make_session(tmp_path, "C--fake-project", "abcd1234-0000-0000-0000-000000000000", lines)
    monkeypatch.setattr(recall, "PROJECTS_DIR", tmp_path)
    return tmp_path


# ---------- 単体関数 ----------

def test_safe_json_loads_valid():
    assert recall.safe_json_loads(b'{"a": 1}') == {"a": 1}


def test_safe_json_loads_broken_returns_none():
    assert recall.safe_json_loads(b'{"a": ') is None
    assert recall.safe_json_loads(b"") is None
    assert recall.safe_json_loads(b"not json at all") is None


def test_iter_sessions_project_filter(fake_projects):
    all_sessions = list(recall.iter_sessions(None))
    assert len(all_sessions) == 1
    filtered = list(recall.iter_sessions("fake-project"))
    assert len(filtered) == 1
    none_match = list(recall.iter_sessions("does-not-exist"))
    assert none_match == []


def test_get_start_info(fake_projects):
    path = next(recall.iter_sessions(None))
    start_ts, first_user = recall.get_start_info(path)
    assert start_ts == "2026-01-01T00:00:00.000Z"
    assert first_user == "hello recall world"


def test_get_end_ts(fake_projects):
    path = next(recall.iter_sessions(None))
    end_ts = recall.get_end_ts(path)
    assert end_ts == "2026-01-01T00:00:09.000Z"


def test_count_compact_events(fake_projects):
    path = next(recall.iter_sessions(None))
    assert recall.count_compact_events(path) == 1


def test_iter_text_blocks_excludes_tool_and_thinking(fake_projects):
    path = next(recall.iter_sessions(None))
    roles_texts = []
    with open(path, "rb") as f:
        for line in f:
            rec = recall.safe_json_loads(line)
            if rec is None:
                continue
            roles_texts.extend(recall.iter_text_blocks(rec))
    texts = [t for _, t in roles_texts]
    # tool_result の中身 (機密トークン) は絶対に検索対象に出ない
    assert not any("SECRET_TOOL_OUTPUT_TOKEN" in t for t in texts)
    # thinking も出ない
    assert not any("let me think" in t for t in texts)
    # 会話テキストは出る
    assert any("hello recall world" in t for t in texts)
    assert any("answering about qa-dashboard now" in t for t in texts)
    # 圧縮要約は role="summary"
    summary_roles = [r for r, t in roles_texts if "continued from a previous" in t]
    assert summary_roles == ["summary"]


def test_context_snippet_contains_query():
    text = "x" * 200 + "NEEDLE" + "y" * 200
    snip = recall.context_snippet(text, "NEEDLE", width=20)
    assert "NEEDLE" in snip
    assert len(snip) < len(text)


def test_build_turns_folds_tool_blocks(fake_projects):
    path = next(recall.iter_sessions(None))
    turns = list(recall.build_turns(path))
    tool_use_lines = [t["text"] for t in turns if t["text"].startswith("[tool: ")]
    tool_result_lines = [t["text"] for t in turns if t["text"].startswith("[tool result: ")]
    assert tool_use_lines == ["[tool: Bash]"]
    assert tool_result_lines == ["[tool result: Bash]"]  # tool_use_id 経由で名前解決できている
    compact_lines = [t for t in turns if t["role"] == "compact"]
    assert len(compact_lines) == 1
    assert "5000" in compact_lines[0]["text"] and "500" in compact_lines[0]["text"]


# ---------- コマンド (統合) ----------

def test_cmd_search_finds_and_excludes_tool_output(fake_projects, capsys):
    args = argparse.Namespace(query="qa-dashboard", project=None, days=None, max=20)
    recall.cmd_search(args)
    out = capsys.readouterr().out
    assert "qa-dashboard" in out
    assert "SECRET_TOOL_OUTPUT_TOKEN" not in out
    assert "abcd1234" in out


def test_cmd_search_no_match(fake_projects, capsys):
    args = argparse.Namespace(query="nonexistent_xyz_query", project=None, days=None, max=20)
    recall.cmd_search(args)
    out = capsys.readouterr().out
    assert "マッチなし" in out


def test_cmd_list_shows_session(fake_projects, capsys):
    args = argparse.Namespace(project=None)
    recall.cmd_list(args)
    out = capsys.readouterr().out
    assert "abcd1234" in out
    assert "compact=1" in out
    assert "hello recall world" in out


def test_cmd_show_head(fake_projects, capsys):
    args = argparse.Namespace(session_prefix="abcd1234", grep=None, head=2, tail=None)
    recall.cmd_show(args)
    out = capsys.readouterr().out
    assert "hello recall world" in out


def test_resolve_session_ambiguous(tmp_path, monkeypatch, capsys):
    make_session(tmp_path, "p1", "aaaa1111-0000-0000-0000-000000000000",
                 [_line({"type": "user", "timestamp": "2026-01-01T00:00:00.000Z",
                         "message": {"role": "user", "content": "x"}})])
    make_session(tmp_path, "p2", "aaaa2222-0000-0000-0000-000000000000",
                 [_line({"type": "user", "timestamp": "2026-01-01T00:00:00.000Z",
                         "message": {"role": "user", "content": "y"}})])
    monkeypatch.setattr(recall, "PROJECTS_DIR", tmp_path)
    result = recall.resolve_session("aaaa")
    assert result is None  # 複数マッチは None (曖昧)
    result_unique = recall.resolve_session("aaaa1111")
    assert result_unique is not None


# ---------- origin ----------

def make_memory_file(mdir, stem, name, description, origin_session_id=None, body=""):
    fm_lines = ["---", f"name: {name}", f"description: {description}"]
    if origin_session_id is not None:
        fm_lines += ["metadata: ", "  node_type: memory", "  type: project",
                     f"  originSessionId: {origin_session_id}",
                     "  modified: 2026-01-01T00:00:00.000Z"]
    else:
        fm_lines += ["metadata: ", "  node_type: memory", "  type: project"]
    fm_lines.append("---")
    text = "\n".join(fm_lines) + "\n\n" + body + "\n"
    (mdir / f"{stem}.md").write_text(text, encoding="utf-8")


@pytest.fixture
def fake_memory_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(recall, "PROJECTS_DIR", tmp_path)
    make_session(tmp_path, "C--fake-project", "11112222-0000-0000-0000-000000000000", [
        _line({"type": "user", "timestamp": "2026-02-01T00:00:00.000Z",
               "message": {"role": "user", "content": "origin test hello"}}),
        _line({"type": "assistant", "timestamp": "2026-02-01T00:00:05.000Z",
               "message": {"role": "assistant",
                           "content": [{"type": "text", "text": "origin test reply"}]}}),
    ])
    mdir = tmp_path / "demo-project" / "memory"
    mdir.mkdir(parents=True)
    (mdir / "MEMORY.md").write_text("- [ok](node_ok.md) — テスト用\n", encoding="utf-8")
    make_memory_file(mdir, "node_ok", "node_ok_slug", "node OK の説明",
                      origin_session_id="11112222-0000-0000-0000-000000000000")
    make_memory_file(mdir, "node_no_origin", "node_no_origin_slug", "originSessionId 無し",
                      origin_session_id=None)
    make_memory_file(mdir, "node_missing_session", "node_missing_session_slug",
                      "セッションが実在しない",
                      origin_session_id="99990000-0000-0000-0000-000000000000")
    make_memory_file(mdir, "dup_a", "dup_a_slug", "曖昧一致テスト A")
    make_memory_file(mdir, "dup_b", "dup_b_slug", "曖昧一致テスト B")
    return tmp_path


def test_cmd_origin_success(fake_memory_origin, capsys):
    args = argparse.Namespace(prefix="node_ok", grep=None, tail=None)
    recall.cmd_origin(args)
    out = capsys.readouterr().out
    assert "node OK の説明" in out
    assert "origin test reply" in out


def test_cmd_origin_ambiguous(fake_memory_origin, capsys):
    args = argparse.Namespace(prefix="dup_", grep=None, tail=None)
    with pytest.raises(SystemExit) as exc:
        recall.cmd_origin(args)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "複数の memory にマッチ" in err


def test_cmd_origin_not_found(fake_memory_origin, capsys):
    args = argparse.Namespace(prefix="totally_bogus_prefix_xyz", grep=None, tail=None)
    with pytest.raises(SystemExit) as exc:
        recall.cmd_origin(args)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "memory が見つからない" in err


def test_cmd_origin_missing_origin_id(fake_memory_origin, capsys):
    args = argparse.Namespace(prefix="node_no_origin", grep=None, tail=None)
    with pytest.raises(SystemExit) as exc:
        recall.cmd_origin(args)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "originSessionId が無い" in err


def test_cmd_origin_session_not_found(fake_memory_origin, capsys):
    args = argparse.Namespace(prefix="node_missing_session", grep=None, tail=None)
    with pytest.raises(SystemExit) as exc:
        recall.cmd_origin(args)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "見つからない" in err


# ---------- check ----------

@pytest.fixture
def fake_memory_check(tmp_path, monkeypatch):
    """4 検査それぞれを 1 件ずつ単離して起こす合成グラフ:
    node_a --link--> node_b (正常なエッジ、どちらも orphan にならない)
    node_c: リンク無し / inbound 無し -> orphan (WARN) かつ MEMORY.md に無い -> index 欠落 (ERROR)
    node_dead: 存在しない対象へリンク -> dead link (WARN)
    node_ghost: MEMORY.md には行があるがファイルが無い -> index 幽霊 (ERROR)
    """
    monkeypatch.setattr(recall, "PROJECTS_DIR", tmp_path)
    mdir = tmp_path / "demo-project" / "memory"
    mdir.mkdir(parents=True)
    (mdir / "MEMORY.md").write_text(
        "- [A](node_a.md) — a\n"
        "- [B](node_b.md) — b\n"
        "- [Dead](node_dead.md) — dead link 持ち\n"
        "- [Ghost](node_ghost.md) — 幽霊\n",
        encoding="utf-8")
    make_memory_file(mdir, "node_a", "node_a", "node A", body="本文 [[node_b]] へのリンク。")
    make_memory_file(mdir, "node_b", "node_b", "node B", body="本文だけ、リンク無し。")
    make_memory_file(mdir, "node_c", "node_c", "node C (孤立+index欠落)", body="孤立ノード。")
    make_memory_file(mdir, "node_dead", "node_dead", "dead link 持ち",
                      body="[[does_not_exist_target]] への死んだリンク。")
    return tmp_path


def test_cmd_check_all_four_categories(fake_memory_check, capsys):
    args = argparse.Namespace(quiet=False)
    with pytest.raises(SystemExit) as exc:
        recall.cmd_check(args)
    assert exc.value.code == 1  # ERROR がある (index 欠落 / 幽霊)
    out = capsys.readouterr().out
    assert "[[does_not_exist_target]] in node_dead.md" in out  # dead link
    assert "node_c.md -- リンク無し(孤立)" in out               # orphan
    assert "node_c.md -- MEMORY.md に行が無い" in out            # index 欠落
    assert "node_ghost.md -- MEMORY.md にあるがファイルが無い" in out  # index 幽霊
    assert "graph: nodes=4 edges=2 dead=1 orphan=1 missing=1 ghost=1" in out


@pytest.fixture
def fake_memory_clean(tmp_path, monkeypatch):
    """相互リンクの 2 ノードのみ、index も一致 -- 問題ゼロのグラフ。"""
    monkeypatch.setattr(recall, "PROJECTS_DIR", tmp_path)
    mdir = tmp_path / "demo-project" / "memory"
    mdir.mkdir(parents=True)
    (mdir / "MEMORY.md").write_text(
        "- [X](node_x.md) — x\n- [Y](node_y.md) — y\n", encoding="utf-8")
    make_memory_file(mdir, "node_x", "node_x", "node X", body="[[node_y]] を参照。")
    make_memory_file(mdir, "node_y", "node_y", "node Y", body="[[node_x]] を参照。")
    return tmp_path


def test_cmd_check_quiet_clean_no_output(fake_memory_clean, capsys):
    args = argparse.Namespace(quiet=True)
    recall.cmd_check(args)  # exit しない (問題ゼロ)
    out, err = capsys.readouterr()
    assert out == ""
    assert err == ""


def test_cmd_check_clean_verbose_reports_zero(fake_memory_clean, capsys):
    args = argparse.Namespace(quiet=False)
    recall.cmd_check(args)  # 問題ゼロなので exit しない (デフォルト exit 0)
    out = capsys.readouterr().out
    assert "graph: nodes=2 edges=2 dead=0 orphan=0 missing=0 ghost=0" in out


@pytest.fixture
def fake_memory_warn_only(tmp_path, monkeypatch):
    """dead link だけがある (ERROR 系は無い) グラフ -- exit 0 になるはず。"""
    monkeypatch.setattr(recall, "PROJECTS_DIR", tmp_path)
    mdir = tmp_path / "demo-project" / "memory"
    mdir.mkdir(parents=True)
    (mdir / "MEMORY.md").write_text("- [P](node_p.md) — p\n", encoding="utf-8")
    make_memory_file(mdir, "node_p", "node_p", "dead link のみ持つノード",
                      body="[[nonexistent_skill_name]] への意図的先行リンク。")
    return tmp_path


def test_cmd_check_warn_only_exits_zero(fake_memory_warn_only, capsys):
    args = argparse.Namespace(quiet=False)
    recall.cmd_check(args)  # WARN のみなので exit しない (exit 0)
    out = capsys.readouterr().out
    assert "WARN dead link (1 件)" in out
    assert "ERROR" not in out
    assert "dead=1" in out and "missing=0" in out and "ghost=0" in out


# ---------- lineage ----------

@pytest.fixture
def fake_lineage(tmp_path, monkeypatch):
    """系譜行の合成グラフ。アンカー aabbccdd@2026-03-01 10:00 は 2 本の鎖 (node_alpha /
    node_gamma) から共有される。node_gamma の鎖は [[node_missing]] (未作成) を含む。
    node_deadanchor の鎖は実在しないセッション ffeeddcc へのアンカーを持つ (check 用)。"""
    monkeypatch.setattr(recall, "PROJECTS_DIR", tmp_path)
    make_session(tmp_path, "C--fake-project", "aabbccdd-0000-0000-0000-000000000000", [
        _line({"type": "user", "timestamp": "2026-03-01T09:59:00.000Z",
               "message": {"role": "user", "content": "quote before anchor"}}),
        _line({"type": "assistant", "timestamp": "2026-03-01T10:00:30.000Z",
               "message": {"role": "assistant",
                           "content": [{"type": "text", "text": "quote right at anchor time"}]}}),
        _line({"type": "user", "timestamp": "2026-03-01T10:10:00.000Z",
               "message": {"role": "user", "content": "quote too far after"}}),
    ])
    mdir = tmp_path / "demo-project" / "memory"
    mdir.mkdir(parents=True)
    (mdir / "MEMORY.md").write_text(
        "- [A](node_alpha.md) — a\n- [B](node_beta.md) — b\n"
        "- [G](node_gamma.md) — g\n- [D](node_deadanchor.md) — d\n"
        "- [S](node_solo.md) — s\n\n"
        "## 系譜\n"
        "- テーマ1 → node_alpha — 一言1\n"
        "- テーマ2 → node_gamma — 一言2\n"
        "- テーマ3 → node_deadanchor — 一言3\n",
        encoding="utf-8")
    make_memory_file(mdir, "node_alpha", "node_alpha", "node alpha",
                      body="系譜: aabbccdd@2026-03-01 10:00 (共通アンカー) → [[node_beta]]")
    make_memory_file(mdir, "node_beta", "node_beta", "node beta")
    make_memory_file(mdir, "node_gamma", "node_gamma", "node gamma",
                      body="系譜: [[node_alpha]] → aabbccdd@2026-03-01 10:00 (2本目) "
                           "→ [[node_missing]]")
    make_memory_file(mdir, "node_deadanchor", "node_deadanchor", "dead anchor 持ち",
                      body="系譜: ffeeddcc@2026-05-05 05:05 (存在しないセッション) "
                           "→ [[node_solo]]")
    make_memory_file(mdir, "node_solo", "node_solo", "solo")
    return tmp_path


def test_cmd_lineage_multiple_chains_by_shared_anchor(fake_lineage, capsys):
    args = argparse.Namespace(query="aabbccdd", deep=False)
    recall.cmd_lineage(args)
    out = capsys.readouterr().out
    assert "node_alpha.md" in out
    assert "node_gamma.md" in out
    assert out.count("aabbccdd@2026-03-01 10:00") == 2  # 2 本の鎖どちらにも出る


def test_cmd_lineage_mixed_types_and_uncreated_node(fake_lineage, capsys):
    args = argparse.Namespace(query="node_alpha", deep=False)
    recall.cmd_lineage(args)
    out = capsys.readouterr().out
    # node_gamma の鎖だけがヒットする ([[node_alpha]] を含む鎖)
    assert "[node_gamma.md]" in out
    assert "[[node_alpha]]" in out              # memory ノード
    assert "aabbccdd@2026-03-01 10:00" in out   # アンカーノード (混在)
    assert "[[node_missing]] (未作成)" in out    # 実在しない memory ノード


def test_cmd_lineage_deep_quotes_within_window_excludes_outside(fake_lineage, capsys):
    args = argparse.Namespace(query="aabbccdd", deep=True)
    recall.cmd_lineage(args)
    out = capsys.readouterr().out
    assert "quote before anchor" in out          # -30秒、window 内
    assert "quote right at anchor time" in out   # window 内
    assert "quote too far after" not in out      # +10分、window 外


def test_cmd_lineage_no_match_exits_one(fake_lineage, capsys):
    args = argparse.Namespace(query="totally_bogus_query_xyz", deep=False)
    with pytest.raises(SystemExit) as exc:
        recall.cmd_lineage(args)
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "系譜なし" in out


def test_cmd_check_flags_dead_anchor(fake_lineage, capsys):
    args = argparse.Namespace(quiet=False)
    recall.cmd_check(args)  # dead link (WARN) のみ、ERROR は無い想定 -> exit しない
    out = capsys.readouterr().out
    assert "ffeeddcc@2026-05-05 05:05 in node_deadanchor.md -- セッションが存在しない" in out


# ---------- check: 系譜アンカー実在検証 (2026-08-13 の stale anchor 事故を踏まえた追加) ----------
# 実例: セッションが fork (文脈切れ継続/compact) すると transcript ファイルが変わるのに、
# 記憶されている session ID prefix は古いまま書かれてしまい、7 本のアンカーが旧 prefix を
# 指したまま実体は別ファイルにしかない、という事故が起きた。ここでは
# ①有効アンカー=緑 ②stale アンカー=ERROR ③ファイル無し=WARN ④キャッシュの (size,mtime)
# 無効化 の 4 系統を検証する。

def _setup_anchor_case(tmp_path, monkeypatch, sessions, memory_body):
    """1 ノード (node_anchor) だけを持つ最小グラフ。sessions は [(session_id, lines), ...]。
    アンカーは "系譜:" 行の外 (本文中) に書く -- 全 *.md を生テキストのまま走査する仕様
    (iter_lineage_chains の "系譜:" 行限定パーサとは独立) を確かめるため。"""
    monkeypatch.setattr(recall, "PROJECTS_DIR", tmp_path)
    for session_id, lines in sessions:
        make_session(tmp_path, "C--fake-project", session_id, lines)
    mdir = tmp_path / "demo-project" / "memory"
    mdir.mkdir(parents=True)
    make_memory_file(mdir, "node_anchor", "node_anchor", "アンカーテスト用", body=memory_body)
    (mdir / "MEMORY.md").write_text("- [A](node_anchor.md) — a\n", encoding="utf-8")


def test_check_anchor_valid_within_window(tmp_path, monkeypatch, capsys):
    """アンカー時刻の ±3分以内に実 timestamp があれば緑 (ERROR に出ない)。"""
    _setup_anchor_case(
        tmp_path, monkeypatch,
        [("aaaa1111-0000-0000-0000-000000000000", [
            _line({"type": "user", "timestamp": "2026-04-01T10:02:30.000Z",
                   "message": {"role": "user", "content": "within window"}}),
        ])],
        "本文中のアンカー aaaa1111@2026-04-01 10:00 (窓内、実 timestamp は 10:02)。")
    args = argparse.Namespace(quiet=False)
    recall.cmd_check(args)  # 窓内 -> stale ERROR 無し、exit しない
    out = capsys.readouterr().out
    assert "陳腐化" not in out
    assert "stale=0" in out


def test_check_anchor_stale_flags_error(tmp_path, monkeypatch, capsys):
    """前綴りに一致する .jsonl はあるが、書かれた時刻の ±3分以内に一致する timestamp が
    1 つも無い -> ERROR (stale anchor)。fork でファイルが変わった可能性の文言と、同時刻帯を
    含む別セッション (ヒント) が無いことを確認する。"""
    _setup_anchor_case(
        tmp_path, monkeypatch,
        [("bbbb2222-0000-0000-0000-000000000000", [
            _line({"type": "user", "timestamp": "2026-04-01T09:00:00.000Z",
                   "message": {"role": "user", "content": "far from anchor"}}),
        ])],
        "本文中のアンカー bbbb2222@2026-04-01 10:00 (窓外、実セッションは 09:00 台のみ)。")
    args = argparse.Namespace(quiet=False)
    with pytest.raises(SystemExit) as exc:
        recall.cmd_check(args)
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "ERROR 系譜アンカー 陳腐化 (1 件)" in out
    assert "bbbb2222@2026-04-01 10:00 in node_anchor.md" in out
    assert "fork でファイルが変わった可能性" in out
    assert "stale=1" in out


def test_check_anchor_stale_with_hint(tmp_path, monkeypatch, capsys):
    """stale だが、同時刻帯を含む別セッションが実在する場合はヒントとして 1 行提示する
    (実例の「9697c2d7@ が stale で実体は 9fc3aedc-* にあった」を模した最小再現)。"""
    _setup_anchor_case(
        tmp_path, monkeypatch,
        [("bbbb2222-0000-0000-0000-000000000000", [   # 書かれた前綴り自身は窓外
            _line({"type": "user", "timestamp": "2026-04-01T09:00:00.000Z",
                   "message": {"role": "user", "content": "far from anchor"}}),
        ]), ("dddd4444-0000-0000-0000-000000000000", [   # fork 後の実体 (窓内)
            _line({"type": "user", "timestamp": "2026-04-01T10:00:30.000Z",
                   "message": {"role": "user", "content": "the real fork continuation"}}),
        ])],
        "本文中のアンカー bbbb2222@2026-04-01 10:00 (窓外、実体は別セッションに fork 済み)。")
    args = argparse.Namespace(quiet=False)
    with pytest.raises(SystemExit):
        recall.cmd_check(args)
    out = capsys.readouterr().out
    assert "候補: dddd4444@2026-04-01 10:00" in out


def test_check_anchor_missing_file_warns(tmp_path, monkeypatch, capsys):
    """前綴りに一致する .jsonl が projects ディレクトリに全く無い -> WARN (dead link 扱い、
    アーカイブ済みの可能性)。ERROR にはならない。"""
    _setup_anchor_case(
        tmp_path, monkeypatch, [],  # セッション無し
        "本文中のアンカー cccc3333@2026-04-01 10:00 (該当セッション無し)。")
    args = argparse.Namespace(quiet=False)
    recall.cmd_check(args)  # WARN のみ -> exit しない
    out = capsys.readouterr().out
    assert "cccc3333@2026-04-01 10:00 in node_anchor.md -- セッションが存在しない" in out
    assert "ERROR" not in out


def test_get_cached_minutes_invalidates_on_size_mtime_change(tmp_path):
    """anchor_cache の核: (path, size, mtime) が変わればファイルを再走査し、変わらなければ
    (壊れていても) キャッシュの値をそのまま返すことを直接確認する。"""
    p = tmp_path / "s.jsonl"
    p.write_text(_line({"type": "user", "timestamp": "2026-01-01T00:00:00.000Z",
                         "message": {"role": "user", "content": "a"}}), encoding="utf-8")
    os.utime(p, (1_000_000, 1_000_000))
    cache = {}
    minutes1 = recall.get_cached_minutes(p, cache)
    assert minutes1 == {"2026-01-01 00:00"}

    # 内容を差し替えて (size 変化) mtime も明示的に更新する
    p.write_text(_line({"type": "user", "timestamp": "2026-02-02T05:05:00.000Z",
                         "message": {"role": "user", "content": "b (longer content here)"}}),
                 encoding="utf-8")
    os.utime(p, (2_000_000, 2_000_000))
    minutes2 = recall.get_cached_minutes(p, cache)
    assert minutes2 == {"2026-02-02 05:05"}  # 再構築されて新しい timestamp を反映

    # キャッシュ済みエントリを直接壊し、(size,mtime) を変えずに再度引く -> 壊れた値のまま
    # 返ってくるはず (再走査されていない証拠)
    key = str(p.resolve())
    cache[key]["minutes"] = ["9999-01-01 00:00"]
    minutes3 = recall.get_cached_minutes(p, cache)
    assert minutes3 == {"9999-01-01 00:00"}


# ---------- check: 系譜索引 (## 系譜 セクション) の同期検査 ----------

def _setup_lineage_index_case(tmp_path, monkeypatch, node_stem, node_body, memory_md_text):
    monkeypatch.setattr(recall, "PROJECTS_DIR", tmp_path)
    mdir = tmp_path / "demo-project" / "memory"
    mdir.mkdir(parents=True)
    make_memory_file(mdir, node_stem, node_stem, "系譜索引テスト用", body=node_body)
    (mdir / "MEMORY.md").write_text(memory_md_text, encoding="utf-8")


def test_check_lineage_sync_ok(tmp_path, monkeypatch, capsys):
    _setup_lineage_index_case(
        tmp_path, monkeypatch, "node_sync", "系譜: [[node_sync]] → [[node_sync]]",
        "- [X](node_sync.md) — x\n\n## 系譜\n- テーマ → node_sync — 一言\n")
    args = argparse.Namespace(quiet=False)
    recall.cmd_check(args)  # 実体 1 本・索引 1 行で同期 -> ERROR 無し、exit しない
    out = capsys.readouterr().out
    assert "系譜索引" not in out
    assert "lineage=1" in out


def test_check_lineage_missing_from_index(tmp_path, monkeypatch, capsys):
    _setup_lineage_index_case(
        tmp_path, monkeypatch, "node_orphan_lineage", "系譜: [[node_orphan_lineage]]",
        "- [X](node_orphan_lineage.md) — x\n\n## 系譜\n")  # セクションはあるが行が無い
    args = argparse.Namespace(quiet=False)
    with pytest.raises(SystemExit) as exc:
        recall.cmd_check(args)
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "ERROR 系譜索引 欠落 (1 件)" in out
    assert "node_orphan_lineage.md -- 系譜 1 本あるが" in out


def test_check_lineage_ghost_in_index(tmp_path, monkeypatch, capsys):
    _setup_lineage_index_case(
        tmp_path, monkeypatch, "node_no_lineage_body", "",  # 系譜: 行を持たない
        "- [X](node_no_lineage_body.md) — x\n\n## 系譜\n"
        "- テーマ → node_no_lineage_body — 一言\n")
    args = argparse.Namespace(quiet=False)
    with pytest.raises(SystemExit) as exc:
        recall.cmd_check(args)
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "ERROR 系譜索引 幽霊 (1 件)" in out
    assert "node_no_lineage_body.md -- ## 系譜 に 1 行あるが" in out


def test_check_lineage_count_mismatch(tmp_path, monkeypatch, capsys):
    _setup_lineage_index_case(
        tmp_path, monkeypatch, "node_two_lineages",
        "系譜: [[a]] → [[b]]\n系譜: [[c]] → [[d]]",  # 実体 2 本
        "- [X](node_two_lineages.md) — x\n\n## 系譜\n"
        "- テーマ → node_two_lineages — 一言\n")  # 索引 1 行
    args = argparse.Namespace(quiet=False)
    with pytest.raises(SystemExit) as exc:
        recall.cmd_check(args)
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "ERROR 系譜索引 本数不一致 (1 件)" in out
    assert "node_two_lineages.md -- 実体 2 本 / 索引 1 行" in out


def test_check_lineage_no_section_no_entity_ok(tmp_path, monkeypatch, capsys):
    _setup_lineage_index_case(
        tmp_path, monkeypatch, "node_plain", "",  # 系譜: 行無し
        "- [X](node_plain.md) — x\n")  # "## 系譜" セクション自体無し
    args = argparse.Namespace(quiet=False)
    recall.cmd_check(args)  # 実体 0・索引セクション無し -> OK、exit しない
    out = capsys.readouterr().out
    assert "系譜索引" not in out
    assert "lineage=0" in out


def test_check_lineage_no_section_but_entity_exists_errors(tmp_path, monkeypatch, capsys):
    _setup_lineage_index_case(
        tmp_path, monkeypatch, "node_has_lineage_no_section", "系譜: [[a]] → [[b]]",
        "- [X](node_has_lineage_no_section.md) — x\n")  # "## 系譜" セクション自体無し
    args = argparse.Namespace(quiet=False)
    with pytest.raises(SystemExit) as exc:
        recall.cmd_check(args)
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "ERROR 系譜索引 欠落 (1 件)" in out


# ---------- suggest (読み取り専用の候補提案) ----------

def test_extract_tokens_ascii_katakana_kanji():
    tokens = recall.extract_tokens("ASCIIword123 と カタカナラン と 漢字二文字 mix.")
    assert "asciiword123" in tokens          # ASCII は小文字化
    assert "カタカナラン" in tokens            # カタカナ 3+ 連はそのまま 1 token
    # 漢字 2+ 連は重複 2-gram 化: "漢字二文字" (5 文字) -> 漢字/字二/二文/文字
    assert {"漢字", "字二", "二文", "文字"}.issubset(set(tokens))


@pytest.fixture
def fake_suggest_lexical(tmp_path, monkeypatch):
    """AlphaSharedToken は node1/node2 (未接続) だけが共有、BetaSharedToken は node3/node4
    (node3→node4 に既存リンク) だけが共有。N=8 で df=2 が 25% 閾値 (=2) を超えないため両語彙
    とも生き残る ── 「未接続対は出る／既接続対は出ない」を同じ条件で isolate できる。"""
    monkeypatch.setattr(recall, "PROJECTS_DIR", tmp_path)
    mdir = tmp_path / "demo-project" / "memory"
    mdir.mkdir(parents=True)
    make_memory_file(mdir, "node1", "node1", "d", body="AlphaSharedToken plus fillerwordone")
    make_memory_file(mdir, "node2", "node2", "d", body="AlphaSharedToken plus fillerwordtwo")
    make_memory_file(mdir, "node3", "node3", "d",
                      body="BetaSharedToken plus fillerwordthree [[node4]]")
    make_memory_file(mdir, "node4", "node4", "d", body="BetaSharedToken plus fillerwordfour")
    make_memory_file(mdir, "node5", "node5", "d", body="uniquewordabc onlyfive")
    make_memory_file(mdir, "node6", "node6", "d", body="uniquewordxyz onlysix")
    make_memory_file(mdir, "node7", "node7", "d", body="uniquewordqrs onlyseven")
    make_memory_file(mdir, "node8", "node8", "d", body="uniquewordtuv onlyeight")
    (mdir / "MEMORY.md").write_text("dummy index\n", encoding="utf-8")
    return tmp_path


def test_suggest_lexical_finds_unlinked_excludes_linked(fake_suggest_lexical):
    node_files = list(recall.iter_memory_files())
    graph = recall.build_undirected_graph(node_files)
    results = recall.suggest_lexical(node_files, graph, 10)
    pairs = [(a, b) for _, a, b, _ in results]
    assert ("node1", "node2") in pairs        # 共有語あり・未接続 -> 候補に出る
    assert ("node3", "node4") not in pairs    # 共有語あり・既にリンク済み -> 除外される


def test_cmd_suggest_end_to_end_output(fake_suggest_lexical, capsys):
    args = argparse.Namespace(max=10)
    recall.cmd_suggest(args)  # クラッシュしない・書き込みもしない (読み取り専用)
    out = capsys.readouterr().out
    assert "node1 <-> node2" in out
    assert "node3 <-> node4" not in out
    assert "候補は表面類似を含む" in out


@pytest.fixture
def fake_suggest_triangle(tmp_path, monkeypatch):
    """hub1/hub2/hub3 がそれぞれ [[main_x]] と [[main_y]] の両方にリンク -> 共通隣人 3、
    main_x/main_y 自体は未接続 -> 三角閉包の典型候補。"""
    monkeypatch.setattr(recall, "PROJECTS_DIR", tmp_path)
    mdir = tmp_path / "demo-project" / "memory"
    mdir.mkdir(parents=True)
    for hub in ("hub1", "hub2", "hub3"):
        make_memory_file(mdir, hub, hub, "d", body="[[main_x]] と [[main_y]] を参照。")
    make_memory_file(mdir, "main_x", "main_x", "d")
    make_memory_file(mdir, "main_y", "main_y", "d")
    (mdir / "MEMORY.md").write_text("dummy\n", encoding="utf-8")
    return tmp_path


def test_suggest_triangles_finds_common_neighbor_pair(fake_suggest_triangle):
    node_files = list(recall.iter_memory_files())
    graph = recall.build_undirected_graph(node_files)
    results = recall.suggest_triangles(graph, 10)
    hits = [(n, a, b) for n, a, b, _ in results if {a, b} == {"main_x", "main_y"}]
    assert hits == [(3, "main_x", "main_y")]


@pytest.fixture
def fake_suggest_selfloop(tmp_path, monkeypatch):
    """系譜行の最終ノードが自分自身を指す実例の再現 (今日踏んだ罠)。"""
    monkeypatch.setattr(recall, "PROJECTS_DIR", tmp_path)
    mdir = tmp_path / "demo-project" / "memory"
    mdir.mkdir(parents=True)
    make_memory_file(mdir, "self_loop_node", "self_loop_node", "d",
                      body="系譜: [[other]] → [[self_loop_node]]")
    make_memory_file(mdir, "other", "other", "d")
    (mdir / "MEMORY.md").write_text("dummy\n", encoding="utf-8")
    return tmp_path


def test_build_undirected_graph_excludes_self_link(fake_suggest_selfloop):
    node_files = list(recall.iter_memory_files())
    graph = recall.build_undirected_graph(node_files)
    assert "self_loop_node" not in graph["self_loop_node"]
    assert "other" in graph["self_loop_node"]  # 自己リンク以外はちゃんと張られる


@pytest.fixture
def fake_suggest_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(recall, "PROJECTS_DIR", tmp_path)
    mdir = tmp_path / "demo-project" / "memory"
    mdir.mkdir(parents=True)
    (mdir / "MEMORY.md").write_text("dummy\n", encoding="utf-8")
    return tmp_path


def test_cmd_suggest_empty_graph_no_crash(fake_suggest_empty, capsys):
    args = argparse.Namespace(max=10)
    recall.cmd_suggest(args)  # memory ファイル 0 件でもクラッシュしない
    out = capsys.readouterr().out
    a_section, b_section = out.split("=== B.")
    assert "候補なし" in a_section
    assert "候補なし" in b_section


# ---------- タスク1: パスの汎用化 (--projects-dir / --memory-dir) ----------

def test_resolve_projects_dir_env_and_cli_priority(tmp_path, monkeypatch):
    monkeypatch.delenv("RECALL_PROJECTS_DIR", raising=False)
    (tmp_path / "only-project").mkdir()
    monkeypatch.setattr(recall, "DEFAULT_PROJECTS_DIR", tmp_path)
    # 何も無ければ既定値の自動検出 (直下がちょうど1件ならそれを採用)
    assert recall.resolve_projects_dir(None) == tmp_path
    # 環境変数があればそれ
    monkeypatch.setenv("RECALL_PROJECTS_DIR", "/env/projects")
    assert recall.resolve_projects_dir(None) == Path("/env/projects")
    # CLI 引数があれば環境変数より優先
    assert recall.resolve_projects_dir("/cli/projects") == Path("/cli/projects")


def test_resolve_memory_dir_env_and_cli_priority(monkeypatch):
    monkeypatch.delenv("RECALL_MEMORY_DIR", raising=False)
    # 何も無ければ None (memory_dir() が PROJECTS_DIR から派生させる合図)
    assert recall.resolve_memory_dir(None) is None
    monkeypatch.setenv("RECALL_MEMORY_DIR", "/env/memory")
    assert recall.resolve_memory_dir(None) == Path("/env/memory")
    assert recall.resolve_memory_dir("/cli/memory") == Path("/cli/memory")


# ---------- stale (30日棚卸し候補) ----------

def _write_stale_node(mdir, stem, modified, description, body):
    lines = ["---", f"name: {stem}", f"description: {description}"]
    if modified is not None:
        lines += ["metadata: ", "  node_type: memory", f"  modified: {modified}"]
    lines.append("---")
    (mdir / f"{stem}.md").write_text("\n".join(lines) + "\n\n" + body + "\n", encoding="utf-8")


@pytest.fixture
def fake_stale(tmp_path, monkeypatch):
    """node_hub (inbound=2, 2000年 = 最古) > node_mid (inbound=1, 2020年) >
    node_lonely (inbound=0, 最近) の順にスコアが下がるよう設計。node_nomod は modified 無し。"""
    monkeypatch.setattr(recall, "PROJECTS_DIR", tmp_path)
    mdir = tmp_path / "demo-project" / "memory"
    mdir.mkdir(parents=True)
    (mdir / "MEMORY.md").write_text("dummy\n", encoding="utf-8")
    _write_stale_node(mdir, "node_hub", "2000-01-01T00:00:00.000Z", "hub node の説明です", "")
    _write_stale_node(mdir, "node_mid", "2020-01-01T00:00:00.000Z", "mid node", "")
    _write_stale_node(mdir, "node_lonely", "2026-08-01T00:00:00.000Z", "lonely node", "")
    _write_stale_node(mdir, "node_a", "2026-08-01T00:00:00.000Z", "linker a",
                       "参照: [[node_hub]] と [[node_mid]]。")
    _write_stale_node(mdir, "node_b", "2026-08-01T00:00:00.000Z", "linker b",
                       "参照: [[node_hub]]。")
    _write_stale_node(mdir, "node_nomod", None, "modified 無し", "本文のみ。")
    return tmp_path


def test_cmd_stale_ranks_by_backlinks_times_age(fake_stale, capsys):
    args = argparse.Namespace(top=10)
    recall.cmd_stale(args)
    out = capsys.readouterr().out
    idx_hub = out.index("node_hub")
    idx_mid = out.index("node_mid")
    idx_lonely = out.index("node_lonely")
    assert idx_hub < idx_mid < idx_lonely   # score 降順
    assert "被リンク 2" in out.splitlines()[0]  # 先頭行 = node_hub、被リンク 2
    assert "hub node の説明です" in out


def test_cmd_stale_excludes_missing_modified_with_warn(fake_stale, capsys):
    args = argparse.Namespace(top=10)
    recall.cmd_stale(args)
    out, err = capsys.readouterr()
    assert "node_nomod" not in out
    assert "WARN node_nomod.md: modified が無い (除外)" in err


def test_cmd_stale_no_nodes(fake_suggest_empty, capsys):
    args = argparse.Namespace(top=10)
    recall.cmd_stale(args)
    out = capsys.readouterr().out
    assert "対象ノードなし" in out


# ---------- backlinks (除染用の逆リンク一覧) ----------

@pytest.fixture
def fake_backlinks(tmp_path, monkeypatch):
    monkeypatch.setattr(recall, "PROJECTS_DIR", tmp_path)
    mdir = tmp_path / "demo-project" / "memory"
    mdir.mkdir(parents=True)
    (mdir / "MEMORY.md").write_text("dummy\n", encoding="utf-8")
    make_memory_file(mdir, "node_ref", "node_ref", "d",
                      body="本文中で [[node_target]] を参照している行。")
    make_memory_file(mdir, "node_lineage_ref", "node_lineage_ref", "d",
                      body="系譜: [[node_target]] → [[node_other]]")
    make_memory_file(mdir, "node_unrelated", "node_unrelated", "d", body="関係ない本文。")
    return tmp_path


def test_cmd_backlinks_normal_and_lineage_mark(fake_backlinks, capsys):
    args = argparse.Namespace(node="node_target")
    recall.cmd_backlinks(args)
    out = capsys.readouterr().out
    normal_lines = [l for l in out.splitlines() if l.startswith("node_ref.md:")]
    lineage_lines = [l for l in out.splitlines() if l.startswith("node_lineage_ref.md:")]
    assert normal_lines and "node_target" in normal_lines[0] and not normal_lines[0].endswith(
        " (系譜)")
    assert lineage_lines and lineage_lines[0].endswith(" (系譜)")
    assert not any(l.startswith("node_unrelated.md:") for l in out.splitlines())


def test_cmd_backlinks_no_hits(fake_backlinks, capsys):
    args = argparse.Namespace(node="totally_unreferenced_node_xyz")
    recall.cmd_backlinks(args)
    out = capsys.readouterr().out
    assert "被リンクなし" in out


# ---------- show --at (指定時刻±window分だけ表示) ----------

@pytest.fixture
def fake_show_at(tmp_path, monkeypatch):
    monkeypatch.setattr(recall, "PROJECTS_DIR", tmp_path)
    make_session(tmp_path, "C--fake-project", "cafe1234-0000-0000-0000-000000000000", [
        _line({"type": "user", "timestamp": "2026-04-01T11:59:30.000Z",
               "message": {"role": "user", "content": "just before anchor"}}),
        _line({"type": "assistant", "timestamp": "2026-04-01T12:00:30.000Z",
               "message": {"role": "assistant",
                           "content": [{"type": "text", "text": "right at anchor"}]}}),
        _line({"type": "user", "timestamp": "2026-04-01T12:10:00.000Z",
               "message": {"role": "user", "content": "far after anchor"}}),
    ])
    return tmp_path


def test_cmd_show_at_window_filters_messages(fake_show_at, capsys):
    args = argparse.Namespace(session_prefix="cafe1234", grep=None, head=None, tail=None,
                               at="2026-04-01 12:00", window=2)
    recall.cmd_show(args)
    out = capsys.readouterr().out
    assert "just before anchor" in out
    assert "right at anchor" in out
    assert "far after anchor" not in out


def test_cmd_show_at_conflicts_with_other_flags_exits(fake_show_at, capsys):
    args = argparse.Namespace(session_prefix="cafe1234", grep="x", head=None, tail=None,
                               at="2026-04-01 12:00", window=2)
    with pytest.raises(SystemExit) as exc:
        recall.cmd_show(args)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "併用できない" in err


def test_cmd_show_at_bad_format_exits(fake_show_at, capsys):
    args = argparse.Namespace(session_prefix="cafe1234", grep=None, head=None, tail=None,
                               at="not-a-date", window=2)
    with pytest.raises(SystemExit) as exc:
        recall.cmd_show(args)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "日時形式が不正" in err


def test_cmd_show_head_still_works_without_at_attr(fake_projects, capsys):
    """既存テストの再確認: Namespace が at/window 属性を持たなくても cmd_show は落ちない。"""
    args = argparse.Namespace(session_prefix="abcd1234", grep=None, head=2, tail=None)
    recall.cmd_show(args)
    out = capsys.readouterr().out
    assert "hello recall world" in out


# ---------- check: MEMORY.md 行数 WARN ----------

@pytest.fixture
def fake_memory_long_index(tmp_path, monkeypatch):
    """MEMORY.md が 251 行 (階層化トリガー 300 行接近の WARN 対象)。他の検査に触れないよう
    "- [x](y.md)" 形式ではない詰め物行にする (index_stems に混ざって ghost/missing を作らない)。"""
    monkeypatch.setattr(recall, "PROJECTS_DIR", tmp_path)
    mdir = tmp_path / "demo-project" / "memory"
    mdir.mkdir(parents=True)
    (mdir / "MEMORY.md").write_text("filler line\n" * 251, encoding="utf-8")
    return tmp_path


def test_cmd_check_warns_long_index(fake_memory_long_index, capsys):
    args = argparse.Namespace(quiet=False)
    recall.cmd_check(args)  # 他の ERROR カテゴリ無し -> exit しない
    out = capsys.readouterr().out
    assert "WARN 索引が 251 行" in out


def test_cmd_check_short_index_no_warn(fake_memory_clean, capsys):
    args = argparse.Namespace(quiet=False)
    recall.cmd_check(args)
    out = capsys.readouterr().out
    assert "WARN 索引が" not in out


# ---------- Group A: projects-dir auto-detect ----------

def test_autodetect_projects_dir_single_subdir(tmp_path, monkeypatch):
    """DEFAULT_PROJECTS_DIR 直下がちょうど1件なら、それを resolve_projects_dir(None) が返す
    (exit しない)。"""
    monkeypatch.delenv("RECALL_PROJECTS_DIR", raising=False)
    (tmp_path / "only-project").mkdir()
    monkeypatch.setattr(recall, "DEFAULT_PROJECTS_DIR", tmp_path)
    assert recall.resolve_projects_dir(None) == tmp_path


def test_autodetect_projects_dir_multiple_subdirs_exits(tmp_path, monkeypatch, capsys):
    """2件以上あれば曖昧なので SystemExit(2)。stderr に両方の名前と --projects-dir の
    使用例が出る。"""
    monkeypatch.delenv("RECALL_PROJECTS_DIR", raising=False)
    (tmp_path / "project-one").mkdir()
    (tmp_path / "project-two").mkdir()
    monkeypatch.setattr(recall, "DEFAULT_PROJECTS_DIR", tmp_path)
    with pytest.raises(SystemExit) as exc:
        recall.resolve_projects_dir(None)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "project-one" in err
    assert "project-two" in err
    assert "--projects-dir" in err


def test_autodetect_projects_dir_missing_base_exits(tmp_path, monkeypatch, capsys):
    """DEFAULT_PROJECTS_DIR 自体が存在しなければ「見つからない」で SystemExit(2)。"""
    monkeypatch.delenv("RECALL_PROJECTS_DIR", raising=False)
    missing = tmp_path / "does-not-exist"
    monkeypatch.setattr(recall, "DEFAULT_PROJECTS_DIR", missing)
    with pytest.raises(SystemExit) as exc:
        recall.resolve_projects_dir(None)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "見つからない" in err


def test_autodetect_projects_dir_zero_subdirs_exits(tmp_path, monkeypatch, capsys):
    """base は存在するがサブディレクトリが0件 -> 一覧提示パスに落ちてもクラッシュせず
    SystemExit(2)。"""
    monkeypatch.delenv("RECALL_PROJECTS_DIR", raising=False)
    monkeypatch.setattr(recall, "DEFAULT_PROJECTS_DIR", tmp_path)  # 存在するが空
    with pytest.raises(SystemExit) as exc:
        recall.resolve_projects_dir(None)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--projects-dir" in err


# ---------- Group B: memory 層のグレースフルデグレード ----------

@pytest.fixture
def fake_projects_no_memory(tmp_path, monkeypatch):
    """memory/ が一切存在しない PROJECTS_DIR (transcript 層のみ)。"""
    monkeypatch.setattr(recall, "PROJECTS_DIR", tmp_path)
    make_session(tmp_path, "demo-project", "eeee5555-0000-0000-0000-000000000000", [
        _line({"type": "user", "timestamp": "2026-06-01T00:00:00.000Z",
               "message": {"role": "user", "content": "no memory layer here"}}),
        _line({"type": "assistant", "timestamp": "2026-06-01T00:00:05.000Z",
               "message": {"role": "assistant",
                           "content": [{"type": "text", "text": "confirmed transcript only"}]}}),
    ])
    return tmp_path


def test_transcript_layer_works_without_memory_dir(fake_projects_no_memory, capsys):
    """list / search / show は memory_dir() に一切触れないので、memory/ が無くても正常に
    動く (SystemExit しない)。"""
    recall.cmd_list(argparse.Namespace(project=None))
    out = capsys.readouterr().out
    assert "eeee5555" in out

    recall.cmd_search(argparse.Namespace(query="transcript", project=None, days=None, max=20))
    out = capsys.readouterr().out
    assert "transcript" in out

    recall.cmd_show(argparse.Namespace(session_prefix="eeee5555", grep=None, head=None,
                                        tail=None))
    out = capsys.readouterr().out
    assert "no memory layer here" in out


def test_memory_layer_commands_degrade_gracefully_without_memory_dir(
        fake_projects_no_memory, capsys):
    """memory 層コマンド (check/lineage/suggest/backlinks/origin/stale) は memory/ が無ければ
    案内して SystemExit(2) する。"""
    cases = [
        (recall.cmd_check, argparse.Namespace(quiet=False)),
        (recall.cmd_lineage, argparse.Namespace(query="x", deep=False)),
        (recall.cmd_suggest, argparse.Namespace(max=10)),
        (recall.cmd_backlinks, argparse.Namespace(node="x")),
        (recall.cmd_origin, argparse.Namespace(prefix="x", grep=None, tail=None)),
        (recall.cmd_stale, argparse.Namespace(top=10)),
    ]
    for func, args in cases:
        with pytest.raises(SystemExit) as exc:
            func(args)
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "memory 層は未設定" in err


def test_autodetect_memory_dir_single_candidate(tmp_path, monkeypatch):
    """PROJECTS_DIR 直下で memory/ を持つプロジェクトがちょうど1件ならそこを採用する。"""
    monkeypatch.setattr(recall, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(recall, "MEMORY_DIR_OVERRIDE", None)
    proj = tmp_path / "only-project"
    (proj / "memory").mkdir(parents=True)
    resolved = recall._autodetect_memory_dir()
    assert resolved == proj / "memory"
    assert recall.memory_dir() == proj / "memory"


def test_autodetect_memory_dir_multiple_candidates_falls_back_missing(tmp_path, monkeypatch):
    """memory/ を持つプロジェクトが2件以上あれば曖昧 -> exists()=False な未検出センチネルを
    返す (例外は投げない)。"""
    monkeypatch.setattr(recall, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(recall, "MEMORY_DIR_OVERRIDE", None)
    (tmp_path / "project-a" / "memory").mkdir(parents=True)
    (tmp_path / "project-b" / "memory").mkdir(parents=True)
    resolved = recall._autodetect_memory_dir()
    assert not resolved.exists()


# ---------- Group C: sample demo (エンドツーエンド smoke test) ----------

def _run_recall(*args):
    return subprocess.run(
        [sys.executable, str(RECALL_PATH), "--projects-dir", str(SAMPLE_PROJECTS_DIR), *args],
        capture_output=True, text=True, encoding="utf-8")


def test_sample_demo_list():
    result = _run_recall("list")
    assert result.returncode == 0
    assert "a1b2c3d4" in result.stdout
    assert "b2c3d4e5" in result.stdout


def test_sample_demo_search():
    result = _run_recall("search", "dashboard")
    assert result.returncode == 0
    assert "dashboard" in result.stdout


def test_sample_demo_check():
    result = _run_recall("check")
    assert result.returncode == 0
    assert "ERROR" not in result.stdout


def test_sample_demo_lineage():
    result = _run_recall("lineage", "b2c3d4e5")
    assert result.returncode == 0
    assert "node_review" in result.stdout


def test_sample_demo_suggest():
    result = _run_recall("suggest")
    assert result.returncode == 0
