"""
NexSandglass — 第二层：投石问路 + 解密读取
===========================================
import sandglass_vault
results = sandglass_vault.search("关键词")
latest = sandglass_vault.recent(5)
"""

from __future__ import annotations

import logging
import mmap
import os
import re
import threading
from datetime import datetime

from nexsandglass.core.sandglass_paths import _NB

_SANDGLASS = os.path.join(_NB, "sandglass.txt")
_IDX = os.path.join(_NB, "sandglass.idx")

logger = logging.getLogger(__name__)

# ═══════ B1 直写治理：NYX_ENFORCE_RUNTIME=1 时检测非 orchestrator 直写 ═══════
_ALLOWED_DIRECT = ("orchestrator", "runtime", "facade", "sandglass_mcp", "memory_provider",
                   "shadow_sand", "weavethread", "bridge", "recall_writer", "search_router",
                   "sandglass_vault", "sandglass_log", "sandglass_think", "sandglass_sqlite",
                   "temporal_fact", "consolidation", "promotion", "intent", "bundle")


def _enforce_runtime(entry: str) -> None:
    """开发模式直写治理：检测调用栈，非 orchestrator 直写则 warning/metrics。

    NYX_ENFORCE_RUNTIME=1 时生效；不阻断（仅告警），CI 可 grep 告警日志发现新增直写。
    """
    if os.environ.get("NYX_ENFORCE_RUNTIME") != "1":
        return
    try:
        import inspect
        stack = inspect.stack()
        # 向上找第一个非本模块的调用者
        for frame in stack[1:]:
            mod = frame.filename
            caller = ""
            for part in mod.replace("\\", "/").split("/"):
                if part.endswith(".py"):
                    caller = part[:-3]
                    break
            if caller and caller not in _ALLOWED_DIRECT:
                logger.warning(
                    "[B1 直写治理] %s 被非 orchestrator 模块直写: %s (NYX_ENFORCE_RUNTIME=1)",
                    entry, caller,
                )
                return
    except Exception:
        pass

def set_idx_path(path: str):
    """重定向投石问路索引路径——基准测试用。"""
    global _IDX
    _IDX = path

# ── idx 内存缓存（偷师 memory-os：O(1) 查 token）──
_idx_cache: dict | None = None
_idx_mtime: float = 0
# 进程内同一时刻只有一个线程在补 / 重建倒排（v7.13.3）。v7.13.2 起有后台预热线程，
# 它和每轮的召回线程会同时发现「索引落后」并各自重建一遍；正确性靠 fsutil.atomic_write，这把锁省掉重复劳动
_idx_lock = threading.RLock()

# 增量与落盘（v7.13.3）。实测 15 万条记忆、真实对话节奏（写一句、查一句）：每轮查询 1.3–2.0 秒 ——
# 每次有新行，这里都把 11MB 的 sandglass.idx 从磁盘整份重读（1.6s）再整份重写（0.6s）。
# 现在：新记录直接从 ID 中枢按行号增量补进内存；文件攒够 PERSIST_EVERY_LINES 行或
# PERSIST_EVERY_SECONDS 秒才在后台写一次（文件头记着覆盖到第几行，下次启动从那里接着补，不会丢）。
PERSIST_EVERY_LINES = 500
PERSIST_EVERY_SECONDS = 600
_idx_persisted = 0          # 磁盘上的 idx 覆盖到第几行
_idx_persisted_at = 0.0
_idx_gen = 0                # 整个索引被替换（重建 / 遗忘 / 还原）时递增：旧快照的后台落盘作废
_idx_file_sig = None        # 本进程最后一次读 / 写 idx 文件时的 (inode, size, mtime_ns)
_persisting = False


# ═══════════════════════════════════════════════
# 投石问路
# ═══════════════════════════════════════════════

