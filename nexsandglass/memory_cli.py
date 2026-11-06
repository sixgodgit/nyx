"""
hermes nyx search / show / forget / restore / edit —— 在终端里查、看、忘、救回、改记忆（v7.20.0）

    hermes nyx search 花生                              找，相关的在前
    hermes nyx search 搬家 --since 2026-03 --until 2026-03   只看 3 月说的
    hermes nyx search --since 2026-11-01                不写词：这段时间说的，新的在前
    hermes nyx show m_ab12…  [--around 3]               全文、时间、谁说的、能不能信，前后几句
    hermes nyx forget m_ab12… [m_cd34…]                 送进隔离区（保留期内能救回）；先列出来问你
    hermes nyx forget --contains 旧地址                 正文里含这段话的都送走
    hermes nyx forget m_ab12… --now                     不留隔离区，当场擦掉（救不回来）
    hermes nyx restore                                  隔离区里有什么、哪天到期
    hermes nyx restore m_ab12…                          救回
    hermes nyx edit m_ab12… 改正后的话                  改一条：旧的进隔离区，新的按你的话、原来的时间记下

这些只在终端里用，不经过模型。agent 那边有 sandglass_search / nyx_read / nyx_forget / nyx_restore，
但 agent 不能当场擦除，也不能改写原话。
不在 Hermes 里时：python3 -m nexsandglass.memory_cli search 花生
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import unicodedata
from datetime import datetime

from nexsandglass.core import hints
from nexsandglass.core.lang import t

logger = logging.getLogger(__name__)

COMMANDS = ("search", "show", "forget", "restore", "edit")


# ── 显示 ──────────────────────────────────────────────────

def _cols(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _fit(text, cols: int) -> str:
    """压成一行，按终端显示宽度截断（中文一个字占两格）。"""
    s = " ".join(str(text or "").split())
    if _cols(s) <= cols:
        return s
    out, used = [], 0
    for c in s:
        w = _cols(c)
        if used + w > cols - 1:
            break
        out.append(c)
        used += w
    return "".join(out) + "…"


def _width() -> int:
    return max(60, shutil.get_terminal_size((100, 20)).columns)


def _who(item) -> str:
    w = item.get("who")
    return {"user": t("you", "你"), "assistant": t("assistant", "助手")}.get(w, w or "?")


def _mark(item) -> str:
    tr = item.get("trust")
    if tr == "tainted":
        return t("  [possible injection]", "  [疑似注入]")
    if tr == "unverified" and item.get("who") != "assistant":       # 助手的话本来就不是你说的，不用再标
        return t("  [unverified]", "  [未经证实]")
    return ""


def _rows(items, indent: str = "") -> None:
    w = _width()
    for it in items:
        mid = it.get("mem_id") or it.get("id") or "-"
        head = f"{indent}{mid:<18}  {it.get('date') or '':<16}  {_who(it):<9}  "
        mark = _mark(it)
        print(head + _fit(it.get("text"), w - _cols(head) - _cols(mark) - 1) + mark)


def _confirm(question: str, word: str = "") -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        ans = input(question).strip().lower()
    except EOFError:
        return False
    return ans == word if word else ans in ("y", "yes", "是", "好")


def _json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1, default=str))


# ── search / show ─────────────────────────────────────────

def cmd_search(args) -> int:
    from nexsandglass.core import lookup
    q = " ".join(args.words).strip()
    if not q and not (args.since or args.until):
        print(t("Give some words to search for, or a date range (--since / --until).",
                "写几个要找的词，或者给一个日期范围（--since / --until）。"), file=sys.stderr)
        return 2
    try:
        items = lookup.search(q, limit=args.n, since=args.since, until=args.until)
    except lookup.BadDate as e:
        print(e, file=sys.stderr)
        return 2
    if args.json:
        _json(items)
        return 0
    if not items:
        print(t("Nothing found.", "没找到。"))
        return 0
    _rows(items)
    print(t(f"\nFull text and what was said around it: {hints.cmd('show', '<id>')}",
            f"\n看全文和前后几句：{hints.cmd('show', '<id>')}"))
    return 0


def cmd_show(args) -> int:
    from nexsandglass.core import lookup
    it = lookup.get(args.ref)
    if not it:
        print(t(f"No memory {args.ref}.", f"没有 {args.ref} 这条记忆。"), file=sys.stderr)
        return 1
    before, after = lookup.around(it, args.around)
    if args.json:
        _json({"memory": it, "before": before, "after": after})
        return 0
    trust = {"trusted": t("trusted", "可信"), "unverified": t("unverified", "未经证实"),
             "tainted": t("possible injection", "疑似注入")}.get(it.get("trust"), it.get("trust") or "")
    print(" · ".join(x for x in (it["mem_id"], str(it["ts"]), _who(it), trust) if x))
    if it["status"] == "quarantined":
        print(t(f"Forgotten: in the quarantine until {str(it.get('purge_after'))[:10]}. "
                f"Bring it back: {hints.cmd('restore', it['mem_id'])}",
                f"已遗忘：在隔离区里，{str(it.get('purge_after'))[:10]} 之前都能救回：{hints.cmd('restore', it['mem_id'])}"))
    elif it["status"] == "erased":
        print(t(f"Erased on {str(it['deleted_at'])[:10]}: only a tombstone is left.",
                f"{str(it['deleted_at'])[:10]} 已擦除，只剩一个墓碑。"))
    if it.get("text"):
        print("─" * min(_width(), 60))
        print(it["text"])
        print("─" * min(_width(), 60))
    if before:
        print(t("before:", "前面："))
        _rows(before, "  ")
    if after:
        print(t("after:", "后面："))
        _rows(after, "  ")
    return 0


# ── forget / restore ──────────────────────────────────────

def cmd_forget(args) -> int:
    from nexsandglass.core import lookup
    from nexsandglass.runtime import facade
    if args.contains:
        if len(args.contains.strip()) < 2:
            print(t("--contains needs at least 2 characters.", "--contains 至少两个字。"), file=sys.stderr)
            return 2
        items = lookup.containing(args.contains.strip())
    else:
        items, missing = [], []
        for ref in args.refs:
            it = lookup.get(ref)
            if it and it["status"] == "live":
                if all(x["mem_id"] != it["mem_id"] for x in items):
                    items.append(it)
            else:
                missing.append(ref)
        for ref in missing:
            print(t(f"{ref}: no such memory, or already forgotten.", f"{ref}：没有这条，或者已经忘了。"), file=sys.stderr)
    if not items:
        if not args.refs and not args.contains:
            print(t("Say what to forget: memory ids, or --contains TEXT.", "说要忘哪些：记忆编号，或者 --contains 一段话。"),
                  file=sys.stderr)
            return 2
        print(t("Nothing to forget.", "没有可以忘的。"))
        return 1 if args.refs else 0
    if args.json and not args.yes:
        _json({"would_forget": items, "permanent": bool(args.now)})
        return 0
    if not args.json:
        _rows(items)
    if not args.yes:
        if args.now:
            ok = _confirm(t(f"Erase these {len(items)} now? This cannot be undone. Type erase to confirm: ",
                            f"现在就擦掉这 {len(items)} 条？擦了就救不回来。输入 erase 确认："), "erase")
        else:
            ok = _confirm(t(f"Move these {len(items)} to the quarantine (restorable)? [y/N] ",
                            f"把这 {len(items)} 条送进隔离区（能救回）？[y/N] "))
        if not ok:
            print(t("Nothing forgotten." + ("" if sys.stdin.isatty() else " (not a terminal: pass --yes)"),
                    "什么都没忘。" + ("" if sys.stdin.isatty() else "（不是终端：要加 --yes）")))
            return 1
    r = facade.forget({"mem_ids": [it["mem_id"] for it in items], "reason": "user_forget_cli",
                       "purge_now": bool(args.now)})
    if args.json:
        _json(r)
        return 0 if r.get("ok") else 1
    if not r.get("ok"):
        print(t(f"Failed: {r.get('error')}", f"失败：{r.get('error')}"), file=sys.stderr)
        return 1
    if args.now:
        print(t(f"Erased {r['removed']}.", f"已擦掉 {r['removed']} 条。"))
    else:
        until = str(r.get("purge_after") or "")[:10]
        print(t(f"Moved {r['removed']} to the quarantine" + (f", restorable until {until}" if until else "")
                + f": {hints.cmd('restore', '<id>')}",
                f"已送进隔离区 {r['removed']} 条" + (f"，{until} 之前都能救回" if until else "")
                + f"：{hints.cmd('restore', '<id>')}"))
    return 0


def cmd_restore(args) -> int:
    from nexsandglass.core import erasure, lookup
    from nexsandglass.runtime import facade
    if not args.refs:
        items = erasure.quarantine_list(limit=args.n)
        if args.json:
            _json(items)
            return 0
        if not items:
            print(t("The quarantine is empty.", "隔离区是空的。"))
            return 0
        w = _width()
        print(t(f"{'id':<18}  {'said on':<10}  {'until':<10}  text", f"{'编号':<18}  {'说的那天':<10}  {'到期':<10}  内容"))
        for q in items:
            head = f"{q['mem_id']:<18}  {str(q.get('ts') or '')[:10]:<10}  {str(q.get('purge_after') or '')[:10]:<10}  "
            print(head + _fit(q.get("preview"), w - _cols(head) - 1))
        print(t(f"\nBring one back: {hints.cmd('restore', '<id>')}", f"\n救回一条：{hints.cmd('restore', '<id>')}"))
        return 0
    rc, results = 0, []
    for ref in args.refs:
        it = lookup.get(ref)
        mid = it["mem_id"] if it else str(ref)
        r = facade.restore(mid)
        results.append(r)
        if args.json:
            continue
        if r.get("ok"):
            print(t(f"{mid}: restored.", f"{mid}：救回来了。"))
        else:
            rc = 1
            why = "; ".join(map(str, r.get("problems") or [])) or r.get("error") or t("not in the quarantine", "不在隔离区里")
            print(t(f"{mid}: not restored ({why}).", f"{mid}：没救回来（{why}）。"), file=sys.stderr)
    if any(r.get("ok") for r in results):
        try:
            lookup.after_write()
        except Exception as e:
            logger.warning("index catch-up after restore failed: %s", e)
    if args.json:
        _json(results)
    return rc


# ── edit ──────────────────────────────────────────────────

def record_as_user(text: str, ts: str) -> str:
    """把一句话按主人说的、在 ts 那个时间记下（和每轮对话同一条写入路：日志 + 晋升门）。返回 mem_id，失败 ""。"""
    from nexsandglass.core import clock
    from nexsandglass.core.sandglass_log import log_message
    try:
        when = datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        when = None
    if when:
        clock.set_global(when)
    try:
        mid = log_message(text, "user", return_id=True)
        if mid:
            try:
                from nexsandglass.runtime.orchestrator import get_orchestrator
                from nexsandglass.runtime.promotion import PromotionEngine
                if PromotionEngine(use_llm=False, timeout=2.0).observe(text).disposition == "promote":
                    get_orchestrator().observe(text, source="user", raw_already_logged=True)
            except Exception as e:
                logger.warning("edit: promotion skipped: %s", e)
        return mid or ""
    finally:
        if when:
            clock.set_global(None)


def cmd_edit(args) -> int:
    from nexsandglass.core import lookup
    from nexsandglass.runtime import facade
    it = lookup.get(args.ref)
    if not it or it["status"] != "live":
        print(t(f"{args.ref}: no such memory, or it was forgotten.", f"{args.ref}：没有这条，或者已经忘了。"), file=sys.stderr)
        return 1
    new = " ".join(args.text).strip() if args.text else None
    if new is None:
        if sys.stdin.isatty():
            print(f"{it['mem_id']} · {it['ts']} · {_who(it)}\n{it['text']}")
            try:
                new = input(t("Change it to (empty = keep it): ", "改成（直接回车 = 不改）：")).strip()
            except EOFError:
                new = ""
        else:
            new = sys.stdin.read().strip()
    if not new or new == str(it["text"]).strip():
        print(t("Nothing changed.", "没有改。"))
        return 0
    new_id = record_as_user(new, it["ts"])
    if not new_id:
        print(t("Could not save the new text; the old one is unchanged.", "新的写不进去，旧的没动。"), file=sys.stderr)
        return 1
    r = facade.forget({"mem_ids": [it["mem_id"]], "reason": "user_edit"})
    try:
        lookup.after_write()
    except Exception as e:
        logger.warning("index catch-up after edit failed: %s", e)
    out = {"new": new_id, "old": it["mem_id"], "old_forgotten": bool(r.get("ok") and r.get("removed")),
           "restorable_until": str(r.get("purge_after") or "")[:10]}
    if args.json:
        _json(out)
        return 0
    print(t(f"Saved {new_id} as your words, dated {it['date']}.", f"已记下 {new_id}：算你说的，日期还是 {it['date']}。"))
    if out["old_forgotten"]:
        print(t(f"The old one ({it['mem_id']}) is in the quarantine until {out['restorable_until']}: "
                f"{hints.cmd('restore', it['mem_id'])} brings it back.",
                f"旧的（{it['mem_id']}）在隔离区里，{out['restorable_until']} 之前能救回：{hints.cmd('restore', it['mem_id'])}"))
    else:
        print(t(f"The old one ({it['mem_id']}) could not be forgotten: {r.get('error') or '?'}",
                f"旧的（{it['mem_id']}）没能送走：{r.get('error') or '?'}"), file=sys.stderr)
        return 1
    return 0


# ── 入口 ──────────────────────────────────────────────────

def _parser() -> argparse.ArgumentParser:
    prog = "hermes nyx" if hints.via_hermes() else "python3 -m nexsandglass.memory_cli"
    ap = argparse.ArgumentParser(prog=prog, description=t(
        "Look up, read, forget, restore and correct memories (terminal only; never goes through the model)",
        "查、看、忘、救回、改记忆（只在终端里用，不经过模型）"))
    sub = ap.add_subparsers(dest="cmd", metavar="{" + ",".join(COMMANDS) + "}")
    p = sub.add_parser("search", help=t("find memories, most relevant first", "找记忆，相关的在前"))
    p.add_argument("words", nargs="*")
    p.add_argument("--since", help=t("from this day on (YYYY-MM-DD, YYYY-MM or YYYY)", "从这天起（含）：YYYY-MM-DD、YYYY-MM 或 YYYY"))
    p.add_argument("--until", help=t("up to this day (inclusive)", "到这天为止（含）"))
    p.add_argument("-n", type=int, default=20, help=t("how many (max 50)", "最多几条（上限 50）"))
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("show", help=t("one memory in full, with what was said around it", "看一条的全文和前后几句"))
    p.add_argument("ref", help=t("memory id (m_…)", "记忆编号（m_…）"))
    p.add_argument("--around", type=int, default=3, help=t("how many lines before and after (max 10)", "前后各几句（上限 10）"))
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("forget", help=t("move memories to the quarantine (restorable)", "送进隔离区（能救回）"))
    p.add_argument("refs", nargs="*")
    p.add_argument("--contains", help=t("every memory whose text contains this", "正文里含这段话的都算"))
    p.add_argument("--now", action="store_true", help=t("erase now instead: cannot be undone", "不进隔离区，当场擦掉：救不回来"))
    p.add_argument("--yes", "-y", action="store_true", help=t("do not ask", "不问"))
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("restore", help=t("bring forgotten memories back; no id = list the quarantine",
                                         "把忘掉的救回来；不写编号 = 看隔离区里有什么"))
    p.add_argument("refs", nargs="*")
    p.add_argument("-n", type=int, default=50)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("edit", help=t("correct a memory: the old one goes to the quarantine", "改一条：旧的进隔离区"))
    p.add_argument("ref")
    p.add_argument("text", nargs="*", help=t("the corrected text (none: asked for, or read from stdin)",
                                             "改正后的话（不写：现场问，或者从标准输入读）"))
    p.add_argument("--json", action="store_true")
    return ap


def main(argv=None) -> int:
    ap = _parser()
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2
    return {"search": cmd_search, "show": cmd_show, "forget": cmd_forget, "restore": cmd_restore,
            "edit": cmd_edit}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
