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
from typing import Optional

logger = logging.getLogger(__name__)

RULES_VERSION = "3"
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

_ZH = {
    "instruction": [
        r"(以后|今后|往后|从现在起|从今天起|下次|每次|一律|永远|总是)[^。！？\n]{0,14}(回复|回答|答复|说话|叫我|称呼|别|不要|不用|请|记得|用中文|用英文|用繁体)",
        r"(回答|回复|输出|写的东西)[^。！？\n]{0,6}(别|不要|不许)用",
        r"(回答|回复)[^。！？\n]{0,8}(简短|简洁|短一点|详细一点|长一点|别超过|不要超过|不超过)",
        r"跟我说话[^。！？\n]{0,8}(别|不要|直接|简单)",
        r"叫我[^，。！？\n]{1,6}(就行|就好|就可以|吧)",
        r"(给我|帮我)推荐[^。！？\n]{0,10}(的时候|时)[，,]?[^。！？\n]{0,4}(别|不要)",
        r"^(请)?(别|不要)(再)?(叫我|称呼我|用.{1,6}(回复|回答|跟我))",
    ],
    "health": [
        r"过敏", r"吃素|素食|纯素", r"我(从来|从小|平时|一直|基本)?(都)?不(吃|喝)(?!饭|东西|早饭|午饭|晚饭)",
        r"不能(吃|喝)", r"忌口", r"乳糖不耐", r"麸质", r"糖尿病|高血压|痛风|心脏(不好|病)|哮喘", r"怀孕|备孕|孕期",
        r"戒(酒|烟|糖|咖啡)",
    ],
    "people": [
        r"我(的)?(女儿|儿子|孩子|闺女|宝宝|老婆|老公|妻子|丈夫|爱人|媳妇|对象|男朋友|女朋友|男友|女友|"
        r"妈妈|母亲|爸爸|父亲|父母|爸妈|我妈|我爸|爷爷|奶奶|外公|外婆|姥姥|姥爷|哥哥|姐姐|弟弟|妹妹|公公|婆婆|岳父|岳母)",
        r"^我(妈|爸)", r"[，。！？\s]我(妈|爸)",
        r"我家(的)?(猫|狗|孩子|宝宝|那位|小朋友)", r"养了(一只|一条|只|条|两只)?[^。！？\n]{0,6}(猫|狗|兔|鸟|鱼|仓鼠)",
    ],
    "identity": [
        r"我叫|我的名字|我名字", r"我(现在|目前|一直)?住在", r"(搬到|搬去|搬家到|定居在?)[^。！？\n]{1,10}",
        r"我在[^。！？\n]{1,16}(上班|工作|任职|教书|教[语数英物化生历地政]|当(医生|老师|护士|编辑|工程师|律师|会计))",
        r"我是(一名|一个|个)?[^，。！？\n]{0,10}(工程师|程序员|开发|老师|教师|医生|护士|设计师|学生|老板|律师|会计|编辑|销售|产品经理|厨师|司机|研究员|作家|记者)",
        r"(我是|我)做[^，。！？\n]{1,10}的", r"(自己|我)(当|是)老板", r"(自己|我)开了?(一家|个|家)?[^，。！？\n]{0,6}(公司|店|工作室)",
        r"我今年\d{1,2}岁", r"(换到|跳槽到|入职|去了)[^，。！？\n]{1,12}(工作|上班|做|当)",
    ],
    "likes": [
        r"我(最|特别|非常|很|真的|一直|还挺|挺)?(喜欢|爱吃|爱喝|爱看|爱玩|热爱|讨厌|不喜欢|受不了|偏好|迷上)",
        r"我(的)?最爱", r"业余(时间)?(喜欢|爱|会)", r"(我一直在练|我在练|我每周都|我周末(经常|常常|都会))",
    ],
}

