"""
python3 -m nexsandglass.doctor —— 一条命令看 nyx 每一项是不是真的在工作（v7.13.3）

    python3 -m nexsandglass.doctor          # 人读：每项 ✅ / ❌ + 原因
    python3 -m nexsandglass.doctor --json   # 机读
    退出码：0 = 全部正常，1 = 有项目失败

为什么要有它：语义检索这一路一条向量都没写进去过、持续一个月，因为失败只是日志里的一行 warning，
而且没有任何一项检查在看它（2026-10-09 诊断报告）。「降级不拖垮主流程」是对的，
但降级之后必须有一个地方能一眼看到 —— 就是这里。Hermes 插件会在自检失败时在系统提示里提醒 agent。
"""
from __future__ import annotations

import argparse
import json
import sys


def run() -> dict:
    from nexsandglass.core import memid
    from nexsandglass.core.sandglass_paths import __version__
    h = memid.health()
    h["version"] = __version__
    h["data_dir"] = memid._nb()
    return h


def _line(name: str, c: dict) -> str:
    mark = "✅" if c.get("ok") else "❌"
    if c.get("error"):
        return f"{mark} {name}: {c['error']}"
    if name == "semantic_index":
        if c.get("state") == "disabled" and c.get("ok"):
            return f"{mark} {name}: 关闭（没有配置嵌入后端；同义改写召不回来）"
        head = (f"{mark} {name}: {c.get('backend')} · 已索引 {c.get('indexed')} · 待补 {c.get('pending')}"
                f" · 孤儿向量 {c.get('orphans')}"
                + ("（正在回填，有进展）" if c.get("state") == "backfilling" else ""))
        return head + "".join(f"\n      - {p}" for p in (c.get("problems") or []))
    keys = [k for k in c if k not in ("ok",)][:6]
    return f"{mark} {name}: " + ", ".join(f"{k}={c[k]}" for k in keys)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="nyx 自检")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    h = run()
    if args.json:
        print(json.dumps(h, ensure_ascii=False, indent=1, default=str))
    else:
        print(f"nyx {h['version']} · 数据目录 {h['data_dir']}")
        for name, c in h["checks"].items():
            print("  " + _line(name, c))
        print("结论：" + ("全部正常" if h["ok"] else "有项目失败（见 ❌）"))
    return 0 if h["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
