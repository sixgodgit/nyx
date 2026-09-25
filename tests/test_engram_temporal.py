"""
Temporal Facts（v6.0）测试。

覆盖：
  1. put_fact 时序覆盖（旧值 historical，新值 active + 链接）
  2. get_current 只返回当前有效
  3. as_of(t) 快照
  4. history_of 演变链
  5. 不静默覆盖
  6. evolution_chain 压缩

独立可运行：python3 tests/test_engram_temporal.py
"""

import os
import sys
import uuid

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
for _candidate in (
    _THIS_DIR,
    os.path.dirname(_THIS_DIR),
    os.path.dirname(os.path.dirname(_THIS_DIR)),
):
    if os.path.isdir(os.path.join(_candidate, "nexsandglass")):
        sys.path.insert(0, _candidate)
        break

from nexsandglass.engram.loops.temporal_fact import (
    resolve_temporal_conflict,
    get_current,
    as_of,
    history_of,
    evolution_chain,
)
import tempfile

# 以前这里是 `_DB = weavethread._DB` —— 测试直接往**真实数据目录**
# （~/.neurobase/shadow_sand.db，生产上就是用户的记忆库）里写"用户xxxx 住在 阿姆斯特丹"。
_DB = os.path.join(tempfile.mkdtemp(prefix="nyx_temporal_"), "shadow_sand.db")


def put_fact(subject, predicate, object, confidence=0.7, source="temporal", source_line=0):
    """测试封装：复用远程 resolve_temporal_conflict 写入。"""
    report = resolve_temporal_conflict(_DB, subject, predicate, object,
                                       source_line=source_line, source=source)
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "status": "active" if report.inserted else "dedup",
        "supersedes": report.expired,
        "valid_from": now,
    }

PASS = 0


def check(name, cond, detail=""):
    """以前只 print 不 assert —— 在 pytest 下这个文件**永远是绿的**，无论实现对错。"""
    global PASS
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} — {detail}")
        raise AssertionError(f"{name} — {detail}")


def test_temporal():
    print("[temporal facts]")
    # 唯一 subject 避免污染
    s = "用户" + uuid.uuid4().hex[:6]

    # 1. 写入 Tesla
    r1 = put_fact(s, "住在", "阿姆斯特丹", confidence=0.8, source="test")
    check("首写 active", r1["status"] == "active")
    check("首写无 supersedes", r1["supersedes"] == [])

    # 2. 覆盖为 Geely
    r2 = put_fact(s, "住在", "鹿特丹", confidence=0.9, source="test")
    check("覆盖 active", r2["status"] == "active")
    check("覆盖链接旧值", len(r2["supersedes"]) >= 1)

    # 3. current_only：只有 Geely
    cur = get_current(_DB, s, "住在")
    check("current 只有 Geely", len(cur) == 1 and cur[0]["object"] == "鹿特丹",
          str([c["object"] for c in cur]))
    check("current active", cur[0].get("valid_until") is None)

    # 4. history 演变链
    hist = history_of(_DB, s, "住在")
    objects = [(h["object"], ("historical" if h.get("valid_until") else "active")) for h in hist]
    check("history 含 Tesla(historical)", ("阿姆斯特丹", "historical") in objects, str(objects))
    check("history 含 Geely(active)", ("鹿特丹", "active") in objects, str(objects))
    check("history 时间倒序", hist[0]["object"] == "鹿特丹", str(hist[0]["object"]))

    # 5. as_of（Tesla 写入时刻）
    asof = as_of(_DB, r1["valid_from"])
    check("as_of 含阿姆", any(f["object"] == "阿姆斯特丹" and f["subject"] == s for f in asof))

    # 6. evolution_chain
    chain = evolution_chain(_DB, s, "住在")
    check("演变链含两个", "阿姆斯特丹" in chain and "鹿特丹" in chain, chain)


def test_no_silent_override():
    print("[不静默覆盖]")
    s = "用户" + uuid.uuid4().hex[:6]
    put_fact(s, "住在", "阿姆斯特丹", source="test")
    put_fact(s, "住在", "鹿特丹", source="test")
    # 两条都存在（旧 historical + 新 active），未静默丢弃
    hist = history_of(_DB, s, "住在")
    check("旧值保留为 historical", any(h["object"] == "阿姆斯特丹" and h.get("valid_until") for h in hist))
    check("新值 active", any(h["object"] == "鹿特丹" and not h.get("valid_until") for h in hist))
    # 无历史
    empty = evolution_chain(_DB, "不存在实体XYZ")
    check("无历史稳定", "无历史记录" in empty)


if __name__ == "__main__":
    test_temporal()
    test_no_silent_override()
    print(f"\nRESULT: {PASS}/14 checks passed")
    if PASS < 14:
        raise SystemExit(1)
    print("ALL GREEN ✅ (ad-hoc verification, not suite)")
