"""待跟进（v7.23.0）—— 用户说过的、有日子也有结果的事：快到了的，和刚过去、还不知道结果的

为什么要有这一段
  每轮召回是拿「这一句」去找相关的记忆：新对话的第一句「你好」「帮我写周报」什么都找不到
  （Hermes 对 hi / ok / thanks 干脆不召回）；常驻画像只放长期不变的事（要求、饮食、家人、住处、喜好）。
  于是上周说的「下周三有个面试」，到了周五新开对话，模型面前什么都没有 —— 自然不会问一句「面试怎么样了」。
  这一段把这类事摆到模型面前；问不问、怎么问，由模型看场合决定。

抽（零 LLM，中英文规则，只看用户本人的、来源可信的话）
  同一个分句里既有「事」又有「日子」：
    事     面试、考试、体检复查、手术、看牙、出差旅行、搬家、答辩、上线、截止、婚礼、生日、比赛、演出、展会…
    日子   今天 / 明天 / 后天 / 下周三 / 这周六 / 周日 / 13号 / 10月8号 / 下个月 / 月底 / 过两天 / 三天后；
           today / tomorrow / next Wednesday / this Saturday / on the 13th / October 8th / next week / in two days…
  日子按说这句话的那天换算成具体日期（或者一段日期：下周、下个月）。
  不算：假设（如果 / 要是 / if）、否定（明天没有考试）、别人的事（我同事明天面试）、已经过去的（周三面试挂了）。

列（每个新会话开始时算一次；按离今天的远近排，最多 3 件）
  快到了          今天到后天
  还不知道结果    过去 10 天之内；那天以后谁都没再提过（用户说了结果，或者助手已经问过，都算提过）
  说过「取消了 / 不去了」的不列；「推迟到 / 改到」以新的日子为准。最后一行是给模型的分寸：
  对话刚开始或闲聊时可以顺口问一件、或者祝一句顺利；不打断正事；一次对话最多一次。

存：只存引用（mem_id、类别、日期范围），正文从中枢取 —— 忘掉原话（nyx_forget、/nyx forget-last），这一项跟着没了。
关：nyx.json 的 follow_ups: off（环境变量 NYX_FOLLOW_UPS=off），或者在对话里 /nyx follow-ups off。
"""
from __future__ import annotations

import calendar
import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

RULES_VERSION = "1"
UPCOMING_DAYS = 2              # 今天到后天
AWAITING_DAYS = 10             # 事情过去 10 天以内还没提过的才问
MAX_SHOWN = 3
MAX_AHEAD_DAYS = 120           # 更远的计划不收（「明年」「年底」本来也认不出）
LINE_CHARS = 160
_MAX_LEN = 1500                # 更长的是贴进来的文档 / 代码 / 日志
BATCH = 2000                   # 会话开始时最多补这么多条，剩下的由后台预热补完
_OFF = ("off", "0", "false", "no")

HEADER = ("[Follow-ups: things they told you about that are coming up or just happened; "
          "their own words, [date] = when they said it]")
GUIDANCE = ("If it fits — at the start of a conversation or in small talk — you may ask how one of these went, "
            "or wish them luck. At most once per conversation and briefly; never interrupt a task for it, "
            "and let it go if they don't pick it up.")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS followup_items (
    mem_id   TEXT NOT NULL,
    seq      INTEGER NOT NULL,
    kinds    TEXT NOT NULL,
    day_from TEXT NOT NULL,
    day_to   TEXT NOT NULL,
    PRIMARY KEY (mem_id, day_from, kinds)
);
CREATE INDEX IF NOT EXISTS idx_followup_to ON followup_items(day_to);
CREATE TABLE IF NOT EXISTS followup_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# ══════════════════════════════════════════════════════════
# 规则：事
# ══════════════════════════════════════════════════════════
#
# 类别只用来判断「后来又提到的是不是同一类事」（说了结果、取消了），不给模型看。
# 先后即优先级：一句话里有几类时，排在前面的算主类（只影响排序时的同类合并）。

