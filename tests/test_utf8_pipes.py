"""What pys reads from a child process is UTF-8 on every system. Python 3.14 on Windows
decodes a text-mode pipe in the locale's code page (GBK on a Chinese Windows), so git's
UTF-8 (a CJK branch name or commit subject) failed in subprocess's _readerthread with
"'gbk' codec can't decode byte 0x80" (pys doctor, the branch widget, the caption)."""
from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "py_screen"


def test_every_text_mode_subprocess_names_its_encoding():
    """A subprocess call that decodes text (text=True or universal_newlines) says
    encoding=: the locale's code page is never the right one for a pipe."""
    missing = []
    for f in SRC.rglob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "subprocess"):
                continue
            kw = {k.arg: k.value for k in node.keywords}
            text = any(isinstance(kw.get(k), ast.Constant) and kw[k].value is True
                       for k in ("text", "universal_newlines"))
            if text and "encoding" not in kw:
                missing.append(f"{f.relative_to(SRC)}:{node.lineno}")
    assert not missing, f"text-mode subprocess without encoding=: {missing}"


def test_every_text_file_call_names_its_encoding():
    """open() in a text mode, Path.read_text / write_text / open: encoding= always."""
    missing = []
    for f in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            kw = {k.arg for k in node.keywords}
            if "encoding" in kw or None in kw:
                continue
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr in ("read_text", "write_text"):
                missing.append(f"{f.relative_to(SRC)}:{node.lineno}")
                continue
            builtin = isinstance(fn, ast.Name) and fn.id == "open"
            path_open = (isinstance(fn, ast.Attribute) and fn.attr == "open"
                         and not (isinstance(fn.value, ast.Name) and fn.value.id in ("os", "pty")))
            if not (builtin or path_open):
                continue
            i = 1 if builtin else 0                      # open(file, mode) / Path.open(mode)
            m = node.args[i] if len(node.args) > i else next(
                (k.value for k in node.keywords if k.arg == "mode"), None)
            mode = "r" if m is None else m.value if isinstance(m, ast.Constant) else "b?"
            if "b" not in mode:
                missing.append(f"{f.relative_to(SRC)}:{node.lineno}")
    assert not missing, f"text file call without encoding=: {missing}"


def test_git_reads_a_cjk_branch_and_subject(tmp_path):
    from py_screen.agents import gitinfo
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    run = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a], env=env, check=True,
                                    capture_output=True)
    run("init", "-q", "-b", "主线")
    (tmp_path / "说明.txt").write_text("你好\n", encoding="utf-8")
    run("add", ".")
    run("commit", "-q", "-m", "第一次提交：中文")
    assert gitinfo.git(str(tmp_path), "branch", "--show-current").strip() == "主线"
    assert gitinfo.git(str(tmp_path), "log", "-1", "--format=%s").strip() == "第一次提交：中文"
    assert gitinfo.place(str(tmp_path))["branch"] == "主线"
