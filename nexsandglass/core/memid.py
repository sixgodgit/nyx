"""
core/memid.py — ID 中枢（Identity Hub）

解决的问题
==========
在此模块之前，Nyx 用 **sandglass.txt 的物理行号** 作为记忆的事实主键：

  - shadow_sand.trust / entities / fact_tags   以 line_num 为键
  - weavethread.wthread_triples.source_line    以 line_num 为来源
  - sandglass.db (FTS5) 的 rowid               以 line_num 为 rowid
  - sandglass.idx 倒排                          token -> [line_num, ...]

行号有三个致命性质：

  1. **不是写入方分配的**。log_message() 追加文件时并不知道自己写的是第几行，
     于是 shadow_index() 用 `SELECT COUNT(*) FROM trust + 1` 猜。一旦某次
     shadow_index 抛异常被 log_message 吞掉（v7.3 修的 `database is locked`
     正是这种场景），计数就永久少一，**之后每一条记忆的 provenance 全部错位一格**，
     静默且不可恢复。
  2. **删除即位移**。删掉第 N 行，N 之后所有记忆的主键全部改变。这就是
     facade.forget() 只敢动 engram_store.jsonl、不敢碰 sandglass.txt 的根因，
     也是"说了忘记但还记得"这个 bug 的根因。
  3. **跨库不可校验**。行号是个裸整数，指错了也看不出来。

本模块引入稳定标识 `mem_id`，把"身份"从"位置"里剥离出来。

设计
====
单一 ID 分配点，单库 `nyx.db`：

    memories(
      mem_id       TEXT PRIMARY KEY,   -- m_<sha1(ts|sender|text)[:16]>，内容确定
      seq          INTEGER UNIQUE,     -- 单调递增序号，删除不回收、不重排
      ts, sender, text,
      content_hash TEXT,               -- sha1(text)，幂等/去重
      line_num     INTEGER,            -- 写入当时的 txt 行号（仅供迁移与审计）
      deleted_at   TEXT                -- tombstone，逻辑删除
    )

三条铁律：

  A. **原子分配**：allocate() 在 *同一把文件锁* 内完成「INSERT memories」+
     「append sandglass.txt」。DB 成功而文件失败则回滚 DB 行 —— 两者永远 1:1，
     `seq == line_num`，不存在猜行号这回事。
  B. **确定性 ID**：mem_id 由 (ts, sender, text) 哈希得出，因此迁移脚本可重复
     运行而不产生新 ID（幂等），同一条记忆在任何库里算出来都一样。
  C. **只增不改**：seq 单调递增。删除写 deleted_at，不物理挪动任何既有记录，
     所以 forget 可以真删而不破坏别人的主键。

兼容
====
resolve() 接受历史上所有 ID 形态（裸行号 / "shadow:12" / "engram:<ts>" / mem_id），
统一解析为 mem_id。各子系统可以逐个迁移，不必一次改完。

纯 stdlib，零依赖。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from nexsandglass.core.sandglass_paths import _NB
from nexsandglass.core import provenance as _prov

logger = logging.getLogger(__name__)

def _nb() -> str:
    """当前数据目录。

    每次调用都重新读环境变量，而不是在 import 时固化 —— 否则
    os.environ['NEXSANDBASE_HOME'] 在模块导入之后改变（测试隔离、多租户、
    同进程切换数据目录）时，ID 中枢会继续写旧库，导致行号落到别处、
    /api/memories 读不到刚写入的记忆。
    """
    return os.environ.get("NEXSANDBASE_HOME") or _NB


# 显式覆盖（set_db_path / 测试 monkeypatch），带「归属目录」标记。
# 一旦环境里的 NEXSANDBASE_HOME 换成另一个目录，旧覆盖即作废——
# 否则 A 测试留下的覆盖会污染 B 测试（实测：三个路径互不相同，日记
# 写进 A 的目录、/api/memories 从 B 的目录读，结果读不到）。
_override_nb: Optional[str] = None
_DB: Optional[str] = None
_SANDGLASS: Optional[str] = None
_LOCK: Optional[str] = None


def _set_override(nb: Optional[str] = None) -> None:
    """记录 set_db_path 建立覆盖时的归属目录。"""
    global _override_nb
    _override_nb = nb


def _belongs(path: Optional[str]) -> bool:
    """覆盖路径是否属于当前数据目录。

    判定顺序：
    1. set_db_path 显式记录过归属 → 比对归属目录；
    2. 否则（测试 monkeypatch 直接 setattr）→ 用路径自身所在目录比对。
    两者任一不符即视为上个测试/租户的残留，自动作废。
    """
    if path is None:
        return False
    cur = os.path.abspath(_nb())
    if _override_nb is not None:
        return _override_nb == cur
    return os.path.dirname(os.path.abspath(path)) == cur


def _db_path() -> str:
    if _DB is not None and _belongs(_DB):
        return _DB
    return os.path.join(_nb(), "nyx.db")


def _journal_path() -> str:
    if _SANDGLASS is not None and _belongs(_SANDGLASS):
        return _SANDGLASS
    return os.path.join(_nb(), "sandglass.txt")


def _lock_path() -> str:
    if _LOCK is not None and _belongs(_LOCK):
        return _LOCK
    return _journal_path() + ".lock"


_conn: Optional[sqlite3.Connection] = None
_conn_lock = threading.RLock()
_conn_path: Optional[str] = None


def _reset_if_path_changed() -> None:
    """数据目录变了就丢弃旧连接（含其 WAL），否则会写错库。"""
    global _conn, _conn_path
    want = _db_path()
    if _conn is not None and _conn_path != want:
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None
        _conn_path = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    mem_id       TEXT PRIMARY KEY,
    seq          INTEGER UNIQUE NOT NULL,
    ts           TEXT NOT NULL,
    sender       TEXT NOT NULL DEFAULT 'agent',
    text         TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    line_start   INTEGER,          -- 该记忆在 sandglass.txt 的首行（1-based）
    line_end     INTEGER,          -- 末行；多行消息 line_end > line_start
    deleted_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_mem_seq        ON memories(seq);
CREATE INDEX IF NOT EXISTS idx_mem_linestart  ON memories(line_start);
CREATE INDEX IF NOT EXISTS idx_mem_hash       ON memories(content_hash);
CREATE INDEX IF NOT EXISTS idx_mem_deleted    ON memories(deleted_at);
CREATE INDEX IF NOT EXISTS idx_mem_ts         ON memories(ts);

-- 逻辑删除的墓碑：保留 mem_id 以阻止重建时复活，不保留内容
CREATE TABLE IF NOT EXISTS tombstones (
    mem_id     TEXT PRIMARY KEY,
    seq        INTEGER,
    reason     TEXT,
    deleted_at TEXT NOT NULL
);
"""


