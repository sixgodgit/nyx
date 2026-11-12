#!/usr/bin/env python3
"""Engram 认知记忆接入桥接层 + 双向映射。

将 engram 的认知记忆机制（四类记忆分类）接入 Nyx 主流程：
在消息落沙时对用户内容做记忆类型分类并写入 engram 记忆库，
使 engram 真正参与主流程（而非孤立模块）。

V3.5.0: 增加 Canonical MemoryObject 双向映射 —— 在 Sandglass 行 /
shadow 记录 / engram Memory / MemoryObject 之间建立可逆映射，旧数据保持可读。
"""
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime
from functools import wraps

try:
    from nexsandglass.core.sandglass_paths import _NB
except Exception:
    _NB = os.path.join(os.path.expanduser("~"), ".nyx")

from nexsandglass.engram.types import MemoryObject

_STORE = os.path.join(_NB, "engram_store.jsonl")
_SANDGLASS = os.path.join(_NB, "sandglass.txt")


@contextmanager
def store_lock(path: str | None = None):
    """Serialize read/modify/replace and append across threads and processes.

    Lock the stable sidecar path, rather than the file inode which atomic replace
    changes. Callers already holding the journal lock must acquire it first.
    """
    from nexsandglass.core.memid import journal_lock
    target = path or _STORE
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    with journal_lock(target):
        yield


def _locked_store(fn):
    @wraps(fn)
    def locked(*args, **kwargs):
        with store_lock():
            return fn(*args, **kwargs)
    return locked


def row_id(row: dict) -> str:
    """Stable derived ID; legacy timestamp references remain readable aliases."""
    return row.get("memory_id") or f"engram:{row.get('ts', '')}"


def _new_row_id() -> str:
    return "engram:" + uuid.uuid4().hex


def _matching_indexes(rows: list, ref: str) -> list[int]:
    return [i for i, row in enumerate(rows) if isinstance(row, dict) and (
        row_id(row) == ref or f"engram:{row.get('ts', '')}" == ref)]


