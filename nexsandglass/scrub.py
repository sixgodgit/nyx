"""
python3 -m nexsandglass.scrub —— 把已经存下来的密钥 / 密码原地隐去（v7.14.0）

    python3 -m nexsandglass.scrub                 # 只读：每个存储里有几处、什么类别；不输出原文、不改任何东西
    python3 -m nexsandglass.scrub --apply         # 原地换成占位符，重建索引，VACUUM，再按原始字节复查整个数据目录
    python3 -m nexsandglass.scrub --tokens-only   # 只动 token / API key / 私钥，不动「密码是 …」
    python3 -m nexsandglass.scrub --apply --to-vault   # 主人自己说过的先收进密钥保险箱（v7.15.0，要先开 NYX_VAULT）
    --json 机读；退出码 0 = 数据目录里一处都没有了

升级到 v7.14.0 之后的写入已经在入口隐去（core/secret_filter.py）；这个命令处理升级之前存下来的。
改之前先停掉 Hermes 网关 / MCP 服务，改完重启。不做备份 —— 备份会把密钥再复制一份。
"""
from __future__ import annotations

import argparse
import json
import sys

from nexsandglass.core import hints
from nexsandglass.core.lang import t


def _print(rep: dict, title: str) -> None:
    print(title)
    if not rep["stores"]:
        print(t("  (none in the stores nyx knows how to read)", "  （nyx 认识格式的存储里没有）"))
    for name, s in sorted(rep["stores"].items()):
        kinds = ", ".join(f"{k} {v}" for k, v in s["kinds"].items())
        seqs = s.get("seqs") or []
        where = f"  seq {', '.join(map(str, seqs[:8]))}{' …' if len(seqs) > 8 else ''}" if seqs else ""
        print(t(f"  {name}: {s['found']} ({kinds}){where}", f"  {name}: {s['found']} 处（{kinds}）{where}"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog=hints.cmd("scrub"), description=t(
        "Hide secrets and passwords that are already stored, in place", "把已经存下来的密钥 / 密码原地隐去"))
    ap.add_argument("--apply", action="store_true", help=t("really change things; without it, only look", "真的改。不加只看"))
    ap.add_argument("--tokens-only", action="store_true",
                    help=t("only tokens / API keys / private keys, leave passwords", "只动 token / API key / 私钥，不动密码"))
    ap.add_argument("--to-vault", action="store_true",
                    help=t("put secrets the user said themselves into the vault first (the placeholder gets an id "
                           "so it can be revealed later); needs NYX_VAULT on",
                           "主人自己说过的密钥先收进保险箱（占位符带编号，之后能取回）；要先开 NYX_VAULT"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    from nexsandglass.core import secret_scrub
    try:
        rep = secret_scrub.run(apply=args.apply, mode="tokens" if args.tokens_only else "on", to_vault=args.to_vault)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
        return 0 if rep["clean"] else 1

    print(t(f"data directory {rep['data_dir']}", f"数据目录 {rep['data_dir']}"))
    if args.to_vault:
        n = rep.get('to_vault', 0)
        print(t(f"vault: {'kept' if args.apply else 'would keep'} {n} (said by the user)",
                f"保险箱：{'收进' if args.apply else '将收进'} {n} 处（主人自己说过的）"))
    if not args.apply:
        _print(rep, t("Read-only check (nothing changed):", "只读检查（什么都没改）："))
        if rep["on_disk"]:
            print(t("Raw bytes (including free pages in database files): ", "按原始字节（含库文件空闲页）找到：")
                  + ", ".join(f"{k} {v}" for k, v in rep["on_disk"].items()))
        if rep["clean"]:
            print(t("Result: no secrets or passwords found in the data directory.", "结论：数据目录里没有找到密钥 / 密码。"))
            return 0
        print(t(f"Result: stop the Hermes gateway / MCP server first, then run\n    {hints.cmd('scrub', '--apply')}\n"
                "(hides them in place, no backup; tokens keep their last 4 characters, passwords keep nothing)",
                f"结论：先停掉 Hermes 网关 / MCP 服务，再运行\n    {hints.cmd('scrub', '--apply')}\n"
                "（原地隐去，不做备份；token 留末 4 位，密码一个字符不留）"))
        return 1

    _print(rep, t("Hidden:", "已隐去："))
    fts, vec = rep.get('fts_rebuilt'), rep.get('vectors_dropped', 0)
    print(t(f"Full-text index rebuilt: {fts}; vectors of changed memories dropped: {vec} (rebuilt in the background)",
            f"全文库重建 {fts} 条；改过正文的向量删掉 {vec} 条（后台按新正文重新补）"))
    if rep.get("derived_copies_removed"):
        print(t("Removed index copies inside backups (they rebuild from the journal in the same backup): ",
                "删掉了备份里的索引副本（能从同一份备份里的日志重建）：") + ", ".join(rep["derived_copies_removed"]))
    for e in rep["errors"]:
        print(f"  ⚠ {e}")
    if rep["clean"]:
        print(t("Re-check: scanned the whole data directory byte by byte; nothing left.",
                "复查：按原始字节扫了整个数据目录，一处都没有了。"))
    else:
        print(t("Re-check: these files still contain some (nyx does not know their format and left them alone; "
                "most likely old repair backups, delete them once you are sure you do not need them):",
                "复查：这些文件里还有（nyx 不认识它们的格式，没有改；多半是旧的修复备份，确认没用可以删掉）："))
        for k, v in rep["on_disk"].items():
            print(f"  {k}: {v}")
    print(t("Remember to restart the Hermes gateway / MCP server. Hermes's own session logs (under ~/.hermes) are not "
            "nyx's to change; clean them separately if needed.",
            "记得重启 Hermes 网关 / MCP 服务。Hermes 自己的会话记录（~/.hermes 下）不归 nyx 管，需要的话另行清理。"))
    return 0 if rep["clean"] else 1

if __name__ == "__main__":
    sys.exit(main())
