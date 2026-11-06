"""
core/lookup.py — 按编号、词和日期查记忆（v7.20.0）

三处共用：
  - 终端：hermes nyx search / show / forget / restore / edit（nexsandglass/memory_cli.py）
  - agent：nyx_read（一条的全文和前后几句），sandglass_search / sandglass_recent 的日期和 since / until

每条结果都带 mem_id、说这句话的时间、谁说的、信任（trusted / unverified / tainted）。
给模型看的（for_model）：tainted 的正文不给，unverified 的正文前加来源标注 —— 和召回注入同一套规矩。

日期范围：since / until 都含当天；写 YYYY-MM-DD、YYYY-MM 或 YYYY（until 2026-03 = 到 3 月底）。
带日期范围的搜索不只是把召回结果按日期筛一遍：那样排在前面的都在范围外时就什么都不剩了。
范围内的记忆另外按问句里的实词（core/relevance.py 的规则）直接在中枢里找。
"""
from __future__ import annotations

import calendar
import re
from datetime import datetime
from typing import Optional

_DAY = re.compile(r"^\s*(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?\s*$")
_COLS = ("mem_id", "seq", "ts", "sender", "text", "line_start", "deleted_at")
_SELECT = "SELECT " + ", ".join(_COLS) + " FROM memories"
MAX_LIMIT = 50


class BadDate(ValueError):
    pass


def bound(value, *, end: bool = False) -> Optional[str]:
    """'2026-03-05' / '2026-03' / '2026' → 和中枢 ts（YYYY-MM-DD HH:MM:SS）比较用的边界，含当天 / 当月 / 当年。"""
    if value is None or str(value).strip() == "":
        return None
    m = _DAY.match(str(value))
    if not m:
        raise BadDate(f"not a date: {value!r} (use YYYY-MM-DD, YYYY-MM or YYYY)")
    y = int(m.group(1))
    mo = int(m.group(2)) if m.group(2) else (12 if end else 1)
    try:
        last = calendar.monthrange(y, mo)[1]
        d = int(m.group(3)) if m.group(3) else (last if end else 1)
        datetime(y, mo, d)
    except (ValueError, calendar.IllegalMonthError):
        raise BadDate(f"not a date: {value!r}") from None
    return f"{y:04d}-{mo:02d}-{d:02d} " + ("23:59:59" if end else "00:00:00")


def _conn():
    from nexsandglass.core import memid
    return memid.get_conn()


def who(sender: str) -> str:
    s = str(sender or "").strip().lower()
    if s in ("user", "owner", "principal"):
        return "user"
    if s in ("agent", "assistant"):
        return "assistant"
    return s or "unknown"


def _item(row) -> dict:
    d = dict(zip(_COLS, row))
    d["who"] = who(d["sender"])
    d["date"] = str(d["ts"] or "")[:16]
    try:
        from nexsandglass.core import provenance
        p = provenance.get(d["mem_id"])
    except Exception:
        p = None
    d["trust"] = p.signal if p else ""
    d["label"] = p.label() if p else ""
    d["status"] = "live" if not d["deleted_at"] else "erased"
    return d


def get(ref) -> Optional[dict]:
    """一条记忆：mem_id、序号、或者召回结果里的 id。在隔离区里的也找得到（status=quarantined，正文从隔离区取）；
    彻底擦掉的只剩一个墓碑（status=erased，没有正文）。找不到 → None。"""
    from nexsandglass.core import memid
    mid = memid.resolve(ref, allow_deleted=True)
    m = None if mid else re.match(r"^\s*[a-z_]+:(\d+)\s*$", str(ref or ""))
    if m:                                       # 召回 id 里带的是日志行号（search_router:123 / sandglass:123）
        r = _conn().execute("SELECT mem_id FROM memories WHERE line_start=?", (int(m.group(1)),)).fetchone()
        mid = r[0] if r else None
    if not mid:
        return None
    row = _conn().execute(_SELECT + " WHERE mem_id=?", (mid,)).fetchone()
    if not row:
        return None
    d = _item(row)
    if d["deleted_at"]:
        try:
            from nexsandglass.core import quarantine
            q = quarantine.get(mid)
        except Exception:
            q = None
        if q:
            d.update(status="quarantined", text=q.get("text") or "", purge_after=q.get("purge_after") or "")
    return d


