"""
python3 -m nexsandglass_hermes install —— 把 Nyx 装成 Hermes 的记忆插件（v7.15.1）

    python3 -m nexsandglass_hermes install             # 一步装好，然后重启 Hermes
    python3 -m nexsandglass_hermes install --vault     # 同时打开密钥保险箱（启用 nyx-reveal、nyx.json 里 vault: on）
    python3 -m nexsandglass_hermes install --vector    # 同时装语义检索的本地模型（首次运行下载约 470MB）
    python3 -m nexsandglass_hermes install --check     # 只检查，不改任何东西

新版 Hermes 其实不需要它：hermes plugins install sixgodgit/nyx/hermes-plugin --enable 就行（只用到 hermes 命令）。
这个命令新老版本都认，按 Hermes 的能力分两条路：

**新版**（v2026.9 起：plugins install 有 --no-deps，会装插件声明的依赖）—— 交给 hermes plugins install。
  Hermes 的文档明确要求「不要用 pip 往 Hermes 的环境里注入 provider」：新版用自己的包管理器按锁文件同步环境，
  pip 装进去的会被同步掉；插件 plugin.yaml 里声明的 python_dependencies 才会在 hermes update 之后重新装上。
  在终端里运行时 Hermes 的依赖确认由你回答；不在终端里时代为加 --yes-deps（主线版非交互时不加就拒绝）。

**老版本**（如 PyPI 上的 0.19.x：只按目录找记忆插件、不看 pip 入口点、plugins install 不装依赖）：
  1. 找到 Hermes 自己用的 Python：pip 装的看 `hermes` 第一行的 #!；`#!/bin/sh` 包装脚本看里面 exec 的那个
     （v7.15.0 把 /bin/sh 当成了 Python —— 在新版的启动文件上直接装不上）。缺 nyx-memory 就装进去（同一个版本）
  2. 在 $HERMES_HOME/plugins/nyx/ 放一个只有几行的入口文件，转到 pip 装的 nexsandglass_hermes
  3. hermes config set memory.provider nyx（--vault 时再 hermes plugins enable nyx-reveal，nyx.json 里 vault: on）
  4. 用 Hermes 自己的加载器（plugins.memory.load_memory_provider）在 Hermes 的 Python 里真加载一次，加载不出来就报错

只用标准库（这个包顶层不许 import nexsandglass，见 __init__.py）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from typing import List, Optional, Tuple

from nexsandglass_hermes.cli import _t

NAME = "nyx"
_SHIM_MARK = "entry file written by nexsandglass_hermes"
_SHIM_MARKS = (_SHIM_MARK, "nexsandglass_hermes 安装的入口文件")      # v7.16.0 之前写的入口文件

SHIM = '''"""Nyx — Hermes memory provider entry ({mark}, v{version})

This Hermes finds memory providers by directory ($HERMES_HOME/plugins/<name>/), not by pip entry points, so this
file only forwards: the real code is the pip-installed nyx-memory, and this file does not change when it is upgraded.
Regenerate it with: python3 -m nexsandglass_hermes install
"""
import logging
import sys

try:
    from nexsandglass_hermes import apply_data_dir as _apply_data_dir
    _missing = None
except ImportError as e:
    _apply_data_dir, _missing = None, e


def register(ctx):
    if _missing is not None:
        logging.getLogger("nyx").error(
            "Nyx: nyx-memory is not installed in the Python Hermes uses (%s; %s). Run: hermes plugins install "
            "sixgodgit/nyx/hermes-plugin --force (older Hermes: %s -m pip install nyx-memory), then restart Hermes",
            sys.executable, _missing, sys.executable)
        return
    _apply_data_dir()
    from nexsandglass.core.memory_provider import NexSandglassProvider
    # The call has to be in this file: newer Hermes recognises memory providers by a register_memory_provider(...)
    # call in the syntax tree; words in comments do not count
    ctx.register_memory_provider(NexSandglassProvider())
'''

CLI_SHIM = '''"""hermes nyx … — Nyx's command line (vault / doctor / scrub / repair / status).