_EVENTS = {
    "interview": (r"面试(?!邀请|通知)|(?<![见碰照])[一二三四]面(?![墙子包条粉积对前孔临镜旗之])|终面|复试|群面|HR面|面签",
                  r"\binterview(?:s|ing)?\b(?! (?:invit|request|question|tips))"),
    "exam": (r"考试|期末考|期中考|月考|模考|考研|考公|国考|省考|高考|中考|会考|雅思|托福|考驾照|驾考|科目[一二三四]|"
             r"路考|笔试|考级|教资|法考|司考|注会|考证",
             r"\b(?:exams?|finals|midterms?|GRE|GMAT|IELTS|TOEFL|LSAT|MCAT|bar exam|"
             r"(?:driving|road|written|entrance|placement|licen[cs]ing) test)\b"),
    "medical": (r"体检|复查|复诊|看医生|看病|就诊|门诊|看牙|拔牙|补牙|洗牙|智齿|根管|手术|开刀|胃镜|肠镜|核磁|B超|彩超|"
                r"产检|打疫苗|住院|出院|化疗|挂号|去医院",
                r"\b(?:appointment|checkup|check-up|physical exam|surgery|dentist|dental|doctor'?s|(?:see|seeing) (?:the|a|my) "
                r"doctor|(?:to|into) (?:the )?hospital|mri|ct scan|biopsy|ultrasound|colonoscopy|blood test|vaccin\w*|"
                r"booster shot|chemo|wisdom teeth|(?:the|a) vet\b|vet (?:visit|appointment))\b"),
    "life": (r"婚礼|结婚|领证|订婚|求婚|生日|满月|周岁|聚会|同学会|相亲|约会|见家长|葬礼|追悼会|毕业典礼|开学|家长会|"
             r"预产期|生孩子",
             r"\b(?:wedding|getting married|birthday|anniversary|(?<![-\w])party|reunion|first date|blind date|"
             r"date night|funeral|graduation|baby shower|parent-teacher)\b"),
    "event": (r"比赛|决赛|半决赛|马拉松|半马|全马|越野跑|运动会|演出|表演|音乐会|演唱会|独奏会|汇演|试镜|选拔赛",
              r"\b(?:marathon|half[- ]marathon|triathlon|race|tournament|recital|concert|audition|tryouts?|"
              r"competition|championship|(?:swim|track) meet|(?:big|final|championship) (?:game|match))\b"),
    "work": (r"上线|发版|发布会|路演|述职|答辩|汇报|演讲|晋升|入职|报到|离职|转正|截止|交稿|交付|签约|"
             r"见客户|年会|展会|博览会|峰会|大会|研讨会|开题|结题",
             r"\b(?:launch|release|go-live|demo day|presentation|pitch|keynote|deadline|ddl|due(?! to\b| for\b)|thesis|"
             r"dissertation|defen[cs]e|defending|performance review|first day|last day|onboarding|"
             r"conference(?! call)|expo|trade show|summit|convention|workshop)\b"),
    "travel": (r"出差|旅行|旅游|度假|出国|回国|回老家|航班|飞(?:去|往|到)|自驾游|出发(?!点)",
               r"\b(?:(?<!shopping )(?<!grocery )trip|vacation|flights?|on holiday|holiday (?:to|in|at)|travel(?:l?ing)? to|"
               r"fly(?:ing)? (?:to|out|home|back)|heading (?:to|out|home|back))\b"),
    "move": (r"搬家|乔迁|交房|收房|(?:我|我们|我家|全家)[^，。！？\s]{0,6}搬(?:到|去|回)",
             r"\b(?:(?:i'?m|we'?re|i am|we are) moving (?:to|out|house|in|back|across)|moving (?:day|house)|"
             r"move (?:out|house)|closing on (?:the|our) (?:house|home|apartment|condo))\b"),
}
KINDS = tuple(_EVENTS)
_KIND_RX = {k: re.compile(zh + "|" + en, re.I) for k, (zh, en) in _EVENTS.items()}

# 假设：整句不算（「如果下周有面试…」）
_HYPO = re.compile(r"如果|假如|(?<!主)要是|万一|假设|倘若|假使|\bif\b|\bin case\b|\bwhether\b|\bsuppose\b|\bwhat if\b|"
                   r"\bhypothetical", re.I)
# 事的前面紧挨着否定：「明天没有考试」「no interview tomorrow」
_NEG_ZH = re.compile(r"(?:没有|没|不用|不必|无需|不需要)[^，。！？\s]{0,3}$|不[^，。！？\s]?$")
_NEG_EN = re.compile(r"\b(?:no|not|don'?t|do not|never|haven'?t|didn'?t|doesn'?t)\b[^,.;!?]{0,20}$", re.I)
# 分句一开头就是别人：「我同事明天面试」「My friend has an interview」（家人的事照收；「朋友的婚礼」照收）
_TIME_HEAD = (r"(?:(?:今天|明天|后天|大后天|下下?(?:个)?(?:周|星期|礼拜)[一二三四五六日天]?|(?:这|本)(?:个)?(?:周|星期|礼拜)"
              r"[一二三四五六日天]?|(?:周|星期|礼拜)[一二三四五六日天]|\d{1,2}号)\s*)?")
_THIRD_ZH = re.compile(r"^\s*" + _TIME_HEAD + r"(?:我的?)?(?:同事|朋友|老板|领导|客户|室友|同学|邻居|网友|他们|她们|他|她|别人|有人)"
                       r"(?!的)")
_THIRD_EN = re.compile(r"^\s*(?:(?:tomorrow|today|tonight|next \w+|this \w+|on \w+),?\s+)?(?:my |our )?"
                       r"(?:friend|colleague|coworker|co-worker|boss|manager|client|roommate|neighbou?r|classmate|teammate|"
                       r"he|she|they)s?\b(?!')", re.I)
# 说的是对方（「祝你明天考试顺利」「你今天生日？」「good luck on your exam」）、或者只是一句祝福
_YOU_ZH, _ME_ZH = re.compile(r"你"), re.compile(r"我|咱")
_YOU_EN, _ME_EN = re.compile(r"\byour?\b", re.I), re.compile(r"\b(?:i|i'm|i've|i'll|my|me|we|we're|our|us)\b", re.I)
_YOU_BEFORE = re.compile(r"(?:\byou(?:'re| are|'ll| will|'ve| have)?|\byour)\s+(?:\w+\s+)?$|你们?的?[一-鿿]{0,2}$", re.I)
_GREETING = re.compile(r"生日快乐|happy birthday", re.I)
_NOT_A_DAY = re.compile(r"\b(?:black|good|cyber|casual) (?:friday|monday)\b", re.I)
# 说了取消 / 改期（这一句里提到同一类事时，之前说的作废；改期的新日子另算一件）
_CANCEL = re.compile(r"取消|不去了|去不了|不考了|不办了|不用去了|黄了|作罢|\bcancel+ed\b|\bcalled off\b|\bnot going\b|"
                     r"\bno longer going\b|\bwon'?t be going\b|\bcan'?t make it\b|\bscrapped\b", re.I)
