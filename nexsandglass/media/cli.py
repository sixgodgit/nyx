"""
hermes nyx media … —— 图片、声音、视频的记忆（v7.21.0 框架）

    hermes nyx media add 猫.jpg --note "朵朵第一次见雪" [--when 2026-01-10]
    hermes nyx media list [--all]          登记过的素材（--all 含已遗忘的）
    hermes nyx media show a_1b2c…          一个素材：文件、时间、状态、从它得出的记录
    hermes nyx media understand --pending  配好理解后端之后，给还没理解的补上描述
    hermes nyx media backends              有哪些理解后端、能不能用
    hermes nyx media forget a_1b2c… [--now]
    hermes nyx media restore a_1b2c…

不在 Hermes 里：python3 -m nexsandglass.media add 猫.jpg
"""
from __future__ import annotations

import argparse
import json
import sys

from nexsandglass.core import hints
from nexsandglass.core.lang import t


def _j(o) -> None:
    print(json.dumps(o, ensure_ascii=False, indent=1, default=str))


def _line(a: dict) -> str:
    dur = f" {int(a['duration_s']) // 60}:{int(a['duration_s']) % 60:02d}" if a.get("duration_s") else ""
    return f"{a['asset_id']}  {str(a['captured_at'])[:16]}  {a['kind']:<5}{dur:>6}  {a['status']:<11}  {a['name']}"


def _show(a: dict) -> None:
    from nexsandglass.core import lookup
    print(_line(a))
    print(t(f"file: {a.get('blob') or a.get('source_path')}", f"文件：{a.get('blob') or a.get('source_path')}"))
    for name, res in (a.get("understood") or {}).items():
        print(f"  {name}: {res}")
    for m in a.get("moments") or []:
        it = lookup.get(m["mem_id"])
        text = (it or {}).get("text") or ""
        state = "" if not it or it["status"] == "live" else f" [{it['status']}]"
        print(f"  {m['mem_id']}  {m['kind']:<11}{state}  {' '.join(text.split())[:100]}")


def main(argv=None) -> int:
    from nexsandglass import media as M
    prog = "hermes nyx media" if hints.via_hermes() else "python3 -m nexsandglass.media"
    ap = argparse.ArgumentParser(prog=prog, description=t(
        "Photos, recordings and videos as memories (framework: understanding backends are optional)",
        "图片、声音、视频的记忆（框架：理解后端可选）"))
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("add", help=t("register a file", "登记一个文件"))
    p.add_argument("path")
    p.add_argument("--note", help=t("your words about it (kept as yours)", "你的附言（算你说的）"))
    p.add_argument("--when", help=t("when it was taken: YYYY-MM-DD or YYYY-MM-DD HH:MM (default now)",
                                    "拍摄 / 录制时间：YYYY-MM-DD 或 YYYY-MM-DD HH:MM（默认现在）"))
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("list", help=t("registered media", "登记过的素材"))
    p.add_argument("--all", action="store_true")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("show", help=t("one item and what was derived from it", "一个素材和从它得出的记录"))
    p.add_argument("asset")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("understand", help=t("run the available backends", "跑现在能用的理解后端"))
    p.add_argument("asset", nargs="?")
    p.add_argument("--pending", action="store_true")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("backends", help=t("understanding backends", "理解后端"))
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("forget", help=t("to the quarantine (--now: erase with the file)", "进隔离区（--now：连文件当场擦掉）"))
    p.add_argument("asset")
    p.add_argument("--now", action="store_true")
    p.add_argument("--yes", "-y", action="store_true")
    p = sub.add_parser("restore", help=t("bring a forgotten item back", "把遗忘的素材救回来"))
    p.add_argument("asset")
    args = ap.parse_args(argv)

    if args.cmd == "add":
        try:
            r = M.add(args.path, note=args.note, when=args.when)
        except M.MediaError as e:
            print(e, file=sys.stderr)
            return 2
        if args.json:
            _j(r)
        elif r["status"] == "refused":
            print(t(f"Not added: {r['reason']}", f"没有登记：{r['reason']}"), file=sys.stderr)
        else:
            _show(r)
            if r["status"] == "unavailable":
                print(t("No understanding backend for this kind yet: it is remembered by its name, time and your note. "
                        f"Once one is set up: {hints.cmd('media', 'understand --pending')}",
                        "这一类还没有理解后端：先按文件名、时间和你的附言记下。配好后端后运行："
                        f"{hints.cmd('media', 'understand --pending')}"))
        return 1 if r["status"] == "refused" else 0
    if args.cmd == "list":
        rows = M.list_assets(include_forgotten=args.all)
        if args.json:
            _j(rows)
        else:
            for a in rows:
                print(_line(a))
            if not rows:
                print(t("No media yet.", "还没有素材。"))
        return 0
    if args.cmd == "show":
        a = M.get(args.asset)
        if not a:
            print(t(f"No media {args.asset}.", f"没有 {args.asset}。"), file=sys.stderr)
            return 1
        _j(a) if args.json else _show(a)
        return 0
    if args.cmd == "understand":
        if not args.asset and not args.pending:
            print(t("Name an item, or --pending.", "写素材编号，或者 --pending。"), file=sys.stderr)
            return 2
        res = M.understand(args.asset, pending=args.pending)
        if args.json:
            _j(res)
        else:
            for r in res:
                print(f"{r['asset_id']}  {r['status']}  {r.get('reason') or json.dumps(r.get('understood'), ensure_ascii=False)}")
            if not res:
                print(t("Nothing to do.", "没有要补的。"))
        return 0
    if args.cmd == "backends":
        rows = M.U.describe()
        if args.json:
            _j(rows)
        else:
            for r in rows:
                print(f"{'✅' if r['available'] else '–'}  {r['name']:<20} {','.join(r['kinds']):<18} {r['reason']}")
        return 0
    if args.cmd == "forget":
        a = M.get(args.asset)
        if not a:
            print(t(f"No media {args.asset}.", f"没有 {args.asset}。"), file=sys.stderr)
            return 1
        _show(a)
        if not args.yes:
            q = (t("Erase it and its file now? Cannot be undone. Type erase: ", "现在连文件擦掉？救不回来。输入 erase：")
                 if args.now else t("Move it to the quarantine (restorable)? [y/N] ", "送进隔离区（能救回）？[y/N] "))
            ans = input(q).strip().lower() if sys.stdin.isatty() else ""
            if ans != ("erase" if args.now else "y"):
                print(t("Nothing forgotten (not a terminal: pass --yes)." if not sys.stdin.isatty() else "Nothing forgotten.",
                        "什么都没忘（不是终端：加 --yes）。" if not sys.stdin.isatty() else "什么都没忘。"))
                return 1
        r = M.forget(a["asset_id"], now=args.now)
        print(json.dumps(r, ensure_ascii=False) if not r.get("ok") else t(
            f"Forgotten: {r['removed']} records" + (" and the file, for good." if r["erased"] else
                                                    f"; restorable until {str(r['purge_after'])[:10]}."),
            f"已遗忘 {r['removed']} 条记录" + ("，文件也删了，救不回来。" if r["erased"] else
                                        f"，{str(r['purge_after'])[:10]} 之前能救回。")))
        return 0 if r.get("ok") else 1
    if args.cmd == "restore":
        r = M.restore(args.asset)
        print(t("Restored." if r.get("ok") else f"Not restored: {r}", "救回来了。" if r.get("ok") else f"没救回来：{r}"))
        return 0 if r.get("ok") else 1
    ap.print_help()
    return 2
