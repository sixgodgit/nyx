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

from nexsandglass.core import hints
from nexsandglass.core.lang import t


def _labels():
    return [
        ("journal_records", t("journal records", "日志记录")),
        ("hub_rows", t("hub rows (including deleted)", "中枢行（含已删除）")),
        ("aligned", t("aligned", "对得上")),
        ("erased_ok", t("correctly forgotten", "已正确遗忘")),
        ("misplaced", t("misplaced (right text, wrong seq) → moved back, id unchanged", "错位（内容对、seq 不对）→ 挪回原位，id 不变")),
        ("text_normalized", t("text differs only by line endings (\\r) → use the journal's form, id unchanged",
                              "正文只差换行符（\\r）→ 换成日志里的写法，id 不变")),
        ("overwritten", t("real memories that were overwritten (id recognised) → text restored, id and source unchanged",
                          "被覆盖的真实记忆（id 认得出来）→ 把正文还回去，id 与来源不变")),
        ("missing_from_hub", t("missing from the hub (present in the journal) → added to the hub", "中枢里缺的（日志里有）→ 补进中枢")),
        ("orphans", t("synthetic orphans (not in the journal) → removed from the hub and all derived data",
                      "合成孤儿（日志里没有）→ 从中枢和所有派生数据里撤下")),
        ("redacted_unregistered", t("erased in the journal, not registered in the hub → registered as deleted",
                                    "日志已抹、中枢没登记 → 补登记为已删除")),
        ("stray_tombstones", t("tombstones pointing at the wrong place → removed", "指错位置的墓碑 → 删掉")),
        ("false_leaks", t("  of which the self-check misreported as \"leaks\"", "  其中被自检误报成「泄漏」的")),
        ("stray_deleted_rows", t("deleted rows left by synthetic records → removed", "合成记录留下的已删除行 → 删掉")),
        ("unfinished_erasures", t("unfinished erasures (journal text still there) → hub stays deleted; "
                                  "--finish-erasures erases the journal text",
                                  "遗忘没做完（日志正文还在）→ 中枢保持已删除；--finish-erasures 才抹日志")),
    ]


_SEQS = {"overwritten": "overwritten_seqs", "missing_from_hub": "missing_seqs", "orphans": "orphan_seqs",
         "false_leaks": "false_leak_seqs", "unfinished_erasures": "unfinished_erasure_seqs"}