Hermes loads register_cli from this file when nyx is the active memory provider. It only forwards: the real code
is in nyx-memory (nexsandglass_hermes/cli.py).
"""
try:
    from nexsandglass_hermes.cli import nexsandglass_command, nyx_command, register_cli  # noqa: F401
except ImportError as _e:
    _missing = _e

    def nyx_command(args):
        print(f"Nyx: nyx-memory is not installed in Hermes's environment ({_missing}). "
              "Run: hermes plugins install sixgodgit/nyx/hermes-plugin --force, then restart Hermes")
        return 1

    def register_cli(subparser):
        subparser.set_defaults(func=nyx_command)

    nexsandglass_command = nyx_command
'''

PLUGIN_YAML = """name: {name}
version: {version}
kind: exclusive
description: >-
  Nyx — local, single-user agent memory. Entry file; the real code is the pip-installed nyx-memory.
"""


# ══════════════════════════════════════════════════════════
# 找东西
# ══════════════════════════════════════════════════════════

# 版本按源码里的 __version__ 读（不 import nexsandglass）：可编辑安装的元数据可能是旧的
_VERSION_SNIPPET = (
    "import importlib.util, os, re; s = importlib.util.find_spec('nexsandglass'); "
    "t = open(os.path.join(os.path.dirname(s.origin), 'core', 'sandglass_paths.py'), encoding='utf-8').read(); "
    "print(re.search(r\"__version__ = '([^']+)'\", t).group(1))")


def _version() -> str:
    try:
        import importlib.util
        import re
        spec = importlib.util.find_spec("nexsandglass")
        with open(os.path.join(os.path.dirname(spec.origin), "core", "sandglass_paths.py"), encoding="utf-8") as f:
            return re.search(r"__version__ = '([^']+)'", f.read()).group(1)
    except Exception:
        return "0"


def default_hermes_home() -> str:
    """与 hermes_constants.get_hermes_home 一致：HERMES_HOME > 平台默认（Windows 是 %LOCALAPPDATA%\\hermes）。"""
    env = (os.environ.get("HERMES_HOME") or "").strip()
    if env:
        return os.path.abspath(os.path.expanduser(env))
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "hermes")
    return os.path.join(os.path.expanduser("~"), ".hermes")


def _interpreter_of(path: str, depth: int = 0) -> Optional[str]:
    """一个 hermes 启动文件最终用哪个 Python。

    pip 装的：第一行 #!/path/to/python。新版官方安装（以及 /usr/local/bin/hermes、profile 包装脚本）是
    #!/bin/sh 包装：exec <python> -I -c …，或者 exec 另一个 hermes —— 把 /bin/sh 当成 Python 就全错了。
    """
    import shlex
    real = os.path.realpath(path)
    try:
        with open(real, "rb") as f:
            text = f.read(8192).decode("utf-8", "replace")
    except OSError:
        return None
    lines = text.splitlines()
    if not lines or not lines[0].startswith("#!"):
        return None
    try:
        parts = shlex.split(lines[0][2:])
    except ValueError:
        parts = lines[0][2:].split()
    if not parts:
        return None
    prog = parts[0]
    if os.path.basename(prog) == "env":
        prog = shutil.which(parts[1]) if len(parts) > 1 else None
        if not prog:
            return None
    base = os.path.basename(prog)
    if base.startswith("python"):
        return prog if os.path.exists(prog) else None
    if base in ("sh", "bash", "dash", "zsh") and depth < 3:
        for line in lines[1:]:
            line = line.strip()
            if not line.startswith("exec "):
                continue
            try:
                toks = shlex.split(line[5:])
            except ValueError:
                continue
            if not toks:
                continue
            target = os.path.expanduser(os.path.expandvars(toks[0]))
            if os.path.basename(target).startswith("python"):
                return target if os.path.exists(target) else None
            if os.path.exists(target):
                return _interpreter_of(target, depth + 1)
    return None


def find_hermes(explicit_python: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """(hermes 命令, Hermes 用的 Python)。找不到的那个是 None。"""
    exe = shutil.which("hermes")
    if explicit_python:
        return exe, explicit_python
    if not exe:
        return None, None
    py = _interpreter_of(exe)
    if py:
        return exe, py
    d = os.path.dirname(os.path.realpath(exe))   # Windows：venv\\Scripts\\hermes.exe 旁边就是 python.exe
    for cand in ("python.exe", "python3", "python"):
        p = os.path.join(d, cand)
        if os.path.exists(p):
            return exe, p
    return exe, None


def _same_python(py: str) -> bool:
    """同一个环境：比 sys.prefix，不比可执行文件 —— 虚拟环境里的 python 就是指向系统 Python 的符号链接。"""
    r = _run([py, "-c", "import sys; print(sys.prefix)"], timeout=60)
    return r.returncode == 0 and os.path.realpath(r.stdout.strip()) == os.path.realpath(sys.prefix)


_NEUTRAL: List[str] = []


def _run(cmd: List[str], env: Optional[dict] = None, timeout: int = 900) -> subprocess.CompletedProcess:
    """看 Hermes 那个 Python 自己装了什么：去掉 PYTHONPATH，并且在一个空目录里跑 ——
    python -c 会把当前目录放进 sys.path，在仓库目录里运行时（install.sh 会先 cd 过去）一 import 就成功了，是误报（实测）。"""
    e = dict(os.environ if env is None else env)
    e.pop("PYTHONPATH", None)
    if not _NEUTRAL:
        import tempfile
        _NEUTRAL.append(tempfile.mkdtemp(prefix="nyx-install-"))
    # stdin 接空：hermes plugins enable 会问「允许覆盖内置工具吗」，输出被收走时那个提问看不见，会一直等
    return subprocess.run(cmd, capture_output=True, text=True, env=e, timeout=timeout, stdin=subprocess.DEVNULL,
                          cwd=_NEUTRAL[0])


_EXTRA_PROBE = {"vault": "cryptography.hazmat.primitives.ciphers.aead", "vector": "sentence_transformers"}


def missing_extras(py: str, extras: List[str]) -> List[str]:
    """Hermes 的 Python 里还缺哪些可选依赖。"""
    return [x for x in extras if _run([py, "-c", f"import {_EXTRA_PROBE[x]}"], timeout=120).returncode != 0]


def nyx_in(py: str) -> Optional[str]:
    """Hermes 的 Python 里装的 nyx-memory 版本；没装返回 None。"""
    r = _run([py, "-c", "import nexsandglass_hermes; " + _VERSION_SNIPPET], timeout=120)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def _local_source() -> Optional[str]:
    """安装命令自己是不是从源码目录运行的（install.sh 会 cd 到仓库里再跑）：是就返回仓库根目录。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(root, "pyproject.toml"), encoding="utf-8") as f:
            txt = f.read()
    except OSError:
        return None
    import re
    return root if re.search(r'^name\s*=\s*"nyx-memory"', txt, re.M) else None