# ══════════════════════════════════════════════════════════
# 连接
# ══════════════════════════════════════════════════════════

def set_db_path(path: str) -> None:
    """重定向 ID 中枢库路径（测试/迁移用）。会关闭已有连接。"""
    global _DB, _conn, _conn_path
    with _conn_lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None
            _conn_path = None
        _DB = path
        _set_override(os.path.dirname(path) or None)


def get_conn() -> sqlite3.Connection:
    """获取 ID 中枢库连接（进程内单例，多线程安全）。

    ⚠ `global _conn_path` 不是可省略的声明。缺了它，下面的 `_conn_path = dbp`
    赋的是**局部变量**，模块级 `_conn_path` 永远是 None，于是每次调用
    `_reset_if_path_changed()` 都判定"路径变了"→ 关掉刚建的连接再开一个。

    后果不是慢一点，是**静默丢写**：调用方在一个 get_conn() 上 execute、
    在下一个 get_conn() 上 commit 时，中间那次关闭把未提交的事务回滚掉了，
    而 rowcount 明明返回 1。docstring 写着"进程内单例"，实际上一次都不是。
    （发现于遗忘隔离区的 restore：DELETE rowcount=1，行却还在。）
    """
    global _conn, _conn_path
    with _conn_lock:
        _reset_if_path_changed()
        if _conn is None:
            dbp = _db_path()
            os.makedirs(os.path.dirname(dbp) or ".", exist_ok=True)
            _conn = sqlite3.connect(dbp, timeout=10, check_same_thread=False)
            _conn_path = dbp
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA synchronous=NORMAL")
            _conn.execute("PRAGMA foreign_keys=ON")
            _conn.executescript(_SCHEMA)
            _prov.ensure(_conn)
            _conn.commit()
        return _conn


# ══════════════════════════════════════════════════════════
# ID 计算
# ══════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════
# 日志切分：一条记忆 ≠ 一个物理行
# ══════════════════════════════════════════════════════════

# 记录起始行：「YYYY-MM-DD HH:MM:SS | sender | text」
# 不匹配此模式的行 = 上一条记忆的续行（消息正文里本来就带换行）。
_RECORD_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| ([^|]*?) \| (.*)$")


def parse_journal_records(path: str = None, from_line: int = 1,
                          seq_start: int = 1) -> list:
    """把 sandglass.txt 切成**逻辑记忆**（而非物理行）。

    这是"日志如何切分"的唯一真相来源 —— migrate / verify / allocate
    全部走这里，避免各自实现出现分歧。

    为什么必须这样：log_message 写的是 f"{ts} | {sender} | {text}\\n"，
    而 text 本身可以包含换行。于是一条记忆会横跨多个物理行，
    「行号 = 记忆」这个假设从根上就是错的 —— 按行切会把多行记忆截断成第一行。

    返回 [{seq, ts, sender, text, line_start, line_end}, ...]。

    from_line / seq_start 用于增量解析：只从第 from_line 行开始扫，
    seq 从 seq_start 计起。调用方必须保证 from_line 落在记录边界上
    （即前一条记录的 line_end + 1），否则该记录的续行会被当成孤儿丢弃。
    """
    path = path or _journal_path()
    records: list = []
    if not os.path.exists(path):
        return records
    cur = None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for n, raw in enumerate(f, 1):
            if n < from_line:            # 增量解析：跳过已处理的行
                continue
            line = raw.rstrip("\n")
            m = _RECORD_RE.match(line)
            if m:
                if cur is not None:
                    records.append(cur)
                cur = {
                    "seq": seq_start + len(records),
                    "ts": m.group(1),
                    "sender": m.group(2).strip(),
                    "text": m.group(3),
                    "line_start": n,
                    "line_end": n,
                }
            elif cur is not None:
                cur["text"] += "\n" + line          # 续行
                cur["line_end"] = n
            # cur is None → 首条记录之前的孤儿行，丢弃
    if cur is not None:
        records.append(cur)
    return records