_MOVE = re.compile(r"推迟|延期|改期|改到|挪到|往后推|提前到|\bpostponed\b|\brescheduled\b|\bpushed (?:back )?to\b|"
                   r"\bmoved (?:it )?to\b|\bdelayed\b", re.I)
# 「今天」+ 结果：是在说结果，不是计划（「今天面试过了」）
_OUTCOME = re.compile(r"完了|结束了|过了|挂了|考完|面完|做完|回来了|结果|顺利|没过|通过了|拿到|搞定|砸了|还行|不错|"
                      r"\b(?:went|was|were|got|finished|done|over|passed|failed|nailed|bombed|aced)\b", re.I)
# 「今天」、只说星期几 / 几号、具体日期：靠时态分辨是计划还是已经发生（「周三面试挂了」「I won the tournament on
# Friday」「I took Ethan to his appointment today」）。「明天」「下周三」这类本身就指将来，不看时态
_PAST_ZH = re.compile(r"了(?![吗吧呢啊呀])|刚(?!好)|做完|考完|面完|结束")
_FUT_ZH = re.compile(r"要|会|将|准备|打算|计划|预约|约了|安排|报了|订了|定了|想去|还有|有个|有一?场|得去")
_PAST_EN = re.compile(r"\b(?:had|was|were|went|did|got|took|won|lost|finished|passed|failed|attended|played|enjoyed|"
                      r"celebrated|visited|met|saw|watched|ran|completed|came|left|gave|presented|competed|performed|"
                      r"joined|hosted|returned|caught|learned|learnt|tried|spent|bought|ate|drove|flew|just|already|been|"
                      r"managed|didn'?t|wasn'?t|weren'?t|earlier today)\b|\b(?:i|we) (?!(?:need|feed|speed|bleed|proceed|"
                      r"succeed|exceed|breed|heed|seed|weed)\b)[a-z]+ed\b", re.I)
_FUT_EN = re.compile(r"\b(?:will|going to|gonna|planning|plan to|hoping|hope to|thinking of|scheduled|booked|need to|"
                     r"want to|would like to|upcoming|coming up)\b|'ll\b|\b(?:have|has) (?:a|an|my|his|her|our|the|to)\b|"
                     r"(?:\b(?:am|is|are)|'m|'re) \w+ing\b", re.I)

# ══════════════════════════════════════════════════════════
# 规则：日子
# ══════════════════════════════════════════════════════════

_N = r"(?:\d{1,2}|[一二三四五六七八九十]{1,3})"
_WD_ZH = r"[一二三四五六日天1-7]"
_WEEK = r"(?:周|星期|礼拜)"
_WD_EN = r"(?P<w>monday|tuesday|wednesday|thursday|friday|saturday|sunday)(?!s\b)"     # on Tuesdays = 每周都有
_MON_EN = (r"(?P<mon>january|february|march|april|may|june|july|august|september|october|november|december|"
           r"jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\.?")

# (名字, 正则, 要不要看时态 —— 「今天」、只说星期几 / 几号、具体日期，可能说的是已经发生的)
_TIME_RULES = [
    ("past", r"昨天|昨晚|昨日|前天|上(?:个)?" + _WEEK + _WD_ZH + "?" + r"|上(?:个)?月|去年", False),
    ("nnweek_wd", r"下下(?:个)?" + _WEEK + r"(?P<a>" + _WD_ZH + ")", False),
    ("nweek_wd", r"下(?:个)?" + _WEEK + r"(?P<a>" + _WD_ZH + ")", False),
    ("tweek_wd", r"(?:这|本)(?:个)?" + _WEEK + r"(?P<a>" + _WD_ZH + ")", False),
    ("nweekend", r"下(?:个)?周末", False),
    ("tweekend", r"(?<!每)(?:这|本)?(?:个)?周末", True),
    ("nnweek", r"下下(?:个)?" + _WEEK, False),
    ("nweek", r"下(?:个)?" + _WEEK, False),
    ("tweek", r"(?:这|本)(?:个)?" + _WEEK, True),
    ("wd", r"(?<!每)(?<!每个)" + _WEEK + r"(?P<a>" + _WD_ZH + ")", True),                  # 每周六 = 每周都有
    ("nmonth_day", r"下(?:个)?月(?P<d>" + _N + r")(?:号|日)", False),
    ("nmonth_end", r"下(?:个)?月(?:底|末)", False),
    ("nmonth_begin", r"下(?:个)?月初", False),
    ("nmonth", r"下(?:个)?月", False),
    ("month_end", r"(?:这|本)?(?:个)?月(?:底|末)", False),
    ("md", r"(?:(?P<y>\d{4})年)?(?P<m>" + _N + r")月(?P<d>" + _N + r")(?:号|日)?", True),
    ("day", r"(?<![\d月.．])(?:(?P<d>\d{1,2})(?:号|日)|(?P<dz>[一二三四五六七八九十]{1,3})号)"
            r"(?![\d线楼门位房厅车口机台球码仓院窗柜桌床])", True),
    ("in_days", r"(?P<n>\d{1,2}|[两三四五六七八九十])天(?:后|以后|之后)", False),
    ("in_weeks", r"(?P<n>\d|[一两三四])(?:个)?" + _WEEK + r"(?:后|以后|之后)", False),
    ("few_days", r"过(?P<f>两|几|2|3)天", False),
    ("dat3", r"大后天", False),
    ("dat", r"后天", False),
    ("tomorrow", r"明天|明日|明早|明晚|明儿", False),
    ("today", r"今天|今日|今晚|今早|今夜|今儿", True),
    # English
    ("past", r"\b(?:yesterday|last (?:night|week|month|year|weekend|monday|tuesday|wednesday|thursday|friday|saturday|"
             r"sunday)|(?:a|one|two|three|\d+) (?:days?|weeks?|months?) ago)\b", False),
    ("dat", r"\b(?:the )?day after tomorrow\b", False),
    ("tomorrow", r"\b(?:tomorrow|tmrw|tmr)\b", False),
    ("today", r"\b(?:today|tonight|this (?:morning|afternoon|evening))\b", True),
    ("nweekend", r"\bnext weekend\b", False),
    ("tweekend", r"\b(?:this|the) weekend\b(?! of)|\bover the weekend\b", True),       # the weekend of Aug 20 ≠ 这周末
    ("nweek_wd", r"\bnext " + _WD_EN, False),
    ("tweek_wd", r"\bthis (?:coming )?" + _WD_EN, False),
    ("wd", r"(?<!every )(?<!\ba )\b(?:on )?" + _WD_EN, True),            # every Saturday / on a Saturday：不是哪一天
    ("nweek", r"\bnext week\b", False),
    ("tweek", r"\b(?:later )?this week\b", True),
    ("nmonth_end", r"\b(?:at |by )?the end of next month\b", False),
    ("nmonth", r"\bnext month\b", False),
    ("month_end", r"\b(?:at |by )?(?:the )?end of (?:the|this) month\b|\bmonth-end\b", False),
    ("md", r"\b" + _MON_EN + r" (?P<d>\d{1,2})(?:st|nd|rd|th)?\b(?:,? (?P<y>\d{4})\b)?", True),
    ("md", r"\b(?P<d>\d{1,2})(?:st|nd|rd|th)? (?:of )?" + _MON_EN + r"\b(?:,? (?P<y>\d{4})\b)?", True),
    ("day", r"\bon the (?P<d>\d{1,2})(?:st|nd|rd|th)\b(?! of " + _MON_EN.replace("(?P<mon>", "(?:") + ")", True),
    ("in_days", r"\bin (?P<n>\d{1,2}|two|three|four|five|six|seven|ten) days\b", False),
    ("few_days", r"\bin (?P<f>a few|a couple of|a couple|few) days\b", False),
    ("in_weeks", r"\bin (?P<n>a|one|two|three|\d) weeks?\b", False),
]
_TIME_RX = [(name, re.compile(rx, re.I), bare) for name, rx, bare in _TIME_RULES]