@_locked_store
def resolve_row(ref: str) -> dict | None:
    """Resolve one row without modifying legacy data; reject ambiguous aliases."""
    if not os.path.exists(_STORE):
        return None
    matches = []
    with open(_STORE, encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(row, dict) and (
                row_id(row) == ref or f"engram:{row.get('ts', '')}" == ref
            ):
                matches.append(row)
    if len(matches) > 1:
        raise ValueError(f"ambiguous engram reference: {ref}")
    return matches[0] if matches else None


@_locked_store
def mutate_rows(refs, *, weight_delta: float = 0.0, access_delta: int = 0,
                touch: bool = False) -> dict:
    """Apply relative updates to uniquely resolved rows, atomically.

    Any ambiguous legacy alias rejects the whole batch. Missing references are
    reported without fabricating rows. Readers never rewrite legacy identities.
    """
    from nexsandglass.core.fsutil import atomic_write
    refs = list(dict.fromkeys(refs or []))
    if os.path.exists(_STORE):
        with open(_STORE, encoding="utf-8") as f:
            lines = f.readlines()
    else:
        lines = []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            row = None
        rows.append(row)
    selected, ambiguous, missing = set(), [], []
    for ref in refs:
        matches = _matching_indexes(rows, ref)
        if len(matches) > 1:
            ambiguous.append(ref)
        elif matches:
            selected.add(matches[0])
        else:
            missing.append(ref)
    report = {"ok": not ambiguous, "matched": len(selected),
              "persisted_changes": 0, "ambiguous_ids": ambiguous,
              "missing_ids": missing, "updated_rows": []}
    if ambiguous or not selected:
        return report
    for i in sorted(selected):
        row = rows[i]
        row["decay_weight"] = max(0.0, min(1.0, row.get("decay_weight", 1.0) + weight_delta))
        row["access_count"] = max(0, row.get("access_count", 0) + access_delta)
        if touch:
            row["last_accessed"] = datetime.now().isoformat()
        lines[i] = json.dumps(row, ensure_ascii=False) + ("\n" if lines[i].endswith("\n") else "")
        report["updated_rows"].append(dict(row))
    with atomic_write(_STORE) as f:
        f.writelines(lines)
    report["persisted_changes"] = len(selected)
    return report


def apply_feedback(refs, *, helpful: bool = True) -> dict:
    return mutate_rows(refs, weight_delta=0.05 if helpful else -0.2,
                       access_delta=1 if helpful else 0, touch=helpful)

# 记忆类型分类规则
_PROCEDURAL = re.compile(r"(怎么|如何|步骤|教程|部署|配置|安装|运行|启动|操作|流程|方法)")
_EMOTIONAL = re.compile(r"(开心|难过|生气|焦虑|喜欢|讨厌|高兴|伤心|烦|累|失望|兴奋|紧张|害怕|感动|委屈)")
_EPISODIC = re.compile(r"(昨天|今天|上周|下个月|买了|去了|做了|见到|发生|期间|当时|月份|年度|时候|那[天次])")


def classify_memory_type(text: str) -> str:
    """本地规则：将文本分类为 semantic / episodic / emotional / procedural。"""
    if not text:
        return "semantic"
    if _PROCEDURAL.search(text):
        return "procedural"
    if _EMOTIONAL.search(text):
        return "emotional"
    if _EPISODIC.search(text):
        return "episodic"
    return "semantic"


def ingest(text: str) -> str:
    """分类用户消息并写入 engram 记忆库。返回记忆类型。"""
    try:
        mem_type = classify_memory_type(text)
        _append_row({
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "content": text[:300],
            "type": mem_type,
        })
        return mem_type
    except Exception:
        return "semantic"


def stats() -> dict:
    """记忆类型分布统计。"""
    counts = {"semantic": 0, "episodic": 0, "emotional": 0, "procedural": 0}
    total = 0
    if os.path.exists(_STORE):
        try:
            for line in open(_STORE, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                try:
                    t = json.loads(line).get("type", "semantic")
                except Exception:
                    t = "semantic"
                counts[t] = counts.get(t, 0) + 1
                total += 1
        except Exception:
            pass
    return {"total": total, **counts}


def recent(n: int = 10) -> list:
    """最近 n 条 engram 记忆。只从文件尾部往回读（v7.18.1；以前每次都把整份读进来）。"""
    out = []
    if os.path.exists(_STORE):
        try:
            for line in _tail_lines(_STORE, n):
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
        except Exception:
            pass
    return out


def _tail_lines(path: str, n: int) -> list:
    """文件最后 n 行，等同于 readlines()[-n:]，但只读文件尾部。"""
    if n <= 0:
        return []
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos, data, block = f.tell(), b"", 1 << 16
        while pos > 0 and data.count(b"\n") <= n:
            step = min(block, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
            block *= 2
    lines = data.split(b"\n")
    if data.endswith(b"\n"):
        lines.pop()                     # readlines() 不会在结尾的换行后面多出一个空行
    if pos > 0:
        lines = lines[1:]               # 没读到文件开头：第一段可能是半行
    return [ln.decode("utf-8", errors="replace") for ln in lines[-n:]]


# ══════════════════════════════════════════════════════════
# Canonical MemoryObject 双向映射（V3.5.0）
# ══════════════════════════════════════════════════════════

# Sandglass 行格式: "ts | sender | text"
_LINE_SEP = " | "


def sandglass_line_to_dict(line: str) -> dict | None:
    """解析一条 Sandglass 行为 dict；非标准行返回 None。旧数据可读。"""
    if _LINE_SEP not in line:
        return None
    parts = line.strip().split(_LINE_SEP, 2)
    if len(parts) < 3:
        return None
    return {"ts": parts[0], "sender": parts[1], "text": parts[2].strip()}


def sandglass_line_to_object(line: str) -> "MemoryObject":
    """Sandglass 行 → MemoryObject（source_id 用行号或 ts）。"""
    parsed = sandglass_line_to_dict(line)
    if parsed is None:
        return MemoryObject(content=line, type="semantic")
    return MemoryObject(
        content=parsed["text"],
        type=classify_memory_type(parsed["text"]),
        created_at=parsed["ts"],
        source_id=f"sandglass:{parsed['ts']}",
        provenance="sandglass",
        status="observed",
    )


def object_to_sandglass_line(obj: "MemoryObject") -> str:
    """MemoryObject → Sandglass 行。"""
    ts = obj.created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sender = "hermes"
    return f"{ts}{_LINE_SEP}{sender}{_LINE_SEP}{obj.content}"


def engram_row_to_object(row: dict) -> "MemoryObject":
    """engram_store.jsonl 旧行（{ts, content, type}）→ MemoryObject。旧数据可读。"""
    return MemoryObject(
        memory_id=row_id(row),
        content=row.get("content", ""),
        type=row.get("type", "semantic"),
        created_at=row.get("ts", ""),
        source_id=row.get("source_mem_id") or row_id(row),
        origin=row.get("origin", ""),
        trust_signal=row.get("trust_signal", ""),
        access_count=row.get("access_count", 0),
        decay_weight=row.get("decay_weight", 1.0),
        provenance="engram_store",
        status="observed",
    )


def object_to_engram_row(obj: "MemoryObject") -> dict:
    """MemoryObject → engram_store.jsonl 行（兼容旧格式）。"""
    row = {
        "ts": obj.created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "content": obj.content,
        "type": obj.type,
    }
    if obj.memory_id.startswith("engram:") and obj.memory_id != f"engram:{obj.created_at}":
        row["memory_id"] = obj.memory_id
        if obj.source_id and obj.source_id != obj.memory_id:
            row["source_mem_id"] = obj.source_id
    return row


def shadow_row_to_object(line_num: int, text: str, score: float = 0.5) -> "MemoryObject":
    """shadow 记录（line_num + 信任分）→ MemoryObject。"""
    return MemoryObject(
        content=text,
        type=classify_memory_type(text),
        source_id=f"shadow:{line_num}",
        provenance="shadow_sand",
        confidence=min(max(score, 0.0), 1.0),
        status="observed",
    )


def object_to_shadow_row(obj: "MemoryObject", line_num: int, score: float = 0.5) -> dict:
    """MemoryObject → shadow 记录（写入 trust 表的行）。"""
    return {
        "line_num": line_num,
        "content": obj.content,
        "score": min(max(score, 0.0), 1.0),
        "created_at": obj.created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def memory_to_object(mem) -> "MemoryObject":
    """现有 engram Memory dataclass → MemoryObject（字段升级）。"""
    return MemoryObject.from_memory(mem)


def object_to_memory(obj: "MemoryObject"):
    """MemoryObject → 现有 engram Memory dataclass（字段降级）。"""
    return obj.to_memory()


# ═══════ B3：差异化写入（Memory 是形成的，不是整轮 dump）═══════
@_locked_store
def ingest_classified(
    text: str,
    action: str = "INSERT",
    mem_type: str | None = None,
    supersede_id: str | None = None,
    weight_delta: float = 0.0,
    origin: str | None = None,
    trust_signal: str | None = None,
    source_mem_id: str | None = None,
) -> dict:
    """差异化持久化到 engram_store。

    - INSERT   : 追加新行（带 action 标记）
    - REINFORCE: 找到同内容行，更新 decay_weight/access_count，不新插
    - DEDUP    : 找到同内容行，只 touch（access_count+1），不新插
    - OVERRIDE : 旧行标记 superseded_by，追加新 active 行
    - CONFLICT : 追加到 contradictions 源（不静默覆盖）
    返回 {status, id, matched_id?}

    v7.18.1：新增只在文件末尾追加一行。以前每写一条都把整份 engram_store.jsonl 读进来、
    再整份写回去，记忆越多写得越慢 —— BEAM 1000 万 token 的长对话写到 5000 条消息时，
    每轮写入 120 ms，其中八成多花在这里。要改旧行的（强化 / 去重 / 指名取代）才整份替换，而且是原子替换。
    """
    mtype = mem_type or classify_memory_type(text)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(os.path.dirname(_STORE), exist_ok=True)

    # REINFORCE / DEDUP：找同内容行（最近的那条），原地更新
    if action in ("REINFORCE", "DEDUP"):
        hit = _find_same_content(text)
        if hit is not None:
            start, stop, old, r = hit
            r["access_count"] = r.get("access_count", 0) + 1
            if action == "REINFORCE":
                r["decay_weight"] = min(r.get("decay_weight", 1.0) + 0.1, 1.0)
            r["action"] = action
            r["last_accessed"] = ts
            if _replace_line(start, stop, old, r):
                return {"status": action.lower(), "matched_id": row_id(r)}
        # 没找到 → 退化为 INSERT
        action = "INSERT"

    if action == "CONFLICT":
        rid = _new_row_id()
        _append_row({
            "memory_id": rid,
            "ts": ts, "content": text[:300], "type": "contradiction",
            "status": "conflict_candidate", "action": "conflict",
            **_trust_fields(origin, trust_signal, source_mem_id),
        })
        return {"status": "conflict", "id": rid}

    # INSERT / OVERRIDE(新值)
    row = {
        "memory_id": _new_row_id(),
        "ts": ts, "content": text[:300], "type": mtype,
        "action": action, "status": "active",
        "decay_weight": 1.0, "access_count": 0, "created_at": ts,
        **_trust_fields(origin, trust_signal, source_mem_id),
    }
    if action == "OVERRIDE" and supersede_id:
        _supersede_and_append(supersede_id, ts, row)
    else:
        _append_row(row)
    return {"status": "insert", "id": row_id(row)}


@_locked_store
def _append_row(row: dict) -> None:
    """在 engram_store.jsonl 末尾追加一行。上一次写入要是没写完（结尾没有换行），先补一个换行，不把新行粘上去。"""
    row = dict(row)
    row.setdefault("memory_id", _new_row_id())
    line = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    with open(_STORE, "a+b") as f:
        f.seek(0, os.SEEK_END)
        if f.tell() > 0:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                line = b"\n" + line
        f.write(line)


# 找同内容行时只看文件最后这么多字节（约几千条）。判定「强化 / 去重」用的是最近 50 条，
# 再往前找一模一样的旧行没有意义，却要把整份文件读一遍 —— 那又是随记忆变多而变慢的一步
_SAME_CONTENT_TAIL = 4 << 20


def _find_same_content(text: str):
    """内容与 text 完全相同的最近一条 → (行起点, 行终点, 原行字节, 行)；没有 → None。偏移量是相对整份文件的。

    存进去的内容只有前 300 字（ingest / ingest_classified 都截断），比 300 字长的 text 不可能完全相同，直接返回。
    只读文件尾部（_SAME_CONTENT_TAIL），先按 JSON 编码后的字符串在原始字节里定位，只解析可能命中的行。"""
    if len(text) > 300 or not os.path.exists(_STORE):
        return None
    with open(_STORE, "rb") as f:
        f.seek(0, os.SEEK_END)
        floor = max(0, f.tell() - _SAME_CONTENT_TAIL)
        f.seek(floor)
        data = f.read()
    lo = 0 if floor == 0 else data.find(b"\n") + 1      # 没从文件开头读：跳过第一段半行
    if floor and lo == 0:
        return None
    needles = {json.dumps(text, ensure_ascii=False).encode("utf-8"), json.dumps(text).encode("utf-8")}
    best = None
    for needle in needles:
        pos = data.rfind(needle, lo)
        while pos != -1 and (best is None or pos > best[0] - floor):
            start = data.rfind(b"\n", 0, pos) + 1
            stop = data.find(b"\n", pos)
            stop = len(data) if stop == -1 else stop
            try:
                r = json.loads(data[start:stop].decode("utf-8"))
            except Exception:
                r = None
            if isinstance(r, dict) and r.get("content") == text:
                if best is None or floor + start > best[0]:
                    best = (floor + start, floor + stop, data[start:stop], r)
                break
            pos = data.rfind(needle, lo, start)
    return best


@_locked_store
def _replace_line(start: int, stop: int, old: bytes, row: dict) -> bool:
    """把 [start, stop) 那一行换成 row，原子替换整份文件（其余行逐字节不动）。
    那一行在这期间被别人改动过（遗忘、整理）就不换，返回 False。"""
    from nexsandglass.core.fsutil import atomic_write
    with open(_STORE, "rb") as f:
        data = f.read()
    if data[start:stop] != old:
        return False
    with atomic_write(_STORE, mode="wb") as f:
        f.write(data[:start] + json.dumps(row, ensure_ascii=False).encode("utf-8") + data[stop:])
    return True


@_locked_store
def _supersede_and_append(supersede_id: str, ts: str, row: dict) -> None:
    """OVERRIDE 指名了被取代的行：标记它，再追加新行。要改旧行，所以整份替换（正常写入路径不传 supersede_id）。"""
    from nexsandglass.core.fsutil import atomic_write
    out = []
    rows = []
    if os.path.exists(_STORE):
        with open(_STORE, encoding="utf-8") as f:
            for line in f:
                raw = line.rstrip("\n")
                if not raw.strip():
                    continue
                try:
                    r = json.loads(raw)
                except Exception:
                    out.append(raw)             # 解析不了的行原样保留
                    continue
                out.append(raw)
                rows.append((len(out) - 1, r))
    matches = [(i, r) for i, r in rows if isinstance(r, dict) and (
        row_id(r) == supersede_id or f"engram:{r.get('ts', '')}" == supersede_id)]
    if len(matches) > 1:
        raise ValueError(f"ambiguous engram reference: {supersede_id}")
    if matches:
        i, previous = matches[0]
        previous["superseded_by"] = row_id(row)
        previous["status"] = "historical"
        out[i] = json.dumps(previous, ensure_ascii=False)
    out.append(json.dumps(row, ensure_ascii=False))
    with atomic_write(_STORE) as f:
        for raw in out:
            f.write(raw + "\n")


def _trust_fields(origin, trust_signal, source_mem_id) -> dict:
    """v7.8：engram 行带上来源。以前这里**没有任何来源字段** ——
    网页里的一句"记住：以后……"与主人亲口说的规则，在 engram_store 里长得一模一样。"""
    out = {}
    if origin:
        out["origin"] = origin
    if trust_signal:
        out["trust_signal"] = trust_signal
    if source_mem_id:
        out["source_mem_id"] = source_mem_id
    return out