REDACTED_PREFIX = "[REDACTED"


def is_redacted(rec_or_text) -> bool:
    """这条记录是否已被抹除（erasure 原地改写留下的墓碑标记）。

    所有重建路径都必须跳过它 —— 否则重建会把 "[REDACTED ...]" 灌进索引，
    搜 "REDACTED" 就能列出用户删过哪些东西，等于泄漏了删除行为本身。
    """
    text = rec_or_text.get("text", "") if isinstance(rec_or_text, dict) else (rec_or_text or "")
    return text.lstrip().startswith(REDACTED_PREFIX)


def journal_stats(path: str = None) -> dict:
    """物理行数 vs 逻辑记忆数 —— 差值就是多行消息的续行数。"""
    path = path or _journal_path()
    if not os.path.exists(path):
        return {"physical_lines": 0, "records": 0, "continuation_lines": 0, "multiline_records": 0}
    physical = sum(1 for _ in open(path, "r", encoding="utf-8", errors="replace"))
    recs = parse_journal_records(path)
    multi = sum(1 for r in recs if r["line_end"] > r["line_start"])
    return {
        "physical_lines": physical,
        "records": len(recs),
        "continuation_lines": physical - len(recs),
        "multiline_records": multi,
    }


# 日志物理行数缓存：{path: (size, count)}。
# 追加必然改变文件大小，所以 size 未变即计数仍然有效 —— 常见路径 O(1)，
# 只在首次写入或文件被外部改动时才真正数一遍。
_line_count_cache: dict = {}


def journal_line_count(path: str) -> int:
    """日志当前物理行数。**这是"下一行落在哪"的唯一真相。**

    不要用 DB 里的 MAX(line_end) 代替：中枢可能落后于日志（Step 2 上线前
    hermes 绕过中枢直接追加过，实测生产上中枢 8846 / 日志 8847）。
    用派生值推算追加位置，会算出一个已经被占用的行号。
    """
    if not os.path.exists(path):
        return 0
    try:
        size = os.path.getsize(path)
    except OSError:
        size = -1
    cached = _line_count_cache.get(path)
    if cached and cached[0] == size and size >= 0:
        return cached[1]
    n = 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for _ in f:
            n += 1
    if size >= 0:
        _line_count_cache[path] = (size, n)
    return n


def content_hash(text: str) -> str:
    """内容哈希（去重/幂等用）。"""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def compute_mem_id(ts: str, sender: str, text: str) -> str:
    """确定性 mem_id。同一条记忆在任何进程、任何时间算出来都一样。"""
    raw = f"{ts}|{sender}|{text}"
    return "m_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _unique_mem_id(conn: sqlite3.Connection, ts: str, sender: str, text: str,
                   exclude_seq: int = None) -> str:
    """在极端情况下（同秒、同 sender、同文本）解决 ID 碰撞。

    exclude_seq：修复某一行时把**它自己**排除在碰撞检查之外。
    否则那行现有的 mem_id 会被当成冲突，白白加一个 -2 后缀 ——
    修复反而改掉了 id，正是修复本该避免的事。
    """
    base = compute_mem_id(ts, sender, text)
    mid = base
    n = 1
    q = "SELECT 1 FROM memories WHERE mem_id=?" + (
        " AND seq<>?" if exclude_seq is not None else "")
    args = lambda m: ((m, exclude_seq) if exclude_seq is not None else (m,))
    while conn.execute(q, args(mid)).fetchone():
        n += 1
        mid = f"{base}-{n}"
        if n > 999:  # 理论不可达
            raise RuntimeError(f"mem_id 碰撞无法解决: {base}")
    return mid


# ══════════════════════════════════════════════════════════
# 文件锁（与 sandglass_log 共用同一把锁，避免双写竞态）
# ══════════════════════════════════════════════════════════

class _FileLock:
    """sandglass.txt 的排他锁。与 sandglass_log.log_message 使用同一路径。

    指数退避 3 轮 × 5s。超时不裸写 —— 抛异常由调用方处理，
    因为 ID 中枢的写入必须是原子的，宁可失败也不能产生错位。
    """

    def __init__(self, path: str = None, attempts: int = 3, window: float = 5.0):
        self.path = path or _lock_path()
        self.attempts = attempts
        self.window = window
        self.acquired = False

    def __enter__(self):
        for _ in range(self.attempts):
            deadline = time.time() + self.window
            while time.time() < deadline:
                try:
                    fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.close(fd)
                    self.acquired = True
                    return self
                except FileExistsError:
                    time.sleep(0.01)
                except OSError:
                    time.sleep(0.01)
        raise TimeoutError(f"获取沙漏锁超时（{self.attempts}×{self.window}s）: {self.path}")

    def __exit__(self, *exc):
        if self.acquired:
            try:
                os.unlink(self.path)
            except OSError:
                pass
        return False