def _file_url_path(url: str) -> Optional[str]:
    if not url.startswith("file://"):
        return None
    from urllib.parse import unquote, urlparse
    path = unquote(urlparse(url).path)
    return path[1:] if sys.platform == "win32" and path.startswith("/") else path


def _install_spec(extras: List[str]) -> List[str]:
    """装进 Hermes 的 Python 用什么，和当前这份保持一致：
      - 当前装的是本地源码（pip install . / -e .）→ 装同一份源码（可编辑的仍然可编辑）
      - 从源码目录运行、但当前环境的安装记录对不上 → 装这份源码
      - 否则 → PyPI 上的同一个版本
    不能只看 importlib.metadata.distribution：在仓库目录里运行时它先找到仓库里过期的 egg-info（实测）。
    """
    ex = f"[{','.join(extras)}]" if extras else ""
    src = _local_source()
    try:
        from importlib.metadata import distributions
        infos = [json.loads(d.read_text("direct_url.json") or "{}") for d in distributions(name="nyx-memory")]
    except Exception:
        infos = []
    for info in infos:
        path = _file_url_path(info.get("url") or "")
        if path and (src is None or os.path.realpath(path) == os.path.realpath(src)):
            return (["-e"] if (info.get("dir_info") or {}).get("editable") else []) + [path + ex]
    if src:
        return [src + ex]
    return [f"nyx-memory{ex}=={_version()}"]


