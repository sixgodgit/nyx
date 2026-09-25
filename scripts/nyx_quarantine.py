#!/usr/bin/env python3
"""nyx_quarantine.py —— 遗忘隔离区的人手入口（看 / 还原 / 到期擦除）。

    # 还剩哪些能反悔，各自还剩几天
    python3 scripts/nyx_quarantine.py list

    # 误删了 —— 还原一条
    python3 scripts/nyx_quarantine.py restore m_1a2b3c4d5e6f7890

    # 到期擦除（先预演，再动手）
    python3 scripts/nyx_quarantine.py purge
    python3 scripts/nyx_quarantine.py purge --apply

cron 里该有 purge --apply：没有人跑它，"保留 30 天"就变成"永久留着"，
而用户以为那些内容早就没了。`nyx_healthcheck.py` 的 pending_purge 项会报警，
但报警不等于清理。

退出码：0 成功 / 1 有问题（还原失败 / 有过期未清）/ 2 跑不起来
"""
import argparse
import json
import sys


def _load():
    from nexsandglass.core import erasure, quarantine
    return erasure, quarantine


def cmd_list(args) -> int:
    erasure, quarantine = _load()
    rows = quarantine.list_all(limit=args.limit)
    st = quarantine.stats()
    if args.json:
        print(json.dumps({"stats": st, "items": rows}, ensure_ascii=False, indent=2))
        return 1 if st["overdue"] else 0
    print("隔离区：%d 条待清（可反悔 %d / 已过期 %d），保留期 %d 天" % (
        st["pending"], st["recoverable"], st["overdue"], st["retention_days"]))
    if st["next_due"]:
        print("最近到期：%s" % st["next_due"])
    for r in rows:
        print("  seq=%-6s %s  到期 %s  %s  「%s」" % (
            r["seq"], r["mem_id"], r["purge_after"], r["reason"], r["preview"]))
    if st["overdue"]:
        print("⚠ 有 %d 条已过隔离期还没擦除 —— 跑 `purge --apply`" % st["overdue"],
              file=sys.stderr)
        return 1
    return 0


def cmd_restore(args) -> int:
    erasure, _ = _load()
    rep = erasure.restore(args.mem_id)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print("还原 %s: %s" % (args.mem_id, "OK" if rep["ok"] else "失败"))
        print("  日志 %s / 中枢 %s / FTS %s / 倒排 %s / engram %s / 影子 %s" % (
            rep["journal"].get("status"), rep["hub"], rep["fts"], rep["idx"],
            rep["engram"], rep["shadow"]))
        for p in rep["problems"]:
            print("  ✗ %s" % p, file=sys.stderr)
        for n in rep.get("notes", []):
            print("  · %s" % n)
    return 0 if rep["ok"] else 1


def cmd_purge(args) -> int:
    erasure, _ = _load()
    rep = erasure.purge(mem_ids=args.mem_id or None, apply=args.apply)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0 if rep.get("ok") else 1
    print("%s：命中 %d 条，已擦除 %d 条%s" % (
        "擦除" if args.apply else "预演（不动任何东西）",
        rep["selected"], rep["purged"],
        "，VACUUM 完成" if rep.get("vacuumed") else ""))
    for p in rep["preview"]:
        print("  seq=%-6s %s  到期 %s" % (p["seq"], p["mem_id"], p["purge_after"]))
    if rep.get("vacuum_error"):
        print("⚠ VACUUM 失败，空闲页里可能仍有正文：%s" % rep["vacuum_error"], file=sys.stderr)
        return 1
    return 0 if rep.get("ok") else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Nyx 遗忘隔离区")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="列出隔离区内容")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("restore", help="还原一条被 forget 的记忆")
    p.add_argument("mem_id")
    p.set_defaults(fn=cmd_restore)

    p = sub.add_parser("purge", help="到期擦除（不可逆）")
    p.add_argument("--apply", action="store_true", help="真的擦除（默认只预演）")
    p.add_argument("--mem-id", dest="mem_id", action="append",
                   help="只擦这几条（可重复），不看到期时间")
    p.set_defaults(fn=cmd_purge)

    args = ap.parse_args()
    try:
        return args.fn(args)
    except Exception as e:
        print("nyx_quarantine: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
