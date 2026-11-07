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

RULES_VERSION = "4"
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
        r"戒(酒|烟|糖|咖啡)", r"(开始|又|重新|恢复)(吃|喝)(肉|荤|酒|咖啡)|不(再)?吃素了|不再吃素",
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
        r"我(也|还|都)?(最|特别|非常|很|真的|一直|还挺|挺)?(喜欢|爱吃|爱喝|爱看|爱玩|热爱|讨厌|不喜欢|受不了|偏好|迷上)",
        r"我(的)?最爱", r"业余(时间)?(喜欢|爱|会)", r"(我一直在练|我在练|我每周都|我周末(经常|常常|都会))",
        r"(不再|不怎么|不太|没那么)(喜欢|爱)", r"(不再|不怎么)(吃|喝)[^，。！？\n]{1,10}了", r"改(吃|喝|看|玩|听)",
        r"(现在|最近)(开始)?(喜欢|爱上|迷上)",
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
        r"\b(started|start|began) eating (meat|fish)\b|\b(eat|eating) (meat|fish) again\b",
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
        r"\bi (don'?t|do not|no longer) (really |actually )?(love|like|enjoy|care for)\b",
        r"\bi'?m (not|no longer) (really )?into\b", r"\bi'?m (really )?into\b",
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
# 同一件事说法改了：新的顶替旧的（v7.22.0）
# ══════════════════════════════════════════════════════════
#
# 「以后用中文回复」半年后变成「以后用英文吧」，「我是素食者」变成「我不吃素了」：以前两句都留在常驻块里，
# 靠模型自己按日期判断哪句算数。现在每个分句标上它说的是哪件事（话题槽）；同一个槽，新说的顶替旧说的。
#   - 一句话里几件事只改了一件：只去掉被改的分句（「以后用中文，不要用 emoji」改了语言，「不要用 emoji」还在）
#   - 认不出话题的分句从不被顶替；家人的事（people）不参与
#   - 被顶替的话不丢：常驻块最后一段「Changed since」列出最近几条，带改口的日期；搜索里一直都在

_LANG = (r"中文|英文|英语|日语|日文|韩语|法语|德语|西班牙语|繁体|简体|粤语|普通话|\b(english|chinese|mandarin|cantonese|"
         r"japanese|korean|french|german|spanish|italian|portuguese|russian|arabic|hindi)\b")
_SLOT_RULES = {
    "instruction": [
        ("lang", re.compile(_LANG, re.I)),
        ("emoji", re.compile(r"emoji|表情", re.I)),
        ("length", re.compile(r"简短|简洁|短一点|短些|长一点|详细|啰嗦|字数|不超过|别超过|\b(short|shorter|brief|briefly|"
                              r"concise|concisely|detailed|longer|lengthy|in detail)\b", re.I)),
        ("tables", re.compile(r"表格|\btables?\b", re.I)),
        ("lists", re.compile(r"列表|分点|\bbullet|\blists?\b|\bnumbered\b", re.I)),
        ("call_me", re.compile(r"叫我|称呼我|\bcall me\b|\baddress me\b", re.I)),
    ],
    "health": [
        ("diet_meat", re.compile(r"吃素|素食|纯素|吃肉|吃荤|\b(vegan|vegetarian|pescatarian)\b|\b(eat|eating|ate) (meat|fish)\b"
                                 r"|\bmeat\b", re.I)),
        ("alcohol", re.compile(r"喝酒|戒酒|酒精|\balcohol\b|\bsober\b|\bdrinking\b", re.I)),
        ("smoking", re.compile(r"抽烟|吸烟|戒烟|\bsmok", re.I)),
        ("coffee", re.compile(r"咖啡|\bcoffee\b|\bcaffeine\b", re.I)),
    ],
    "identity": [
        ("name", re.compile(r"我叫|名字|\bmy name is\b|\bi'?m called\b", re.I)),
        ("residence", re.compile(r"住在|搬到|搬去|搬家到?|定居|\blive in\b|\bliving in\b|\bmoved (from [^,.;]{1,30} )?to\b",
                                 re.I)),
        ("age", re.compile(r"\d{1,2}岁|\byears old\b", re.I)),
    ],
}
_JOB_VERB = re.compile(r"上班|工作|任职|教书|教[语数英物化生历地政]|当|做|\bwork(s|ing|ed)? (as|at|for|in)\b|\bteach|"
                       r"\bi'?m an? ", re.I)
_JOB_WHAT = re.compile(r"工程师|程序员|开发|老师|教师|医生|护士|设计师|学生|老板|律师|会计|编辑|销售|经理|厨师|司机|研究员|作家|记者|"
                       r"教授|公务员|店长|主管|公司|医院|学校|高中|初中|小学|大学|银行|工厂|出版社|单位|集团|事务所|实验室|研究所|报社|"
                       r"电商|外贸|餐厅|语文|数学|\b(engineer|developer|programmer|teacher|nurse|doctor|designer|student|lawyer|"
                       r"editor|chef|manager|founder|writer|researcher|accountant|scientist|consultant|analyst|professor|"
                       r"journalist|company|startup|hospital|school|bank|firm|agency|studio|university|clinic|restaurant|"
                       r"math|science)\b", re.I)
