"""
hermes nyx import —— 把以前在 Hermes 里的对话导进 Nyx（v7.20.0）

装上 Nyx 之前在 Hermes 里聊过的，Nyx 一句都不知道：你说过的过敏、家人、对 agent 的要求，第一天就该记得。
这个命令只读打开 Hermes 的会话库（当前 profile 的 <HERMES_HOME>/state.db），把每段会话里你和 agent 的往来
按原来的时间、走和平时一样的写入路径（密钥先隐去、来源绑定、晋升、画像、信念）写进 Nyx。

    hermes nyx import                   列出能导的会话，问你：全部 / 不导 / 挑几个
    hermes nyx import --list            只列出来，什么都不写
    hermes nyx import --all --yes       不问，全部导
    hermes nyx import --session ID …    只导这几个（ID 可以只写开头几位）
    hermes nyx import --since 2026-01-01
    hermes nyx import --db PATH         指定 state.db（默认当前 Hermes profile 的）

导什么
  - 本机（CLI / TUI / 桌面）和私聊里的会话。群聊和频道不导：分不清哪句是你说的
  - 只取原话（messages.content）。不取 api_content —— 那里面带着当时注入的记忆，导进来就成了记忆套记忆；
    不取被回退的（active=0）、压缩摘要、系统插进来的消息（自动续写、子任务完成通知……）、群里旁听到的、工具调用和结果
  - 子 agent、定时任务、测试、API 服务、bot room 的会话不导：那里的「用户」不是你
不重复
  - 每个会话记下导到了哪条消息，再跑只导新的；中途断了接着导
  - Nyx 里已经有的同一句话（同一边、处理后文本相同、时间相差一天内）跳过 —— 装着 Nyx 的那段时间本来就记过
时间
  - 每条按它原来的时间写入（日期、「上个月」这类说法都按当时算）。比 Nyx 已知的更早的事实只补成历史，
    不会顶掉后来才知道的（「去年说住杭州」不会盖过「上周说搬到上海」）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from typing import Optional

from nexsandglass.core import hints
from nexsandglass.core.lang import t

# 这些来源里的「用户」不是主人：子 agent 的任务、定时任务的提示、测试、程序调用、机器人之间
SKIP_SOURCES = frozenset({"subagent", "cron", "test", "api_server", "bot_room", "batch"})
GROUP_TYPES = frozenset({"group", "channel", "thread", "supergroup"})
DEDUP_WINDOW = timedelta(days=1)

_PROGRESS = """
CREATE TABLE IF NOT EXISTS import_progress (
    db          TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    last_msg_id INTEGER NOT NULL,
    turns       INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT,
    PRIMARY KEY (db, session_id)
);
"""


# ══════════════════════════════════════════════════════════
# 读 Hermes 的会话库（只读）
# ══════════════════════════════════════════════════════════

def default_db() -> Optional[str]:
    try:
        import nexsandglass_hermes as nh
        home = nh.hermes_home()
    except Exception:
        home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    return os.path.join(home, "state.db") if home else None


def open_ro(path: str) -> sqlite3.Connection:
    """只读打开。Hermes 可能正在用它（WAL）；只读连接不写、不建表、不改 journal 模式。"""
    conn = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def text_of(content) -> str:
    """messages.content → 纯文本。多模态的列表（[{type: text, text: …}, {type: image_url, …}]）只取文字部分。"""
    if content is None:
        return ""
    s = str(content)
    st = s.lstrip()
    if st.startswith("[{") or st.startswith("[ {"):
        try:
            parts = json.loads(st)
            if isinstance(parts, list):
                return "\n".join(str(p.get("text") or "") for p in parts
                                 if isinstance(p, dict) and p.get("type") in ("text", "input_text")).strip()
        except Exception:
            pass
    return s.strip()


class HermesDB:
    def __init__(self, path: str):
        self.path = os.path.realpath(path)
        self.conn = open_ro(path)
        self.scols = _columns(self.conn, "sessions")
        self.mcols = _columns(self.conn, "messages")
        if not {"id", "source", "started_at"} <= self.scols or not {"id", "session_id", "role", "content",
                                                                     "timestamp"} <= self.mcols:
            raise RuntimeError(t(f"{path} does not look like a Hermes session database",
                                 f"{path} 看起来不是 Hermes 的会话库"))

    def _msg_filter(self) -> str:
        """只要原话：去掉被回退的、压缩摘要、系统插入的、群里旁听到的（列不存在就不管，老版本 Hermes 没有）。"""
        conds = ["role IN ('user', 'assistant')", "content IS NOT NULL", "content != ''"]
        if "active" in self.mcols:
            conds.append("active = 1")
        if "_compressed_summary" in self.mcols:
            conds.append("_compressed_summary = 0")
        if "display_kind" in self.mcols:
            conds.append("(display_kind IS NULL OR display_kind = '')")
        if "observed" in self.mcols:
            conds.append("(observed IS NULL OR observed = 0)")
        return " AND ".join(conds)

    def sessions(self) -> list:
        col = lambda c: c if c in self.scols else f"NULL AS {c}"
        rows = self.conn.execute(
            f"SELECT id, source, {col('chat_type')}, started_at, {col('ended_at')}, {col('title')}, "
            f"{col('parent_session_id')}, {col('user_id')}, {col('display_name')} FROM sessions "
            f"ORDER BY started_at").fetchall()
        counts = dict(self.conn.execute(
            f"SELECT session_id, COUNT(*) FROM messages WHERE {self._msg_filter()} GROUP BY session_id").fetchall())
        out = []
        for r in rows:
            out.append({"id": r["id"], "source": r["source"] or "", "chat_type": r["chat_type"] or "",
                        "started_at": r["started_at"], "ended_at": r["ended_at"], "title": r["title"] or "",
                        "parent": r["parent_session_id"], "messages": int(counts.get(r["id"], 0)),
                        "user_id": r["user_id"], "who": r["display_name"] or r["user_id"] or ""})
        return out

    def messages(self, session_id: str, after_id: int = 0) -> list:
        rows = self.conn.execute(
            f"SELECT id, role, content, timestamp FROM messages WHERE session_id = ? AND id > ? AND "
            f"{self._msg_filter()} ORDER BY id", (session_id, after_id)).fetchall()
        return [{"id": r["id"], "role": r["role"], "text": text_of(r["content"]), "ts": float(r["timestamp"])}
                for r in rows]

    def first_user_text(self, session_id: str) -> str:
        r = self.conn.execute(f"SELECT content FROM messages WHERE session_id = ? AND role = 'user' AND "
                              f"{self._msg_filter()} ORDER BY id LIMIT 1", (session_id,)).fetchone()
        return text_of(r[0]) if r else ""


def _owners() -> set:
    """nyx.json 里配的主人（owner_ids）；没配就是空集合（单人使用）。"""
    try:
        import nexsandglass_hermes as nh
        return {str(o) for o in (nh.load_config().get("owner_ids") or [])}
    except Exception:
        return set()


def is_owner(s: dict, owners: set) -> bool:
    if not owners or not s.get("user_id"):
        return True                    # 没配主人（单人使用），或者本机会话（没有平台用户）
    ident = {str(s["user_id"]), f"{s['source']}:{s['user_id']}", str(s.get("who") or "")}
    return bool(ident & owners)


def importable(s: dict, owners: set = frozenset()) -> Optional[str]:
    """这个会话能不能导；不能的话返回原因。"""
    if (s["source"] or "").lower() in SKIP_SOURCES:
        return t(f"source {s['source']}", f"来源 {s['source']}")
    if (s["chat_type"] or "").lower() in GROUP_TYPES:
        return t("group chat", "群聊")
    if not s["messages"]:
        return t("no messages", "没有消息")
    if not is_owner(s, owners):
        return t("someone else's private chat", "别人的私聊")
    return None


# ══════════════════════════════════════════════════════════
# 写进 Nyx
# ══════════════════════════════════════════════════════════

def _progress(conn) -> None:
    conn.executescript(_PROGRESS)


def done_upto(conn, db: str, session_id: str) -> int:
    _progress(conn)
    r = conn.execute("SELECT last_msg_id FROM import_progress WHERE db = ? AND session_id = ?", (db, session_id)).fetchone()
    return int(r[0]) if r else 0


def _mark(conn, db: str, session_id: str, last_id: int, turns: int) -> None:
    conn.execute("INSERT INTO import_progress(db, session_id, last_msg_id, turns, updated_at) VALUES (?, ?, ?, ?, ?) "
                 "ON CONFLICT(db, session_id) DO UPDATE SET last_msg_id = excluded.last_msg_id, "
                 "turns = import_progress.turns + excluded.turns, updated_at = excluded.updated_at",
                 (db, session_id, last_id, turns, datetime.now().isoformat(timespec="seconds")))
    conn.commit()


def already_in_nyx(conn, sender: str, text: str, when: datetime) -> bool:
    """Nyx 里是不是已经有这一句（同一边、处理后文本相同、时间相差一天内）。"""
    from nexsandglass.core import memid, secret_filter
    cands = {text.rstrip("\n"), secret_filter.clean(text).rstrip("\n")}
    for c in cands:
        for (ts,) in conn.execute("SELECT ts FROM memories WHERE content_hash = ? AND sender = ? AND deleted_at IS NULL",
                                  (memid.content_hash(c), sender)):
            try:
                if abs(datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S") - when) <= DEDUP_WINDOW:
                    return True
            except ValueError:
                continue
    return False


def session_turns(db: HermesDB, session: dict, after_id: int = 0) -> list:
    """一个会话里还没导过的往来：[{ts, user, assistant, session, last_id}]，按消息顺序。

    一句用户消息配它后面第一句助手回复；工具调用之间的几段助手话各自成一轮（没有用户那一边）。
    """
    out, pending = [], None
    for m in db.messages(session["id"], after_id):
        if m["role"] == "user":
            if pending is not None:
                out.append({"ts": pending["ts"], "user": pending, "assistant": None,
                            "session": session["id"], "last_id": pending["id"]})
            pending = m
        else:
            anchor = pending or m
            out.append({"ts": anchor["ts"], "user": pending, "assistant": m, "session": session["id"], "last_id": m["id"]})
            pending = None
    if pending is not None:
        out.append({"ts": pending["ts"], "user": pending, "assistant": None, "session": session["id"],
                    "last_id": pending["id"]})
    return out


def write_turns(db: HermesDB, provider, turns: list, *, progress=None) -> dict:
    """按时间先后写进 Nyx（几个会话交错时也按全局时间）。返回 {turns, skipped_dupes, by_session}。"""
    from nexsandglass.core import clock, memid
    conn = memid.get_conn()
    turns = sorted(turns, key=lambda x: (x["ts"], x["last_id"]))
    written = dupes = 0
    last = {}                                        # 会话 → 最后写到的消息 id
    count = {}
    try:
        for i, tr in enumerate(turns, 1):
            u = tr["user"]["text"] if tr["user"] else ""
            a = tr["assistant"]["text"] if tr["assistant"] else ""
            if u and already_in_nyx(conn, "user", u, datetime.fromtimestamp(tr["user"]["ts"])):
                u, dupes = "", dupes + 1
            if a and already_in_nyx(conn, "agent", a, datetime.fromtimestamp(tr["assistant"]["ts"])):
                a, dupes = "", dupes + 1
            if u or a:
                clock.set_global(datetime.fromtimestamp(tr["ts"]))
                try:
                    provider.sync_turn(u, a, session_id=f"hermes:{tr['session']}")
                finally:
                    clock.set_global(None)
                written += 1
                count[tr["session"]] = count.get(tr["session"], 0) + 1
            last[tr["session"]] = tr["last_id"]
            if i % 50 == 0:
                for sid, mid in last.items():
                    _mark(conn, db.path, sid, mid, count.pop(sid, 0))
                last.clear()
                if progress:
                    progress(i, len(turns))
    finally:
        for sid, mid in last.items():
            _mark(conn, db.path, sid, mid, count.pop(sid, 0))
    return {"turns": written, "skipped_dupes": dupes}


def _provider():
    from nexsandglass.core.memory_provider import NexSandglassProvider
    p = NexSandglassProvider()
    # 不起预热线程、不碰 Hermes 的配置：命令行已经按当前 profile 定好了数据目录
    p._agent_context = "primary"
    p._session_id = "import"
    return p


def _after_import() -> None:
    try:
        from nexsandglass.core import sandglass_sqlite, profile, semantic
        from nexsandglass.features import sandglass_vault
        sandglass_sqlite.sync_incremental()
        sandglass_vault._sync_index()
        profile.refresh()
        semantic.schedule_index()
    except Exception as e:
        print(t(f"(index refresh after import failed, it will catch up on the next start: {e})",
                f"（导入后的索引补齐失败，下次启动时会自动补上：{e}）"), file=sys.stderr)


# ══════════════════════════════════════════════════════════
# 命令行
# ══════════════════════════════════════════════════════════

def _date(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
    except Exception:
        return "?"


def _preview(db: HermesDB, s: dict) -> str:
    text = s["title"] or db.first_user_text(s["id"])
    text = " ".join(text.split())
    return text[:40] + ("…" if len(text) > 40 else "")


def _pick(answer: str, rows: list) -> list:
    """「a」= 全部；「1 3 5-8」= 序号；也可以写会话 ID 的开头。"""
    answer = answer.strip().lower()
    if answer in ("a", "all", "全部", "y", "yes"):
        return rows
    if answer in ("", "n", "no", "none", "不导", "q"):
        return []
    picked = []
    for tok in re.split(r"[\s,，]+", answer):
        if not tok:
            continue
        m = re.fullmatch(r"(\d+)-(\d+)", tok)
        if m:
            picked += [r for r in rows if int(m.group(1)) <= r["n"] <= int(m.group(2))]
        elif tok.isdigit() and any(r["n"] == int(tok) for r in rows):
            picked += [r for r in rows if r["n"] == int(tok)]
        else:
            picked += [r for r in rows if str(r["id"]).lower().startswith(tok)]
    seen, out = set(), []
    for r in picked:
        if r["id"] not in seen:
            seen.add(r["id"])
            out.append(r)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog=hints.cmd("import"), description=t(
        "Import past Hermes conversations into Nyx (read-only on Hermes's side)",
        "把以前在 Hermes 里的对话导进 Nyx（Hermes 那边只读）"))
    ap.add_argument("--db", help=t("Hermes state.db (default: the current profile's)", "Hermes 的 state.db（默认当前 profile 的）"))
    ap.add_argument("--list", action="store_true", help=t("only list what can be imported", "只列出能导的会话"))
    ap.add_argument("--all", action="store_true", help=t("import every importable session", "导全部能导的会话"))
    ap.add_argument("--session", nargs="+", default=[], help=t("only these sessions (an id prefix is enough)",
                                                               "只导这几个会话（ID 写开头几位就行）"))
    ap.add_argument("--since", help=t("only sessions started on or after YYYY-MM-DD", "只导这一天（含）以后开始的会话"))
    ap.add_argument("--yes", "-y", action="store_true", help=t("do not ask", "不问"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    path = args.db or default_db()
    if not path or not os.path.exists(path):
        print(t(f"No Hermes session database at {path}. Pass --db PATH.", f"{path} 不存在：没有找到 Hermes 的会话库，用 --db 指定。"),
              file=sys.stderr)
        return 2
    try:
        db = HermesDB(path)
    except (sqlite3.Error, RuntimeError) as e:
        print(t(f"Cannot read {path}: {e}", f"读不了 {path}：{e}"), file=sys.stderr)
        return 2

    from nexsandglass.core import memid
    conn = memid.get_conn()
    since = datetime.strptime(args.since, "%Y-%m-%d").timestamp() if args.since else None
    rows, skipped = [], {}
    owners = _owners()
    for s in db.sessions():
        why = importable(s, owners)
        if why:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        if since and float(s["started_at"] or 0) < since:
            continue
        s["done"] = done_upto(conn, db.path, s["id"])
        s["left"] = len(db.messages(s["id"], s["done"]))
        if s["left"]:
            rows.append(s)
    for i, s in enumerate(rows, 1):
        s["n"] = i
    if args.session:
        rows = _pick(" ".join(args.session), rows)

    if args.json and args.list:
        print(json.dumps({"db": db.path, "sessions": [{k: s[k] for k in ("n", "id", "source", "started_at", "left")}
                                                      for s in rows], "skipped": skipped}, ensure_ascii=False, indent=1))
        return 0
    print(t(f"Hermes sessions: {db.path}", f"Hermes 会话库：{db.path}"))
    if skipped:
        print(t("  not imported: ", "  不导：") + ", ".join(f"{k} {v}" for k, v in sorted(skipped.items())))
    if not rows:
        print(t("Nothing new to import.", "没有新的可以导。"))
        return 0
    print(t(f"{'#':>4}  {'session':<12} {'source':<10} {'who':<12} {'from':<10} {'to':<10} {'messages':>8}  first words",
            f"{'#':>4}  {'会话':<12} {'来源':<10} {'谁':<12} {'开始':<10} {'结束':<10} {'消息':>6}  开头"))
    for s in rows:
        print(f"{s['n']:>4}  {str(s['id'])[:12]:<12} {s['source'][:10]:<10} {str(s['who'] or '-')[:12]:<12} "
              f"{_date(s['started_at']):<10} {_date(s['ended_at'] or s['started_at']):<10} {s['left']:>8}  "
              f"{_preview(db, s)}")
    total = sum(s["left"] for s in rows)
    people = {(s["source"], str(s["user_id"])) for s in rows if s.get("user_id")}
    if not owners and len(people) > 1:
        # 私聊来自好几个人，又没配主人：分不清哪些是你。不默认全导
        print(t(f"These private chats come from {len(people)} different people "
                f"({', '.join(sorted({str(s['who']) for s in rows if s.get('user_id')}))}). Nyx cannot tell which are "
                f"you: set owner_ids in nyx.json (hermes memory setup), or pick sessions with --session.",
                f"这些私聊来自 {len(people)} 个不同的人（{'、'.join(sorted({str(s['who']) for s in rows if s.get('user_id')}))}），"
                f"Nyx 分不清哪些是你：在 nyx.json 里配 owner_ids（hermes memory setup），或者用 --session 挑。"))
        if args.all:
            print(t("--all refused for that reason.", "所以不执行 --all。"), file=sys.stderr)
            return 1
    if args.list:
        print(t(f"{len(rows)} sessions, {total} messages. Import with {hints.cmd('import', '--all')} "
                f"or {hints.cmd('import', '--session ID')}.",
                f"共 {len(rows)} 个会话、{total} 条消息。导入：{hints.cmd('import', '--all')} "
                f"或 {hints.cmd('import', '--session ID')}"))
        return 0

    chosen = rows if (args.all or args.session) else None
    if chosen is None:
        if args.yes:
            chosen = rows
        elif not sys.stdin.isatty():
            print(t("Not a terminal: add --all (or --session ID …) to import.", "不在终端里：加 --all（或 --session ID …）才会导。"))
            return 1
        else:
            ans = input(t("Import: [a]ll / [n]one / numbers like 1 3 5-8: ", "导入：a 全部 / n 不导 / 序号，比如 1 3 5-8："))
            chosen = _pick(ans, rows)
    if not chosen:
        print(t("Nothing imported.", "什么都没导。"))
        return 0
    chosen.sort(key=lambda x: float(x["started_at"] or 0))
    if (args.all or args.session) and not args.yes and sys.stdin.isatty():
        n = sum(s["left"] for s in chosen)
        if input(t(f"Import {len(chosen)} sessions ({n} messages)? [y/N] ", f"导入 {len(chosen)} 个会话（{n} 条消息）？[y/N] ")
                 ).strip().lower() not in ("y", "yes", "是"):
            print(t("Nothing imported.", "什么都没导。"))
            return 0

    p = _provider()
    t0 = time.time()
    plan = [tr for s in chosen for tr in session_turns(db, s, s["done"])]

    def progress(i, n):
        print(t(f"  {i}/{n} turns …", f"  {i}/{n} 轮 …"), flush=True)

    r = write_turns(db, p, plan, progress=lambda i, n: progress(i, n) if i % 500 == 0 else None)
    turns, dupes = r["turns"], r["skipped_dupes"]
    _after_import()
    print(t(f"Done: {turns} turns from {len(chosen)} sessions in {time.time() - t0:.0f}s"
            + (f"; {dupes} messages were already in Nyx" if dupes else "") + ".",
            f"完成：{len(chosen)} 个会话、{turns} 轮，用时 {time.time() - t0:.0f} 秒"
            + (f"；{dupes} 条 Nyx 里已经有了" if dupes else "") + "。"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