def pip_install(py: str, extras: List[str]) -> Tuple[bool, str]:
    """把 nyx-memory 装进 Hermes 的 Python：有 pip 用 pip；没有 pip（uv 建的虚拟环境）用 uv，再不行先 ensurepip。"""
    spec = _install_spec(extras)
    if _run([py, "-m", "pip", "--version"], timeout=60).returncode == 0:
        cmd = [py, "-m", "pip", "install", *spec]
    elif shutil.which("uv"):
        cmd = [shutil.which("uv"), "pip", "install", "--python", py, *spec]
    else:
        boot = _run([py, "-m", "ensurepip", "--upgrade", "--default-pip"])
        if boot.returncode != 0:
            return False, (boot.stderr or boot.stdout)[-800:]
        cmd = [py, "-m", "pip", "install", *spec]
    print("  $ " + " ".join(cmd))
    r = _run(cmd)
    return r.returncode == 0, (r.stderr or r.stdout)[-1200:]


# ══════════════════════════════════════════════════════════
# 写入口文件
# ══════════════════════════════════════════════════════════

def _init_src(path: str) -> Optional[str]:
    try:
        with open(os.path.join(path, "__init__.py"), encoding="utf-8", errors="replace") as f:
            return f.read(16384)
    except OSError:
        return None


def _ours(path: str) -> bool:
    """这个目录是不是 nyx 自己的（新的入口文件，或旧说明里整个拷过去的 nexsandglass_hermes）。"""
    src = _init_src(path)
    if src is None:
        return not os.listdir(path)
    return any(m in src for m in _SHIM_MARKS) or "nexsandglass_hermes" in src or "NexSandglassProvider" in src


def _is_shim(src: Optional[str]) -> bool:
    return bool(src) and any(m in src for m in _SHIM_MARKS)


def _not_ours(target: str) -> str:
    return _t(f"❌ {target} already exists and is not nyx's plugin; not overwriting it. If it is unused, add --force",
              f"❌ {target} 已经存在，而且不是 nyx 的插件；不覆盖。确认没用的话加 --force")


def _leftover(old: str) -> str:
    return _t(f"{old} is left from an old install: delete it (rm -rf \"{old}\"), or the same message may be "
              "remembered twice",
              f"旧版安装留下的 {old}：请删掉（rm -rf \"{old}\"），否则同一句话可能被记两遍")


_DID = {"created": ("created", "新建"), "updated": ("updated", "更新"),
        "replaced": ("replaced the old full copy with", "替换旧的整目录拷贝成")}


def write_shim(home: str, name: str = NAME, force: bool = False) -> Tuple[str, str]:
    """返回 (目录, 做了什么：created / updated / replaced)。"""
    target = os.path.join(home, "plugins", name)
    did = "created"
    if os.path.isdir(target):
        if not (force or _ours(target)):
            raise SystemExit(_not_ours(target))
        did = "updated" if _is_shim(_init_src(target)) else "replaced"
        shutil.rmtree(target)
    os.makedirs(target)
    ver = _version()
    with open(os.path.join(target, "__init__.py"), "w", encoding="utf-8") as f:
        f.write(SHIM.format(mark=_SHIM_MARK, version=ver))
    with open(os.path.join(target, "plugin.yaml"), "w", encoding="utf-8") as f:
        f.write(PLUGIN_YAML.format(name=name, version=ver))
    with open(os.path.join(target, "cli.py"), "w", encoding="utf-8") as f:
        f.write(CLI_SHIM)                       # hermes nyx …（v7.15.2）
    return target, did


def legacy_dirs(home: str) -> List[str]:
    """v3.x 的安装脚本复制过的旧插件：留着会和现在的插件同时生效（旧网关插件会把每句话再记一遍）。"""
    return [p for p in (os.path.join(home, "plugins", "memory", "nexsandglass"), os.path.join(home, "plugins", "sandglass"))
            if os.path.exists(p)]


