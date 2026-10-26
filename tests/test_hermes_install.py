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
from tests.test_hermes_provider import env  # noqa: F401  复用隔离目录


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
    assert did == "created" and target == os.path.join(str(tmp_path), "plugins", "nyx")
    src = open(os.path.join(target, "__init__.py"), encoding="utf-8").read()
    assert "register_memory_provider" in src[:8192], "Hermes 的 _is_memory_provider_dir 只看这个词"
    yml = open(os.path.join(target, "plugin.yaml"), encoding="utf-8").read()
    assert "name: nyx" in yml and "kind: exclusive" in yml, "普通插件加载器跳过它，只由记忆插件加载器加载"
    p = _load_like_hermes(target)
    assert p is not None and p.name == "nexsandglass"
    assert I.write_shim(str(tmp_path))[1] == "updated"



def test_entry_file_from_before_v7_16_is_still_ours(tmp_path):
    """v7.16.0 之前写的入口文件带的是中文标记：照样认成 nyx 的入口文件，重装是「更新」而不是当成别人的目录。"""
    target, _ = I.write_shim(str(tmp_path))
    path = os.path.join(target, "__init__.py")
    old = open(path, encoding="utf-8").read().replace(I._SHIM_MARK, "nexsandglass_hermes 安装的入口文件")
    open(path, "w", encoding="utf-8").write(old)
    assert I._is_shim(old) and I.write_shim(str(tmp_path))[1] == "updated"

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
    assert I.write_shim(home)[1] == "replaced"
    assert sorted(os.listdir(old)) == ["__init__.py", "cli.py", "plugin.yaml"], "升级之后旧拷贝还在跑旧代码 —— 换成入口文件"
    other = os.path.join(home, "plugins", "nyx2")
    os.makedirs(other)
    open(os.path.join(other, "__init__.py"), "w").write("# 别人的插件\n")
    with pytest.raises(SystemExit, match="is not nyx's plugin"):
        I.write_shim(home, name="nyx2")
    assert I.write_shim(home, name="nyx2", force=True)[1] == "replaced"


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
    (d / "hermes").write_text("#!/usr/bin/env python3\n")                 # env 式的 #!：运行时用的就是 PATH 上那个
    import shutil
    assert I.find_hermes() == (str(d / "hermes"), shutil.which("python3"))
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
    later = [json.loads(l) for l in open(calls)][n:]
    assert all(a[-1] == "--help" or a[:2] in (["config", "get"], ["memory", "status"]) for a in later), \
        f"--check 只能读：{later}"
    # 旧版安装留下的网关插件：要报出来
    os.makedirs(home / "plugins" / "sandglass")
    assert I.main(["--hermes-home", str(home), "--no-config"]) == 1
    assert "left from an old install" in capsys.readouterr().out


def test_module_entry_is_inert_on_import():
    """旧说明让人把整个目录拷进 plugins/nyx/：Hermes 会把每个 .py 当子模块执行，__main__ 不能在 import 时做事。"""
    import nexsandglass_hermes
    path = os.path.join(os.path.dirname(nexsandglass_hermes.__file__), "__main__.py")
    spec = importlib.util.spec_from_file_location("_hermes_user_memory.nyx.__main__", path)
    spec.loader.exec_module(importlib.util.module_from_spec(spec))      # 不退出、不打印用法


# ── 新版 Hermes（v7.15.1）：启动文件是 shell 包装；插件依赖交给 hermes plugins install ──────────────

def _write(p, text, mode=0o755):
    p.write_text(text, encoding="utf-8")
    p.chmod(mode)
    return str(p)


def test_interpreter_behind_shell_wrappers(tmp_path, monkeypatch):
    """新版官方安装、/usr/local/bin/hermes、profile 包装都是 #!/bin/sh：v7.15.0 把 /bin/sh 当成了 Hermes 的 Python。"""
    py = sys.executable
    direct = _write(tmp_path / "direct", f"#!{py}\nimport sys\n")
    assert I._interpreter_of(direct) == py
    sh = _write(tmp_path / "hermes", f"#!/bin/sh\nexec '{py}' -I -c 'import sys; sys.exit(0)' \"$@\"\n")
    assert I._interpreter_of(sh) == py
    prof = _write(tmp_path / "work", f"#!/bin/sh\nexec {sh} -p work \"$@\"\n")     # profile 包装：exec 另一个 hermes
    assert I._interpreter_of(prof) == py
    monkeypatch.setenv("NYX_TEST_PYDIR", os.path.dirname(py))
    var = _write(tmp_path / "var", "#!/bin/sh\nexec \"$NYX_TEST_PYDIR/" + os.path.basename(py) + "\" -I -c x \"$@\"\n")
    assert I._interpreter_of(var) == os.path.join(os.path.dirname(py), os.path.basename(py))
    assert I._interpreter_of(_write(tmp_path / "junk", "#!/bin/sh\necho hi\n")) is None
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    assert I.find_hermes() == (sh, py)