_ZH_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_EN_NUM = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "ten": 10}
_WD_INDEX = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6, "七": 6,
             "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6,
             "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10,
           "nov": 11, "dec": 12}


def _num(s: str) -> Optional[int]:
    s = (s or "").strip().lower()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s in _EN_NUM:
        return _EN_NUM[s]
    if s == "十":
        return 10
    if "十" in s:
        a, _, b = s.partition("十")
        return (_ZH_DIGIT.get(a, 1) if a else 1) * 10 + (_ZH_DIGIT.get(b, 0) if b else 0)
    return _ZH_DIGIT.get(s)


def _month_last(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _next_month(d: date) -> tuple:
    return (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)


def _safe(y: int, m: int, dd: Optional[int]) -> Optional[date]:
    try:
        return date(y, m, int(dd)) if dd else None
    except (TypeError, ValueError):
        return None


def _resolve(name: str, m: re.Match, d: date) -> Optional[tuple]:
    """(开始, 结束) 或 None（过去的、换算不出来的）。d = 说这句话的那天。"""
    g = m.groupdict()
    monday = d - timedelta(days=d.weekday())
    wd = _WD_INDEX.get((g.get("a") or g.get("w") or "").lower())
    one = lambda x: (x, x) if x else None        # noqa: E731
    if name == "past":
        return None
    if name == "today":
        return d, d
    if name == "tomorrow":
        return one(d + timedelta(days=1))
    if name == "dat":
        return one(d + timedelta(days=2))
    if name == "dat3":
        return one(d + timedelta(days=3))
    if name in ("nweek_wd", "nnweek_wd", "tweek_wd") and wd is not None:
        weeks = {"tweek_wd": 0, "nweek_wd": 1, "nnweek_wd": 2}[name]
        x = monday + timedelta(days=7 * weeks + wd)
        return one(x) if x >= d else None
    if name == "wd" and wd is not None:
        return one(d + timedelta(days=(wd - d.weekday()) % 7))
    if name == "tweekend":
        sat = monday + timedelta(days=5)
        return (max(d, sat), sat + timedelta(days=1))
    if name == "nweekend":
        return monday + timedelta(days=12), monday + timedelta(days=13)
    if name in ("tweek", "nweek", "nnweek"):
        k = {"tweek": 0, "nweek": 1, "nnweek": 2}[name]
        a = monday + timedelta(days=7 * k)
        return max(a, d), a + timedelta(days=6)
    y, mo = _next_month(d)
    if name == "nmonth":
        return date(y, mo, 1), _month_last(y, mo)
    if name == "nmonth_begin":
        return date(y, mo, 1), date(y, mo, 5)
    if name == "nmonth_end":
        return date(y, mo, 25), _month_last(y, mo)
    if name == "nmonth_day":
        return one(_safe(y, mo, _num(g.get("d"))))
    if name == "month_end":
        last = _month_last(d.year, d.month)
        return max(d, date(d.year, d.month, min(25, last.day))), last
    if name == "md":
        mon = g.get("mon")
        mm = _MONTHS.get(mon.lower()[:3]) if mon else _num(g.get("m"))
        dd = _num(g.get("d"))
        if not mm or not dd or not 1 <= mm <= 12:
            return None
        if g.get("y"):                                      # 说了年份：照它（「June 15th, 2022」）
            return one(_safe(int(g["y"]), mm, dd))
        x = _safe(d.year, mm, dd)
        if x and x < d - timedelta(days=7):                 # 年底说「1月5号」= 明年
            x = _safe(d.year + 1, mm, dd)
        return one(x)
    if name == "day":
        dd = _num(g.get("d") or g.get("dz"))
        if not dd or not 1 <= dd <= 31:
            return None
        x = _safe(d.year, d.month, dd) if dd >= d.day else None
        return one(x or _safe(*_next_month(d), dd))
    if name == "in_days":
        n = _num(g.get("n"))
        return one(d + timedelta(days=n)) if n else None
    if name == "in_weeks":
        n = _num(g.get("n"))
        return one(d + timedelta(days=7 * n)) if n else None
    if name == "few_days":
        f = (g.get("f") or "").lower()
        hi = 3 if f in ("两", "2", "a couple of", "a couple") else 5
        return d + timedelta(days=2), d + timedelta(days=hi)
    return None


def _times(text: str, d: date) -> list:
    """一句话里的时间说法：[(起点位置, 名字, (开始, 结束) 或 None, 要不要看时态)]，左起最长、不重叠。"""
    text = _NOT_A_DAY.sub(lambda m: " " * len(m.group(0)), text)      # Black Friday 不是「周五」
    found = []
    for i, (name, rx, bare) in enumerate(_TIME_RX):
        for m in rx.finditer(text):
            found.append((m.start(), -(m.end() - m.start()), i, m, name, bare))
    found.sort(key=lambda x: (x[0], x[1], x[2]))
    out, end = [], -1
    for start, _neg_len, _i, m, name, bare in found:
        if start < end:
            continue
        end = m.end()
        out.append((start, end, name, _resolve(name, m, d), bare))
    return out


# ══════════════════════════════════════════════════════════
# 抽
# ══════════════════════════════════════════════════════════

_SPLIT = re.compile(r"(?<=[。！？!?])|(?<=\.)\s+|\n+")
_CLAUSE = re.compile(r"[，；;]+\s*|,(?!\s?\d)\s*")          # 10,000 和 April 10, 2023 不拆开


def _sentences(text: str) -> list:
    return [s.strip() for s in _SPLIT.split(text or "") if s and s.strip()]


def _clauses(sentence: str) -> list:
    return [c.strip() for c in _CLAUSE.split(sentence or "") if c and c.strip()]


def kinds_in(text: str) -> list:
    """这段话提到了哪几类事（按优先级）。"""
    return [k for k in KINDS if _KIND_RX[k].search(text or "")]


def _event_spans(text: str, kinds) -> list:
    return sorted((m.start(), m.end()) for k in kinds for m in _KIND_RX[k].finditer(text))


def _is_past_tense(clause: str, sentence: str = "") -> bool:
    """说的是已经发生的事。分句本身看不出时态（「including the Katy Perry concert today」）时看整句。"""
    zh = bool(re.search(r"[一-鿿]", clause))
    past, fut = (_PAST_ZH, _FUT_ZH) if zh else (_PAST_EN, _FUT_EN)
    text = clause if (past.search(clause) or fut.search(clause) or not sentence) else sentence
    return bool(past.search(text)) and not fut.search(text)


def _future_spans(clause: str, d: date, near: Optional[list] = None, sentence: str = "") -> list:
    """分句里指向今天或以后的日子 [(离「事」多远, 位置, (开始, 结束))]；改期（推迟到…）只看改期之后的。"""
    times = _times(clause, d)
    mv = _MOVE.search(clause)
    if mv:
        times = [t for t in times if t[0] >= mv.start()] or times
    ok = []
    for ts, te, name, span, check_tense in times:
        if not span or span[1] < d or span[0] > d + timedelta(days=MAX_AHEAD_DAYS):
            continue
        if check_tense and _is_past_tense(clause, sentence):
            continue
        if name == "today" and _OUTCOME.search(clause):
            continue                                     # 「今天面试过了」：在说结果
        gap = min((max(0, es - te, ts - ee) for es, ee in near), default=0) if near else 0
        ok.append((gap, ts, span))
    return sorted(ok)


def _future_time(clause: str, d: date, near: Optional[list] = None, sentence: str = "") -> Optional[tuple]:
    """分句里离「事」最近的、指向今天或以后的日子 (开始, 结束)。
    离「事」最近的那个日子本身是过去的（「my Costco trip last weekend … this week」），这件事就是过去的。"""
    if near:
        times = _times(clause, d)
        if times:
            gap = lambda t: min(max(0, es - t[1], t[0] - ee) for es, ee in near)     # noqa: E731
            closest = min(times, key=lambda t: (gap(t), t[0]))
            if closest[3] is None or closest[3][1] < d:
                if not (_MOVE.search(clause) and closest[0] < _MOVE.search(clause).start()):
                    return None
    ok = _future_spans(clause, d, near, sentence)
    return ok[0][2] if ok else None


_AND = re.compile(r"\s+(?:and|but)\s+", re.I)


def _part_spans(part: str, kinds: list) -> Optional[list]:
    """这一段里「事」的位置；说的是否定、别人、对方时返回 None。"""
    spans = _event_spans(part, kinds)
    before = part[:spans[0][0]]
    if _NEG_ZH.search(before) or _NEG_EN.search(before) or _THIRD_ZH.search(part) or _THIRD_EN.search(part):
        return None
    if (_YOU_ZH.search(part) and not _ME_ZH.search(part)) or (_YOU_EN.search(part) and not _ME_EN.search(part)):
        return None
    if _YOU_BEFORE.search(before):           # 「you're heading to Koenji」「your exam」：对方的事
        return None
    return spans


def extract(text: str, said) -> list:
    """一条用户消息里的待跟进：[(类别元组, 开始日期, 结束日期, 所在的句子)]。said = 说这句话的时间。"""
    text = str(text or "").strip()
    if not text or len(text) > _MAX_LEN:
        return []
    d = said.date() if isinstance(said, datetime) else said
    out = []
    for sent in _sentences(text):
        if _HYPO.search(sent) or _CANCEL.search(sent) or _GREETING.search(sent):
            continue
        for cl in _clauses(sent):
            if not kinds_in(cl):
                continue
            # 英文的 and / but 再分一层：「a meeting on Monday and a deadline by Friday」各配各的日子；
            # 「I just got back from a trip and … this weekend」里的 trip 是过去的事
            parts = [cl] if re.search(r"[一-鿿]", cl) else [p for p in _AND.split(cl) if p.strip()]
            only = None
            if len(parts) > 1:
                # 只借给并列的另一件事：「an interview and a dentist appointment tomorrow」；
                # 没有事的那一段的日子不借（「…Portland, Oregon today and … my next trip」）
                whole = _future_spans(cl, d, sentence=sent)
                owned = [s for p in parts if kinds_in(p) for s in _future_spans(p, d, sentence=sent)]
                only = whole[0][2] if len(whole) == 1 and len(owned) == 1 else None
            for part in parts:
                kinds = kinds_in(part)
                spans = _part_spans(part, kinds) if kinds else None
                if not spans:
                    continue
                span = _future_time(part, d, near=spans, sentence=sent)
                if span is None and only and not _is_past_tense(part, sent) and not _times(part, d):
                    span = only                                  # 「an interview and a dentist appointment tomorrow」
                if span:
                    out.append((tuple(kinds), span[0], span[1], sent))
    return out


# ══════════════════════════════════════════════════════════
# 存（增量，同 profile.py）
# ══════════════════════════════════════════════════════════

def _ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


def _meta(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM followup_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def _said(ts) -> datetime:
    s = str(ts or "")
    for fmt, n in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%dT%H:%M:%S", 19), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(s[:n], fmt)
        except ValueError:
            continue
    return datetime.now()


def _store(conn, mem_id: str, seq: int, ts, text: str, line_start) -> int:
    from nexsandglass.core.profile import _trusted
    found = extract(text, _said(ts))
    if not found or not _trusted(line_start):
        return 0
    for kinds, a, b, _ in found:
        conn.execute("INSERT OR REPLACE INTO followup_items(mem_id, seq, kinds, day_from, day_to) VALUES (?, ?, ?, ?, ?)",
                     (mem_id, seq, ",".join(kinds), a.isoformat(), b.isoformat()))
    return len(found)


_REFRESH_LOCK = threading.Lock()


def refresh(conn: Optional[sqlite3.Connection] = None, max_rows: Optional[int] = None, wait: bool = True) -> int:
    """把水位线之后用户说的话里的待跟进收进来。返回新收的件数。wait=False：别的线程正在补就直接返回。"""
    if not _REFRESH_LOCK.acquire(blocking=wait):
        return 0
    try:
        return _refresh(conn, max_rows)
    finally:
        _REFRESH_LOCK.release()


def refresh_ready(conn: Optional[sqlite3.Connection] = None, *, deadline: float) -> int:
    """Readiness variant for one-shot voice context, through an initial watermark.

    Bound lock waiting and check the shared deadline around 64-row batches;
    never return a partial index as a complete set of plans/cancellations.
    SQLite I/O keeps its connection timeout and the host's outer deadline.
    """
    if not _REFRESH_LOCK.acquire(timeout=max(0.0, deadline - time.monotonic())):
        raise TimeoutError("Nyx voice follow-ups are still refreshing")
    try:
        from nexsandglass.core import memid
        conn = conn or memid.get_conn()
        target = int(conn.execute("SELECT COALESCE(MAX(seq), 0) FROM memories").fetchone()[0])
        added = 0
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("Nyx voice follow-ups are still refreshing")
            added += _refresh(conn, max_rows=64)
            ready = int(_meta(conn, "seq", "0") or 0) >= target
            if time.monotonic() >= deadline:
                raise TimeoutError("Nyx voice follow-ups are still refreshing")
            if ready:
                return added
    finally:
        _REFRESH_LOCK.release()


def _refresh(conn: Optional[sqlite3.Connection], max_rows: Optional[int]) -> int:
    from nexsandglass.core import memid
    conn = conn or memid.get_conn()
    _ensure(conn)
    if _meta(conn, "rules") != RULES_VERSION:
        conn.execute("DELETE FROM followup_items")
        conn.execute("INSERT OR REPLACE INTO followup_meta(key, value) VALUES ('rules', ?)", (RULES_VERSION,))
        conn.execute("INSERT OR REPLACE INTO followup_meta(key, value) VALUES ('seq', '0')")
        conn.commit()
    mark = int(_meta(conn, "seq", "0") or 0)
    top = int(conn.execute("SELECT COALESCE(MAX(seq), 0) FROM memories").fetchone()[0] or 0)
    sql = ("SELECT mem_id, seq, ts, text, line_start FROM memories WHERE seq > ? AND sender = 'user' "
           "AND deleted_at IS NULL ORDER BY seq")
    rows = conn.execute(sql + (" LIMIT ?" if max_rows else ""), (mark, max_rows) if max_rows else (mark,)).fetchall()
    added = 0
    for mem_id, seq, ts, text, line_start in rows:
        added += _store(conn, mem_id, seq, ts, text, line_start)
    last = rows[-1][1] if (max_rows and len(rows) >= max_rows) else top
    conn.execute("INSERT OR REPLACE INTO followup_meta(key, value) VALUES ('seq', ?)", (str(max(mark, last)),))
    conn.commit()
    return added


def reconsider(mem_ids) -> int:
    """从隔离区救回来的话重新看一遍（水位线早就过了它们）。"""
    from nexsandglass.core import memid
    conn = memid.get_conn()
    _ensure(conn)
    added = 0
    with _REFRESH_LOCK:
        for mid in mem_ids or ():
            r = conn.execute("SELECT seq, ts, text, line_start FROM memories WHERE mem_id = ? AND sender = 'user' "
                             "AND deleted_at IS NULL", (mid,)).fetchone()
            if r:
                added += _store(conn, mid, r[0], r[1], r[2], r[3])
        conn.commit()
    return added


# ══════════════════════════════════════════════════════════
# 列
# ══════════════════════════════════════════════════════════

_STOP_EN = {"have", "with", "next", "this", "that", "from", "they", "will", "going", "pretty", "really", "about", "after",
            "before", "tomorrow", "today", "week", "weekend", "month", "monday", "tuesday", "wednesday", "thursday",
            "friday", "saturday", "sunday", "been", "were", "what", "when", "your", "just", "some", "then", "than",
            "there", "their", "them", "into", "over", "went", "fine", "good", "great", "well", "also", "back", "getting",
            "it's", "i'm", "we're", "he's", "she's", "morning", "evening", "afternoon", "night", "nervous", "excited"}
_STOP_ZH = {"下周", "这周", "本周", "周一", "周二", "周三", "周四", "周五", "周六", "周日", "周末", "明天", "后天", "今天",
            "我们", "一个", "有个", "要去", "已经", "还是", "什么", "怎么", "这个", "那个", "时候", "可以", "没有", "不是",
            "就是", "我的", "去了", "有点", "一下", "下个", "个月"}


def _tokens(text: str) -> set:
    """比对「说的是不是同一件事」用的实词：英文 4 个字母以上的词，中文相邻两个字。"""
    out = {w for w in re.findall(r"[a-z][a-z'-]{3,}", (text or "").lower()) if w not in _STOP_EN}
    for chunk in re.sub(r"[^一-鿿]", " ", text or "").split():
        out.update(chunk[i:i + 2] for i in range(len(chunk) - 1) if chunk[i:i + 2] not in _STOP_ZH)
    return out


def _about(sent: str, kinds: tuple, tokens: set) -> bool:
    """这句话说的是不是这件事：提到了它的主类；或者提到了它的次类、而且有同一个实词（「the Seattle trip」）。
    「在诊所的面试」次类是看病：之后「我爸体检结果不错」不能算这场面试的结果。"""
    if _KIND_RX[kinds[0]].search(sent):
        return True
    return any(_KIND_RX[k].search(sent) for k in kinds[1:]) and bool(_tokens(sent) & tokens)


def _mentions(text: str, kinds: tuple, tokens: set, when: datetime) -> str:
    """一条后来的消息怎么提到这件事：'cancel'（取消 / 改期）/ 'plan'（又说了一次计划，带将来的日子）/
    'mention'（提到了，没有将来的日子：说结果、问结果）/ ''（没提）。"""
    best = ""
    d = when.date()
    for sent in _sentences(text):
        if not _about(sent, kinds, tokens):
            continue
        if _CANCEL.search(sent) or _MOVE.search(sent):
            return "cancel"
        for cl in _clauses(sent):
            if not any(_KIND_RX[k].search(cl) for k in kinds):
                continue
            if _future_time(cl, d):
                best = best or "plan"
            else:
                best = "mention"
    return best


def due(now: Optional[datetime] = None, conn: Optional[sqlite3.Connection] = None) -> list:
    """现在该列的待跟进，排好序、最多 MAX_SHOWN 件：
    [{state: upcoming|awaiting, start, end, said, text, mem_id, kinds}]"""
    from nexsandglass.core import clock, memid
    conn = conn or memid.get_conn()
    _ensure(conn)
    now = now or clock.now()
    today = now.date()
    lo = (today - timedelta(days=AWAITING_DAYS)).isoformat()
    hi = (today + timedelta(days=UPCOMING_DAYS)).isoformat()
    rows = conn.execute(
        "SELECT f.mem_id, f.kinds, f.day_from, f.day_to, m.ts, m.text FROM followup_items f JOIN memories m "
        "ON m.mem_id = f.mem_id WHERE m.deleted_at IS NULL AND m.sender = 'user' AND f.day_to >= ? "
        "AND f.day_from <= ? AND m.ts <= ? ORDER BY m.ts", (lo, hi, now.strftime("%Y-%m-%d %H:%M:%S"))).fetchall()
    cands = []
    for mem_id, kinds, a, b, ts, text in rows:
        said = _said(ts)
        start, end = date.fromisoformat(a), date.fromisoformat(b)
        ks = tuple(kinds.split(","))
        # 正文按现在的样子重新抽一遍：被改写 / 隐去以后对不上的不列
        sent = next((s for k2, a2, b2, s in extract(text, said) if k2 == ks and a2 == start and b2 == end), None)
        if sent is None:
            continue
        cands.append({"mem_id": mem_id, "kinds": ks, "start": start, "end": end, "said": said, "text": text,
                      "sentence": sent, "tokens": _tokens(sent),
                      "state": "awaiting" if end < today else "upcoming"})
    if not cands:
        return []
    # 同一类、日子重叠的：以后说的为准（「下周三面试」之后又说「明天面试」）
    cands.sort(key=lambda c: c["said"], reverse=True)
    kept = []
    for c in cands:
        if any(c["kinds"][0] == k["kinds"][0] and c["start"] <= k["end"] and k["start"] <= c["end"] for k in kept):
            continue
        kept.append(c)
    # 后来的消息：取消 / 改期了没有；事情过去以后提过没有（用户说了结果、助手问过，都算）
    since = min(c["said"] for c in kept).strftime("%Y-%m-%d %H:%M:%S")
    later = conn.execute(
        "SELECT ts, sender, text FROM memories WHERE deleted_at IS NULL AND sender IN ('user', 'agent') "
        "AND ts > ? AND ts <= ? ORDER BY ts LIMIT 20000", (since, now.strftime("%Y-%m-%d %H:%M:%S"))).fetchall()
    out = []
    for c in kept:
        drop = False
        for ts, sender, text in later:
            when = _said(ts)
            if when <= c["said"]:
                continue
            how = _mentions(text, c["kinds"], c["tokens"], when)
            if how == "cancel":
                drop = True
            elif how == "mention":
                wd = when.date()
                if c["state"] == "awaiting":
                    # 当天说的要带结果（「今天面试完了」）；当天早上的「面试准备好了」不算
                    drop = wd > c["start"] or (wd == c["start"] and bool(_OUTCOME.search(text)))
                else:
                    # 还没到：助手已经在前一两天提过（祝过顺利）就不再列；紧跟着那句话的回复不算
                    drop = (sender == "agent" and wd >= c["start"] - timedelta(days=UPCOMING_DAYS)
                            and (when - c["said"]) > timedelta(hours=1))
            if drop:
                break
        if not drop:
            out.append(c)

    def dist(c) -> int:
        if c["start"] <= today <= c["end"]:
            return 0
        return (today - c["end"]).days if c["end"] < today else (c["start"] - today).days

    # 一段日期太长的（下个月、下周）往后排：具体的日子更该问
    out.sort(key=lambda c: ((c["end"] - c["start"]).days > 7, dist(c), -c["said"].timestamp()))
    return out[:MAX_SHOWN]


_WEEKDAY = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _when(c: dict) -> str:
    a, b = c["start"], c["end"]
    if a == b:
        return f"on {_WEEKDAY[a.weekday()]} {a.isoformat()}"
    return f"between {a.isoformat()} and {b.isoformat()}"


def _excerpt(c: dict) -> str:
    flat = " ".join(str(c["text"]).split())
    if len(flat) <= LINE_CHARS:
        return flat
    s = " ".join(c["sentence"].split())
    return s if len(s) <= LINE_CHARS else s[: LINE_CHARS - 1].rstrip() + "…"


def lines(items: list, today: Optional[date] = None) -> list:
    if today is None:
        from nexsandglass.core import clock
        today = clock.now().date()
    out = []
    for c in items:
        said = c["said"].strftime("%Y-%m-%d")
        if c["state"] == "awaiting":
            head = f"Was {_when(c)}; nothing said about it since"
        elif c["start"] < c["end"] and c["start"] <= today:
            head = f"Some time {_when(c)} (no exact day given)"      # 「下个月」已经到了这个月：不一定还没发生
        else:
            head = f"Coming up {_when(c)}"
        out.append(f"- {head}: [{said}] {_excerpt(c)}")
    return out


def current(now: Optional[datetime] = None) -> list:
    """先把新说的话收进来，再算现在该列的（/nyx follow-ups、/nyx status 用）。"""
    refresh(max_rows=BATCH, wait=False)
    return due(now)


def render(now: Optional[datetime] = None, conn: Optional[sqlite3.Connection] = None,
           *, deadline: Optional[float] = None) -> str:
    """常驻块里「待跟进」那一段；关着或者没有就返回空串。"""
    if not enabled():
        return ""
    if deadline is not None:
        refresh_ready(conn, deadline=deadline)
    try:
        from nexsandglass.core import clock
        now = now or clock.now()
        if deadline is None:
            refresh(conn, max_rows=BATCH, wait=False)       # 不让会话开始等：老库的历史由后台补
        got = due(now, conn)
    except Exception as e:
        logger.warning("待跟进读取失败（常驻块里先不放）: %s", e)
        return ""
    if not got:
        return ""
    return HEADER + "\n" + "\n".join(lines(got, now.date())) + "\n" + GUIDANCE


# ══════════════════════════════════════════════════════════
# 开关
# ══════════════════════════════════════════════════════════

def _path() -> str:
    from nexsandglass.core.sandglass_paths import _NB
    return os.path.join(_NB, "follow_ups.json")


def switched_off() -> Optional[dict]:
    """在对话里关掉的（/nyx follow-ups off）：{since, by}；没关是 None。"""
    p = _path()
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f) or {"since": ""}
    except Exception:
        return {"since": ""}


def setting_off() -> bool:
    """nyx.json 的 follow_ups / 环境变量 NYX_FOLLOW_UPS 关掉了。"""
    from nexsandglass.core import settings
    return (settings.get("NYX_FOLLOW_UPS") or "on").strip().lower() in _OFF


def enabled() -> bool:
    return not setting_off() and switched_off() is None


def switch(on: bool, by: str = "") -> bool:
    """/nyx follow-ups on|off。返回状态有没有变。"""
    if on:
        try:
            os.remove(_path())
            return True
        except FileNotFoundError:
            return False
    if switched_off() is not None:
        return False
    from nexsandglass.core.fsutil import atomic_write
    with atomic_write(_path()) as f:
        json.dump({"since": datetime.now().isoformat(timespec="seconds"), "by": by}, f, ensure_ascii=False)
    return True