def _print_summary(s: dict, title: str) -> None:
    print(title)
    for k, label in _labels():
        v = s.get(k)
        if v is None or (v == 0 and k not in ("journal_records", "hub_rows", "aligned")):
            continue
        seqs = s.get(_SEQS.get(k, "")) or []
        extra = ("  seq " + ", ".join(seqs[:8]) + (" …" if len(seqs) > 8 else "")) if seqs else ""
        print(f"  {label}: {v}{extra}")
    if s.get("orphans_by_sender"):
        print(t("  synthetic orphans by sender: ", "  合成孤儿按发送者: ")
              + ", ".join(f"{k}={v}" for k, v in s["orphans_by_sender"].items()))
    if s.get("suspect_senders_in_journal"):
        senders = s["suspect_senders_in_journal"]
        print(t("  ⚠ the journal also has senders that look like test data (they are in the journal; repair leaves them): ",
                "  ⚠ 日志里也有像测试数据的发送者（这些在日志里，修复不会动）: ")
              + ", ".join(f"{k}={v}" for k, v in senders.items())
              + t("\n    If they are test data: --apply --forget-senders ", "\n    确认是测试数据的话：--apply --forget-senders ")
              + ",".join(senders))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog=hints.cmd("repair"), description=t(
        "Check and repair the nyx hub / journal / tombstones", "nyx 中枢 / 日志 / 墓碑体检与修复"))
    ap.add_argument("--apply", action="store_true", help=t("really repair (backs up first); without it, only check",
                                                           "真的修（先自动备份）。不加只体检"))
    ap.add_argument("--finish-erasures", action="store_true",
                    help=t("erase the journal text of unfinished erasures", "把遗忘没做完的那几条在日志里抹掉"))
    ap.add_argument("--forget-senders", default="",
                    help=t("after aligning, quarantine these senders' records (comma-separated)",
                           "对齐之后把这些发送者的记录送进隔离区，逗号分隔"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if (args.finish_erasures or args.forget_senders) and not args.apply:
        ap.error(t("--finish-erasures / --forget-senders need --apply", "--finish-erasures / --forget-senders 要和 --apply 一起用"))

    from nexsandglass.core import forensics, memid
    senders = [s.strip() for s in args.forget_senders.split(",") if s.strip()]
    rep = forensics.repair(apply=args.apply, finish_erasures=args.finish_erasures,
                           forget_senders=senders or None)
    final = rep["after"] if args.apply else rep
    ok = final["clean"] and (not args.apply or all(v for v in rep["health_after"].values()))
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
        return 0 if ok else 1

    print(t(f"data directory {memid._nb()}", f"数据目录 {memid._nb()}"))
    if not args.apply:
        _print_summary(rep, t("Check (read-only, nothing changed):", "体检（只读，什么都没改）："))
        if rep["clean"]:
            print(t("Result: hub, journal and tombstones line up record by record.", "结论：中枢、日志、墓碑逐条对得上。"))
        else:
            print(t(f"Result: there is something to repair. Stop the Hermes gateway / MCP server first, then run\n"
                    f"    {hints.cmd('repair', '--apply')}\n(backs up automatically; the journal is not changed; "
                    "restart the services afterwards)",
                    "结论：有要修的。先停掉 Hermes 网关 / MCP 服务，再运行\n"
                    f"    {hints.cmd('repair', '--apply')}\n"
                    "（自动备份；日志不改；修完重启服务）"))
        return 0 if rep["clean"] else 1

    _print_summary(rep["before"], t("Before:", "修复前："))
    print(t(f"backup: {rep['backup']}", f"备份：{rep['backup']}"))
    d = {k: (rep.get("derived_removed") or {}).get(k, 0) for k in (
        "triples", "entity_links", "entities_removed", "engram", "vectors", "beliefs_reopened", "beliefs_reclosed",
        "beliefs_to_review", "triples_unknown_source", "engram_unknown_source")}
    print(t("derived data removed: belief triples {triples} · entity links {entity_links} (entities removed entirely "
            "{entities_removed}) · engram {engram} · vectors {vectors}",
            "撤下的派生数据：信念三元组 {triples} · 实体链接 {entity_links}（整个删掉的实体 {entities_removed}）"
            " · engram {engram} · 向量 {vectors}").format(**d))
    if d["beliefs_reopened"] or d["beliefs_reclosed"]:
        print(t(f"real beliefs cut short by synthetic facts: {d['beliefs_reopened']} made current again, "
                f"{d['beliefs_reclosed']} end dates recomputed from later real facts",
                f"被合成事实截断的真实信念：恢复成现任 {d['beliefs_reopened']} 条，"
                f"按后来的真实事实重算终点 {d['beliefs_reclosed']} 条"))
    if d["beliefs_to_review"]:
        print(t(f"⚠ {d['beliefs_to_review']} beliefs were changed by synthetic facts through retract-and-reinsert; "
                "not restored automatically, please review them",
                f"⚠ 有 {d['beliefs_to_review']} 条信念被合成事实以「撤回再插回」的方式改过，没有自动还原，请人工核对"))
    if d["triples_unknown_source"] or d["engram_unknown_source"]:
        print(t(f"ℹ derived data whose source id is not in the hub (cannot tell real from synthetic; kept): beliefs "
                f"{d['triples_unknown_source']} · engram {d['engram_unknown_source']}",
                f"ℹ 来源 id 在中枢里找不到的派生数据（分不清真假，没删）：信念 {d['triples_unknown_source']}"
                f" · engram {d['engram_unknown_source']}"))
    if rep.get("restored_with_original_provenance"):
        n = rep['restored_with_original_provenance']
        print(t(f"{n} of the restored records got back the source they were written with",
                f"补回的记录里 {n} 条找回了写入时绑定的来源"))
    if "finished_erasures" in rep:
        st = rep['finished_erasures_backup'].get('status')
        print(t(f"erased {rep['finished_erasures']} more journal lines (shadow copy: {st})",
                f"日志里补抹了 {rep['finished_erasures']} 行（影子副本：{st}）"))
    if "forget_senders" in rep:
        fs = rep["forget_senders"]
        print(t("quarantined by sender: ", "按发送者送进隔离区：")
              + (fs.get("skipped") or t(f"{fs.get('removed', 0)} (restorable for 30 days)", f"{fs.get('removed', 0)} 条（30 天内可还原）")))
    _print_summary(rep["after"], t("After:", "修复后："))
    print(t("self-check: ", "自检：") + " ".join(f"{'✅' if v else '❌'}{k}" for k, v in rep["health_after"].items()))
    if rep["after"]["unfinished_erasures"] and "finished_erasures" not in rep:
        print(t("Some erasures are still unfinished: they are gone from the hub and from search, but the journal text "
                f"is still there.\n    {hints.cmd('repair', '--apply --finish-erasures')}",
                "还剩遗忘没做完的记录：它们已经从中枢和检索里撤下，但日志正文还在。\n"
                f"    {hints.cmd('repair', '--apply --finish-erasures')}"))
    print(t("Remember to restart the Hermes gateway / MCP server.", "记得重启 Hermes 网关 / MCP 服务。"))
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