def _tokenize(text: str) -> set:
    """通用分词：中文2字词+英文2+字母+英文3-4字符滑动窗口（n-gram）。
    不索引单汉字（高频噪音）。"""
    tokens = set()
    t = text.lower()
    # 英文词（2+字母）
    tokens.update(re.findall(r"[a-zA-Z0-9_]{2,}", t))
    # 英文字符滑动窗口——2到4字窗口，覆盖"sup/up/port/grou/oup"等子串
    eng = "".join(re.findall(r"[a-z0-9]", t))
    for n in (2, 3, 4):
        for i in range(len(eng) - n + 1):
            tokens.add(eng[i:i + n])
    # 中文2字词
    chars = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    for i in range(len(chars) - 1):
        tokens.add(chars[i : i + 2])
    return {t for t in tokens if t}


_EN_STOP = frozenset("""
a about above after again all am an and any are as at be because been before being below between both
but by can could did do does doing down during each few for from further had has have having he her here
hers him his how i if in into is it its just me more most my no nor not now of off on once only or other
our out over own same she should so some such than that the their them then there these they this those
through to too under until up very was we were what when where which while who whom why will with would
you your yours yourself tell know remember mentioned say said ever recently last please
""".split())


def _en_stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def _query_tokens(text: str) -> set:
    """搜索分词。中文与 _tokenize 相同（单字仅当输入只有一个中文字时保留）。

    英文（v7.12）：只取实词 + 轻量词干 + **词内** 4-gram。旧做法把整句字母连成一串再切
    2–4 字窗口（"whatisthenameofmydog" → th / is / he …），这些碎片几乎命中每一句英文，
    排序被它们淹没：问「我的狗叫什么」，排第一的是讲书的那句。索引侧分词不变。
    """
    chars = "".join(re.findall(r"[一-鿿]", text))
    tokens = {chars[i:i + 2] for i in range(len(chars) - 1)}
    if len(chars) == 1:
        tokens.add(chars)
    low = (text or "").lower()
    # 编号 / 账号 / 邮箱 / 版本号这类复合标识整体保留（v7.13）：拆开后「detail-7」只剩
    # 「detail」，和 detail-0…detail-29 一模一样；「7」单字符又进不了分词
    tokens.update(m.group(0) for m in re.finditer(r"[a-z0-9]+(?:[-_.@/:][a-z0-9]+)+", low))
    words = re.findall(r"[a-z0-9_]{2,}", low)
    content = [w for w in words if w not in _EN_STOP] or words
    for w in content:
        tokens.add(w)
        stem = _en_stem(w)
        tokens.add(stem)
        if len(stem) >= 5:
            tokens.update(stem[i:i + 4] for i in range(len(stem) - 3))
    return {t for t in tokens if t}


def _parse_line(line: str) -> tuple:
    """返回 (ts, sender, text) 或 (None, None, None)。
    V2.4.0: 明文存储，直接返回原文。"""
    if " | " not in line:
        return None, None, None
    parts = line.strip().split(" | ", 2)
    if len(parts) < 3:
        return None, None, None
    return parts[0], parts[1], parts[2].strip()


def _journal_lines() -> int:
    # 增量计数（v7.13.2）：以前每次调用都把整份日志从头读一遍，而倒排与 TF-IDF
    # 每次召回各调一次 —— 15 万条记忆时光数行就占了召回时间的大头
    if not os.path.exists(_SANDGLASS):
        return 0
    from nexsandglass.core import journal_mirror
    return journal_mirror.line_count(_SANDGLASS)


def _write_idx(idx, covered_lines: int = None):
    """Write idx dict to sandglass.idx file (atomic replace).

    头部记录构建时日志的物理行数 —— 这是判断索引是否过期的唯一可靠依据。
    旧实现拿"倒排里最大的行号"和"日志总行数"比，按记录索引之后前者是最后
    一条记录的首行号，永远小于后者，判据恒假。
    """
    if covered_lines is None:
        covered_lines = _journal_lines()
    from nexsandglass.core.fsutil import atomic_write
    # 唯一临时文件名（v7.13.3）：固定的 sandglass.idx.tmp 在两个写入方同时写时会
    # 互相截断 / 被对方挪走（生产日志：FileNotFoundError sandglass.idx.tmp -> sandglass.idx）
    with atomic_write(_IDX, fsync=False) as f:
        _write_idx_body(f, idx, covered_lines)
    _remember_file_sig()


