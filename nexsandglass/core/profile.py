"""常驻画像（v7.19.0）—— 用户亲口说过的、关于自己的长期信息，每个会话都放在系统提示里

以前系统提示里的 [Core facts] 是一次「空问题」召回的结果，实际就是日志里最早的十来条消息
（多数是第一天的寒暄和工作对话），还混着助手的客套话。对 agent 有用的是另一类东西：
用户对它的长期要求（以后用中文、别用 emoji）、饮食和健康禁忌（花生过敏、吃素）、家人和宠物、
住在哪 / 做什么、喜欢什么 —— 这些问「今晚吃什么」时字面上一个词都对不上，靠每轮召回是找不到的。

分类（零 LLM，中英文规则，只看用户本人的话）
  instruction  对 agent 的长期要求：以后 / 每次 / 别再 / 叫我…；from now on / please never / call me / keep your answers…
  health       饮食和健康：过敏、吃素、不吃、乳糖不耐、糖尿病、怀孕…；allergic / vegan / don't eat / lactose…
  people       家人、伴侣、宠物：我女儿 / 我老婆 / 我妈 / 养了一只猫；my son / my wife / my mum / we have a dog…
  identity     名字、住处、工作：我叫 / 住在 / 搬到 / 在…上班；my name is / I live in / I moved to / I work as…
  likes        喜好：喜欢 / 讨厌 / 业余时间；I love / I hate / my favourite / in my spare time…

存储：画像只存引用（mem_id、类别），正文渲染时从中枢取 —— 被遗忘（隔离区）的、密钥被隐去的，常驻块跟着变。
增量：按中枢 seq 水位线扫描新消息；规则版本变了从头重扫。
安全：只收主人（sender = user）的、来源可信的行；长消息（像是贴进来的文档）不当「对 agent 的要求」，
      免得贴进来的网页里一句「from now on…」变成每个会话都在的指令。
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

RULES_VERSION = "6"
KINDS = ("instruction", "health", "people", "identity", "likes")
HEADERS = {
    "instruction": "Standing requests to you",
    "health": "Health and diet",
    "people": "Family, partner and pets",
    "identity": "About them",
    "likes": "Likes and dislikes",
}
PER_KIND = {"instruction": 6, "health": 6, "people": 5, "identity": 5, "likes": 5}
LINE_CHARS = 160
TOTAL_CHARS = 1800
_INSTRUCTION_MAX_LEN = 300          # 更长的像是贴进来的文档，不当作对 agent 的要求

_SCHEMA = """
CREATE TABLE IF NOT EXISTS profile_items (
    mem_id TEXT PRIMARY KEY,
    seq    INTEGER NOT NULL,
    kind   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_profile_seq ON profile_items(seq);
CREATE TABLE IF NOT EXISTS profile_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# ══════════════════════════════════════════════════════════
# 规则
# ══════════════════════════════════════════════════════════

def classify(text: str, *, adapter=None) -> Optional[tuple]:
    from nexsandglass.language import DEFAULT_ADAPTER
    return (adapter or DEFAULT_ADAPTER).classify_profile(text)


def _classify_record(text, mid, conn, ts=None):
    from nexsandglass.language import observation
    with observation.bind(observation.for_record(conn, mid, ts)):
        return classify(text)


def slots(kind: str, clause: str, *, adapter=None) -> set:
    from nexsandglass.language import DEFAULT_ADAPTER
    return (adapter or DEFAULT_ADAPTER).profile_slots(kind, clause)


# Private compatibility helpers used by older callers.
from nexsandglass.language.bilingual import _clauses, _sentences, _excerpt


# ══════════════════════════════════════════════════════════
# 扫描（增量）
# ══════════════════════════════════════════════════════════

def _ensure(conn: sqlite3.Connection) -> None:
    for statement in _SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def _meta(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM profile_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def _trusted(line_start, conn: Optional[sqlite3.Connection] = None) -> bool:
    try:
        from nexsandglass.core import provenance as _prov
        if line_start is None:
            return True
        prov = _prov.for_line(int(line_start), conn=conn)
        return prov is None or prov.signal == _prov.TRUSTED
    except Exception:
        # Failure to read the bound source must not admit it as a trusted fact.
        return False


_REFRESH_LOCK = threading.Lock()
BATCH = 2000                     # 会话开始时最多补这么多条；剩下的由后台预热线程补完（provider._warm）


def refresh(conn: Optional[sqlite3.Connection] = None, max_rows: Optional[int] = None, wait: bool = True) -> int:
    """把水位线之后用户说的话分类进画像。返回新收进的条数。

    max_rows：这一次最多看多少条（None = 全部）。wait=False：别的线程正在补就直接返回，不等。
    """
    if not _REFRESH_LOCK.acquire(blocking=wait):
        return 0
    try:
        return _refresh(conn, max_rows)
    finally:
        _REFRESH_LOCK.release()


def refresh_ready(conn: Optional[sqlite3.Connection] = None, *, deadline: float) -> int:
    """Index through a fixed initial journal watermark before a one-shot read.

    Unlike best-effort render(), an incomplete first index must not masquerade
    as an empty/current voice snapshot. The deadline bounds lock waiting and
    is checked around 64-row batches; SQLite I/O retains its connection timeout
    and the host request's outer timeout still applies.
    """
    if not _REFRESH_LOCK.acquire(timeout=max(0.0, deadline - time.monotonic())):
        raise TimeoutError("Nyx voice profile is still refreshing")
    try:
        from nexsandglass.core import memid, projection_jobs
        conn = conn or memid.get_conn()
        target = int(conn.execute("SELECT COALESCE(MAX(seq), 0) FROM memories").fetchone()[0])
        added = 0
        previous_progress = None
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("Nyx voice profile is still refreshing")
            added += _refresh(conn, max_rows=64)
            ready = projection_jobs.ready_through(conn, "profile", RULES_VERSION, target)
            if time.monotonic() >= deadline:
                raise TimeoutError("Nyx voice profile is still refreshing")
            if ready:
                return added
            job = projection_jobs.get(conn, "profile")
            progress = (job.processor_version, job.checkpoint_seq, job.reset_required) if job else None
            # A committed batch can continue immediately. Back off only when
            # another process holds the lease or the checkpoint is stalled.
            if progress == previous_progress:
                time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
            previous_progress = progress
    finally:
        _REFRESH_LOCK.release()


def _refresh(conn: Optional[sqlite3.Connection], max_rows: Optional[int]) -> int:
    from nexsandglass.core import memid, projection_jobs
    conn = conn or memid.get_conn()
    _ensure(conn)
    version = RULES_VERSION
    legacy_matches = _meta(conn, "rules") == version
    legacy_mark = int(_meta(conn, "seq", "0") or 0) if legacy_matches else 0
    top = int(conn.execute("SELECT COALESCE(MAX(seq), 0) FROM memories").fetchone()[0] or 0)

    def batch(bound, lease):
        if lease.reset_required:
            bound.execute("DELETE FROM profile_items")
        mark = lease.checkpoint_seq
        sql = ("SELECT mem_id, seq, text, line_start FROM memories WHERE seq > ? AND seq <= ? "
               "AND sender = 'user' AND deleted_at IS NULL ORDER BY seq")
        params = (mark, lease.target_seq, max_rows) if max_rows else (mark, lease.target_seq)
        rows = bound.execute(sql + (" LIMIT ?" if max_rows else ""), params).fetchall()
        last = rows[-1][1] if max_rows and len(rows) >= max_rows else lease.target_seq
        bound.execute("DELETE FROM profile_items WHERE seq > ? AND seq <= ?", (mark, last))
        added = 0
        for mid, seq, text, line_start in rows:
            hit = _classify_record(text, mid, bound)
            if hit and _trusted(line_start, conn=bound):
                bound.execute("INSERT OR REPLACE INTO profile_items(mem_id, seq, kind) VALUES (?, ?, ?)",
                              (mid, seq, hit[0]))
                added += 1
        last = rows[-1][1] if max_rows and len(rows) >= max_rows else lease.target_seq
        bound.execute("INSERT OR REPLACE INTO profile_meta(key,value) VALUES ('rules',?)", (version,))
        bound.execute("INSERT OR REPLACE INTO profile_meta(key,value) VALUES ('seq',?)", (str(last),))
        return last, added

    return projection_jobs.run(conn, "profile", version, top, batch,
                               initial_checkpoint=legacy_mark, initial_reset=not legacy_matches)


def reconsider(mem_ids) -> int:
    """从隔离区救回来的记忆重新分类（v7.20.0）。

    refresh 只看水位线之后的、没删的行：一条话在画像刷新时正好在隔离区里，就被跳过了；救回来以后水位线早就过了它，
    不会再看 —— 救回的「我对花生过敏」从此不在常驻块里。救回时调这个补上。
    """
    from nexsandglass.core import memid
    conn = memid.get_conn()
    _ensure(conn)
    added = 0
    with _REFRESH_LOCK:
        for mid in mem_ids or ():
            r = conn.execute("SELECT seq, text, line_start FROM memories WHERE mem_id = ? AND sender = 'user' "
                             "AND deleted_at IS NULL", (mid,)).fetchone()
            if not r:
                continue
            hit = _classify_record(r[1], mid, conn)
            if hit and _trusted(r[2], conn=conn):
                conn.execute("INSERT OR REPLACE INTO profile_items(mem_id, seq, kind) VALUES (?, ?, ?)",
                             (mid, r[0], hit[0]))
                added += 1
        conn.commit()
    return added


# ══════════════════════════════════════════════════════════
# 渲染
# ══════════════════════════════════════════════════════════

CHANGED_KEEP = 3                 # 「Changed since」里最多列几条


def items(conn: Optional[sqlite3.Connection] = None) -> dict:
    """{类别: [(日期, 摘句, mem_id)]}，新的在前，每类有上限，内容相同的只留最新一条；
    同一件事后来说法改了的，旧的分句不在类别里，列在 out["changed"]：[(日期, 被改的话, mem_id, 改口的日期)]。"""
    from nexsandglass.core import memid, projection_jobs
    conn = conn or memid.get_conn()
    _ensure(conn)
    out = {k: [] for k in KINDS}
    out["changed"] = []
    job = projection_jobs.get(conn, "profile")
    if job and (job.processor_version != RULES_VERSION or job.reset_required):
        return out
    seen = set()
    claimed = {}                                     # 话题槽 → 最新说它的那句的日期
    # 按说话的时间排，不按写入先后：导入的旧对话（hermes nyx import）写得晚，但不能排到最新
    rows = conn.execute("SELECT p.kind, m.mem_id, m.ts, m.text, m.line_start FROM profile_items p JOIN memories m "
                        "ON m.mem_id = p.mem_id WHERE m.deleted_at IS NULL AND m.sender='user' "
                        "ORDER BY m.ts DESC, m.seq DESC")
    for kind, mem_id, ts, text, line_start in rows:
        if all(len(out[k]) >= PER_KIND[k] for k in KINDS) and len(out["changed"]) >= CHANGED_KEEP:
            break                                    # 每类都满了：更早的不用再看
        if kind not in PER_KIND:
            continue
        if not _trusted(line_start, conn=conn):
            continue
        hit = _classify_record(text, mem_id, conn, ts)                         # 正文可能已被隐去密钥 / 改写：按现在的正文重新摘
        if not hit:
            continue
        date, excerpt = str(ts or "")[:10], hit[1]
        clauses = _clauses(excerpt)
        from nexsandglass.language.bilingual import comparison_slots
        cslots = comparison_slots(kind, clauses)
        mine = set().union(*cslots) if cslots else set()
        gone = {sl for sl in mine if sl in claimed}
        if gone:
            keep = [c for c, ss in zip(clauses, cslots) if ss and not ss <= gone]
            removed = [c for c, ss in zip(clauses, cslots) if ss and ss <= gone]
            if removed and len(out["changed"]) < CHANGED_KEEP:
                when = max(claimed[sl] for sl in gone)
                out["changed"].append((date, ("，" if _is_zh(excerpt) else ", ").join(removed), mem_id, when))
            if not keep:
                continue                             # 这句说的事后来都改了
            joiner = "，" if _is_zh(excerpt) else ", "
            excerpt = ("…" if clauses and clauses[0] not in keep else "") + joiner.join(keep)
        for sl in mine - gone:
            claimed[sl] = date
        if len(out[kind]) >= PER_KIND[kind]:
            continue
        key = re.sub(r"[\W_]+", "", excerpt.lower())
        if key in seen:
            continue
        seen.add(key)
        out[kind].append((date, excerpt, mem_id))
    return out


def _is_zh(text: str) -> bool:
    return bool(re.search(r"[一-鿿]", text or ""))


def render(conn: Optional[sqlite3.Connection] = None, *, deadline: Optional[float] = None) -> str:
    """常驻块里「关于用户」那一段；没有就返回空串。"""
    if deadline is not None:
        refresh_ready(conn, deadline=deadline)
    try:
        if deadline is None:
            refresh(conn, max_rows=BATCH, wait=False)       # 不让会话开始等：老库的历史由后台补
        got = items(conn)
    except Exception as e:
        logger.warning("画像读取失败（常驻块里先不放）: %s", e)
        return ""
    parts, used = [], 0
    for kind in KINDS:
        lines = []
        for date, text, _ in got[kind]:
            line = f"- [{date}] {text}" if date else f"- {text}"
            if used + len(line) > TOTAL_CHARS:
                break
            lines.append(line)
            used += len(line)
        if lines:
            parts.append(HEADERS[kind] + ":\n" + "\n".join(lines))
    if not parts:
        return ""
    changed = []
    for date, text, _, when in got.get("changed", []):
        line = f"- [{date}] {text} (changed {when})"
        if used + len(line) > TOTAL_CHARS:
            break
        changed.append(line)
        used += len(line)
    if changed:
        parts.append("Changed since (their earlier words, replaced by what is above):\n" + "\n".join(changed))
    return ("[About the user: their own words, newest first; [date] = when they said it]\n"
            + "\n".join(parts))