# ══════════════════════════════════════════════════════════
# 分配（唯一写入入口）
# ══════════════════════════════════════════════════════════

def allocate(text: str, sender: str = "agent", ts: str = None,
             write_journal: bool = True, journal_path: str = None) -> str:
    """分配一个 mem_id 并（可选）追加到 sandglass.txt。

    **原子性保证**：DB 插入与文件追加在同一把文件锁内完成。
    文件写入失败 → 回滚 DB 行。因此 seq 与 txt 行号永远 1:1，
    调用方不再需要猜行号。

    Args:
        text:   记忆原文
        sender: user / agent / runtime / ...
        ts:     时间戳（默认 now），格式 "%Y-%m-%d %H:%M:%S"
        write_journal: 是否追加 sandglass.txt（迁移时置 False）
        journal_path: 日志文件路径。**调用方若自己持有路径（如 sandglass_log
            的模块级 _SANDGLASS 被重定向），必须显式传进来** —— 否则中枢写
            自己的路径、调用方读另一个路径，又是一次"两个真相来源"。

    Returns:
        mem_id

    Raises:
        TimeoutError: 获取锁超时（调用方应重试或报错，不应静默继续）
    """
    from nexsandglass.core import clock
    ts = ts or f"{clock.now():%Y-%m-%d %H:%M:%S}"
    text = text.rstrip("\n")
    # 正文里长成记录头的续行必须转义，否则重新解析日志时它会被切成一条独立记录，
    # sender 由正文作者（可能是网页、邮件）决定 —— 见 core/provenance.py「日志格式本身的伪造面」
    text, n_forged = _prov.escape_forged_headers(text)
    prov = _prov.assess(text, sender)
    if n_forged:
        forged = {"kind": "high_risk", "rule": "forged_journal_header", "match": str(n_forged)}
        prov.flags.append(forged)
        if prov.origin_class != _prov.PRINCIPAL:
            prov.signal, prov.may_instruct, prov.trust = _prov.TAINTED, False, min(prov.trust, 0.1)
        else:
            prov.sensitive = True
    conn = get_conn()
    jpath = journal_path or _journal_path()

    with _FileLock(jpath + ".lock"):
        mem_id = _unique_mem_id(conn, ts, sender, text)
        seq = int(conn.execute("SELECT COALESCE(MAX(seq), 0) FROM memories").fetchone()[0]) + 1

        # 行区间：多行消息占多个物理行，line_end > line_start。
        # 起点必须来自**日志文件本身**的行数，不能用 DB 的 MAX(line_end) —— 
        # 中枢可能落后（外部直写过），那样会算出一个已被占用的行号。
        if write_journal:
            prev_end = journal_line_count(jpath)
        else:
            prev_end = int(conn.execute(
                "SELECT COALESCE(MAX(line_end), 0) FROM memories").fetchone()[0])
        line_start = int(prev_end) + 1
        line_end = line_start + text.count("\n")

        conn.execute(
            "INSERT INTO memories (mem_id, seq, ts, sender, text, content_hash, line_start, line_end)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (mem_id, seq, ts, sender, text, content_hash(text), line_start, line_end),
        )
        # 来源在写入这一刻绑定，与中枢插入同一个事务：文件写失败 → 一起回滚
        _prov.record(conn, mem_id, prov, bound=True)
        if write_journal:
            try:
                os.makedirs(os.path.dirname(jpath) or ".", exist_ok=True)
                with open(jpath, "a", encoding="utf-8") as f:
                    f.write(f"{ts} | {sender} | {text}\n")
                try:    # 刷新行数缓存，省掉下一次的全文件扫描
                    _line_count_cache[jpath] = (os.path.getsize(jpath), line_end)
                except OSError:
                    _line_count_cache.pop(jpath, None)
            except Exception:
                conn.rollback()          # 文件失败 → DB 回滚，绝不留下孤儿 seq
                raise
        conn.commit()
    return mem_id


# ══════════════════════════════════════════════════════════
# 解析（兼容层）
# ══════════════════════════════════════════════════════════

def resolve(ref, *, allow_deleted: bool = False) -> Optional[str]:
    """把历史上任意一种引用形态解析为 mem_id。

    支持：
      - "m_xxxxxxxxxxxxxxxx"      已经是 mem_id
      - 12 / "12"                 裸行号（按 seq 匹配）
      - "shadow:12" / "line:12"   带前缀的行号
      - "engram:2026-08-27 10:00:00"  engram 旧数据用 ts 作 source
      - "sandglass:<ts>"          同上

    找不到返回 None（而不是猜一个）。
    """
    if ref is None:
        return None
    conn = get_conn()

    if isinstance(ref, int):
        return _by_seq(conn, ref, allow_deleted)

    s = str(ref).strip()
    if not s:
        return None
    if s.startswith("m_"):
        row = conn.execute(
            "SELECT mem_id FROM memories WHERE mem_id=?" +
            ("" if allow_deleted else " AND deleted_at IS NULL"), (s,)).fetchone()
        return row[0] if row else None
    if s.isdigit():
        return _by_seq(conn, int(s), allow_deleted)

    prefix, _, rest = s.partition(":")
    rest = rest.strip()
    if not rest:
        return None
    if prefix in ("shadow", "line", "sg", "sandglass_line") and rest.isdigit():
        return _by_seq(conn, int(rest), allow_deleted)
    if prefix in ("engram", "sandglass", "ts"):
        row = conn.execute(
            "SELECT mem_id FROM memories WHERE ts=?" +
            ("" if allow_deleted else " AND deleted_at IS NULL") +
            " ORDER BY seq LIMIT 1", (rest,)).fetchone()
        return row[0] if row else None
    return None