_JOB_CHANGE = re.compile(r"换(了)?(一)?(家|个|份)?[^，。！？\s]{0,6}(工作|公司|单位)|跳槽|入职|辞职|离职|退休|转行|新工作|\bnew job\b|\bswitched jobs\b|"
                         r"\bretired\b|\bquit my job\b|\bjoined\b", re.I)
_ALLERGY = re.compile(r"对([^，。！？\s]{1,8}?)过敏|allergic to ([a-z][a-z -]{1,30}?)(?=[,.;!?]|$|\s+(?:and|so|even|but)\b)", re.I)
_LIKE_ZH = re.compile(r"(?:最爱|爱吃|爱喝|爱看|爱玩|爱听|热爱|喜欢|讨厌|受不了|偏好|迷上|爱上|爱|改|不再|不怎么)"
                      r"(?P<o>[^，。！？、\s了的啊呀吧]{1,10})")
_LIKE_EN = re.compile(r"\b(?:love|like|enjoy|hate|dislike|prefer|adore|can'?t stand|fan of|into)\s+(?P<o>[a-z][a-z' -]{1,40}?)"
                      r"(?=[,.;!?]|$|\s+(?:and|but|because|so|now|anymore|any more|these days|lately|too|a lot|very much|"
                      r"every|at|in|on)\b)", re.I)
_EN_DET = re.compile(r"^(the|a|an|my|some|watching|eating|playing|listening to|reading|drinking|going to|to)\s+", re.I)
_CLAUSE = re.compile(r"[，,；;。！？!?]+\s*|\s+(?:and|but)\s+", re.I)


def _en_stem(w: str) -> str:
    """只用来比较两个说法是不是同一样东西：movie / movies、comedy / comedies、table / tables 落到同一个键。"""
    if len(w) <= 3 or w.endswith("ss"):
        return w
    w = re.sub(r"s$", "", w)
    return re.sub(r"(ie|y|e)$", "", w) if len(w) > 3 else w


def _like_objects(clause: str) -> set:
    out = set()
    for m in _LIKE_ZH.finditer(clause):
        o = re.sub(r"^(吃|喝|看|玩|听|去|用)", "", m.group("o"))
        if len(o) >= 1 and o not in ("吃", "喝", "看", "玩", "听"):
            out.add("like:" + o)
    for m in _LIKE_EN.finditer(clause):
        o = _EN_DET.sub("", m.group("o").strip().lower())
        o = " ".join(_en_stem(w) for w in o.split() if w not in ("really", "actually", "just"))
        if o and o not in ("it", "that", "this", "them", "you"):
            out.add("like:" + o)
    return out


def slots(kind: str, clause: str) -> set:
    """这个分句说的是哪几件事。people 不参与；认不出的返回空集（不会被顶替）。"""
    c = clause or ""
    found = set()
    for name, pat in _SLOT_RULES.get(kind, ()):
        if pat.search(c):
            found.add(name)
    if kind == "identity" and (_JOB_CHANGE.search(c) or (_JOB_VERB.search(c) and _JOB_WHAT.search(c))):
        found.add("job")
    if kind == "health":
        for m in _ALLERGY.finditer(c):
            found.add("allergy:" + (m.group(1) or m.group(2) or "").strip().lower())
    if kind == "likes":
        found |= _like_objects(c)
    return found


def _clauses(text: str) -> list:
    return [c.strip() for c in _CLAUSE.split(text or "") if c and c.strip()]


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

CHANGED_KEEP = 3                 # 「Changed since」里最多列几条


def items(conn: Optional[sqlite3.Connection] = None) -> dict:
    """{类别: [(日期, 摘句, mem_id)]}，新的在前，每类有上限，内容相同的只留最新一条；
    同一件事后来说法改了的，旧的分句不在类别里，列在 out["changed"]：[(日期, 被改的话, mem_id, 改口的日期)]。"""
    from nexsandglass.core import memid
    conn = conn or memid.get_conn()
    _ensure(conn)
    out = {k: [] for k in KINDS}
    out["changed"] = []
    seen = set()
    claimed = {}                                     # 话题槽 → 最新说它的那句的日期
    # 按说话的时间排，不按写入先后：导入的旧对话（hermes nyx import）写得晚，但不能排到最新
    rows = conn.execute("SELECT p.kind, m.mem_id, m.ts, m.text FROM profile_items p JOIN memories m "
                        "ON m.mem_id = p.mem_id WHERE m.deleted_at IS NULL ORDER BY m.ts DESC, m.seq DESC")
    for kind, mem_id, ts, text in rows:
        if all(len(out[k]) >= PER_KIND[k] for k in KINDS) and len(out["changed"]) >= CHANGED_KEEP:
            break                                    # 每类都满了：更早的不用再看
        if kind not in PER_KIND:
            continue
        hit = classify(text)                         # 正文可能已被隐去密钥 / 改写：按现在的正文重新摘
        if not hit:
            continue
        date, excerpt = str(ts or "")[:10], hit[1]
        clauses = _clauses(excerpt)
        cslots = [slots(kind, c) for c in clauses]
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