def _write_idx_body(f, idx, covered_lines, lens=None):
    f.write("# AUTO-GENERATED BY NEXSANDGLASS — DO NOT EDIT\n")
    f.write(f"# covered_lines: {covered_lines}\n")
    f.write("# token:line_number,line_number,...\n")
    f.write("# delete this file and run rebuild_index() to regenerate\n")
    f.write("\n")
    for token in sorted(lens if lens is not None else idx):
        postings = idx[token] if lens is None else idx[token][:lens[token]]
        if postings:
            f.write(f"{token}:{','.join(map(str, sorted(set(postings))))}\n")


def _file_sig():
    try:
        st = os.stat(_IDX)
        return (st.st_ino, st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def _remember_file_sig() -> None:
    global _idx_file_sig
    _idx_file_sig = _file_sig()


def _extend(idx: dict, covered: int) -> None:
    """把第 covered 行之后的新记录补进 idx。正文取自 ID 中枢（按 line_start 走索引，毫秒级）；
    没有中枢的旧日志退回逐行解析。"""
    from nexsandglass.core import memid
    conn = memid.get_conn()
    if conn.execute("SELECT 1 FROM memories LIMIT 1").fetchone() is None:
        items = [(r["line_start"], r["text"]) for r in memid.parse_journal_records(_SANDGLASS, from_line=covered + 1)
                 if not memid.is_redacted(r)]
    else:
        items = conn.execute("SELECT line_start, text FROM memories WHERE deleted_at IS NULL AND line_start > ?"
                             " ORDER BY line_start", (covered,)).fetchall()
    for ln, text in items:
        for token in _tokenize(text or ""):
            idx.setdefault(token, []).append(ln)


def _maybe_persist() -> None:
    import time
    dirty = _idx_mtime - _idx_persisted
    if dirty <= 0:
        return
    if dirty >= PERSIST_EVERY_LINES or time.time() - _idx_persisted_at >= PERSIST_EVERY_SECONDS:
        _persist_async()


def _persist_async() -> None:
    """后台把内存索引写回文件（调用方持有 _idx_lock）。写的是快照；写完之前索引若被整体替换
    （遗忘 / 还原 / 重建）就丢弃这次结果 —— 绝不拿旧快照盖掉刚抹掉 posting 的文件。"""
    global _persisting
    if _persisting or _idx_cache is None:
        return
    _persisting = True
    gen, cache, covered, path = _idx_gen, _idx_cache, _idx_mtime, _IDX
    lens = {k: len(v) for k, v in cache.items()}

    def run():
        global _persisting, _idx_persisted, _idx_persisted_at
        import tempfile, time
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)) or ".",
                                       prefix="." + os.path.basename(path) + ".", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                _write_idx_body(f, cache, covered, lens)
            with _idx_lock:
                if _idx_gen == gen and _IDX == path:
                    os.replace(tmp, path)
                    tmp = None
                    _idx_persisted, _idx_persisted_at = covered, time.time()
                    _remember_file_sig()
        except Exception:
            logger.warning("sandglass: 倒排索引后台落盘失败（内存索引不受影响，下次再写）", exc_info=True)
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            _persisting = False

    threading.Thread(target=run, name="nyx-idx-persist", daemon=True).start()


def flush_index() -> None:
    """把还没落盘的增量同步写回（进程退出时、测试与维护脚本用）。"""
    global _idx_persisted
    with _idx_lock:
        if _idx_cache is not None and _idx_mtime > _idx_persisted:
            _write_idx(_idx_cache, _idx_mtime)
            _idx_persisted = _idx_mtime


def _set_index(idx: dict, covered: int) -> None:
    """整体替换索引并立即落盘（遗忘 / 还原 / 重建用）。递增代号，让在途的后台落盘作废。"""
    global _idx_cache, _idx_mtime, _idx_persisted, _idx_gen
    import time
    with _idx_lock:
        _write_idx(idx, covered)
        _idx_cache, _idx_mtime, _idx_persisted, _idx_gen = idx, covered, covered, _idx_gen + 1
        globals()["_idx_persisted_at"] = time.time()


