"""
Nyx Memory Book — Phase 1 API 服务
FastAPI 骨架 + 只读端点：/api/today /api/memories /api/persona
启动：uvicorn nexsandglass.nyx_server:app --host 0.0.0.0 --port 7310
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── 数据目录 ──────────────────────────────────────────────
# 惰性解析：每次访问都读当前 NEXSANDBASE_HOME，而不是 import 时固化。
#
# 固化版本的坑（实测踩到）：测试用 monkeypatch 换掉 NEXSANDBASE_HOME 后，
# 即使 del sys.modules[...] 再重新 import，父包 nexsandglass 仍持有旧模块
# 对象，`from nexsandglass import nyx_server` 拿回的是旧实例 —— 于是
# 写入端(memid，已惰性)和读取端(本模块，固化)指向不同目录，
# /api/memories 永远读不到刚写进去的日记。
# 惰性化之后无需重载，同进程切换数据目录天然安全。

_DEFAULT_NB = "/root/.hermes/nexsandglass"


def _nb() -> Path:
    return Path(os.environ.get("NEXSANDBASE_HOME") or _DEFAULT_NB)


def __getattr__(name: str):
    # PEP 562：模块级属性按需计算，保持 NEXSANDBASE / SANDGLASS_TXT 等旧名字可用
    if name == "NEXSANDBASE":
        return _nb()
    if name == "SANDGLASS_TXT":
        return _nb() / "sandglass.txt"
    if name == "PERSONA_MD":
        return _nb() / "persona" / "persona.md"
    if name == "ENGRAm_STORE":
        return _nb() / "engram_store.jsonl"
    if name == "EMOTION_LOG":
        return _nb() / "emotion_log.jsonl"
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

app = FastAPI(title="Nyx Memory Book", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── 内部读取工具 ──────────────────────────────────────────

def _read_sandglass(limit: int = 20, since: Optional[datetime] = None) -> list[dict]:
    """读 sandglass.txt，按 (ts,text) 去重后返回最近 limit 条（或 since 之后全部）。

    已脱敏的记录直接跳过：它们的正文已经被删除，剩下的 `[REDACTED ... reason=...]`
    不是记忆，而且把它返回出去等于对外公布「用户在什么时候删了什么」——
    泄漏删除行为本身。
    """
    lines: list[dict] = []
    if not (_nb() / 'sandglass.txt').exists():
        return lines
    seen = set()
    with open(_nb() / 'sandglass.txt', encoding="utf-8", errors="replace") as f:
        for raw in f:
            raw = raw.rstrip("\n")
            if not raw:
                continue
            parts = raw.split(" | ", 2)
            if len(parts) < 3:
                continue
            ts_str, sender, text = parts[0], parts[1], parts[2]
            if text.lstrip().startswith("[REDACTED"):
                continue
            try:
                ts = datetime.fromisoformat(ts_str.replace(" ", "T", 1))
            except ValueError:
                continue
            if since and ts < since:
                continue
            key = (ts_str, text)
            if key in seen:
                continue
            seen.add(key)
            lines.append({"ts": ts.isoformat(), "sender": sender, "text": text})
    return lines[-limit:]


def _read_engram_store() -> list[dict]:
    """读 engram_store.jsonl 全量。"""
    if not (_nb() / 'engram_store.jsonl').exists():
        return []
    rows = []
    for raw in open(_nb() / 'engram_store.jsonl', encoding="utf-8"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            rows.append(json.loads(raw))
        except json.JSONDecodeError:
            pass
    return rows


def _read_emotion_log(limit: int = 50) -> list[dict]:
    """读 emotion_log.jsonl 最后 limit 条。"""
    if not (_nb() / 'emotion_log.jsonl').exists():
        return []
    lines = []
    for raw in open(_nb() / 'emotion_log.jsonl', encoding="utf-8"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            lines.append(json.loads(raw))
        except json.JSONDecodeError:
            pass
    return lines[-limit:]


def _read_persona() -> str:
    """读 persona.md 原文。"""
    if not (_nb() / 'persona' / 'persona.md').exists():
        return ""
    return (_nb() / 'persona' / 'persona.md').read_text(encoding="utf-8")


def _get_open_loops(engram_rows: list[dict]) -> list[dict]:
    """从未闭合/冲突记忆中提取 open loops（线索）。"""
    return [
        r for r in engram_rows
        if r.get("status") in ("conflict_candidate", "pending", "open")
        or r.get("type") in ("contradiction",)
    ]


# ── Pydantic 模型 ─────────────────────────────────────────

class TodayPage(BaseModel):
    date: str
    summary_stats: dict
    recent_memories: list[dict]
    open_loops: list[dict]
    recent_emotions: list[dict]
    persona_excerpt: str


class MemoryItem(BaseModel):
    ts: str
    sender: str
    text: str


class PersonaPage(BaseModel):
    raw_md: str
    excerpt: str  # 前 800 字


# ── Pydantic 请求模型 ──────────────────────────────────────

class DiaryEntry(BaseModel):
    text: str

class StampEntry(BaseModel):
    title: str
    text: str = ""
    arousal: float = 0.6  # 默认中等情绪强度


# ── 情绪章注册表（12 章）─────────────────────────────────

STAMPS = {
    "🔥": {"name": "热烈", "arousal": 0.9},
    "💪": {"name": "振奋", "arousal": 0.7},
    "🎉": {"name": "喜悦", "arousal": 0.6},
    "🌊": {"name": "平静", "arousal": 0.3},
    "🌫️": {"name": "迷茫", "arousal": 0.4},
    "😔": {"name": "低落", "arousal": 0.5},
    "😠": {"name": "烦躁", "arousal": 0.7},
    "😰": {"name": "焦虑", "arousal": 0.8},
    "🔍": {"name": "回味", "arousal": 0.3},
    "💡": {"name": "顿悟", "arousal": 0.6},
    "⚡": {"name": "转折", "arousal": 0.7},
    "🕯️": {"name": "留念", "arousal": 0.4},
}


def _append_to_sandglass(text: str, sender: str = "user") -> int:
    """直接追加一行到 sandglass.txt，返回新行号（从0计）。"""
    # 走 ID 中枢。原实现自己拼行、自己 open("a")、再重读全文数行号：
    # 没有锁（并发写会交错），不进中枢（verify 立刻红），
    # 数行号还要把整个文件读一遍（59463 行）。Step 2 修掉的毛病它占全了。
    from nexsandglass.core import memid
    mem_id = memid.allocate(text, sender=sender)
    if not mem_id:
        raise RuntimeError("写入沙漏失败（锁超时或中枢拒绝）")
    rec = memid.get(mem_id)
    return (rec["line_start"] - 1) if rec else -1  # 0-indexed，与旧返回一致


def _append_to_emotion_log(mood: str, ts: Optional[str] = None):
    """追加到 emotion_log.jsonl。"""
    record = {"ts": ts or datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "mood": mood}
    with open(_nb() / 'emotion_log.jsonl', "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _try_nyx_kindle(line_num: int, text: str):
    """尝试调 nyx_kindle（更新 Bloom+Mist），失败静默忽略。"""
    try:
        import importlib
        mod = importlib.import_module("nexsandglass.interfaces.nyx")
        mod.nyx_kindle(line_num, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), text)
    except Exception:
        pass  # 服务不因 nyx_kindle 失败而挂


def _try_shadow_index(text: str, line_num: int = 0):
    """尝试 shadow_index（更新影子索引），失败静默忽略。"""
    try:
        from nexsandglass.features.shadow_sand import shadow_index
        shadow_index(text, line_num=line_num)
    except Exception:
        pass


# ── 端点 ──────────────────────────────────────────────────

@app.get("/api/health")
def health():
    """健康检查。"""
    return {"status": "ok", "base": str(_nb()), "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/today", response_model=TodayPage)
def today_page():
    """
    今日扉页：
    - 最近 24h 记忆数 + 最近 10 条
    - 未闭合线索（open_loops）
    - 最近情绪
    - 人格画像摘录
    """
    now = datetime.now()  # 本地时间（sandglass.txt 无时区）
    since_24h = now - timedelta(hours=24)

    recent = _read_sandglass(limit=20, since=since_24h)
    engram  = _read_engram_store()
    emotions = _read_emotion_log(limit=10)
    persona = _read_persona()
    open_loops = _get_open_loops(engram)

    return TodayPage(
        date=now.strftime("%Y-%m-%d"),
        summary_stats={
            "memories_last_24h": len(recent),
            "engram_total": len(engram),
            "open_loop_count": len(open_loops),
        },
        recent_memories=recent[-10:],
        open_loops=[
            {"ts": r.get("ts", ""), "content": r.get("content", ""), "type": r.get("type", ""), "status": r.get("status", "")}
            for r in open_loops[-5:]
        ],
        recent_emotions=emotions[-5:],
        persona_excerpt=persona[:800] if persona else "（画像尚未生成）",
    )


@app.get("/api/memories")
def list_memories(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    type: Optional[str] = Query(None, description="过滤类型：semantic|episodic|emotional|procedural|contradiction"),
):
    """
    记忆列表（分页）。
    - 如果指定 type，从 engram_store 读并过滤；
    - 否则从 sandglass.txt 读原始条目。
    """
    if type:
        rows = _read_engram_store()
        rows = [r for r in rows if r.get("type") == type]
        total = len(rows)
        page = rows[offset: offset + limit]
        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "items": page,
        }
    else:
        # 从 sandglass.txt 读
        all_lines = _read_sandglass(limit=offset + limit)
        total_lines = len(all_lines)
        page = all_lines[offset: offset + limit]
        return {
            "total": total_lines,
            "offset": offset,
            "limit": limit,
            "items": page,
        }


@app.get("/api/persona", response_model=PersonaPage)
def persona_page():
    """人格画像原文 + 摘录。"""
    raw = _read_persona()
    return PersonaPage(
        raw_md=raw,
        excerpt=raw[:800] if raw else "（画像尚未生成）",
    )


@app.get("/api/engram")
def engram_store(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """读 engram_store 全量（分页），供记忆书前端日历网格使用。"""
    rows = _read_engram_store()
    return {
        "total": len(rows),
        "offset": offset,
        "limit": limit,
        "items": rows[offset: offset + limit],
    }


@app.get("/api/emotions")
def emotions(limit: int = Query(50, ge=1, le=200)):
    """情绪日志。"""
    return {"items": _read_emotion_log(limit)}


# ── 情感入口端点（Phase 2）────────────────────────────────

@app.get("/api/stamps")
def list_stamps():
    """返回情绪章注册表（12 章）供前端渲染按钮。"""
    return {"stamps": [{"icon": k, "name": v["name"], "arousal": v["arousal"]} for k, v in STAMPS.items()]}


@app.post("/api/diary", status_code=201)
def write_diary(entry: DiaryEntry):
    """
    写日记。正文写入 sandglass.txt（带 📖 前缀），并同步 shadow 索引 + Nyx 感知。
    返回写入行号，供前端定位。
    """
    text = entry.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="日记内容不能为空")
    if len(text) > 2000:
        raise HTTPException(status_code=400, detail="日记内容过长（最多 2000 字）")

    tagged = f"📖 [日记] {text}"
    line_num = _append_to_sandglass(tagged, sender="user")
    _try_shadow_index(tagged, line_num)
    _try_nyx_kindle(line_num, tagged)
    return {"status": "ok", "line_num": line_num, "text": tagged}


@app.post("/api/stamp", status_code=201)
def write_stamp(stamp: StampEntry):
    """
    盖章。title 为情绪章 icon（🔥 等），或章名。
    写入 sandglass.txt（带 emoji + 📌[心情] 前缀），追加 emotion_log，更新 Nyx 感知。
    """
    icon = stamp.title.strip()
    if icon not in STAMPS:
        # 允许用章名匹配
        matched = [k for k, v in STAMPS.items() if v["name"] == icon]
        if not matched:
            raise HTTPException(status_code=400, detail=f"未知情绪章：{icon}。可用：{list(STAMPS.keys())}")
        icon = matched[0]
    meta = STAMPS[icon]
    note = stamp.text.strip()
    content = f"{icon}📌[心情]{meta['name']}" + (f"：{note}" if note else "")
    line_num = _append_to_sandglass(content, sender="user")
    _append_to_emotion_log(meta["name"])
    _try_nyx_kindle(line_num, content)
    return {
        "status": "ok",
        "line_num": line_num,
        "icon": icon,
        "name": meta["name"],
        "arousal": meta["arousal"],
        "recorded_mood": meta["name"],
    }