def _by_seq(conn, seq: int, allow_deleted: bool) -> Optional[str]:
    row = conn.execute(
        "SELECT mem_id FROM memories WHERE seq=?" +
        ("" if allow_deleted else " AND deleted_at IS NULL"), (seq,)).fetchone()
    return row[0] if row else None


# ══════════════════════════════════════════════════════════
# 读取
# ══════════════════════════════════════════════════════════

def get(mem_id: str, *, allow_deleted: bool = False) -> Optional[dict]:
    """按 mem_id 取回一条记忆。"""
    conn = get_conn()
    sql = ("SELECT mem_id, seq, ts, sender, text, content_hash, line_start, line_end, deleted_at "
           "FROM memories WHERE mem_id=?")
    if not allow_deleted:
        sql += " AND deleted_at IS NULL"
    row = conn.execute(sql, (mem_id,)).fetchone()
    if not row:
        return None
    return dict(zip(("mem_id", "seq", "ts", "sender", "text", "content_hash",
                     "line_start", "line_end", "deleted_at"), row))


def get_many(mem_ids: list, *, allow_deleted: bool = False) -> dict:
    """批量取回。返回 {mem_id: row}，缺失的键不出现。"""
    if not mem_ids:
        return {}
    conn = get_conn()
    out = {}
    CHUNK = 400
    for i in range(0, len(mem_ids), CHUNK):
        part = list(mem_ids[i:i + CHUNK])
        qs = ",".join("?" * len(part))
        sql = (f"SELECT mem_id, seq, ts, sender, text, content_hash, line_start, line_end, "
               f"deleted_at FROM memories WHERE mem_id IN ({qs})")
        if not allow_deleted:
            sql += " AND deleted_at IS NULL"
        for row in conn.execute(sql, part).fetchall():
            out[row[0]] = dict(zip(
                ("mem_id", "seq", "ts", "sender", "text", "content_hash",
                 "line_start", "line_end", "deleted_at"), row))
    return out


def count(*, include_deleted: bool = False) -> int:
    conn = get_conn()
    sql = "SELECT COUNT(*) FROM memories"
    if not include_deleted:
        sql += " WHERE deleted_at IS NULL"
    return int(conn.execute(sql).fetchone()[0])


def iter_all(include_deleted: bool = False):
    """按 seq 升序遍历全部记忆（迁移/重建索引用）。"""
    conn = get_conn()
    sql = "SELECT mem_id, seq, ts, sender, text, line_start, line_end FROM memories"
    if not include_deleted:
        sql += " WHERE deleted_at IS NULL"
    sql += " ORDER BY seq"
    for row in conn.execute(sql):
        yield dict(zip(("mem_id", "seq", "ts", "sender", "text",
                        "line_start", "line_end"), row))


# ══════════════════════════════════════════════════════════
# 删除（tombstone；级联由 forget 层负责）
# ══════════════════════════════════════════════════════════

def tombstone(mem_id: str, reason: str = "user_forget") -> bool:
    """逻辑删除：打墓碑 + 清空正文，但保留 seq 占位。

    保留 seq 是关键 —— 别人的主键不会因为这次删除而位移。
    正文被清空，所以任何漏改的检索路径也读不到内容。
    """
    conn = get_conn()
    row = conn.execute("SELECT seq FROM memories WHERE mem_id=?", (mem_id,)).fetchone()
    if not row:
        return False
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "UPDATE memories SET text='', content_hash='', deleted_at=? WHERE mem_id=?",
        (now, mem_id))
    conn.execute(
        "INSERT OR REPLACE INTO tombstones (mem_id, seq, reason, deleted_at) VALUES (?,?,?,?)",
        (mem_id, row[0], reason, now))
    conn.commit()
    return True


def is_tombstoned(mem_id: str) -> bool:
    conn = get_conn()
    return conn.execute("SELECT 1 FROM tombstones WHERE mem_id=?", (mem_id,)).fetchone() is not None


def tombstoned_ids() -> set:
    """全部墓碑 ID —— 重建索引时用来阻止已删记忆复活。"""
    conn = get_conn()
    return {r[0] for r in conn.execute("SELECT mem_id FROM tombstones")}


# ══════════════════════════════════════════════════════════
# 自检
# ══════════════════════════════════════════════════════════