# ══════════════════════════════════════════════════════════
# 配置 / 验证
# ══════════════════════════════════════════════════════════

def _hermes(exe: str, home: str, *args: str) -> subprocess.CompletedProcess:
    return _run([exe, *args], env={**os.environ, "HERMES_HOME": home}, timeout=120)


_VERIFY = r"""
import importlib.metadata as m, json, os, sys
os.environ["HERMES_HOME"] = sys.argv[1]
out = {}
try:
    from plugins.memory import find_provider_dir, load_memory_provider
except Exception as e:
    print(json.dumps({"hermes": False, "error": repr(e)})); sys.exit(0)
d = find_provider_dir(sys.argv[2])
out["dir"] = str(d) if d else None
p = load_memory_provider(sys.argv[2]) if d else None
out["provider"] = type(p).__name__ if p else None
out["available"] = bool(p and p.is_available())
out["reveal_entry_point"] = any(e.name == "nyx-reveal" for e in m.entry_points(group="hermes_agent.plugins"))
print(json.dumps(out))
"""


def verify(py: str, home: str, name: str = NAME) -> dict:
    """用 Hermes 自己的加载器在 Hermes 的 Python 里加载一次。"""
    r = _run([py, "-c", _VERIFY, home, name], timeout=180)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return {"hermes": False, "error": (r.stderr or r.stdout)[-800:]}


# ══════════════════════════════════════════════════════════
# 新版 Hermes：交给 hermes plugins install
# ══════════════════════════════════════════════════════════

SOURCE = "sixgodgit/nyx/hermes-plugin"


def plugin_install_help(exe: str, home: str) -> str:
    r = _hermes(exe, home, "plugins", "install", "--help")
    return (r.stdout + r.stderr) if r.returncode == 0 else ""


def supports_plugin_deps(exe: str, home: str) -> bool:
    """这个 Hermes 的 plugins install 会不会装插件声明的 Python 依赖（有 --no-deps 选项的就会：v2026.9 起）。

    会的话就该交给它：Hermes 的文档明确说不要用 pip 往它的环境里塞 provider —— 新版用自己的包管理器按锁文件
    同步环境，pip 装进去的东西会被同步掉；插件声明的依赖则在 hermes update 之后重新应用。
    """
    return "--no-deps" in plugin_install_help(exe, home)


def _memory_status_ok(exe: str, home: str) -> Tuple[bool, str]:
    import re
    r = _hermes(exe, home, "memory", "status")
    out = r.stdout + r.stderr
    ok = bool(re.search(r"Provider:\s+nyx\b", out)) and "available" in out and "not available" not in out
    return ok, out