def around(item: dict, n: int = 3) -> tuple:
    """前后各 n 句（按说话时间，没删的）：(前面的, 后面的)，都按时间先后排。"""
    n = max(0, min(int(n or 0), 10))
    if not n or not item:
        return [], []
    ts, seq = item["ts"], item["seq"]
    c = _conn()
    before = c.execute(_SELECT + " WHERE deleted_at IS NULL AND ts <= ? AND NOT (ts = ? AND seq >= ?) "
                       "ORDER BY ts DESC, seq DESC LIMIT ?", (ts, ts, seq, n)).fetchall()
    after = c.execute(_SELECT + " WHERE deleted_at IS NULL AND ts >= ? AND NOT (ts = ? AND seq <= ?) "
                      "ORDER BY ts ASC, seq ASC LIMIT ?", (ts, ts, seq, n)).fetchall()
    return [_item(r) for r in reversed(before)], [_item(r) for r in after]


def _range_sql(lo, hi) -> tuple:
    where, args = ["deleted_at IS NULL"], []
    if lo:
        where.append("ts >= ?")
        args.append(lo)
    if hi:
        where.append("ts <= ?")
        args.append(hi)
    return " AND ".join(where), args


def recent(n: int = 10, *, since=None, until=None) -> list:
    """最近说的 n 句（按说话时间，新的在前）；可以限定日期范围。"""
    lo, hi = bound(since), bound(until, end=True)
    where, args = _range_sql(lo, hi)
    rows = _conn().execute(_SELECT + f" WHERE {where} ORDER BY ts DESC, seq DESC LIMIT ?",
                           (*args, max(1, min(int(n or 10), MAX_LIMIT)))).fetchall()
    return [_item(r) for r in rows]


def containing(substr: str, limit: int = 500) -> list:
    """正文里含这段话的（子串，英文字母不分大小写；% 和 _ 就是字面的字符），按时间先后。"""
    rows = _conn().execute(_SELECT + " WHERE deleted_at IS NULL AND instr(lower(text), lower(?)) > 0 "
                           "ORDER BY ts, seq LIMIT ?", (str(substr), int(limit))).fetchall()
    return [_item(r) for r in rows]


def search(query: str, *, limit: int = 10, since=None, until=None) -> list:
    """按问句找记忆，相关的在前。走和每轮注入同一条召回路，再过同一道实词门；
    召回里不是哪一句原话的（当前信念、画像），没有日期范围时也给（mem_id 为空，id 是召回 id，who = derived）。"""
    from nexsandglass.core import relevance
    lo, hi = bound(since), bound(until, end=True)
    limit = max(1, min(int(limit or 10), MAX_LIMIT))
    q = str(query or "").strip()
    if not q:
        return recent(limit, since=since, until=until)
    ranged = bool(lo or hi)
    out, seen = [], set()

    from nexsandglass.runtime.orchestrator import get_orchestrator
    mc = get_orchestrator().recall(q, token_budget=(max(limit, 40) if ranged else limit) * 200)
    objs = relevance.keep(q, [o for o in (mc.objects or []) if o.content and str(o.content).strip()])
    lines = sorted({int(o.source_id) for o in objs if o.source_id and str(o.source_id).isdigit()})
    by_line = {}
    if lines:
        marks = ",".join("?" * len(lines))
        by_line = {r[5]: r for r in _conn().execute(
            _SELECT + f" WHERE deleted_at IS NULL AND line_start IN ({marks})", lines).fetchall()}
    for o in objs:
        row = by_line.get(int(o.source_id)) if o.source_id and str(o.source_id).isdigit() else None
        if row is not None:
            if row[0] in seen or (lo and row[2] < lo) or (hi and row[2] > hi):
                continue
            seen.add(row[0])
            out.append(_item(row))
        elif not ranged and o.source_id is None:
            text = str(o.content)
            if text.startswith("(unverified") and ") " in text:
                continue                         # 带来源标注、又对不上原话的：不给（原话那条会单独出现）
            out.append({"mem_id": None, "id": o.memory_id, "text": text, "who": "derived",
                        "kind": getattr(o, "provenance", "") or "", "date": "", "trust": "", "label": "",
                        "status": "live"})
        if len(out) >= limit:
            return out

    if ranged and len(out) < limit:
        terms = relevance.content_terms(q)[:12]
        if terms:
            where, args = _range_sql(lo, hi)
            likes = " OR ".join("text LIKE ?" for _ in terms)
            rows = _conn().execute(_SELECT + f" WHERE {where} AND ({likes}) ORDER BY ts DESC, seq DESC LIMIT 2000",
                                   (*args, *[f"%{t}%" for t, _ in terms])).fetchall()
            scored = [(relevance.matches(terms, r[4]), r) for r in rows if r[0] not in seen]
            scored = [x for x in scored if x[0]]
            scored.sort(key=lambda x: (x[0], x[1][2], x[1][1]), reverse=True)   # 对上的实词多的在前，一样多的新的在前
            for _, r in scored[:limit - len(out)]:
                out.append(_item(r))
    return out


