"""
python3 -m nexsandglass_hermes install —— 把 Nyx 装成 Hermes 的记忆插件（v7.15.0）

    pip install nyx-memory
    python3 -m nexsandglass_hermes install             # 一步装好，然后重启 Hermes
    python3 -m nexsandglass_hermes install --vault     # 同时打开密钥保险箱（装 [vault]、启用 nyx-reveal）
    python3 -m nexsandglass_hermes install --vector    # 同时装语义检索（[vector]，首次运行下载约 470MB）
    python3 -m nexsandglass_hermes install --check     # 只检查，不改任何东西

为什么要有它：hermes-agent 0.19.0 只按目录找记忆插件 —— 内置的 plugins/memory/<名字>/，和 $HERMES_HOME/plugins/<名字>/
（plugins/memory/__init__.py 的 find_provider_dir），**不看 pip 的入口点**。所以光 pip install nyx-memory，
Hermes 找不到 nyx，而且什么都不报（加载失败只在 debug 日志里）。v7.14.0 及以前的 README 说的「pip 装完自动发现」不成立。

这里做四件事：
  1. 找到 Hermes 自己用的 Python（`hermes` 命令第一行的 #!）。官方安装脚本把 Hermes 装在单独的虚拟环境里，
     装在系统 Python 里的 nyx-memory 它看不见 —— 看不见就装进去（同一个版本）
  2. 在 $HERMES_HOME/plugins/nyx/ 放一个只有几行的入口文件，转到 pip 装的 nexsandglass_hermes。
     以后 pip 升级 nyx-memory 不用再动它（以前要整个目录拷过去，升级后旧拷贝还在跑旧代码）
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

NAME = "nyx"
_SHIM_MARK = "nexsandglass_hermes 安装的入口文件"

SHIM = '''"""Nyx 夜神 —— Hermes 记忆插件入口（{mark}，v{version}）

hermes-agent 只按目录找记忆插件（$HERMES_HOME/plugins/<名字>/），不看 pip 的入口点；这里只是转一下：
真正的代码在 pip 装的 nyx-memory 里（nexsandglass_hermes.register → ctx.register_memory_provider），
pip 升级之后这个文件不用动。重新生成：python3 -m nexsandglass_hermes install
"""
import logging
import sys

try:
    from nexsandglass_hermes import register as _register
    _missing = None
except ImportError as e:
    _register, _missing = None, e


def register(ctx):
    if _register is None:
        logging.getLogger("nyx").error(
            "Nyx：Hermes 用的 Python（%s）里没装 nyx-memory（%s）。运行：%s -m pip install nyx-memory，然后重启 Hermes",
            sys.executable, _missing, sys.executable)
        return
    _register(ctx)
'''

PLUGIN_YAML = """name: {name}
version: {version}
kind: exclusive
description: >-
  Nyx 夜神 — 单用户、本地的 agent 记忆。入口文件，真正的代码在 pip 装的 nyx-memory 里。
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


