"""
nexsandglass.media — 图片、声音、视频的记忆（v7.21.0：框架）

看到的、听到的也是记忆。这里先把骨架定下来：

  登记   add(path, note=…, when=…)：按内容（sha256）去重；文件复制进数据目录 <data>/media/blobs/
         （NYX_MEDIA_COPY=off 时只记路径）；超过 NYX_MEDIA_MAX_MB（默认 500）的不收
  记忆   每个素材至少写一条记录（「分享了一张照片：IMG_0412.jpg」），你写的附言按**你说的话**记
         （画像、事实抽取照常）；理解后端得出的文字（看图、转写）按来源 media 记 —— 是模型推断，
         召回时带「unverified · source: media」，不能变成关于你的规则
         这些都是普通记忆：搜索、召回、nyx_read、遗忘都直接适用，日期是拍摄 / 录制的时间
  理解   后端可插拔，默认一个都没有（media/understanders.py）。没有后端时素材状态是 unavailable，
         配好后 understand(pending=True) 补上
  遗忘   forget(asset)：它的所有记录进隔离区（可还原），文件先留着；now=True 当场擦掉记录和文件。
         隔离区到期清理时（facade.purge_forgotten），记录都擦掉了的素材，文件一起删

还没做（等真要用多模态时）：Hermes 对话里收到的图片 / 语音自动登记；给 agent 的工具；
OpenAI 兼容接口的看图 / 转写后端；EXIF 拍摄时间。
"""
from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import shutil
import subprocess
import tempfile
import wave
from datetime import datetime
from typing import List, Optional

from nexsandglass.media import understanders as U
from nexsandglass.media.understanders import Moment, register  # noqa: F401  公开接口

logger = logging.getLogger(__name__)