def for_model(item: dict, chars: Optional[int] = 300) -> dict:
    """给 agent 看的一条：忘掉的和 tainted 的不给正文，unverified 的加来源标注，太长的截断并标 more。"""
    d = {"id": item.get("mem_id") or item.get("id"), "date": item.get("date") or None, "who": item.get("who")}
    status = item.get("status") or "live"
    if status != "live":
        d["status"] = status
        d["text"] = None
        if status == "quarantined":
            d["note"] = "the user had this forgotten; nyx_restore brings it back if they ask"
            d["restorable_until"] = str(item.get("purge_after") or "")[:10] or None
        else:
            d["note"] = "erased for good"
    elif item.get("trust") == "tainted":
        d["text"] = None
        d["withheld"] = "flagged as a possible prompt injection when it was written; not shown"
    else:
        text = str(item.get("text") or "")
        if chars and len(text) > chars:
            text, d["more"] = text[:chars], True
        # 来源标注只给外部来源（网页、工具、群里别人）：助手自己说过的话 who 已经写明是 assistant
        label = "" if item.get("who") == "assistant" else (item.get("label") or "")
        d["text"] = label + text
    return {k: v for k, v in d.items() if v is not None or k == "text"}


def tool(name: str, args: Optional[dict]) -> str:
    """sandglass_search / sandglass_recent / nyx_read 的实现（Hermes 记忆提供器和 MCP 服务器共用），返回 JSON。"""
    import json
    args = args or {}

    def _int(v, default):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    try:
        if name == "sandglass_search":
            items = search(str(args.get("query") or ""), limit=_int(args.get("limit"), 10),
                           since=args.get("since"), until=args.get("until"))
            return json.dumps([for_model(i) for i in items], ensure_ascii=False)
        if name == "sandglass_recent":
            items = recent(_int(args.get("n", args.get("limit")), 10), since=args.get("since"), until=args.get("until"))
            return json.dumps([for_model(i) for i in items], ensure_ascii=False)
        if name == "nyx_read":
            ref = str(args.get("id") or args.get("mem_id") or "").strip()
            if not ref:
                return json.dumps({"error": "nyx_read needs id"})
            it = get(ref)
            if not it:
                return json.dumps({"status": "not_found", "id": ref,
                                   "note": "No stored message has that id. Ids such as wthread:… are facts derived "
                                           "from messages: use nyx_belief for those."}, ensure_ascii=False)
            if it["status"] != "live":
                return json.dumps({"memory": for_model(it)}, ensure_ascii=False)
            before, after = around(it, _int(args.get("around"), 3))
            return json.dumps({"memory": for_model(it, chars=None), "before": [for_model(x) for x in before],
                               "after": [for_model(x) for x in after]}, ensure_ascii=False)
    except BadDate as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    return json.dumps({"error": f"unknown tool: {name}"})


def after_write() -> None:
    """在 provider 之外写了记忆（导入、终端里改一条）之后：补全文 / 倒排索引、画像、向量。"""
    from nexsandglass.core import sandglass_sqlite, profile, semantic
    from nexsandglass.features import sandglass_vault
    sandglass_sqlite.sync_incremental()
    sandglass_vault._sync_index()
    profile.refresh()
    semantic.schedule_index()
