"""tests/test_hermes_install.py — python3 -m nexsandglass_hermes install（v7.15.0）

hermes-agent 0.19.0 只按目录找记忆插件（$HERMES_HOME/plugins/<名字>/），不看 pip 入口点，所以光 pip install 找不到 nyx。
这里验安装命令本身（不装 Hermes）：
  - 入口文件：过得了 Hermes 的免 import 探测；按 Hermes 加载用户插件的方式加载出 Provider；nyx-memory 不在时只报错不崩
  - 重复运行是「更新」；旧说明里整目录拷过去的副本换成入口文件；别人的目录不覆盖（除非 --force）
  - 从 hermes 命令第一行的 #! 找到 Hermes 用的 Python；同一个环境比 sys.prefix（venv 的 python 是符号链接）
  - 往 Hermes 的 Python 里装什么：本地源码装的装同一份源码，否则装 PyPI 上的同一个版本
  - 整个流程：改 memory.provider、--vault 时启用 nyx-reveal 并在 nyx.json 里打开、旧版残留要报出来
在真实 Hermes 里装一遍、加载出来、跑通对话：benchmarks/vault_hermes_e2e.py。
"""
import importlib.util
import json
import logging
import os
import stat
import sys

import pytest

from nexsandglass_hermes import install as I


def _load_like_hermes(target):
    """plugins/memory/__init__.py 的 _load_provider_from_dir：用户插件按 _hermes_user_memory.<名字> 加载，再 register(ctx)。"""
    name = f"_hermes_user_memory.{os.path.basename(target)}"
    spec = importlib.util.spec_from_file_location(name, os.path.join(target, "__init__.py"),
                                                  submodule_search_locations=[target])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class Ctx:
        provider = None

        def register_memory_provider(self, p):
            self.provider = p

        def register_tool(self, *a, **k):
            pass

        def register_hook(self, *a, **k):
            pass
    c = Ctx()
    mod.register(c)
    return c.provider


def test_shim_is_found_and_loaded_like_hermes(tmp_path):
    target, did = I.write_shim(str(tmp_path))
    assert did == "新建" and target == os.path.join(str(tmp_path), "plugins", "nyx")
    src = open(os.path.join(target, "__init__.py"), encoding="utf-8").read()
    assert "register_memory_provider" in src[:8192], "Hermes 的 _is_memory_provider_dir 只看这个词"
    yml = open(os.path.join(target, "plugin.yaml"), encoding="utf-8").read()
    assert "name: nyx" in yml and "kind: exclusive" in yml, "普通插件加载器跳过它，只由记忆插件加载器加载"
    p = _load_like_hermes(target)
    assert p is not None and p.name == "nexsandglass"
    assert I.write_shim(str(tmp_path))[1] == "更新"


def test_shim_without_the_package_logs_instead_of_crashing(tmp_path, monkeypatch, caplog):
    target, _ = I.write_shim(str(tmp_path))
    monkeypatch.setitem(sys.modules, "nexsandglass_hermes", None)        # import 会抛 ImportError
    with caplog.at_level(logging.ERROR, logger="nyx"):
        assert _load_like_hermes(target) is None
    assert "pip install nyx-memory" in caplog.text and sys.executable in caplog.text


def test_old_full_copy_is_replaced_foreign_dir_is_not(tmp_path):
    import shutil
    import nexsandglass_hermes
    home = str(tmp_path)
    old = os.path.join(home, "plugins", "nyx")
    shutil.copytree(os.path.dirname(nexsandglass_hermes.__file__), old)   # v7.14.0 README 的第二种装法
    assert I.write_shim(home)[1] == "替换旧的整目录拷贝"
    assert sorted(os.listdir(old)) == ["__init__.py", "plugin.yaml"], "升级之后旧拷贝还在跑旧代码 —— 换成入口文件"
    other = os.path.join(home, "plugins", "nyx2")
    os.makedirs(other)
    open(os.path.join(other, "__init__.py"), "w").write("# 别人的插件\n")
    with pytest.raises(SystemExit, match="不是 nyx 的插件"):
        I.write_shim(home, name="nyx2")
    assert I.write_shim(home, name="nyx2", force=True)[1] == "替换旧的整目录拷贝"


