"""
python3 -m nexsandglass.repair —— 中枢 / 日志 / 墓碑对不上时的体检与修复（v7.13.9）

    python3 -m nexsandglass.repair                       # 只读体检：分类计数 + seq 范围，不输出正文、不写任何东西
    python3 -m nexsandglass.repair --apply               # 先自动备份，再把中枢对齐到日志；日志一个字节都不改
    python3 -m nexsandglass.repair --apply --finish-erasures
                                                         # 另外把「遗忘没做完」的那几条在日志里抹掉（身份已核实）
    python3 -m nexsandglass.repair --apply --forget-senders golden,e2e
                                                         # 对齐之后，把这些发送者在日志里的记录送进隔离区（30 天可还原）
    --json 机读；退出码 0 = 干净（或修完干净），1 = 还有要处理的

什么时候用：doctor 里 hub_vs_journal / fts_index / erasure_integrity 红了。原理见 core/forensics.py。
修之前先停掉正在写记忆的进程（Hermes 网关、MCP 服务）；修完重启它们，让内存里的缓存重新读库。
"""
from __future__ import annotations

import argparse
import json
import sys

_LABELS = [
    ("journal_records", "日志记录"),
    ("hub_rows", "中枢行（含已删除）"),
    ("aligned", "对得上"),
    ("erased_ok", "已正确遗忘"),
    ("misplaced", "错位（内容对、seq 不对）→ 挪回原位，id 不变"),
    ("overwritten", "被覆盖的真实记忆（id 认得出来）→ 把正文还回去，id 与来源不变"),
    ("missing_from_hub", "中枢里缺的（日志里有）→ 补进中枢"),
    ("orphans", "合成孤儿（日志里没有）→ 从中枢和所有派生数据里撤下"),
    ("redacted_unregistered", "日志已抹、中枢没登记 → 补登记为已删除"),
    ("stray_tombstones", "指错位置的墓碑 → 删掉"),
    ("false_leaks", "  其中被自检误报成「泄漏」的"),
    ("stray_deleted_rows", "合成记录留下的已删除行 → 删掉"),
    ("unfinished_erasures", "遗忘没做完（日志正文还在）→ 中枢保持已删除；--finish-erasures 才抹日志"),
]
_SEQS = {"overwritten": "overwritten_seqs", "missing_from_hub": "missing_seqs", "orphans": "orphan_seqs",
         "false_leaks": "false_leak_seqs", "unfinished_erasures": "unfinished_erasure_seqs"}


def _print_summary(s: dict, title: str) -> None:
    print(title)
    for k, label in _LABELS:
        v = s.get(k)
        if v is None or (v == 0 and k not in ("journal_records", "hub_rows", "aligned")):
            continue
        seqs = s.get(_SEQS.get(k, "")) or []
        extra = ("  seq " + ", ".join(seqs[:8]) + (" …" if len(seqs) > 8 else "")) if seqs else ""
        print(f"  {label}: {v}{extra}")
    if s.get("orphans_by_sender"):
        print("  合成孤儿按发送者: " + ", ".join(f"{k}={v}" for k, v in s["orphans_by_sender"].items()))
    if s.get("suspect_senders_in_journal"):
        print("  ⚠ 日志里也有像测试数据的发送者（这些在日志里，修复不会动）: "
              + ", ".join(f"{k}={v}" for k, v in s["suspect_senders_in_journal"].items())
              + "\n    确认是测试数据的话：--apply --forget-senders " + ",".join(s["suspect_senders_in_journal"]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="nyx 中枢 / 日志 / 墓碑体检与修复")
    ap.add_argument("--apply", action="store_true", help="真的修（先自动备份）。不加只体检")
    ap.add_argument("--finish-erasures", action="store_true", help="把遗忘没做完的那几条在日志里抹掉")
    ap.add_argument("--forget-senders", default="", help="对齐之后把这些发送者的记录送进隔离区，逗号分隔")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if (args.finish_erasures or args.forget_senders) and not args.apply:
        ap.error("--finish-erasures / --forget-senders 要和 --apply 一起用")

    from nexsandglass.core import forensics, memid
    senders = [s.strip() for s in args.forget_senders.split(",") if s.strip()]
    rep = forensics.repair(apply=args.apply, finish_erasures=args.finish_erasures,
                           forget_senders=senders or None)
    final = rep["after"] if args.apply else rep
    ok = final["clean"] and (not args.apply or all(v for v in rep["health_after"].values()))
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
        return 0 if ok else 1

    print(f"数据目录 {memid._nb()}")
    if not args.apply:
        _print_summary(rep, "体检（只读，什么都没改）：")
        if rep["clean"]:
            print("结论：中枢、日志、墓碑逐条对得上。")
        else:
            print("结论：有要修的。先停掉 Hermes 网关 / MCP 服务，再运行\n"
                  "    python3 -m nexsandglass.repair --apply\n"
                  "（自动备份；日志不改；修完重启服务）")
        return 0 if rep["clean"] else 1

    _print_summary(rep["before"], "修复前：")
    print(f"备份：{rep['backup']}")
    d = rep.get("derived_removed") or {}
    print("撤下的派生数据：信念三元组 {triples} · 实体链接 {entity_links}（整个删掉的实体 {entities_removed}）"
          " · engram {engram} · 向量 {vectors}".format(**{k: d.get(k, 0) for k in (
              "triples", "entity_links", "entities_removed", "engram", "vectors")}))
    if d.get("beliefs_reopened") or d.get("beliefs_reclosed"):
        print(f"被合成事实截断的真实信念：恢复成现任 {d.get('beliefs_reopened', 0)} 条，"
              f"按后来的真实事实重算终点 {d.get('beliefs_reclosed', 0)} 条")
    if d.get("beliefs_to_review"):
        print(f"⚠ 有 {d['beliefs_to_review']} 条信念被合成事实以「撤回再插回」的方式改过，没有自动还原，请人工核对")
    if d.get("triples_unknown_source") or d.get("engram_unknown_source"):
        print(f"ℹ 来源 id 在中枢里找不到的派生数据（分不清真假，没删）：信念 {d.get('triples_unknown_source', 0)}"
              f" · engram {d.get('engram_unknown_source', 0)}")
    if rep.get("restored_with_original_provenance"):
        print(f"补回的记录里 {rep['restored_with_original_provenance']} 条找回了写入时绑定的来源")
    if "finished_erasures" in rep:
        print(f"日志里补抹了 {rep['finished_erasures']} 行（影子副本：{rep['finished_erasures_backup'].get('status')}）")
    if "forget_senders" in rep:
        fs = rep["forget_senders"]
        print("按发送者送进隔离区：" + (fs.get("skipped") or f"{fs.get('removed', 0)} 条（30 天内可还原）"))
    _print_summary(rep["after"], "修复后：")
    print("自检：" + " ".join(f"{'✅' if v else '❌'}{k}" for k, v in rep["health_after"].items()))
    if rep["after"]["unfinished_erasures"] and "finished_erasures" not in rep:
        print("还剩遗忘没做完的记录：它们已经从中枢和检索里撤下，但日志正文还在。\n"
              "    python3 -m nexsandglass.repair --apply --finish-erasures")
    print("记得重启 Hermes 网关 / MCP 服务。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