def install_via_hermes(exe: str, home: str, args) -> int:
    problems: List[str] = []
    target = os.path.join(home, "plugins", NAME)
    print(_t("  This Hermes installs the dependencies plugins declare: handing over to hermes plugins install "
             "(nyx-memory goes into Hermes's own environment and comes back after hermes update)",
             "  这个 Hermes 自己会装插件声明的依赖：交给 hermes plugins install"
             "（nyx-memory 装进 Hermes 自己的环境，hermes update 之后重新应用）"))
    if args.check:
        ok, out = _memory_status_ok(exe, home)
        print(_OK_STATUS() if ok else _t(
            "❌ hermes memory status: the provider is not nyx, or it is not available. Run: python3 -m nexsandglass_hermes install",
            "❌ hermes memory status 里 provider 不是 nyx 或不可用：运行 python3 -m nexsandglass_hermes install"))
        return 0 if ok else 1
    cmd = ["plugins", "install", args.source, "--enable"]
    if os.path.isdir(target):
        if not (args.force or _ours(target)):
            raise SystemExit(_not_ours(target))
        cmd.append("--force")                   # 以前的入口文件 / 整目录拷贝：换成 Hermes 记账的安装
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if not interactive and "--yes-deps" in plugin_install_help(exe, home):
        # 主线版 Hermes 装依赖要用户确认，非交互时不加这个就直接拒绝。依赖只有 nyx-memory（来自 PyPI）
        cmd.append("--yes-deps")
        print(_t("  (not in a terminal: answering the dependency prompt with --yes-deps; the dependency is nyx-memory "
                 "from PyPI)", "  （不在终端里：代为回答依赖确认 --yes-deps；依赖是 PyPI 上的 nyx-memory）"))
    print("→ hermes " + " ".join(cmd))
    if interactive:
        # 在终端里：Hermes 的提问（依赖确认、安全扫描）由你自己回答
        rc = subprocess.run([exe, *cmd], env={**os.environ, "HERMES_HOME": home}).returncode
    else:
        r = _hermes(exe, home, *cmd)
        rc = r.returncode
        tail = "\n".join(l for l in (r.stdout + r.stderr).splitlines()
                         if l.strip() and not l.lstrip().startswith(("│", "╭", "╰")))
        print("\n".join("    " + l for l in tail.splitlines()[-8:]))
    if rc != 0:
        problems.append(_t("hermes plugins install failed (see the output above)", "hermes plugins install 失败（见上面的输出）"))
    if not args.no_config:
        _configure(exe, home, args, problems)
    ok, out = _memory_status_ok(exe, home)
    if ok:
        print(_OK_STATUS())
    elif not problems:
        problems.append(_t("nyx is not available in hermes memory status:\n", "hermes memory status 里 nyx 不可用：\n")
                        + "\n".join("      " + l for l in out.splitlines()[-12:]))
    for old in legacy_dirs(home):
        problems.append(_leftover(old))
    if problems:
        return _report(problems)
    print(_DONE())
    return 0


def _OK_STATUS() -> str:
    return _t("✅ hermes memory status: the provider is nyx, available", "✅ hermes memory status：provider 是 nyx，可用")


def _DONE() -> str:
    return _t("\nDone. Restart Hermes (gateway: hermes gateway restart). Then hermes memory setup adjusts nyx's options.",
              "\n完成。重启 Hermes（网关：hermes gateway restart）。之后可以用 hermes memory setup 调 nyx 的选项。")


def _report(problems: List[str]) -> int:
    print(_t("\nStill to do:", "\n还有这些要处理："))
    for p in problems:
        print("  ❌ " + p)
    return 1


def _configure(exe: str, home: str, args, problems: List[str]) -> None:
    """memory.provider: nyx；--vault 时启用 nyx-reveal、nyx.json 里 vault: on。"""
    cur = _hermes(exe, home, "config", "get", "memory.provider")
    prev = cur.stdout.strip() if cur.returncode == 0 else ""
    if prev != NAME:
        r = _hermes(exe, home, "config", "set", "memory.provider", NAME)
        if r.returncode == 0:
            print(f"→ hermes config set memory.provider {NAME}"
                  + (_t(f" (it was {prev}; to go back: hermes config set memory.provider {prev})",
                        f"（原来是 {prev}；改回：hermes config set memory.provider {prev}）")
                     if prev and prev not in ("None", "null", "") else ""))
        else:
            problems.append(_t(f"hermes config set memory.provider {NAME} failed: ",
                               f"hermes config set memory.provider {NAME} 失败：") + (r.stderr or r.stdout)[-300:])
    if args.vault:
        r = _hermes(exe, home, "plugins", "enable", "nyx-reveal")
        if r.returncode == 0:
            print("→ hermes plugins enable nyx-reveal")
        else:
            problems.append(_t("hermes plugins enable nyx-reveal failed: ", "hermes plugins enable nyx-reveal 失败：")
                            + (r.stderr or r.stdout)[-300:])
        from nexsandglass_hermes import save_config
        save_config({"vault": "on"}, home)
        print(f"→ {os.path.join(home, 'nyx.json')}: vault: on")


