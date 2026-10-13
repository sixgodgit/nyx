"""tests/test_prod_guard.py — 测试进程碰不到生产库（v7.13.9）

2026-09-13 事故：测试把 107 条合成记忆（sender=golden / test / e2e / mcp）写进了生产中枢，
agent 把「用户喜欢喝拿铁」当成真记忆说了出来。v7.13.8 时复现：不设 NEXSANDBASE_HOME 直接跑测试，
真实数据目录的日志里多出「e2e | 用户喜欢手冲咖啡」。

教训：真正的问题不是某个测试写错了目标，而是「测试能写生产」这件事本身 —— 只要可能，就迟早会发生，
而且发生时没有任何东西会喊一声。所以两道防线都要有测试：
  1. tests/conftest.py 把整个会话的数据目录强制指到临时目录
  2. core/prodguard.py：测试进程开临时目录以外的库 / 写那里的日志 → 直接报错
"""
import os
import subprocess
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = os.path.realpath(tempfile.gettempdir())


def _in_tmp(p):
    return os.path.realpath(p).startswith(TMP + os.sep)


def test_session_data_dir_is_temporary():
    """每个在 import 时绑死路径的模块，都必须指在临时目录里。"""
    from nexsandglass.core import memid, sandglass_sqlite, sandglass_log, erasure
    from nexsandglass.features import sandglass_vault, shadow_sand, weavethread
    paths = {"NEXSANDBASE_HOME": os.environ["NEXSANDBASE_HOME"], "HERMES_HOME": os.environ["HERMES_HOME"],
             "memid db": memid._db_path(), "journal": memid._journal_path(),
             "sandglass_sqlite._DB": sandglass_sqlite._DB, "sandglass_log._SANDGLASS": sandglass_log._SANDGLASS,
             "sandglass_vault._SANDGLASS": sandglass_vault._SANDGLASS, "erasure._NB": erasure._NB,
             "shadow_sand._SHADOW_DB": shadow_sand._SHADOW_DB, "weavethread._DB": weavethread._DB}
    bad = {k: v for k, v in paths.items() if not _in_tmp(v)}
    assert not bad, f"这些路径不在临时目录里：{bad}"


def _outside_tmp_path(name):
    # 不真的创建：守卫必须在建目录之前就拦下
    return os.path.join(os.path.expanduser("~"), f".nyx-prodguard-probe-{os.getpid()}", name)


def test_guard_refuses_db_outside_temp():
    from nexsandglass.core import sqlite_open
    p = _outside_tmp_path("nyx.db")
    with pytest.raises(RuntimeError, match="临时目录以外"):
        sqlite_open.connect(p)
    assert not os.path.exists(os.path.dirname(p)), "守卫之前不许建目录"


def test_guard_refuses_journal_append_outside_temp():
    from nexsandglass.core import memid
    before = memid.count()
    p = _outside_tmp_path("sandglass.txt")
    with pytest.raises(RuntimeError, match="临时目录以外"):
        memid.allocate("不该写进生产的一句话", "test", journal_path=p)
    assert memid.count() == before, "日志写不进去时，中枢那一行必须一起回滚"
    assert not os.path.exists(os.path.dirname(p))


def test_guard_override_and_scope(monkeypatch):
    from nexsandglass.core import prodguard
    p = _outside_tmp_path("nyx.db")
    monkeypatch.setenv("NYX_ALLOW_PROD_DB", "1")
    prodguard.check(p)                                    # 显式豁免
    # 生产进程（没有加载 pytest、没有 PYTEST_CURRENT_TEST）：守卫什么都不做
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTEST_CURRENT_TEST", "PYTEST_VERSION", "NYX_ALLOW_PROD_DB")}
    env["PYTHONPATH"] = ROOT
    r = subprocess.run([sys.executable, "-c",
                        "from nexsandglass.core import prodguard; prodguard.check(%r); print('ok')" % p],
                       env=env, capture_output=True, text=True, timeout=60)
    assert r.stdout.strip() == "ok", r.stderr[-1000:]
    # 生产进程即使间接 import 了 _pytest（某个库顺手加载了它）也不能被当成测试进程
    r = subprocess.run([sys.executable, "-c",
                        "import _pytest.config; from nexsandglass.core import prodguard; "
                        "prodguard.check(%r); print('ok')" % p],
                       env=env, capture_output=True, text=True, timeout=60)
    assert r.stdout.strip() == "ok", r.stderr[-1000:]


@pytest.mark.parametrize("conftest", [True, False])
def test_incident_replay_production_untouched(tmp_path, conftest):
    """重演事故现场：NEXSANDBASE_HOME 设了、指着「生产库」，然后跑一个会写合成记忆的测试文件。

    conftest=False 时用 --noconftest 拆掉第一道防线，只剩运行时守卫 —— 也必须挡住（测试会报错，这是对的：要喊出来）。
    「临时目录」在子进程里收窄为 tmp_path/tmp，tmp_path/prod 因此算作临时目录以外。"""
    tmp_root = tmp_path / "tmp"
    prod = tmp_path / "prod"
    tmp_root.mkdir()
    prod.mkdir()
    sentinel = "2026-09-01 10:00:00 | user | 生产库里的一条真实记忆\n"
    (prod / "sandglass.txt").write_text(sentinel, encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTEST_CURRENT_TEST", "PYTEST_VERSION", "NYX_ALLOW_PROD_DB")}
    env.update(TMPDIR=str(tmp_root), NEXSANDBASE_HOME=str(prod), NYX_EMBED="off", PYTHONPATH=ROOT)
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-x",
           os.path.join(ROOT, "tests", "test_runtime_api.py")] + ([] if conftest else ["--noconftest"])
    r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
    assert (prod / "sandglass.txt").read_text(encoding="utf-8") == sentinel, r.stdout[-2000:]
    assert sorted(os.listdir(prod)) == ["sandglass.txt"], sorted(os.listdir(prod))
    if conftest:
        assert r.returncode == 0, r.stdout[-2000:]
    else:
        assert "临时目录以外" in r.stdout, r.stdout[-2000:]