def _fake_new_hermes(tmp_path, plugin_src):
    """会装插件依赖的 Hermes（plugins install 有 --no-deps）：plugins install 把插件目录拷进 $HERMES_HOME/plugins/nyx。"""
    d = tmp_path / "newbin"
    d.mkdir()
    calls = tmp_path / "calls.jsonl"
    _write(d / "hermes", f"""#!{sys.executable}
import json, os, shutil, sys
home = os.environ["HERMES_HOME"]
a = sys.argv[1:]
with open({str(calls)!r}, "a") as f:
    f.write(json.dumps(a) + "\\n")
cfg = os.path.join(home, "provider.txt")
if a[:3] == ["plugins", "install", "--help"]:
    print("usage: hermes plugins install [--force] [--no-deps] [--enable | --no-enable] identifier")
elif a[:2] == ["plugins", "install"]:
    t = os.path.join(home, "plugins", "nyx")
    if os.path.exists(t) and "--force" not in a:
        print("Error: already installed"); sys.exit(1)
    shutil.rmtree(t, ignore_errors=True)
    shutil.copytree({plugin_src!r}, t)
    print("✓ Installed Python dependencies: nyx-memory>=7.15.0")
elif a[:3] == ["config", "get", "memory.provider"]:
    print(open(cfg).read() if os.path.exists(cfg) else "")
elif a[:2] == ["config", "set"]:
    open(cfg, "w").write(a[3])
elif a[:2] == ["memory", "status"]:
    p = open(cfg).read() if os.path.exists(cfg) else "built-in"
    ok = os.path.exists(os.path.join(home, "plugins", "nyx", "__init__.py"))
    print(f"  Provider:  {{p}}\\n  Status:    {{'available ✓' if ok else 'not available'}}")
""")
    return d, calls