_EN = {
    "instruction": [
        r"\b(from now on|going forward|in (the )?future)\b",
        r"\bplease (never|always|don'?t|do not|stop|use|call|keep|reply|answer|write|respond)\b",
        r"\b(always|never) (use|reply|answer|write|respond|call|give|include|format|keep|add|start|end|recommend|suggest|mention|put)\b",
        r"\b(don'?t|do not) (ever )?(use|call|put|give|send|write|recommend|suggest|include|add)\b",
        r"\bstop (using|calling|saying|adding|putting)\b",
        r"\bcall me (?!back\b|later\b|when\b|if\b|tomorrow\b|tonight\b|at\b|on\b|in\b|after\b|before\b|again\b|now\b)\w+",
        r"\bkeep (your )?(answers|replies|responses)\b", r"\b(answer|reply|respond) (in|with|briefly|concisely)\b",
        r"\bbe (blunt|brief|concise|direct|honest) with me\b", r"\bskip the (pleasantries|small talk|intro)",
        r"\bwhen you (recommend|suggest|reply|answer|write|respond)\b",
    ],
    "health": [
        r"\ballerg", r"\b(vegan|vegetarian|pescatarian|lactose|gluten|coeliac|celiac|kosher|halal)\b",
        r"\bi (don'?t|do not|can'?t|cannot|never) (eat|drink)\b", r"\b(diabet|pregnan|intoleran|asthma|hypertension)",
        r"\b(heart condition|blood pressure)\b",
    ],
    "people": [
        r"\bmy (wife|husband|partner|spouse|girlfriend|boyfriend|fianc[eé]e?|son|daughter|kids?|children|child|baby|"
        r"mom|mum|mother|dad|father|parents|brother|sister|grandma|grandmother|grandpa|grandfather|family|"
        r"dog|cat|puppy|kitten|pet)\b",
        r"\bwe have an? [a-z -]{0,20}(called|named)\b",
    ],
    "identity": [
        r"\b(my name is|i'?m called|people call me)\b", r"\bi (currently )?live in\b",
        r"\b(i|we) (just |recently )?moved (from [^.,!?]{1,30} )?to\b", r"\bi work (as|at|for|in)\b",
        r"\bi'?m an? [a-z -]{0,25}(engineer|developer|programmer|teacher|nurse|doctor|designer|student|lawyer|"
        r"editor|chef|manager|founder|writer|researcher|accountant|scientist)\b",
        r"\bi teach\b", r"\bi (run|own|started) an? [a-z -]{0,25}(startup|company|business|shop|store|studio)\b",
        r"\bi'?m \d{1,2} years old\b", r"\bi'?m from\b", r"\bi grew up in\b",
        r"\b(to become|i became|i'?m becoming) an? [a-z -]{0,25}(engineer|developer|teacher|nurse|doctor|designer|"
        r"lawyer|editor|chef|manager|founder|writer|researcher|accountant|scientist|consultant|analyst|artist|"
        r"photographer|professor|journalist|producer)\b",
        r"\b(my new job|i (just )?(started|joined) (a new job|working at))\b",
    ],
    "likes": [
        r"\bi (really |absolutely |just |also )?(love|like|enjoy|hate|dislike|prefer|adore)\b", r"\bi can'?t stand\b",
        r"\bmy favou?rite\b", r"\bi'?m (a (big|huge) )?fan of\b", r"\bin my spare time\b",
        r"\bi'?m training for\b", r"\bmost (evenings|weekends|mornings)\b",
    ],
}

_COMPILED = {k: [re.compile(p, re.I) for p in _ZH[k] + _EN[k]] for k in KINDS}
_MAX_LEN = 1500                     # 更长的是贴进来的文档 / 代码 / 日志，不进画像
_SPLIT = re.compile(r"(?<=[。！？!?])|(?<=\.)\s+|\n+")


def _sentences(text: str) -> list:
    out = [s.strip() for s in _SPLIT.split(text or "") if s and s.strip()]
    return out or [str(text or "").strip()]


# 句子说的是家人（开头就是「我妈 / 我老婆 / My mother …」）
_ABOUT_RELATIVE = re.compile(r"^\s*(我(的)?(女儿|儿子|孩子|闺女|宝宝|老婆|老公|妻子|丈夫|爱人|媳妇|对象|男朋友|女朋友|妈妈|母亲|爸爸|父亲|"
                             r"父母|爸妈|妈|爸|爷爷|奶奶|外公|外婆|姥姥|姥爷|哥哥|姐姐|弟弟|妹妹)|my (wife|husband|partner|"
                             r"girlfriend|boyfriend|son|daughter|kids?|child|baby|mom|mum|mother|dad|father|parents|"
                             r"brother|sister|grandma|grandmother|grandpa|grandfather)\b)", re.I)

# 对着助手说的评价（「I like the way you…」「你这个方案我喜欢」）是对这次回答的反馈，不是这个人的喜好
_TO_ASSISTANT = re.compile(r"\byou(r|'ve|'re|'d|'ll)?\b|你(的|这|写|说|改|给)", re.I)


def classify(text: str) -> Optional[tuple]:
    """(类别, 摘句) 或 None。按 instruction > health > people > identity > likes 的先后，取第一句命中的。"""
    text = str(text or "").strip()
    if not text or len(text) > _MAX_LEN:
        return None
    sents = _sentences(text)
    for kind in KINDS:
        if kind == "instruction" and len(text) > _INSTRUCTION_MAX_LEN:
            continue
        for i, s in enumerate(sents):
            if kind == "likes" and _TO_ASSISTANT.search(s):
                continue
            if any(p.search(s) for p in _COMPILED[kind]):
                if kind == "health" and _ABOUT_RELATIVE.search(s):
                    kind = "people"          # 「我妈有糖尿病」是家人的事，放在「健康」下面像是用户自己的
                return kind, _excerpt(text, sents, i)
    return None