def verify(journal_path: str = None) -> dict:
    """校验 ID 中枢与 sandglass.txt 的一致性。

    返回 {ok, memories, journal_lines, mismatches: [...], orphan_seq: [...]}
    这是迁移后与日常巡检都该跑的东西 —— 行号错位再也不会静默。
    """
    journal_path = journal_path or _journal_path()
    conn = get_conn()
    rows = conn.execute(
        "SELECT seq, ts, sender, text, deleted_at FROM memories ORDER BY seq").fetchall()

    # 按**逻辑记忆**比对，不按物理行 —— 多行消息横跨多行，按行比对必然误报
    recs = parse_journal_records(journal_path)
    by_seq = {r["seq"]: r for r in recs}
    stats = journal_stats(journal_path)

    live = sum(1 for r in rows if not r[4])          # 未 tombstone 的条数
    report = {"ok": True, "memories": len(rows), "live_memories": live,
              "journal_lines": stats["physical_lines"],
              "journal_records": stats["records"],
              "continuation_lines": stats["continuation_lines"],
              "multiline_records": stats["multiline_records"],
              # coverage = 中枢**覆盖**了日志多少，不是"还活着多少"。
              # 墓碑记忆也是被覆盖了的（seq 还在、正文已抹）。
              # 早先用 live/records，于是每 forget 一条 coverage 就掉一点，
              # 巡检看上去像在缓慢劣化 —— 那是把"正常删除"画成了"数据丢失"。
              "coverage": (len(rows) / stats["records"]) if stats["records"] else 1.0,
              "hub_empty": (len(rows) == 0 and stats["records"] > 0),
              "mismatches": [], "orphan_seq": [], "gaps": []}

    expected = 0
    for seq, ts, sender, text, deleted in rows:
        expected += 1
        if seq != expected:
            report["gaps"].append({"expected": expected, "got": seq})
            expected = seq
        if deleted:
            continue
        rec = by_seq.get(seq)
        if rec is None:
            report["orphan_seq"].append(seq)
            continue
        if rec["ts"] != ts or rec["text"].strip() != text.strip():
            report["mismatches"].append({
                "seq": seq,
                "db": f"{ts} | {sender} | {text[:40]}",
                "journal": f"{rec['ts']} | {rec['sender']} | {rec['text'][:40]}",
            })

    # 空中枢绝不能报 ok —— 遍历零行必然零不一致，
    # 那样"没迁移"和"迁移完美"返回值一模一样，正是最坏的失败模式。
    # gaps 也必须参与判定：seq 断层意味着有记忆没进中枢。
    # 之前 gaps 只被记录不被判定，导致 8846 条只进 3900 条却报 ok。
    # 中枢条数与日志记录数必须相等。少了 = 有记忆没进中枢，多了 = 日志变短了。
    #
    # 这一条以前不在 ok 的判据里，后果在生产上出现过：中枢 8852 / 日志 8854，
    # verify() 照样报 ok=True —— 因为遍历的是中枢已有的 8852 行，每一行都能在
    # 日志里找到对应，于是零 mismatch、零 orphan、零 gap。**没被遍历到的那两条
    # 根本没机会被发现。** 巡检因此会在写路径正在漏记忆的时候报全绿。
    #
    # 换句话说：只检查"已知的部分对不对"，永远发现不了"有东西没进来"。
    report["hub_behind"] = max(0, stats["records"] - len(rows))
    report["hub_ahead"] = max(0, len(rows) - stats["records"])
    report["ok"] = not (report["mismatches"] or report["orphan_seq"]
                        or report["hub_empty"] or report["gaps"]
                        or report["hub_behind"] or report["hub_ahead"])
    return report


# ══════════════════════════════════════════════════════════
# 自愈：把中枢对齐回日志
# ══════════════════════════════════════════════════════════