def test_new_hermes_gets_its_own_plugin_install(tmp_path, monkeypatch, capsys):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d, calls = _fake_new_hermes(tmp_path, os.path.join(root, "hermes-plugin"))
    monkeypatch.setenv("PATH", str(d) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setattr(I, "pip_install", lambda *a, **k: pytest.fail("新版 Hermes 不许往它的环境里 pip 装"))
    home = tmp_path / "hh"
    I.write_shim(str(home))                                   # v7.15.0 的安装命令留下的入口文件：要接管过来
    assert I.main(["--hermes-home", str(home), "--vault"]) == 0
    out = capsys.readouterr().out
    argv = [json.loads(l) for l in open(calls)]
    assert ["plugins", "install", I.SOURCE, "--enable", "--force"] in argv
    assert ["config", "set", "memory.provider", "nyx"] in argv and ["plugins", "enable", "nyx-reveal"] in argv
    assert "the provider is nyx, available" in out and json.load(open(home / "nyx.json"))["vault"] == "on"
    import re
    assert len(re.findall(r"^python_dependencies:", open(home / "plugins" / "nyx" / "plugin.yaml", encoding="utf-8").read(),
                          re.M)) == 1
    assert I.main(["--hermes-home", str(home), "--check"]) == 0
    assert I.main(["--hermes-home", str(tmp_path / "h2"), "--vector"]) == 0
    assert ["plugins", "install", I.SOURCE + "-vector", "--enable"] in [json.loads(l) for l in open(calls)]
    other = tmp_path / "h3" / "plugins" / "nyx"
    other.mkdir(parents=True)
    (other / "__init__.py").write_text("# 别人的\n")
    with pytest.raises(SystemExit, match="is not nyx's plugin"):
        I.main(["--hermes-home", str(tmp_path / "h3")])


@pytest.mark.parametrize("sub,dep", [("hermes-plugin", "nyx-memory>="), ("hermes-plugin-vector", "nyx-memory[vector]>=")])
def test_repo_plugin_dirs(tmp_path, sub, dep):
    """hermes plugins install sixgodgit/nyx/hermes-plugin 装的就是这两个目录：Hermes 认得出是记忆插件、依赖写对、加载得出来。"""
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src_dir = os.path.join(root, sub)
    src = open(os.path.join(src_dir, "__init__.py"), encoding="utf-8").read()
    assert "register_memory_provider" in src[:8192]
    top = [l for l in src.splitlines() if re.match(r"^(from|import)\s", l)]
    assert not any("nexsandglass" in l for l in top), "顶层 import nexsandglass 会把数据目录定死；只能在 try 里 import nexsandglass_hermes"
    yml = open(os.path.join(src_dir, "plugin.yaml"), encoding="utf-8").read()
    assert re.search(r"^name:\s*nyx\s*$", yml, re.M), "目录名由 manifest 的 name 定：$HERMES_HOME/plugins/nyx"
    m = re.search(r"^\s*-\s*(\S+)\s*$", yml.split("python_dependencies:", 1)[1], re.M)
    assert m and m.group(1).startswith(dep), m
    floor = tuple(int(x) for x in re.search(r">=([\d.]+)", m.group(1)).group(1).split("."))
    assert floor <= tuple(int(x) for x in I._version().split(".")), "最低版本不能高于当前版本"
    assert re.search(r",<\d+", m.group(1)), "依赖要有上限（Hermes 插件目录规则 10）"
    assert re.search(r'^requires_hermes:\s*">=\d+\.\d+\.\d+"', yml, re.M), "SemVer 下限（规则 14）"
    assert os.path.exists(os.path.join(src_dir, "README.md")), "插件目录页显示的就是这里的 README"
    assert re.search(r"^version:\s*(\S+)", yml, re.M).group(1) == I._version()
    import shutil
    t = tmp_path / "plugins" / "nyx"
    shutil.copytree(src_dir, t)
    p = _load_like_hermes(str(t))
    assert p is not None and p.name == "nexsandglass"


def _defines_memory_provider(path: str) -> bool:
    """Hermes 主线版 plugins/memory/__init__.py 的 _defines_memory_provider：解析（不 import）插件目录顶层的 .py，
    要有 register_memory_provider(...) 调用或 MemoryProvider 子类；注释、文档字符串里的字不算。"""
    import ast
    import glob
    for module in sorted(glob.glob(os.path.join(path, "*.py"))):
        tree = ast.parse(open(module, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and "register_memory_provider" in (
                    getattr(node.func, "attr", None), getattr(node.func, "id", None)):
                return True
            if isinstance(node, ast.ClassDef) and any(
                    (getattr(b, "attr", None) or getattr(b, "id", None)) == "MemoryProvider" for b in node.bases):
                return True
    return False


@pytest.mark.parametrize("sub", ["hermes-plugin", "hermes-plugin-vector"])
def test_new_hermes_recognises_memory_provider(tmp_path, sub):
    """v7.15.2 的入口文件只在注释里提到 register_memory_provider：主线版 Hermes 把它当普通插件，
    `hermes plugins install --enable` 不设 memory.provider（实测）。调用要写在插件目录自己的文件里。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert _defines_memory_provider(os.path.join(root, sub))
    target, _ = I.write_shim(str(tmp_path))                  # 老版本 Hermes 的安装路径写出的入口文件
    assert _defines_memory_provider(target)
    after = open(os.path.join(root, sub, "after-install.md"), encoding="utf-8").read()
    assert after.startswith("## Nyx is installed") and "hermes config set memory.provider nyx" in after.split("---")[0]


# ── hermes nyx …（v7.15.2）──────────────────────────────────────────────

def _hermes_parser():
    """Hermes 的做法（hermes_cli/main.py 的 _attach_plugin_cli_command）：顶层 subparsers 里加 nyx，交给 register_cli。"""
    import argparse
    from nexsandglass_hermes import cli
    root = argparse.ArgumentParser(prog="hermes")
    subs = root.add_subparsers(dest="command")
    p = subs.add_parser("nyx")
    cli.register_cli(p)
    p.set_defaults(func=cli.nyx_command)
    return root


def test_hermes_nyx_dispatch(env, tmp_path, monkeypatch, capsys):  # noqa: F811
    import nexsandglass_hermes as nh
    from tests.test_secret_filter import HF
    from nexsandglass.core import secret_vault
    hh = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(hh))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    # nyx_command 会直接改进程环境（NYX_VIA_HERMES、按 nyx.json 补 NYX_VAULT）。先用 setenv 登记，结束时 monkeypatch 还原；
    # delenv 一个本来不存在的变量不登记还原，改动会漏到后面的测试里（实测）
    for k in ("NYX_VAULT", "NYX_VIA_HERMES", "NYX_VAULT_REVEAL", "NYX_VAULT_KINDS", "NYX_L3_LLM"):
        monkeypatch.setenv(k, "")
    nh.save_config({"vault": "on"}, str(hh))                  # 开关在当前 profile 的 nyx.json 里
    if not secret_vault._have_crypto():
        pytest.skip("要装 cryptography")
    root = _hermes_parser()
    args = root.parse_args(["nyx", "status"])
    assert args.func(args) == 0 and "Vault: on" in capsys.readouterr().out, "nyx.json 的 vault: on 要生效"
    from tests.test_hermes_provider import _provider
    _provider().sync_turn(f"帮我配一下 Hugging Face，key 是 {HF}", "好的，记下了，之后需要随时问我")
    vid = secret_vault.entries()[0].id
    args = root.parse_args(["nyx", "vault", "show", f"v{vid}"])
    assert args.func(args) == 0 and capsys.readouterr().out.strip() == HF
    args = root.parse_args(["nyx", "doctor", "--json"])          # 选项原样交给那个工具自己的 argparse
    args.func(args)
    assert json.loads(capsys.readouterr().out)["checks"]["vault"]["state"] == "on"
    from nexsandglass.core import hints
    assert hints.cmd("vault", "show v3") == "hermes nyx vault show v3", "经 hermes nyx 进来：提示写成 hermes nyx …"
    monkeypatch.setenv("NYX_VIA_HERMES", "")
    assert hints.cmd("vault", "show v3") == "python3 -m nexsandglass.vault show v3"
    args = root.parse_args(["nyx"])
    assert args.func(args) == 2


@pytest.mark.parametrize("sub", ["hermes-plugin", "hermes-plugin-vector"])
def test_plugin_cli_shims(sub, tmp_path, monkeypatch, capsys):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, sub, "cli.py")
    assert open(path, encoding="utf-8").read() == I.CLI_SHIM, "仓库里的 cli.py 和 install 写的入口文件一字不差"
    # Hermes 的加载方式：_hermes_user_memory.<名字>.cli，只载入 cli.py、不执行插件的 __init__.py
    spec = importlib.util.spec_from_file_location("_hermes_user_memory.nyx.cli", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from nexsandglass_hermes import cli
    assert mod.register_cli is cli.register_cli and mod.nyx_command is cli.nyx_command
    monkeypatch.setitem(sys.modules, "nexsandglass_hermes.cli", None)     # Hermes 的环境里没有 nyx-memory
    mod2 = importlib.util.module_from_spec(importlib.util.spec_from_file_location("_x.cli", path))
    mod2.__spec__.loader.exec_module(mod2)

    class P:
        def set_defaults(self, **kw):
            self.kw = kw
    p = P()
    mod2.register_cli(p)
    assert p.kw["func"](None) == 1 and "hermes plugins install" in capsys.readouterr().out
    target, _ = I.write_shim(str(tmp_path))
    assert open(os.path.join(target, "cli.py"), encoding="utf-8").read() == I.CLI_SHIM


def test_tool_hint_inside_hermes(monkeypatch):
    """agent 进程里（hermes_cli 已加载）：转告主人的命令是 hermes nyx …，新版 Hermes 的系统 Python 里没有 nyx。"""
    from nexsandglass.core import hints
    monkeypatch.delenv("NYX_VIA_HERMES", raising=False)
    monkeypatch.setitem(sys.modules, "hermes_cli", type(sys)("hermes_cli"))
    assert hints.cmd("doctor") == "hermes nyx doctor"


def test_language_follows_nyx_json(tmp_path, monkeypatch, capsys):
    """hermes nyx …：没设 NYX_LANG 时按 nyx.json 的 lang；设了 NYX_LANG 以它为准（v7.16.0）。"""
    from nexsandglass_hermes import cli
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("NYX_LANG", raising=False)
    for k in ("LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LANG", "C.UTF-8")
    assert cli._t("en", "zh") == "en"
    (tmp_path / "nyx.json").write_text('{"lang": "zh"}', encoding="utf-8")
    assert cli._t("en", "zh") == "zh"
    monkeypatch.setenv("NYX_LANG", "en")
    assert cli._t("en", "zh") == "en"
    monkeypatch.setenv("NYX_LANG", "auto")
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    (tmp_path / "nyx.json").write_text('{}', encoding="utf-8")
    assert cli._t("en", "zh") == "zh", "auto 按系统语言"