def _excerpt(text: str, sents: list, i: int) -> str:
    """短消息给整句；长消息给命中的那句（不够长时带上下一句），截到 LINE_CHARS。"""
    flat = " ".join(text.split())
    if len(flat) <= LINE_CHARS:
        return flat
    out = sents[i]
    if len(out) < 60 and i + 1 < len(sents):
        out = out + " " + sents[i + 1]
    out = " ".join(out.split())
    return out if len(out) <= LINE_CHARS else out[: LINE_CHARS - 1].rstrip() + "…"


# ══════════════════════════════════════════════════════════
# 扫描（增量）
# ══════════════════════════════════════════════════════════

def _ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


def _meta(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM profile_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def _trusted(line_start) -> bool:
    try:
        from nexsandglass.core import provenance as _prov
        if line_start is None:
            return True
        prov = _prov.for_line(int(line_start))
        return prov is None or prov.signal == _prov.TRUSTED
    except Exception:
        return True


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


def _refresh(conn: Optional[sqlite3.Connection], max_rows: Optional[int]) -> int:
    from nexsandglass.core import memid
    conn = conn or memid.get_conn()
    _ensure(conn)
    if _meta(conn, "rules") != RULES_VERSION:
        conn.execute("DELETE FROM profile_items")
        conn.execute("INSERT OR REPLACE INTO profile_meta(key, value) VALUES ('rules', ?)", (RULES_VERSION,))
        conn.execute("INSERT OR REPLACE INTO profile_meta(key, value) VALUES ('seq', '0')")
        conn.commit()
    mark = int(_meta(conn, "seq", "0") or 0)
    top = int(conn.execute("SELECT COALESCE(MAX(seq), 0) FROM memories").fetchone()[0] or 0)
    sql = ("SELECT mem_id, seq, text, line_start FROM memories WHERE seq > ? AND sender = 'user' "
           "AND deleted_at IS NULL ORDER BY seq")
    rows = conn.execute(sql + (" LIMIT ?" if max_rows else ""), (mark, max_rows) if max_rows else (mark,)).fetchall()
    added = 0
    for mem_id, seq, text, line_start in rows:
        hit = classify(text)
        if hit and _trusted(line_start):
            conn.execute("INSERT OR REPLACE INTO profile_items(mem_id, seq, kind) VALUES (?, ?, ?)",
                         (mem_id, seq, hit[0]))
            added += 1
    # 没看完（max_rows 截住了）：水位线只推到看过的最后一条；看完了：推到最新（中间助手说的话不用再看）
    last = rows[-1][1] if (max_rows and len(rows) >= max_rows) else top
    conn.execute("INSERT OR REPLACE INTO profile_meta(key, value) VALUES ('seq', ?)", (str(max(mark, last)),))
    conn.commit()
    return added


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
            hit = classify(r[1])
            if hit and _trusted(r[2]):
                conn.execute("INSERT OR REPLACE INTO profile_items(mem_id, seq, kind) VALUES (?, ?, ?)",
                             (mid, r[0], hit[0]))
                added += 1
        conn.commit()
    return added


# ══════════════════════════════════════════════════════════
# 渲染
# ══════════════════════════════════════════════════════════

def items(conn: Optional[sqlite3.Connection] = None) -> dict:
    """{类别: [(日期, 摘句, mem_id)]}，新的在前，每类有上限，内容相同的只留最新一条。"""
    from nexsandglass.core import memid
    conn = conn or memid.get_conn()
    _ensure(conn)
    out = {k: [] for k in KINDS}
    seen = set()
    # 按说话的时间排，不按写入先后：导入的旧对话（hermes nyx import）写得晚，但不能排到最新
    rows = conn.execute("SELECT p.kind, m.mem_id, m.ts, m.text FROM profile_items p JOIN memories m "
                        "ON m.mem_id = p.mem_id WHERE m.deleted_at IS NULL ORDER BY m.ts DESC, m.seq DESC")
    for kind, mem_id, ts, text in rows:
        if all(len(out[k]) >= PER_KIND[k] for k in KINDS):
            break                                    # 每类都满了：更早的不用再看
        if kind not in out or len(out[kind]) >= PER_KIND[kind]:
            continue
        hit = classify(text)                         # 正文可能已被隐去密钥 / 改写：按现在的正文重新摘
        if not hit:
            continue
        key = re.sub(r"[\W_]+", "", hit[1].lower())
        if key in seen:
            continue
        seen.add(key)
        out[kind].append((str(ts or "")[:10], hit[1], mem_id))
    return out


def render(conn: Optional[sqlite3.Connection] = None) -> str:
    """常驻块里「关于用户」那一段；没有就返回空串。"""
    try:
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
    return ("[About the user: their own words, newest first; [date] = when they said it]\n"
            + "\n".join(parts))