def find_hermes(explicit_python: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """(hermes 命令, Hermes 用的 Python)。找不到的那个是 None。"""
    exe = shutil.which("hermes")
    if explicit_python:
        return exe, explicit_python
    if not exe:
        return None, None
    real = os.path.realpath(exe)
    try:
        with open(real, "rb") as f:
            first = f.readline(512)
    except OSError:
        first = b""
    if first.startswith(b"#!"):
        parts = first[2:].decode("utf-8", "replace").strip().split()
        if parts and os.path.basename(parts[0]) != "env" and os.path.exists(parts[0]):
            return exe, parts[0]
    d = os.path.dirname(real)                   # Windows：venv\\Scripts\\hermes.exe 旁边就是 python.exe
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
    return _SHIM_MARK in src or "nexsandglass_hermes" in src or "NexSandglassProvider" in src


def write_shim(home: str, name: str = NAME, force: bool = False) -> Tuple[str, str]:
    """返回 (目录, 做了什么)。"""
    target = os.path.join(home, "plugins", name)
    did = "新建"
    if os.path.isdir(target):
        if not (force or _ours(target)):
            raise SystemExit(f"❌ {target} 已经存在，而且不是 nyx 的插件；不覆盖。确认没用的话加 --force")
        did = "更新" if _SHIM_MARK in (_init_src(target) or "") else "替换旧的整目录拷贝"
        shutil.rmtree(target)
    os.makedirs(target)
    ver = _version()
    with open(os.path.join(target, "__init__.py"), "w", encoding="utf-8") as f:
        f.write(SHIM.format(mark=_SHIM_MARK, version=ver))
    with open(os.path.join(target, "plugin.yaml"), "w", encoding="utf-8") as f:
        f.write(PLUGIN_YAML.format(name=name, version=ver))
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

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m nexsandglass_hermes install",
                                 description="把 Nyx 装成 Hermes 的记忆插件")
    ap.add_argument("--hermes-home", help="Hermes 的 home（默认 $HERMES_HOME 或 ~/.hermes）；多个 profile 时每个各装一次")
    ap.add_argument("--hermes-python", help="Hermes 用的 Python（默认从 hermes 命令第一行的 #! 找）")
    ap.add_argument("--vault", action="store_true", help="同时打开密钥保险箱：装 [vault]、启用 nyx-reveal、nyx.json 里 vault: on")
    ap.add_argument("--vector", action="store_true", help="同时装语义检索 [vector]")
    ap.add_argument("--no-pip", action="store_true", help="不往 Hermes 的 Python 里装 nyx-memory（只提示）")
    ap.add_argument("--no-config", action="store_true", help="不改 Hermes 的配置（只放入口文件）")
    ap.add_argument("--force", action="store_true", help="$HERMES_HOME/plugins/nyx 是别的东西时也覆盖")
    ap.add_argument("--check", action="store_true", help="只检查，不改任何东西")
    args = ap.parse_args(argv)

    home = os.path.abspath(os.path.expanduser(args.hermes_home)) if args.hermes_home else default_hermes_home()
    exe, py = find_hermes(args.hermes_python)
    extras = (["vault"] if args.vault else []) + (["vector"] if args.vector else [])
    ver = _version()
    print(f"nyx-memory {ver} → Hermes home {home}")
    print(f"  hermes 命令：{exe or '没找到（不在 PATH 里）'}")
    print(f"  Hermes 的 Python：{py or '没找到'}" + ("（就是当前这个）" if py and _same_python(py) else ""))
    problems: List[str] = []

    # 1. Hermes 的 Python 里有没有 nyx-memory
    if py is None:
        problems.append("没找到 Hermes 用的 Python：用 --hermes-python 指定（hermes 命令文件第一行 #! 后面那个路径）")
    else:
        have = nyx_in(py)
        miss = missing_extras(py, extras) if have else extras
        if have:
            print(f"  Hermes 的 Python 里已有 nyx-memory {have}" + (f"，缺 [{','.join(miss)}]" if miss else ""))
        if have != ver or miss:
            what = ("没装 nyx-memory" if have is None else f"是 nyx-memory {have}" if have != ver
                    else f"缺可选依赖 [{','.join(miss)}]")
            if args.check or args.no_pip:
                problems.append(f"Hermes 的 Python 里{what}：{py} -m pip install "
                                f"'nyx-memory{('[' + ','.join(extras) + ']') if extras else ''}=={ver}'")
            else:
                print(f"→ 装进 Hermes 的 Python（{what}）")
                ok, err = pip_install(py, extras)
                if not ok:
                    print(err)
                    problems.append("往 Hermes 的 Python 里装 nyx-memory 失败（见上面的输出）")

    # 2. 入口文件
    target = os.path.join(home, "plugins", NAME)
    if args.check:
        src = _init_src(target) if os.path.isdir(target) else None
        state = ("没有" if not os.path.isdir(target) else "入口文件" if src and _SHIM_MARK in src else
                 "旧的整目录拷贝（重新运行 install 会换成入口文件）" if src and _ours(target) else "不是 nyx 的东西")
        print(f"  {target}：{state}")
        if state != "入口文件":
            problems.append(f"{target} 不是入口文件：运行 python3 -m nexsandglass_hermes install")
    else:
        os.makedirs(home, exist_ok=True)
        target, did = write_shim(home, force=args.force)
        print(f"→ {did}入口文件 {target}")
    for old in legacy_dirs(home):
        problems.append(f"旧版安装留下的 {old}：请删掉（rm -rf \"{old}\"），否则同一句话可能被记两遍")

    # 3. Hermes 配置
    if not args.no_config and not args.check:
        if exe is None:
            problems.append("没找到 hermes 命令，没改配置：手动在 config.yaml 里写 memory.provider: nyx")
        else:
            cur = _hermes(exe, home, "config", "get", "memory.provider")
            prev = cur.stdout.strip() if cur.returncode == 0 else ""
            if prev != NAME:
                r = _hermes(exe, home, "config", "set", "memory.provider", NAME)
                if r.returncode == 0:
                    print(f"→ hermes config set memory.provider {NAME}"
                          + (f"（原来是 {prev}；改回：hermes config set memory.provider {prev}）"
                             if prev and prev not in ("None", "null", "") else ""))
                else:
                    problems.append(f"hermes config set memory.provider {NAME} 失败：{(r.stderr or r.stdout)[-300:]}")
            if args.vault:
                r = _hermes(exe, home, "plugins", "enable", "nyx-reveal")
                if r.returncode == 0:
                    print("→ hermes plugins enable nyx-reveal")
                else:
                    problems.append(f"hermes plugins enable nyx-reveal 失败：{(r.stderr or r.stdout)[-300:]}")
                from nexsandglass_hermes import save_config
                save_config({"vault": "on"}, home)
                print(f"→ {os.path.join(home, 'nyx.json')}：vault: on")

    # 4. 用 Hermes 自己的加载器验一次
    if py is not None:
        v = verify(py, home)
        if v.get("hermes") is False:
            problems.append("在 Hermes 的 Python 里 import 不到 Hermes（--hermes-python 指错了？）：" + str(v.get("error"))[:300])
        elif v.get("provider") != "NexSandglassProvider":
            problems.append(f"Hermes 的加载器没加载出 nyx（目录：{v.get('dir')}）。用 {py} -c 'import nexsandglass_hermes' 看报错")
        else:
            print(f"✅ Hermes 的加载器能加载 nyx（{v['dir']}）" + ("" if v.get("available") else "，但 is_available() 为假：记忆目录不可写？"))
            if args.vault and not v.get("reveal_entry_point"):
                problems.append("Hermes 的 Python 里找不到 nyx-reveal 入口点：重装 nyx-memory")

    if problems:
        print("\n还有这些要处理：")
        for p in problems:
            print("  ❌ " + p)
        return 1
    if args.check:
        print("检查完毕，没有问题。")
    else:
        print("\n完成。重启 Hermes（网关：hermes gateway restart）。之后可以用 hermes memory setup 调 nyx 的选项。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