_EXT = {
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif", ".bmp", ".tif", ".tiff"},
    "audio": {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus", ".amr", ".wma"},
    "video": {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".3gp"},
}
_NOUN = {"image": "a photo", "audio": "a recording", "video": "a video"}
_TS = "%Y-%m-%d %H:%M:%S"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS media_assets (
    asset_id    TEXT PRIMARY KEY,
    sha256      TEXT UNIQUE NOT NULL,
    kind        TEXT NOT NULL,
    mime        TEXT,
    size        INTEGER,
    name        TEXT,
    blob        TEXT,
    source_path TEXT,
    duration_s  REAL,
    captured_at TEXT NOT NULL,
    added_at    TEXT NOT NULL,
    status      TEXT NOT NULL,
    understood  TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS media_moments (
    mem_id   TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    kind     TEXT NOT NULL,
    start_s  REAL,
    end_s    REAL,
    by       TEXT
);
CREATE INDEX IF NOT EXISTS idx_media_moments_asset ON media_moments(asset_id);
"""
_COLS = ("asset_id", "sha256", "kind", "mime", "size", "name", "blob", "source_path", "duration_s",
         "captured_at", "added_at", "status", "understood")


class MediaError(ValueError):
    pass


# ══════════════════════════════════════════════════════════
# 存储
# ══════════════════════════════════════════════════════════

def _conn():
    from nexsandglass.core import memid
    c = memid.get_conn()
    c.executescript(_SCHEMA)
    return c


def media_dir() -> str:
    from nexsandglass.core.sandglass_paths import _NB
    return os.path.join(_NB, "media")


def _setting(name: str, default: str) -> str:
    from nexsandglass.core import settings
    return (settings.get(name) or default).strip()


def kind_of(path: str) -> Optional[str]:
    ext = os.path.splitext(path)[1].lower()
    for k, exts in _EXT.items():
        if ext in exts:
            return k
    mime = mimetypes.guess_type(path)[0] or ""
    top = mime.split("/")[0]
    return top if top in U.KINDS else None


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _duration(path: str, kind: str) -> Optional[float]:
    if kind == "image":
        return None
    try:
        with wave.open(path, "rb") as w:
            return round(w.getnframes() / float(w.getframerate()), 2)
    except Exception:
        pass
    if shutil.which("ffprobe"):
        try:
            r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                "default=nw=1:nk=1", path], capture_output=True, text=True, timeout=30)
            return round(float(r.stdout.strip()), 2)
        except Exception:
            return None
    return None


def _store_blob(path: str, sha: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    d = os.path.join(media_dir(), "blobs", sha[:2])
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, sha + ext)
    if not os.path.exists(dst):
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".incoming-")
        os.close(fd)
        try:
            shutil.copyfile(path, tmp)
            os.replace(tmp, dst)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    return dst


def _row(r) -> Optional[dict]:
    if not r:
        return None
    d = dict(zip(_COLS, r))
    d["understood"] = json.loads(d["understood"] or "{}")
    return d


def get(asset_id: str) -> Optional[dict]:
    """素材（可以写编号开头几位），附带它的记录 moments。"""
    c = _conn()
    aid = str(asset_id or "").strip()
    r = c.execute(f"SELECT {','.join(_COLS)} FROM media_assets WHERE asset_id = ?", (aid,)).fetchone()
    if not r and len(aid) >= 4:
        rows = c.execute(f"SELECT {','.join(_COLS)} FROM media_assets WHERE asset_id LIKE ?", (aid + "%",)).fetchall()
        r = rows[0] if len(rows) == 1 else None
    a = _row(r)
    if a:
        a["moments"] = [dict(zip(("mem_id", "kind", "start_s", "end_s", "by"), m)) for m in c.execute(
            "SELECT mem_id, kind, start_s, end_s, by FROM media_moments WHERE asset_id = ? ORDER BY rowid",
            (a["asset_id"],))]
    return a


def list_assets(include_forgotten: bool = False, limit: int = 200) -> List[dict]:
    where = "" if include_forgotten else "WHERE status NOT IN ('forgotten', 'erased')"
    rows = _conn().execute(f"SELECT {','.join(_COLS)} FROM media_assets {where} "
                           "ORDER BY captured_at DESC LIMIT ?", (int(limit),)).fetchall()
    return [_row(r) for r in rows]


def _path_of(a: dict) -> Optional[str]:
    for p in (a.get("blob"), a.get("source_path")):
        if p and os.path.exists(p):
            return p
    return None


# ══════════════════════════════════════════════════════════
# 写入
# ══════════════════════════════════════════════════════════

def _write(text: str, sender: str, when: str) -> str:
    from nexsandglass.core import clock
    from nexsandglass.core.sandglass_log import log_message
    # Historical asset time belongs to this write, not to concurrent requests.
    # frozen() also restores a caller's existing thread/global simulation clock.
    with clock.frozen(datetime.strptime(when, _TS)):
        return log_message(text, sender, return_id=True) or ""


def _link(c, mem_id: str, asset_id: str, kind: str, by: str, start=None, end=None) -> None:
    if mem_id:
        c.execute("INSERT OR REPLACE INTO media_moments(mem_id, asset_id, kind, start_s, end_s, by) "
                  "VALUES (?, ?, ?, ?, ?, ?)", (mem_id, asset_id, kind, start, end, by))


def _span(m: Moment) -> str:
    def mmss(s):
        s = int(s or 0)
        return f"{s // 60}:{s % 60:02d}"
    if m.start_s is None:
        return ""
    return f" {mmss(m.start_s)}" + (f"-{mmss(m.end_s)}" if m.end_s is not None else "")


def _understand(c, a: dict, path: str, only_new: bool = True) -> dict:
    """跑这一类现在能用的后端；已经成功过的跳过。返回 {后端: ok / error: …}。"""
    done = dict(a.get("understood") or {})
    for u in U.for_kind(a["kind"]):
        if only_new and done.get(u.name) == "ok":
            continue
        try:
            moments = U.run(u, a, path)
        except Exception as e:
            done[u.name] = f"error: {e}"[:300]
            logger.warning("media: %s failed on %s: %s", u.name, a["asset_id"], e)
            continue
        for m in moments:
            text = f"[{a['kind']} {a['name']} · {m.kind}{_span(m)}] {m.text.strip()}"
            _link(c, _write(text, "media", a["captured_at"]), a["asset_id"], m.kind, u.name, m.start_s, m.end_s)
        done[u.name] = "ok"
    return done


def _status(understood: dict) -> str:
    if not understood:
        return "unavailable"
    oks = sum(1 for v in understood.values() if v == "ok")
    return "ok" if oks == len(understood) else ("partial" if oks else "failed")


def add(path: str, note: Optional[str] = None, when: Optional[str] = None, name: Optional[str] = None) -> dict:
    """登记一个素材。返回 {status, asset_id, new, …}；status 是 ok / partial / failed / unavailable，
    或 refused（不是文件、类型不认识、太大）。同一个文件再登记：不重复，附言照样记下。"""
    if not path or not os.path.isfile(path):
        return {"status": "refused", "reason": f"not a file: {path}"}
    kind = kind_of(path)
    if not kind:
        return {"status": "refused", "reason": "not an image, audio or video file"}
    size = os.path.getsize(path)
    max_mb = float(_setting("NYX_MEDIA_MAX_MB", "500") or 500)
    if size > max_mb * 1024 * 1024:
        return {"status": "refused", "reason": f"larger than NYX_MEDIA_MAX_MB={max_mb:g}"}
    try:
        captured = datetime.strptime(when.strip(), "%Y-%m-%d %H:%M") if when and len(when.strip()) > 10 else (
            datetime.strptime(when.strip(), "%Y-%m-%d") if when else None)
    except ValueError:
        raise MediaError(f"not a time: {when!r} (use YYYY-MM-DD or YYYY-MM-DD HH:MM)") from None
    from nexsandglass.core import clock
    captured_at = (captured or clock.now()).strftime(_TS)

    c = _conn()
    sha = _sha256(path)
    old = c.execute(f"SELECT {','.join(_COLS)} FROM media_assets WHERE sha256 = ?", (sha,)).fetchone()
    a = _row(old)
    if a and a["status"] == "forgotten":
        return {"status": "refused", "asset_id": a["asset_id"],
                "reason": "this file was forgotten and is in the quarantine; restore it instead"}
    if a and a["status"] == "erased":
        c.execute("DELETE FROM media_assets WHERE asset_id = ?", (a["asset_id"],))
        c.execute("DELETE FROM media_moments WHERE asset_id = ?", (a["asset_id"],))
        a = None
    new = a is None
    if new:
        copy = _setting("NYX_MEDIA_COPY", "on").lower() not in ("off", "0", "false", "no")
        a = {"asset_id": "a_" + sha[:16], "sha256": sha, "kind": kind, "mime": mimetypes.guess_type(path)[0],
             "size": size, "name": name or os.path.basename(path), "blob": _store_blob(path, sha) if copy else None,
             "source_path": os.path.abspath(path), "duration_s": _duration(path, kind),
             "captured_at": captured_at, "added_at": datetime.now().strftime(_TS), "status": "unavailable",
             "understood": {}}
        c.execute(f"INSERT INTO media_assets({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})",
                  tuple(json.dumps(a[k]) if k == "understood" else a[k] for k in _COLS))
        dur = f" ({int(a['duration_s']) // 60}:{int(a['duration_s']) % 60:02d})" if a["duration_s"] else ""
        _link(c, _write(f"Shared {_NOUN[kind]}{dur}: {a['name']}", "media", a["captured_at"]),
              a["asset_id"], "shared", "nyx")
    if note and note.strip():
        from nexsandglass.memory_cli import record_as_user
        _link(c, record_as_user(note.strip(), a["captured_at"]), a["asset_id"], "note", "user")
    if new:
        p = _path_of(a)
        a["understood"] = _understand(c, a, p) if p else {}
        a["status"] = _status(a["understood"])
        c.execute("UPDATE media_assets SET understood = ?, status = ? WHERE asset_id = ?",
                  (json.dumps(a["understood"]), a["status"], a["asset_id"]))
    c.commit()
    _after_write()
    out = get(a["asset_id"])
    out["new"] = new
    return out


def understand(asset_id: Optional[str] = None, pending: bool = False) -> List[dict]:
    """补理解：给一个素材（或 pending=True：所有还没理解完的）跑现在能用的后端。"""
    c = _conn()
    if asset_id:
        targets = [get(asset_id)]
    elif pending:
        targets = [get(r[0]) for r in c.execute(
            "SELECT asset_id FROM media_assets WHERE status IN ('unavailable', 'partial', 'failed')")]
    else:
        targets = []
    out = []
    for a in targets:
        if not a or a["status"] in ("forgotten", "erased"):
            continue
        p = _path_of(a)
        if not p:
            out.append({"asset_id": a["asset_id"], "status": a["status"], "reason": "the file is gone"})
            continue
        und = _understand(c, a, p)
        st = _status(und)
        c.execute("UPDATE media_assets SET understood = ?, status = ? WHERE asset_id = ?",
                  (json.dumps(und), st, a["asset_id"]))
        c.commit()
        out.append({"asset_id": a["asset_id"], "status": st, "understood": und})
    if out:
        _after_write()
    return out


def _after_write() -> None:
    try:
        from nexsandglass.core import lookup
        lookup.after_write()
    except Exception as e:
        logger.warning("media: index catch-up failed: %s", e)


# ══════════════════════════════════════════════════════════
# 遗忘 / 还原 / 清理
# ══════════════════════════════════════════════════════════

def forget(asset_id: str, now: bool = False) -> dict:
    """它的所有记录进隔离区（now=True：当场擦掉，文件一起删）。"""
    from nexsandglass.runtime import facade
    a = get(asset_id)
    if not a or a["status"] in ("forgotten", "erased"):
        return {"ok": False, "error": "no such media, or already forgotten"}
    ids = [m["mem_id"] for m in a["moments"]]
    r = facade.forget({"mem_ids": ids, "reason": "media_forget", "purge_now": bool(now)}) if ids else {"ok": True}
    if not r.get("ok") and ids:
        return {"ok": False, "error": r.get("error")}
    c = _conn()
    if now:
        _delete_blob(a)
        c.execute("UPDATE media_assets SET status = 'erased', blob = NULL WHERE asset_id = ?", (a["asset_id"],))
    else:
        c.execute("UPDATE media_assets SET status = 'forgotten' WHERE asset_id = ?", (a["asset_id"],))
    c.commit()
    return {"ok": True, "asset_id": a["asset_id"], "removed": int(r.get("removed") or 0),
            "erased": bool(now), "purge_after": r.get("purge_after", "")}


def restore(asset_id: str) -> dict:
    from nexsandglass.runtime import facade
    a = get(asset_id)
    if not a or a["status"] != "forgotten":
        return {"ok": False, "error": "not a forgotten media item"}
    bad = [m["mem_id"] for m in a["moments"] if not facade.restore(m["mem_id"]).get("ok")]
    c = _conn()
    c.execute("UPDATE media_assets SET status = ? WHERE asset_id = ?", (_status(a["understood"]), a["asset_id"]))
    c.commit()
    _after_write()
    return {"ok": not bad, "asset_id": a["asset_id"], "not_restored": bad}


def _delete_blob(a: dict) -> None:
    b = a.get("blob")
    if b and os.path.exists(b):
        others = _conn().execute("SELECT COUNT(*) FROM media_assets WHERE blob = ? AND asset_id != ? "
                                 "AND status != 'erased'", (b, a["asset_id"])).fetchone()[0]
        if not others:
            os.remove(b)


def purge() -> int:
    """隔离区清理之后调用：记录都已擦掉的被遗忘素材，删文件、标 erased。返回删掉的个数。"""
    from nexsandglass.core import quarantine
    c = _conn()
    n = 0
    for (aid,) in c.execute("SELECT asset_id FROM media_assets WHERE status = 'forgotten'").fetchall():
        a = get(aid)
        if any(quarantine.get(m["mem_id"]) for m in a["moments"]):
            continue                                # 还有能还原的：文件留着
        _delete_blob(a)
        c.execute("UPDATE media_assets SET status = 'erased', blob = NULL WHERE asset_id = ?", (aid,))
        n += 1
    c.commit()
    return n