def repair_from_journal(journal_path: str = None, apply: bool = False) -> dict:
    """把中枢对齐到日志。**日志是唯一真相，中枢是它的索引。**

    为什么需要这个：verify() 只能告诉你"不一致了"，没有任何工具能修。
    而迁移是按 `WHERE seq=?` 判断"这条已经迁过"就跳过的 —— 它压根不检查
    内容对不对。于是 seq 一旦错位（历史上真实发生过：中枢落后于日志时
    新记忆被分配了一个已被占用的 seq），错误会永久留在库里，重跑迁移也不会修。

    三类不一致，处理方式不同：

      missing   日志里有、中枢里没有            → 插入（安全，纯补齐）
      mismatch  同一个 seq，内容对不上          → 用日志的内容覆盖
      extra     中枢里的 seq 超出日志范围        → **只报告，不自动删**

    extra 不自动处理是有意的：它意味着日志变短了（被截断、被手工编辑、
    或者恢复了一个旧备份）。那是需要人来判断的事故，不是脚本该替你决定的。

    ⚠ mismatch 修复会改变该条的 mem_id（mem_id 由内容哈希得出）。
    修完必须重建索引，否则 shadow_sand.mem_ids 等处会指向不存在的 id。
    返回值里的 `id_changes` 列出了所有变化，`needs_reindex` 标明是否需要重建。

    apply=False（默认）只体检不改。
    """
    journal_path = journal_path or _journal_path()
    conn = get_conn()
    recs = parse_journal_records(journal_path)
    by_seq = {r["seq"]: r for r in recs}

    report = {
        "journal_records": len(recs),
        "hub_rows": 0,
        "missing": [], "mismatch": [], "extra": [],
        "id_changes": [], "repaired": 0,
        "needs_reindex": False, "applied": apply,
    }

    rows = conn.execute(
        "SELECT seq, mem_id, ts, sender, text, deleted_at FROM memories ORDER BY seq").fetchall()
    report["hub_rows"] = len(rows)
    hub_by_seq = {r[0]: r for r in rows}

    for seq, mem_id, ts, sender, text, deleted in rows:
        if seq not in by_seq:
            report["extra"].append({"seq": seq, "mem_id": mem_id})
            continue
        if deleted:
            continue                       # 已 tombstone，正文本就被抹掉
        rec = by_seq[seq]
        if rec["ts"] != ts or rec["text"].strip() != text.strip():
            report["mismatch"].append({
                "seq": seq, "hub": f"{ts} | {text[:40]}",
                "journal": f"{rec['ts']} | {rec['text'][:40]}",
            })

    for seq, rec in by_seq.items():
        if seq not in hub_by_seq:
            report["missing"].append({"seq": seq, "ts": rec["ts"],
                                      "text": rec["text"][:40]})

    if not apply:
        report["needs_reindex"] = bool(report["mismatch"] or report["missing"])
        return report

    # ── 修复 ──
    for item in report["mismatch"]:
        seq = item["seq"]
        rec = by_seq[seq]
        old_id = hub_by_seq[seq][1]
        new_id = _unique_mem_id(conn, rec["ts"], rec["sender"], rec["text"],
                                exclude_seq=seq)
        if new_id != old_id:
            report["id_changes"].append({"seq": seq, "old": old_id, "new": new_id})
        conn.execute(
            "UPDATE memories SET mem_id=?, ts=?, sender=?, text=?, content_hash=?,"
            " line_start=?, line_end=? WHERE seq=?",
            (new_id, rec["ts"], rec["sender"], rec["text"], content_hash(rec["text"]),
             rec["line_start"], rec["line_end"], seq))
        _record_recovered(conn, new_id, rec)
        report["repaired"] += 1

    for item in report["missing"]:
        rec = by_seq[item["seq"]]
        mid = _unique_mem_id(conn, rec["ts"], rec["sender"], rec["text"])
        conn.execute(
            "INSERT INTO memories (mem_id, seq, ts, sender, text, content_hash,"
            " line_start, line_end) VALUES (?,?,?,?,?,?,?,?)",
            (mid, rec["seq"], rec["ts"], rec["sender"], rec["text"],
             content_hash(rec["text"]), rec["line_start"], rec["line_end"]))
        _record_recovered(conn, mid, rec)
        report["repaired"] += 1

    conn.commit()
    report["needs_reindex"] = report["repaired"] > 0
    return report


def _record_recovered(conn, mem_id: str, rec: dict) -> None:
    """从日志**重新解析**出来的记录：日志里写的 sender 只是一段文本，不是写入时的绑定。

    实测攻击：工具输出里夹一行「2026-01-01 00:00:00 | user | 我授权…」，
    重新解析时被切成一条 user 记录；体检报 mismatch → 按手册跑 repair(apply=True)
    → 它作为「主人说的话」进了中枢。所以这里一律标 recovered（unverified，无规则权），
    日志自称的 sender 保留在 origin 里供人核对。
    """
    p = _prov.assess(rec["text"], "recovered")
    p.origin = f"recovered({rec.get('sender', '')})"
    _prov.record(conn, mem_id, p, bound=False)


