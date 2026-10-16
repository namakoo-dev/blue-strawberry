"""session_start_recall.sh (SessionStart フック) のテスト。一時ディレクトリに recall.py と
合成 archive/ を並べ、BLUE_STRAWBERRY_DIR で向けて実行する。bash が要るので Windows では skip。"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "session_start_recall.sh"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None or shutil.which("python3") is None,
    reason="bash / python3 が必要",
)


def run_hook(bs_dir):
    env = dict(os.environ, BLUE_STRAWBERRY_DIR=str(bs_dir))
    r = subprocess.run(["bash", str(HOOK)], env=env, capture_output=True, text=True,
                       encoding="utf-8", check=True)
    return json.loads(r.stdout)


def make_bs_dir(tmp_path, projects):
    shutil.copy(ROOT / "recall.py", tmp_path / "recall.py")
    for name, index in projects.items():
        mem = tmp_path / "archive" / name / "memory"
        mem.mkdir(parents=True)
        (mem / "MEMORY.md").write_text(index, encoding="utf-8")
    return tmp_path


def test_no_archive_emits_empty_object(tmp_path):
    shutil.copy(ROOT / "recall.py", tmp_path / "recall.py")
    assert run_hook(tmp_path) == {}


def test_injects_index_and_per_project_graph(tmp_path):
    # プロジェクトが複数あっても、memory 自動検出に頼らずプロジェクトごとに check できること
    bs = make_bs_dir(tmp_path, {"alpha": "- alpha の索引\n", "beta": "- beta の索引\n"})
    out = run_hook(bs)["hookSpecificOutput"]
    assert out["hookEventName"] == "SessionStart"
    ctx = out["additionalContext"]
    assert "### alpha" in ctx and "alpha の索引" in ctx
    assert "### beta" in ctx and "beta の索引" in ctx
    assert ctx.count("graph: nodes=") == 2
    assert "check 失敗" not in ctx
    assert ctx.index("### alpha") < ctx.index("### beta")


def test_real_archive_is_readable():
    out = run_hook(ROOT)["hookSpecificOutput"]["additionalContext"]
    assert "### codex-mcp" in out and "graph: nodes=" in out