def index_for_update() -> dict:
    """遗忘 / 还原要改索引时用：磁盘文件被**别的进程**改过才重读，否则直接用内存索引。
    （以前每次都整份重读 —— 15 万条时还原一条要 1.6 秒。）"""
    global _idx_cache, _idx_mtime
    with _idx_lock:
        if _idx_cache is not None and _file_sig() not in (None, _idx_file_sig):
            # 本进程还没落盘的增量不会丢：重读文件后，会从文件覆盖到的行起按中枢重新补
            _idx_cache, _idx_mtime = None, 0
        return _sync_index_locked()


import atexit as _atexit  # noqa: E402
_atexit.register(lambda: flush_index() if _idx_cache is not None else None)


def idx_search(query: str, limit: int = 100) -> list:
    """IDX搜索：语言感知分词→倒排→返回候选行号列表"""
    try:
        idx = _sync_index()
        if not idx:
            rebuild_index()
            idx = _sync_index()
        if not idx:
            return []
        from nexsandglass.features.sandglass_think import _tokenize_for_density
        tokens = _tokenize_for_density(query)
        if not tokens:
            return []
        candidate_lines = set()
        for token in tokens:
            if token in idx:
                candidate_lines.update(idx[token])
        results = []
        with open(_SANDGLASS, "r", encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                if n in candidate_lines:
                    ts, sender, text = _parse_line(line)
                    if ts and text:
                        results.append((n, ts, text))
                        if len(results) >= limit:
                            break
        return results
    except Exception:
        return []


def rebuild_index() -> int:
    with _idx_lock:
        return _rebuild_index_locked()


def _rebuild_index_locked() -> int:
    """全量重建投石问路倒排索引。原子写。返回词条数。

    **按逻辑记忆索引，不按物理行。** 旧实现对续行 `if not ts: continue`，
    于是多行消息只有第一行进了倒排 —— 生产日志 85% 的内容从未被索引。
    token 映射到记录首行号（line_start），与 FTS5 的 id 保持一致。
    """
    global _idx_cache
    _idx_cache = None
    try:
        if not os.path.exists(_SANDGLASS):
            return 0
        from nexsandglass.core import memid
        idx = {}
        for rec in memid.parse_journal_records(_SANDGLASS):
            if memid.is_redacted(rec):
                continue                          # 已抹除：不进索引
            ln = rec["line_start"]
            for token in _tokenize(rec["text"]):
                idx.setdefault(token, []).append(ln)
        _set_index(idx, _journal_lines())
        return len(idx)
    except Exception:
        logger.warning("sandglass: rebuild_index() failed", exc_info=True)
        return 0


def _sync_index() -> dict:
    with _idx_lock:
        return _sync_index_locked()


def _sync_index_locked() -> dict:
    """返回内存中的倒排索引 dict。带缓存，必要时增量补齐。

    修了两个既存缺陷：

    1. `_idx_mtime` 的赋值在函数末尾，而 `idx_max >= total` 分支提前 return，
       于是它永远停在 0 —— 下次进来 mtime 必不匹配，清缓存并 `return {}`。
       表现为**每隔一次调用就返回空索引**：倒排和 TF-IDF 两路在一半的查询里
       完全失效，而调用方看到空又会触发一次全量 rebuild。
    2. `if len(idx) > 5000: _idx_cache = {}; return {}` —— 生产索引有 20 万词条，
       这个"防膨胀"分支注定每次触发，索引永远缓存不住。

    现在：缓存键是日志物理行数（写进 idx 文件头），行数没变就直接返回缓存。
    """
    global _idx_cache, _idx_mtime

    try:
        if not os.path.exists(_SANDGLASS):
            _idx_cache, _idx_mtime = {}, 0
            return {}

        total = _journal_lines()
        # 缓存命中：日志行数未变 → 索引仍然是最新的
        if _idx_cache is not None and _idx_mtime == total:
            return _idx_cache
        # 日志只是变长了：新记录直接补进内存（v7.13.3），不再整份重读文件
        if _idx_cache is not None and 0 < _idx_mtime < total:
            _extend(_idx_cache, _idx_mtime)
            _idx_mtime = total
            _maybe_persist()
            return _idx_cache

        idx, covered, has_header, nonempty = {}, 0, False, False
        if os.path.exists(_IDX):
            with open(_IDX, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("#"):
                        if "covered_lines:" in line:
                            has_header = True
                            try:
                                covered = int(line.split(":", 1)[1].strip())
                            except ValueError:
                                covered, has_header = 0, False
                        continue
                    if ":" not in line:
                        continue
                    nonempty = True
                    t, rest = line.strip().split(":", 1)
                    nums = [int(x) for x in rest.split(",") if x]
                    if nums:
                        idx[t] = nums

        # 旧版 idx 文件没有 covered_lines 头，也不是按记录索引的。
        # 不能把它当成"覆盖了 0 行"去做增量 —— 那会把全部 token 重复追加进去，
        # posting list 直接翻倍。缺头 = 格式过期 = 全量重建。
        if nonempty and not has_header:
            logger.warning("sandglass: idx 为旧格式（无 covered_lines 头），全量重建")
            idx, covered = {}, 0
            from nexsandglass.core import memid
            for rec in memid.parse_journal_records(_SANDGLASS):
                if memid.is_redacted(rec):
                    continue
                for token in _tokenize(rec["text"]):
                    idx.setdefault(token, []).append(rec["line_start"])
            _write_idx(idx, total)
            _idx_cache, _idx_mtime = idx, total
            return idx

        if covered > total:
            logger.warning("sandglass: idx 覆盖行数(%d) > 日志行数(%d)，日志可能被截断，重建中", covered, total)
            from nexsandglass.features.sandglass_vault import rebuild_index as _rb
            _rb()
            return _sync_index()

        global _idx_persisted, _idx_persisted_at
        import time as _t
        _remember_file_sig()
        _idx_persisted, _idx_persisted_at = covered, _t.time()   # 刚从文件读进来：不必马上再写
        if covered < total:
            # 增量：只补新记录（按记录边界，不按物理行）；落盘交给 _maybe_persist
            _extend(idx, covered)

        _idx_cache, _idx_mtime = idx, total
        _maybe_persist()
        return idx
    except Exception:
        logger.warning("sandglass: _sync_index() failed", exc_info=True)
        return _idx_cache or {}

def search(query: str, limit: int = 10, month: str = "") -> list:
    """搜索沙漏。返回 [(行号, 时间, 明文), ...]。
    委托给 SearchRouter 三层架构——影子沙→投石问路→mmap。"""
    _enforce_runtime("vault.search")
    try:
        from nexsandglass.core.search_router import SearchRouter, ShadowSearch, Fts5Search, IdxSearch, TfidfSearch, MmapFallback
        router = SearchRouter(
            ShadowSearch(_SANDGLASS),
            Fts5Search(),
            IdxSearch(_SANDGLASS, _IDX),
            TfidfSearch(),
            MmapFallback(_SANDGLASS)
        )
        return router.search(query, limit)
    except Exception:
        return _legacy_search(query, limit, month)


def _legacy_search(query, limit, month):
    """Fallback: mmap全量扫描 → FTS5精排。只在SearchRouter不可用时触发。"""
    try:
        mmap_results = _mmap_search(query, 500, month)
        if mmap_results:
            from nexsandglass.core.sandglass_sqlite import search_in, sync_incremental
            sync_incremental()
            line_nums = [r[0] for r in mmap_results[:500]]
            ranked = search_in(line_nums, query)
            if ranked:
                return [(r[0], r[1], r[2]) for r in ranked[:limit]]
        return [(r[0], r[1], r[2]) for r in mmap_results[:limit]]
    except Exception:
        logger.warning("sandglass: search(%r) failed", query, exc_info=True)
        return []



def recent(n: int = 10) -> list:
    _enforce_runtime("vault.recent")
    """最近 N 条。[(行号, 时间, 明文), ...]。

    返回物理行号（与 search/FTS5 的 id 一致），从文件尾部取最后 N 条有效消息。
    """
    try:
        if n <= 0 or not os.path.exists(_SANDGLASS):
            return []
        results = []
        with open(_SANDGLASS, "r", encoding="utf-8", errors="ignore") as f:
            for ln, line in enumerate(f, 1):
                ts, sender, text = _parse_line(line)
                if ts:
                    results.append((ln, ts, text))
        return results[-n:]
    except Exception:
        logger.warning("sandglass: recent(%d) failed", n, exc_info=True)
        return []


def count() -> int:
    try:
        if not os.path.exists(_SANDGLASS):
            return 0
        with open(_SANDGLASS, "r", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except Exception:
        logger.warning("sandglass: count() failed", exc_info=True)
        return 0


def _mmap_search(query: str, limit: int, month: str, stage_filter: bool = False) -> list:
    """三级降级：mmap 直接内存搜索。stage_filter=True 时只扫当前阶段+上一阶段。"""
    results = []
    try:
        # 阶段过滤——缩小扫描范围
        scan_months = [month]
        if stage_filter:
            try:
                from nexsandglass.features.sandglass_think import _current_stage, stage_list
                current = _current_stage()
                scan_months = [current]
                stages = stage_list()
                if len(stages) >= 2:
                    scan_months.append(stages[-2].get("name", current))
            except: pass
        with open(_SANDGLASS, "rb") as f:
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                line_start = 0; line_num = 0
                while line_start < len(mm):
                    line_end = mm.find(b"\n", line_start)
                    if line_end == -1: line_end = len(mm)
                    line_num += 1
                    line = mm[line_start:line_end].decode("utf-8", errors="replace")
                    ts, sender, text = _parse_line(line)
                    if ts and query.lower() in text.lower():
                        if not month or ts.startswith(month):
                            results.append((line_num, ts, text))
                            if limit > 0 and len(results) >= limit: break
                    line_start = line_end + 1
    except Exception:
        pass
    return results


# ═══════════════════════════════════════════════
# 时间回溯
# ═══════════════════════════════════════════════

def timeline(query: str) -> dict:
    """时间回溯——按年份分层返回关键词的演变轨迹。"""
    try:
        all_results = search(query, limit=10000)
        if not all_results:
            return {}
        # 按行号升序（时间顺序），而非 reverse search 的匹配数排序
        all_results.sort(key=lambda x: x[0])

        years = {}
        for ln, ts, text in all_results:
            year = ts[:4]
            if year not in years:
                years[year] = {"count": 0, "earliest": text, "earliest_at": ts, "latest": text, "latest_at": ts}
            years[year]["count"] += 1
            years[year]["latest"] = text
            years[year]["latest_at"] = ts

        result = {}
        for year in sorted(years):
            y = years[year]
            result[year] = {"count": y["count"], "earliest": y["earliest"], "earliest_at": y["earliest_at"],
                            "latest": y["latest"], "latest_at": y["latest_at"]}
        return result
    except Exception:
        logger.warning("sandglass: timeline(%r) failed", query, exc_info=True)
        return {}

# ═══════════════════════════════════════════════
# 沙漏导入/合并 —— V2.9.3-dev
# ═══════════════════════════════════════════════

def sandglass_import(source_path: str, source_format: str = "sandglass") -> dict:
    """导入外部对话记录到沙漏。支持格式:
    - sandglass: 同格式沙漏导出 (时间戳 | 发送者 | 文本)
    - chatgpt: ChatGPT JSON 导出
    - claude: Claude 对话 JSON 导出
    - plain: 纯文本，每行一条
    
    返回 {imported: N, skipped: N, total: N}
    """
    imported = 0
    skipped = 0
    
    try:
        if source_format == "sandglass":
            imported, skipped = _import_sandglass(source_path)
        elif source_format in ("chatgpt", "claude"):
            imported, skipped = _import_json_convo(source_path, source_format)
        elif source_format == "plain":
            imported, skipped = _import_plain(source_path)
        else:
            return {"error": f"不支持格式: {source_format}"}
        
        # 导入后重建索引
        try:
            from nexsandglass.core.sandglass_sqlite import sync_incremental
            sync_incremental()
        except: pass
        
        return {"imported": imported, "skipped": skipped, "total": imported + skipped}
    except Exception as e:
        logger.error(f"导入失败: {e}")
        return {"error": str(e), "imported": imported, "skipped": skipped}




def sandglass_export(output_path: str = None, limit: int = None, month: str = "") -> str:
    """导出沙漏为可迁移文件。默认导出全部。
    返回导出文件路径。
    """
    if output_path is None:
        from nexsandglass.core.sandglass_paths import _NB
        output_path = os.path.join(_NB, "sandglass_export.txt")
    
    lines = []
    if os.path.exists(_SANDGLASS):
        with open(_SANDGLASS, "r", encoding="utf-8") as src:
            for line in src:
                if month and not line.startswith(month[:7]):
                    continue
                lines.append(line)
                if limit and len(lines) >= limit:
                    break
        
        # 取最后 limit 条（从全量读取后截断）
        if limit and len(lines) > limit:
            lines = lines[-limit:]
    
    with open(output_path, "w", encoding="utf-8") as dst:
        dst.writelines(lines)
    
    return output_path

def _import_sandglass(source_path: str) -> tuple:
    """导入同格式沙漏——按行号去重"""
    # 读已有沙漏的时间戳集合
    existing_ts = set()
    if os.path.exists(_SANDGLASS):
        with open(_SANDGLASS, "r", encoding="utf-8") as f:
            for line in f:
                if " | " in line:
                    ts = line.split(" | ")[0].strip()
                    existing_ts.add(ts)
    
    imported = 0
    skipped = 0
    with open(source_path, "r", encoding="utf-8") as src:
        for line in src:
            line = line.strip()
            if not line or not line.startswith("20"):  # 跳过非时间戳行
                continue
            if " | " not in line:
                continue
            ts = line.split(" | ")[0].strip()
            if ts in existing_ts:
                skipped += 1
                continue
            
            # 追加到沙漏
            try:
                from nexsandglass.core.sandglass_log import log_message as _log_message
                parts = line.split(" | ", 2)
                if len(parts) >= 3:
                    sender = parts[1].strip()
                    text = parts[2].strip()
                    _log_message(text, sender)
                    existing_ts.add(ts)
                    imported += 1
            except Exception:
                skipped += 1
    
    return imported, skipped


def _import_json_convo(source_path: str, fmt: str) -> tuple:
    """导入 ChatGPT/Claude JSON 导出"""
    import json
    with open(source_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    imported = 0
    skipped = 0
    
    # ChatGPT 格式: [{"mapping": {...}}, ...]
    # Claude 格式: [{"chat_messages": [...]}, ...]
    messages = []
    if fmt == "chatgpt":
        for conv in (data if isinstance(data, list) else [data]):
            mapping = conv.get("mapping", {})
            for mid, node in sorted(mapping.items(), key=lambda x: x[1].get("create_time", 0) if x[1] else 0):
                if node and node.get("message"):
                    msg = node["message"]
                    role = msg.get("author", {}).get("role", "user")
                    content = "".join(p for p in (msg.get("content", {}).get("parts", []) if isinstance(msg.get("content"), dict) else []) if isinstance(p, str))
                    if content:
                        messages.append((role, content))
    elif fmt == "claude":
        for conv in (data if isinstance(data, list) else [data]):
            for msg in conv.get("chat_messages", []):
                role = msg.get("sender", "user")
                content = msg.get("text", "")
                if content:
                    messages.append((role, content))
    
    for role, text in messages:
        try:
            from nexsandglass.core.sandglass_log import log_message as _log_message
            sender = "user" if role in ("user", "human") else "agent"
            _log_message(text, sender)
            imported += 1
        except Exception:
            skipped += 1
    
    return imported, skipped


def _import_plain(source_path: str) -> tuple:
    """导入纯文本——每行一条用户消息"""
    imported = 0
    skipped = 0
    with open(source_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                from nexsandglass.core.sandglass_log import log_message as _log_message
                _log_message(line, "user")
                imported += 1
            except Exception:
                skipped += 1
    return imported, skipped