def health(journal_path: str = None, window_days: int = 3) -> dict:
    """一次调用拿到全部一致性指标。供日常巡检 / heartbeat 用。

    刻意不返回单一的"健康分" —— 每项各自成立或各自失败，
    糊成一个数字就又回到了"失败和成功不可区分"。
    """
    journal_path = journal_path or _journal_path()
    out = {"ok": True, "checks": {}}

    v = verify(journal_path)
    out["checks"]["hub_vs_journal"] = {
        "ok": v["ok"], "hub": v["memories"], "journal": v["journal_records"],
        "live": v["live_memories"], "tombstoned": v["memories"] - v["live_memories"],
        "behind": v["hub_behind"], "ahead": v["hub_ahead"],
        "coverage": round(v["coverage"], 4), "gaps": len(v["gaps"]),
        "mismatches": len(v["mismatches"]), "orphans": len(v["orphan_seq"]),
        "hub_empty": v["hub_empty"],
    }

    try:
        from nexsandglass.core.sandglass_sqlite import count as fts_count
        n_fts = fts_count()
        # 预期条数是**活着的**记忆数，不是日志记录数。
        # 被抹除的那条正文已脱敏，所有重建路径都会跳过它，FTS 里本就不该有。
        # 拿 journal_records 当预期的话，用户每 forget 一条，巡检就永久红一项；
        # 红着红着就没人看了 —— 那时真出事也报不出来。
        expect = v["live_memories"]
        out["checks"]["fts_index"] = {
            "ok": n_fts == expect,
            "indexed": n_fts, "expected": expect, "records": v["journal_records"],
        }
    except Exception as e:
        out["checks"]["fts_index"] = {"ok": False, "error": str(e)}

    # 墓碑和日志正文必须对得上：打了墓碑、正文却还在，就是"以为删了其实没删"，
    # 这正是这一步要根治的东西，所以它得有自己独立的一项，不能藏在别的指标里。
    try:
        tomb = {r[0]: r[1] for r in get_conn().execute("SELECT seq, mem_id FROM tombstones")}
        by_seq = {r["seq"]: r for r in parse_journal_records(journal_path)}
        leaked = [s for s in tomb if s in by_seq and not is_redacted(by_seq[s])]
        missing = [s for s in tomb if s not in by_seq]
        out["checks"]["erasure_integrity"] = {
            "ok": not leaked, "tombstones": len(tomb),
            "leaked": leaked[:20], "leaked_count": len(leaked),
            "seq_not_in_journal": len(missing),
        }
    except Exception as e:
        out["checks"]["erasure_integrity"] = {"ok": False, "error": str(e)}

    try:
        from nexsandglass.features.sandglass_vault import _sync_index
        sizes = [len(_sync_index()) for _ in range(3)]
        out["checks"]["inverted_index"] = {
            "ok": len(set(sizes)) == 1 and sizes[0] > 0,
            "sizes": sizes,
        }
    except Exception as e:
        out["checks"]["inverted_index"] = {"ok": False, "error": str(e)}

    # 写入放大：同一秒 + 同一发送者 + 同一正文连续出现多次 = 一条消息被写了 N 份。
    # 生产上这东西从 3 倍一路爬到 56 倍，爬了七天没被发现 ——
    # 不是因为难查，是因为**没有任何一项指标在看它**。
    # 只看最近三天：历史上的重复是存量，这一项要回答的是「现在还在不在发生」。
    try:
        cutoff = (datetime.now() - timedelta(days=window_days)).strftime("%Y-%m-%d %H:%M:%S")
        rows = get_conn().execute(
            "SELECT ts, sender, text FROM memories "
            "WHERE deleted_at IS NULL AND ts >= ? ORDER BY seq", (cutoff,)).fetchall()
        max_run, cur, prev = 0, 0, None
        for r in rows:
            cur = cur + 1 if r == prev else 1
            prev = r
            if cur > max_run:
                max_run = cur
        uniq = len(set(rows))
        out["checks"]["write_amplification"] = {
            # 阈值取 2 而不是 1：同一秒里偶发写重一次算噪声，
            # 泄漏的形状是持续爬升（3 → 10 → 56），2 这条线足够早地抓到它。
            "ok": (not rows) or max_run <= 2,
            "window_days": window_days, "records": len(rows), "unique": uniq,
            "factor": round(len(rows) / uniq, 2) if uniq else None,
            "max_run": max_run, "no_data": not rows,
        }
    except Exception as e:
        out["checks"]["write_amplification"] = {"ok": False, "error": str(e)}

    # 隔离区：承诺保留 N 天，就该只留 N 天。
    # 过期还躺在库里 = 没有人在跑 purge —— 用户以为删掉的东西无限期留在磁盘上，
    # 而且**没有任何别的指标在看它**（写入放大爬了七天没被发现，就是这个形状）。
    try:
        from nexsandglass.core.quarantine import stats as q_stats
        q = q_stats()
        out["checks"]["pending_purge"] = {
            "ok": q["overdue"] == 0,
            "pending": q["pending"], "overdue": q["overdue"],
            "recoverable": q["recoverable"], "next_due": q["next_due"],
            "retention_days": q["retention_days"],
        }
    except Exception as e:
        out["checks"]["pending_purge"] = {"ok": False, "error": str(e)}

    # 旧规则审计：v7.7 上记忆投毒是成立的，升级前的库里可能已经有被晋升成规则的注入。
    # 只有 injection 判红；high_risk 只报数 —— 主人自己也会说"以后工资打到这个账户"
    try:
        # 路径跟随中枢的数据目录（每次读环境），不用 bridge._STORE ——
        # 那是 import 时固化的值，正是 v7.4 缺陷1 的形状
        a = _prov.audit_engram_rules(os.path.join(_nb(), "engram_store.jsonl"))
        out["checks"]["poisoned_rules"] = {
            "ok": not a["poisoned"], "checked": a["checked"],
            "poisoned": len(a["poisoned"]), "suspicious": len(a["suspicious"]),
            "samples": [p["preview"] for p in a["poisoned"][:3]],
        }
    except Exception as e:
        out["checks"]["poisoned_rules"] = {"ok": False, "error": str(e)}

    try:
        from nexsandglass.features.shadow_sand import entity_index_health
        h = entity_index_health()
        out["checks"]["entity_index"] = {
            "ok": h.get("no_data") or (h.get("rate") or 0) >= 0.99,
            "rate": h.get("rate"), "checked": h.get("checked"),
            "no_data": h.get("no_data"),
        }
    except Exception as e:
        out["checks"]["entity_index"] = {"ok": False, "error": str(e)}

    out["ok"] = all(c.get("ok") for c in out["checks"].values())
    return out