# ══════════════════════════════════════════════════════════

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m nexsandglass_hermes install",
                                 description=_t("Install Nyx as Hermes's memory provider", "把 Nyx 装成 Hermes 的记忆插件"))
    ap.add_argument("--hermes-home", help=_t("Hermes home (default $HERMES_HOME or ~/.hermes); with several profiles, "
                                             "install once per profile",
                                             "Hermes 的 home（默认 $HERMES_HOME 或 ~/.hermes）；多个 profile 时每个各装一次"))
    ap.add_argument("--hermes-python", help=_t("the Python Hermes uses (default: from the #! line of the hermes command)",
                                               "Hermes 用的 Python（默认从 hermes 命令第一行的 #! 找）"))
    ap.add_argument("--vault", action="store_true", help=_t(
        "also turn on the secret vault: install [vault], enable nyx-reveal, vault: on in nyx.json",
        "同时打开密钥保险箱：装 [vault]、启用 nyx-reveal、nyx.json 里 vault: on"))
    ap.add_argument("--vector", action="store_true", help=_t("also install semantic search [vector]", "同时装语义检索 [vector]"))
    ap.add_argument("--no-pip", action="store_true", help=_t("do not install nyx-memory into Hermes's Python (only say how)",
                                                             "不往 Hermes 的 Python 里装 nyx-memory（只提示）"))
    ap.add_argument("--no-config", action="store_true", help=_t("do not change Hermes's config (only write the entry file)",
                                                                "不改 Hermes 的配置（只放入口文件）"))
    ap.add_argument("--force", action="store_true", help=_t("overwrite $HERMES_HOME/plugins/nyx even if it is something else",
                                                            "$HERMES_HOME/plugins/nyx 是别的东西时也覆盖"))
    ap.add_argument("--check", action="store_true", help=_t("only check, change nothing", "只检查，不改任何东西"))
    ap.add_argument("--source", default=SOURCE, help=_t(f"where newer Hermes installs the plugin from (default {SOURCE})",
                                                        f"新版 Hermes 从哪里装插件（默认 {SOURCE}）"))
    ap.add_argument("--legacy", action="store_true", help=_t(
        "do not hand over to hermes plugins install: pip install into Hermes's Python + write the entry file "
        "(the way for older Hermes)",
        "不交给 hermes plugins install：往 Hermes 的 Python 里 pip 装 + 放入口文件（老版本 Hermes 的做法）"))
    args = ap.parse_args(argv)

    home = os.path.abspath(os.path.expanduser(args.hermes_home)) if args.hermes_home else default_hermes_home()
    exe, py = find_hermes(args.hermes_python)
    extras = (["vault"] if args.vault else []) + (["vector"] if args.vector else [])
    ver = _version()
    print(f"nyx-memory {ver} → Hermes home {home}")
    print(_t(f"  hermes command: {exe or 'not found (not on PATH)'}", f"  hermes 命令：{exe or '没找到（不在 PATH 里）'}"))
    if exe and not args.legacy and supports_plugin_deps(exe, home):
        if args.vector and args.source == SOURCE:
            args.source = SOURCE + "-vector"
        return install_via_hermes(exe, home, args)
    same = bool(py and _same_python(py))
    print(_t(f"  Hermes's Python: {py or 'not found'}" + (" (this one)" if same else ""),
             f"  Hermes 的 Python：{py or '没找到'}" + ("（就是当前这个）" if same else "")))
    problems: List[str] = []

    # 1. Hermes 的 Python 里有没有 nyx-memory
    if py is None:
        problems.append(_t("could not find the Python Hermes uses: pass --hermes-python (the path after #! on the first "
                           "line of the hermes command file)",
                           "没找到 Hermes 用的 Python：用 --hermes-python 指定（hermes 命令文件第一行 #! 后面那个路径）"))
    else:
        have = nyx_in(py)
        miss = missing_extras(py, extras) if have else extras
        if have:
            print(_t(f"  Hermes's Python already has nyx-memory {have}" + (f", missing [{','.join(miss)}]" if miss else ""),
                     f"  Hermes 的 Python 里已有 nyx-memory {have}" + (f"，缺 [{','.join(miss)}]" if miss else "")))
        if have != ver or miss:
            what = (_t("nyx-memory is not installed", "没装 nyx-memory") if have is None else
                    _t(f"it has nyx-memory {have}", f"是 nyx-memory {have}") if have != ver else
                    _t(f"optional dependencies [{','.join(miss)}] are missing", f"缺可选依赖 [{','.join(miss)}]"))
            if args.check or args.no_pip:
                problems.append(_t(f"Hermes's Python: {what}: ", f"Hermes 的 Python 里{what}：") + f"{py} -m pip install "
                                f"'nyx-memory{('[' + ','.join(extras) + ']') if extras else ''}=={ver}'")
            else:
                print(_t(f"→ installing into Hermes's Python ({what})", f"→ 装进 Hermes 的 Python（{what}）"))
                ok, err = pip_install(py, extras)
                if not ok:
                    print(err)
                    problems.append(_t("installing nyx-memory into Hermes's Python failed (see the output above)",
                                       "往 Hermes 的 Python 里装 nyx-memory 失败（见上面的输出）"))

    # 2. 入口文件
    target = os.path.join(home, "plugins", NAME)
    if args.check:
        src = _init_src(target) if os.path.isdir(target) else None
        state = ("missing" if not os.path.isdir(target) else "shim" if _is_shim(src) else
                 "copy" if src and _ours(target) else "foreign")
        print(f"  {target}: " + {
            "missing": _t("not there", "没有"), "shim": _t("entry file", "入口文件"),
            "copy": _t("old full copy (running install again replaces it with the entry file)",
                       "旧的整目录拷贝（重新运行 install 会换成入口文件）"),
            "foreign": _t("not nyx's", "不是 nyx 的东西")}[state])
        if state != "shim":
            problems.append(_t(f"{target} is not the entry file: run python3 -m nexsandglass_hermes install",
                               f"{target} 不是入口文件：运行 python3 -m nexsandglass_hermes install"))
    else:
        os.makedirs(home, exist_ok=True)
        target, did = write_shim(home, force=args.force)
        print(_t(f"→ {_DID[did][0]} the entry file {target}", f"→ {_DID[did][1]}入口文件 {target}"))
    for old in legacy_dirs(home):
        problems.append(_leftover(old))

    # 3. Hermes 配置
    if not args.no_config and not args.check:
        if exe is None:
            problems.append(_t("hermes command not found, config not changed: put memory.provider: nyx in config.yaml "
                               "yourself", "没找到 hermes 命令，没改配置：手动在 config.yaml 里写 memory.provider: nyx"))
        else:
            _configure(exe, home, args, problems)

    # 4. 用 Hermes 自己的加载器验一次
    if py is not None:
        v = verify(py, home)
        if v.get("hermes") is False:
            problems.append(_t("cannot import Hermes in Hermes's Python (wrong --hermes-python?): ",
                               "在 Hermes 的 Python 里 import 不到 Hermes（--hermes-python 指错了？）：") + str(v.get("error"))[:300])
        elif v.get("provider") != "NexSandglassProvider":
            problems.append(_t(f"Hermes's loader did not load nyx (directory: {v.get('dir')}). "
                               f"See the error with {py} -c 'import nexsandglass_hermes'",
                               f"Hermes 的加载器没加载出 nyx（目录：{v.get('dir')}）。用 {py} -c 'import nexsandglass_hermes' 看报错"))
        else:
            print(_t(f"✅ Hermes's loader loads nyx ({v['dir']})" + (
                "" if v.get("available") else ", but is_available() is false: is the memory directory writable?"),
                f"✅ Hermes 的加载器能加载 nyx（{v['dir']}）" + (
                "" if v.get("available") else "，但 is_available() 为假：记忆目录不可写？")))
            if args.vault and not v.get("reveal_entry_point"):
                problems.append(_t("no nyx-reveal entry point in Hermes's Python: reinstall nyx-memory",
                                   "Hermes 的 Python 里找不到 nyx-reveal 入口点：重装 nyx-memory"))

    if problems:
        return _report(problems)
    if args.check:
        print(_t("Check finished, no problems.", "检查完毕，没有问题。"))
    else:
        print(_DONE())
    return 0


if __name__ == "__main__":
    sys.exit(main())