def _fake_hermes(tmp_path, shebang):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    exe = d / "hermes"
    state = tmp_path / "hermes_calls.jsonl"
    exe.write_text(f"""{shebang}
import json, os, sys
home = os.environ["HERMES_HOME"]
cfg = os.path.join(home, "provider.txt")
with open({str(state)!r}, "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:4] == ["config", "get", "memory.provider"]:
    print(open(cfg).read() if os.path.exists(cfg) else "")
elif sys.argv[1:3] == ["config", "set"]:
    open(cfg, "w").write(sys.argv[4])
""", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return d, state


def test_find_hermes_python_from_shebang(tmp_path, monkeypatch):
    d, _ = _fake_hermes(tmp_path, f"#!{sys.executable}")
    monkeypatch.setenv("PATH", str(d) + os.pathsep + os.environ.get("PATH", ""))
    exe, py = I.find_hermes()
    assert exe == str(d / "hermes") and py == sys.executable
    assert I._same_python(py), "同一个环境（比 sys.prefix）"
    assert I.nyx_in(py) == I._version()
    (d / "hermes").write_text("#!/usr/bin/env python3\n")                 # env 式的 #! 认不出解释器
    assert I.find_hermes() == (str(d / "hermes"), None)
    assert I.find_hermes("/opt/hermes/venv/bin/python")[1] == "/opt/hermes/venv/bin/python"


def test_install_spec(monkeypatch, tmp_path):
    class Dist:
        def __init__(self, info):
            self.info = info

        def read_text(self, name):
            return json.dumps(self.info) if self.info is not None else None
    import importlib.metadata as md

    def dists(*infos):
        monkeypatch.setattr(md, "distributions", lambda **kw: [Dist(i) for i in infos])
    monkeypatch.setattr(I, "_local_source", lambda: None)                 # 从 site-packages 里运行
    dists({"url": "file:///src/nyx", "dir_info": {"editable": True}})
    assert I._install_spec(["vault"]) == ["-e", "/src/nyx[vault]"]
    dists({"url": "file:///src/nyx", "dir_info": {}})
    assert I._install_spec([]) == ["/src/nyx"]
    dists(None)
    assert I._install_spec(["vault", "vector"]) == [f"nyx-memory[vault,vector]=={I._version()}"]
    # 从仓库目录运行（install.sh）：当前环境的安装记录指向别处 / 只有过期的 egg-info → 装这份源码
    repo = str(tmp_path / "repo")
    monkeypatch.setattr(I, "_local_source", lambda: repo)
    dists(None, {"url": "file:///elsewhere", "dir_info": {}})
    assert I._install_spec([]) == [repo]
    dists({"url": "file://" + repo, "dir_info": {"editable": True}})
    assert I._install_spec([]) == ["-e", repo]


def test_local_source_detection():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert I._local_source() == root, "测试从仓库里运行：认得出这是 nyx-memory 的源码"


def test_full_run(tmp_path, monkeypatch, capsys):
    d, calls = _fake_hermes(tmp_path, f"#!{sys.executable}")
    monkeypatch.setenv("PATH", str(d) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setattr(I, "missing_extras", lambda py, extras: [])
    monkeypatch.setattr(I, "verify", lambda py, home, name=I.NAME: {
        "dir": os.path.join(home, "plugins", name), "provider": "NexSandglassProvider", "available": True,
        "reveal_entry_point": True})
    home = tmp_path / "hh"
    assert I.main(["--hermes-home", str(home), "--vault"]) == 0
    out = capsys.readouterr().out
    assert "✅" in out and (home / "plugins" / "nyx" / "__init__.py").exists()
    argv = [json.loads(l) for l in open(calls)]
    assert ["config", "set", "memory.provider", "nyx"] in argv and ["plugins", "enable", "nyx-reveal"] in argv
    assert json.load(open(home / "nyx.json"))["vault"] == "on"
    # 已经是 nyx：不再改；--check 不改任何东西
    n = len(argv)
    assert I.main(["--hermes-home", str(home), "--check"]) == 0
    assert [json.loads(l) for l in open(calls)][n:] == []
    # 旧版安装留下的网关插件：要报出来
    os.makedirs(home / "plugins" / "sandglass")
    assert I.main(["--hermes-home", str(home), "--no-config"]) == 1
    assert "旧版安装留下的" in capsys.readouterr().out


def test_module_entry_is_inert_on_import():
    """旧说明让人把整个目录拷进 plugins/nyx/：Hermes 会把每个 .py 当子模块执行，__main__ 不能在 import 时做事。"""
    import nexsandglass_hermes
    path = os.path.join(os.path.dirname(nexsandglass_hermes.__file__), "__main__.py")
    spec = importlib.util.spec_from_file_location("_hermes_user_memory.nyx.__main__", path)
    spec.loader.exec_module(importlib.util.module_from_spec(spec))      # 不退出、不打印用法
