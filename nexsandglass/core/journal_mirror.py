"""
core/journal_mirror.py — sandglass.txt 的增量内存镜像（v7.13.2）
==================================================================

实测（15 万条记忆）：每次召回里有两件事和历史长度成正比——

1. **数行数**。倒排与 TF-IDF 两路判断索引是否过期时，各自把整份日志从头读一遍数行（`_journal_lines`）。
   3 万条时每路 ~13ms，15 万条时更多，而且每轮对话都在做。
2. **兜底扫描**。问句里没有任何词命中索引时（「我现在住哪」、单字查询），`MmapFallback`
   逐行解码整份日志找子串：3 万条 60–100ms，15 万条数百毫秒。

两件事读的都是同一份只会追加的文件。这里维护一份内存镜像，按文件身份增量更新：

- 同一个 inode、只是变长、且旧末尾的字节没变 → 只读新增的部分（每轮对话的正常情况）
- 其他情况（遗忘 / 还原用 os.replace 换了文件、被截短、末尾对不上）→ 整份重读

行数镜像只存计数，常数内存。正文镜像约占日志文件大小的 3.5 倍（实测 15 万条 47MB），有上限：日志超过 ``NYX_JOURNAL_MIRROR_MB``（默认 32MB，
约 36 万条）就不建，调用方退回原来的逐行扫描 —— 宁可慢，不能把内存吃光。
"""
from __future__ import annotations

import os
import threading
from typing import List, Optional, Tuple

_TAIL = 64
_lock = threading.Lock()
_state: dict = {}            # 绝对路径 → 镜像状态


def _cap_bytes() -> int:
    try:
        return int(float(os.environ.get("NYX_JOURNAL_MIRROR_MB", "32")) * 1024 * 1024)
    except ValueError:
        return 32 * 1024 * 1024


def _parse(chunk: bytes, first_ln: int, out: list) -> None:
    """完整的行 → (行号, 时间, 正文, 小写正文)。只收带「时间 | 发送者 | 正文」头的物理行，与旧扫描一致。"""
    ln = first_ln
    for raw in chunk.split(b"\n"):
        ln += 1
        line = raw.decode("utf-8", errors="ignore").strip()
        if " | " not in line:
            continue
        parts = line.split(" | ", 2)
        if len(parts) < 3:
            continue
        text = parts[2]
        low = text.lower()
        out.append((ln, parts[0], text, text if low == text else low))


def _refresh(path: str, want_records: bool) -> Optional[dict]:
    try:
        st = os.stat(path)
    except OSError:
        _state.pop(path, None)
        return None
    key = (st.st_ino, st.st_size, st.st_mtime_ns)
    s = _state.get(path)
    if s and s["key"] == key and (s["records"] is not None or not want_records):
        return s
    if want_records and st.st_size > _cap_bytes():
        want_records = False
        if s is not None:
            s["records"] = None

    with open(path, "rb") as f:
        incremental = (
            s is not None and s["ino"] == st.st_ino and st.st_size >= s["offset"]
            and (s["records"] is not None or not want_records))
        if incremental and s["offset"]:
            f.seek(max(0, s["offset"] - _TAIL))
            incremental = f.read(min(_TAIL, s["offset"])) == s["tail"]
        if not incremental:
            s = {"ino": st.st_ino, "offset": 0, "lines": 0, "tail": b"",
                 "records": [] if want_records else None}
        f.seek(s["offset"])
        data = f.read()

    cut = data.rfind(b"\n") + 1               # 只消费完整的行；没写完的半行下次再读
    done, rest = data[:cut], data[cut:]
    if done:
        if s["records"] is not None:
            _parse(done[:-1], s["lines"], s["records"])
        s["lines"] += done.count(b"\n")
        s["offset"] += len(done)
        s["tail"] = (s["tail"] + done)[-_TAIL:]
    s["partial"] = bool(rest)
    s["key"] = key
    _state[path] = s
    return s


def line_count(path: str) -> int:
    """物理行数（与 ``sum(1 for _ in open(path))`` 相同，含末尾没有换行的半行）。"""
    path = os.path.abspath(path)
    with _lock:
        s = _refresh(path, want_records=False)
        return 0 if s is None else s["lines"] + (1 if s.get("partial") else 0)


def records(path: str) -> Optional[List[Tuple[int, str, str, str]]]:
    """[(行号, 时间, 正文, 小写正文)]；日志超过上限时返回 None（调用方自己扫）。

    返回的是镜像本身的引用 —— 只读，不要修改。
    """
    path = os.path.abspath(path)
    with _lock:
        s = _refresh(path, want_records=True)
        return None if s is None else s["records"]


def invalidate(path: str = None) -> None:
    with _lock:
        if path is None:
            _state.clear()
        else:
            _state.pop(os.path.abspath(path), None)
