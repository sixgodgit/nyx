"""
core/understand.py — 话语理解：一句话 → (谁, 关系, 值, 从何时起, 是否至今仍真)（v7.10）

为什么需要
==========
v7.9 的纵向评测把瓶颈定位得很清楚：时间与信任的逻辑已经经得起测（oracle 抽取下 100%），
端到端的短板在**理解**。旧的正则层（features/weavethread.py）：

  - 抽不出「住在 / 公司 / 邮箱 / 电话」—— 根本没有这些模式
  - 实体边界粗：「改用吉利了，特斯拉太贵」→ 对象是「吉利了」
  - 把泛化的「用了 X」一律归为单值关系「使用」—— 「用了 Python」之后「用了 Docker」被当成切换
  - 不读时间：「2 月 25 号就换成吉利了」里的起始时间被丢掉，系统只能假设 = 说话时刻
  - 不读"体"：「很早以前住在鹿特丹」与「现在住在鹿特丹」写进去一模一样

存储层（v7.7–v7.9）早已准备好接收这五样东西：`record_fact(valid_from=, ongoing=)`。
缺的只是把话读懂的那一层。

设计
====
1. **规则后端（默认，零依赖）**：按关系写的句式 + 一份小词典（城市 / 车企）定边界，
   词典之外用"截断到助词/标点"的启发式兜底。时间表达按说话时刻解析成绝对时间。
2. **LLM 后端（可选）**：`NYX_UNDERSTAND_LLM=1` 时经现有 OpenAI 兼容网关
   （`LLM_EXTRACT_API_URL`，与 core/llm_extract.py 同一个）抽取同样的五元组。
   LLM 的输出**必须过校验**才采用：关系在本体内、对象与证据是原文子串、时间可解析 ——
   否则丢弃。任何失败都退回规则结果，从不阻塞写入。
3. **不猜**：说的是往事却没给时间（「我以前住在鹿特丹」）→ 不写成现任，
   标 `deferred`（原句仍在沙漏里可检索）。编一个起始时间比不记更糟。

关系本体（与 temporal_fact.TEMPORAL_RELATIONS 对齐）
==================================================
    单值、随时间变化：住在 / 使用（座驾）/ 公司 / 职位 / 邮箱 / 电话
    多值、不做时序截断：偏好 / 反感

"使用"在这里只指**座驾**（有车的语境或车企词典命中）。泛化的「用了 Python」
不再进入单值关系 —— 那是 v7.7 就记录在案的已知局限。

如实说明
========
规则后端覆盖的是常见说法，不是全部说法。纵向评测的轨道 C 用**开发句式**与
**留出句式**两组分别报数：开发句式是写规则时看着的，留出句式是规则冻结之后才写的。
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

SINGLE_VALUED = ("住在", "使用", "公司", "职位", "邮箱", "电话")
MULTI_VALUED = ("偏好", "反感")
ONTOLOGY = SINGLE_VALUED + MULTI_VALUED


@dataclass
class Fact:
    subject: str
    relation: str
    object: str
    valid_from: Optional[datetime] = None     # 陈述的起始时间；None = 没说
    ongoing: Optional[bool] = None            # 至今仍真？None = 没法判断
    confidence: float = 0.7
    evidence: str = ""                        # 原文里支撑这条事实的片段
    backend: str = "rules"
    deferred: bool = False                    # 往事但没给时间 → 不写成现任

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["valid_from"] = self.valid_from.strftime("%Y-%m-%dT%H:%M:%S") if self.valid_from else None
        return d


# ══════════════════════════════════════════════════════════
# 词典（只用来定边界；词典外的实体走启发式）
# ══════════════════════════════════════════════════════════

CITIES = [
    "阿姆斯特丹", "鹿特丹", "乌得勒支", "代尔夫特", "埃因霍温", "格罗宁根", "马斯特里赫特",
    "奈梅亨", "哈勒姆", "蒂尔堡", "布雷达", "莱顿", "海牙", "阿纳姆", "兹沃勒", "阿尔梅勒",
    "北京", "上海", "广州", "深圳", "杭州", "成都", "西安", "南京", "武汉", "重庆", "天津",
    "苏州", "长沙", "郑州", "青岛", "厦门", "香港", "澳门", "台北", "新加坡",
    "伦敦", "巴黎", "柏林", "慕尼黑", "布鲁塞尔", "安特卫普", "纽约", "旧金山", "东京", "首尔",
    "Amsterdam", "Rotterdam", "The Hague", "Den Haag", "Utrecht", "Delft", "Leiden",
    "Eindhoven", "Groningen", "London", "Paris", "Berlin", "Brussels",
]
CAR_BRANDS = [
    "特斯拉", "吉利", "比亚迪", "蔚来", "小鹏", "理想", "极氪", "领克", "问界", "小米",
    "大众", "丰田", "本田", "日产", "宝马", "奔驰", "奥迪", "保时捷", "沃尔沃", "福特",
    "现代", "起亚", "马自达", "斯柯达", "标致", "雪铁龙", "雷诺", "雷克萨斯", "红旗",
    "长城", "哈弗", "奇瑞", "路虎", "捷豹", "凯迪拉克", "别克", "雪佛兰",
    "Tesla", "BMW", "Audi", "Volvo", "Toyota", "Volkswagen", "VW", "Mercedes", "Porsche",
]

_CITY_RX = "|".join(sorted(map(re.escape, CITIES), key=len, reverse=True))
_CAR_RX = "|".join(sorted(map(re.escape, CAR_BRANDS), key=len, reverse=True))

# 实体边界兜底：到这些字/词为止
_STOP = re.compile(r"(了|啦|呢|吧|啊|呀|哦|的|做|当|作为|来|去|上班|工作|任职|，|,|。|！|!|？|\?|；|;|、|\s{2,}|$)")
_TRAIL = re.compile(r"(了|啦|呢|吧|啊|呀|哦|的|啦)+$")


def _cut(s: str, maxlen: int = 20) -> str:
    """从 s 开头取一个实体：到第一个停止词为止，再去掉尾部助词。"""
    s = s.strip()
    m = _STOP.search(s)
    ent = s[:m.start()] if m else s
    ent = _TRAIL.sub("", ent.strip())[:maxlen].strip()
    return ent


# ══════════════════════════════════════════════════════════
# 时间表达 → 绝对时间（相对于说话时刻）
# ══════════════════════════════════════════════════════════

_CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
           "八": 8, "九": 9, "十": 10, "半": 0.5}


def _num(s: str) -> Optional[float]:
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return float(s)
    if s in _CN_NUM:
        return float(_CN_NUM[s])
    if len(s) == 2 and s[0] == "十" and s[1] in _CN_NUM:
        return 10 + _CN_NUM[s[1]]
    if len(s) == 2 and s[1] == "十" and s[0] in _CN_NUM:
        return _CN_NUM[s[0]] * 10
    if len(s) == 3 and s[1] == "十":
        return _CN_NUM.get(s[0], 0) * 10 + _CN_NUM.get(s[2], 0)
    return None


_N = r"(\d+|[一二两三四五六七八九十半]{1,3})"

_TIME_PATTERNS = [
    # 2026-02-25 / 2026/2/25 / 2026.2.25
    ("ymd", re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")),
    # 2026年2月25日/号
    ("ymd_cn", re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")),
    # 今年/去年/前年 3月(25日)
    ("rel_year_md", re.compile(r"(今年|去年|前年)\s*(\d{1,2}|[一二三四五六七八九十]{1,2})\s*月(?:份)?"
                               r"(?:\s*(\d{1,2})\s*[日号])?")),
    # 2月25日/号
    ("md", re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")),
    # 2026年2月
    ("ym_cn", re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")),
    # 2026年 / 2023 年
    ("y_cn", re.compile(r"(\d{4})\s*年")),
    # N天/周/个月/年 前
    ("ago", re.compile(_N + r"\s*(天|日|周|个星期|星期|个礼拜|礼拜|个月|月|年)(?:之)?前")),
    ("yesterday", re.compile(r"(前天|昨天|今天)")),
    ("last_unit", re.compile(r"(上周|上个星期|上礼拜|上个月|上月|去年|前年|今年年初|年初)")),
    # 3月初/中/底、3月份
    ("m_only", re.compile(r"(?<![\d年])(\d{1,2}|[一二三四五六七八九十]{1,2})\s*月(?:份|初|中|底)?(?!\s*\d)")),
    # English
    ("iso_en", re.compile(r"\bsince\s+(\d{4})-(\d{1,2})-(\d{1,2})\b", re.I)),
    ("en_ago", re.compile(r"\b(\d+)\s+(day|week|month|year)s?\s+ago\b", re.I)),
]


def _safe(y, m, d=1) -> Optional[datetime]:
    try:
        return datetime(int(y), int(m), int(d))
    except (ValueError, TypeError):
        return None


def parse_when(text: str, now: datetime) -> Optional[datetime]:
    """找出句子里描述**起始时间**的表达，解析为绝对时间（天粒度）。找不到返回 None。

    没写年份的「2 月 25 号」：取不晚于说话时刻的最近一个 —— 在 9 月说「2 月」指今年 2 月，
    在 1 月说「11 月」指去年 11 月。
    """
    today = datetime(now.year, now.month, now.day)
    for kind, rx in _TIME_PATTERNS:
        m = rx.search(text)
        if not m:
            continue
        g = m.groups()
        if kind in ("ymd", "ymd_cn", "iso_en"):
            d = _safe(g[0], g[1], g[2])
        elif kind == "ym_cn":
            d = _safe(g[0], g[1])
        elif kind == "y_cn":
            d = _safe(g[0], 1)
        elif kind == "md":
            d = _safe(now.year, g[0], g[1])
            if d and d > today:
                d = _safe(now.year - 1, g[0], g[1])
        elif kind == "rel_year_md":
            y = now.year - {"今年": 0, "去年": 1, "前年": 2}[g[0]]
            mo = _num(g[1])
            d = _safe(y, int(mo), g[2] or 1) if mo else None
        elif kind == "m_only":
            mo = _num(g[0])
            if not mo or not 1 <= mo <= 12:
                continue
            day = 1
            tail = text[m.end() - 1:m.end()]
            if "中" in m.group(0):
                day = 15
            elif "底" in m.group(0):
                day = 25
            d = _safe(now.year, int(mo), day)
            if d and d > today:
                d = _safe(now.year - 1, int(mo), day)
        elif kind == "ago":
            n = _num(g[0])
            if n is None:
                continue
            unit = g[1]
            if unit in ("天", "日"):
                d = today - timedelta(days=n)
            elif "周" in unit or "星期" in unit or "礼拜" in unit:
                d = today - timedelta(days=7 * n)
            elif "月" in unit:
                d = today - timedelta(days=round(30.4 * n))
            else:
                d = today - timedelta(days=round(365.25 * n))
        elif kind == "en_ago":
            n = int(g[0])
            days = {"day": 1, "week": 7, "month": 30.4, "year": 365.25}[g[1].lower()]
            d = today - timedelta(days=round(n * days))
        elif kind == "yesterday":
            d = today - timedelta(days={"今天": 0, "昨天": 1, "前天": 2}[g[0]])
        elif kind == "last_unit":
            w = g[0]
            if w in ("上周", "上个星期", "上礼拜"):
                d = today - timedelta(days=today.weekday() + 7)
            elif w in ("上个月", "上月"):
                first = today.replace(day=1)
                d = (first - timedelta(days=1)).replace(day=1)
            elif w == "去年":
                d = _safe(now.year - 1, 1)
            elif w == "前年":
                d = _safe(now.year - 2, 1)
            else:
                d = _safe(now.year, 1)
        else:
            d = None
        if d is not None:
            return d
    return None


# ══════════════════════════════════════════════════════════
# 体：至今仍真 / 往事
# ══════════════════════════════════════════════════════════

_PAST = re.compile(
    r"(以前|之前|曾经|很早以前|早些年|那时候|那时|当时|中间那段|那段时间|过去|原来|小时候|上学时|"
    r"住过|用过|开过|待过|干过|做过|呆过|"
    r"\bused to\b|\bpreviously\b|\bback then\b|\bformerly\b)", re.I)
_ONGOING = re.compile(
    r"(现在|目前|如今|至今|一直|眼下|这阵子|最近|已经|就(?:换|搬|改|去|跳)|换成|改用|搬到|搬去|搬进|"
    r"跳槽到|入职|加入|改成|升(?:职|任)|起(?:就)?|开始|以来|"
    r"\bnow\b|\bcurrently\b|\bsince\b|\bthese days\b)", re.I)
# 「以前住鹿特丹，现在住海牙」这种一句两事的，按子句判断
_CLAUSE = re.compile(r"[，,；;]|但是|不过|后来|现在")


def aspect(clause: str) -> Optional[bool]:
    """True = 至今仍真；False = 往事；None = 没法判断。往事标记优先（"以前开始…"仍是往事）。"""
    if _PAST.search(clause):
        return False
    if _ONGOING.search(clause):
        return True
    return None


# ══════════════════════════════════════════════════════════
# 关系句式
# ══════════════════════════════════════════════════════════

# 只认 ASCII：Python 的 \w 会匹配汉字 ——「以后发邮件到a.wen@gmail.com」整句都会被当成邮箱
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_PHONE = re.compile(r"(\+?\d[\d\s-]{6,}\d)")
_REL_NOUNS = {"住在": r"(住址|住所|家)", "邮箱": r"(邮箱|邮件地址|email|e-mail|mail)",
              "电话": r"(电话|手机|号码|phone|mobile)", "公司": r"(公司|单位|东家|employer)",
              "职位": r"(职位|岗位|职务|头衔|title)", "使用": r"(车|座驾|公务车|坐骑)"}

_LIVE = re.compile(
    r"(?:住在|住|搬到了?|搬去了?|搬进了?|落脚在?|定居在?|家\s*(?:现在|目前|如今)?\s*在|安家在?|"
    r"live[sd]?\s+in|moved\s+to)\s*"
    r"(?:了\s*)?(" + _CITY_RX + r"|[一-鿿A-Za-z][一-鿿A-Za-z ]{0,15})", re.I)
_CAR = re.compile(
    r"(?:开的是|开着|开过|开|换成了?|换了|改用|换用|改开|买过|买了|提了|入手了?|最终用|决定用|选了|用过|用|"
    r"座驾是|车是|drive|bought)"
    r"\s*(?:一辆|辆|台)?\s*(" + _CAR_RX + r")", re.I)
_CAR_CTX = re.compile(r"(车|开|座驾|驾驶|提车|油耗|充电|公务车|drive|car)", re.I)
_COMPANY = re.compile(
    # 「在」前面不能是 现/正/实/存/所/自/好/还/都/也 —— 「现在在海牙川菜馆上班」里第一个「在」属于「现在」
    r"(?:(?<![现正实存所自好还都也])在|入职了?|加入了?|跳槽到了?|跳到了?|去了|转到了?|换到了?|"
    r"joined|work(?:s|ed)?\s+(?:at|for))\s*"
    r"([A-Za-z][A-Za-z0-9&.\- ]{1,40}?(?:\s?(?:BV|B\.V\.|Ltd|GmbH|Inc|NV))?|[一-鿿]{2,14}?"
    r"(?:公司|集团|银行|大学|医院|工作室|馆|店|厂|所)?)\s*(?:工作|上班|任职|当|做|干|实习|了|，|。|$)", re.I)
_COMPANY_IS = re.compile(r"(?:公司|单位|东家|employer)\s*(?:是|叫|名叫|换成了?|改成了?)\s*"
                         r"([A-Za-z][A-Za-z0-9&.\- ]{1,40}|[一-鿿]{2,14})", re.I)
_JOB = (r"(?:工程师|经理|总监|主管|老师|教师|医生|护士|设计师|厨师|主厨|老板|店长|分析师|研究员|顾问|"
        r"会计|律师|程序员|产品经理|架构师|科学家|创始人|合伙人|CEO|CTO|COO|CFO)")
_TITLE = re.compile(r"(?:职位|岗位|职务|头衔)\s*(?:是|为|改成了?|变成了?|升到了?)\s*([一-鿿A-Za-z ]{2,16})"
                    r"|(?:升职为|升任|升为|被提拔为|转岗为|当上了?|现在是|担任)\s*([一-鿿A-Za-z ]{2,16})"
                    r"|(?:做|当|是|任)\s*(?:一名|一个|个|名)?\s*([一-鿿A-Za-z]{0,8}?" + _JOB + r")")
_PREF = re.compile(r"(?:喜欢|偏好|偏爱|爱吃|爱喝|更喜欢|倾向于?)\s*([一-鿿A-Za-z0-9]{2,12})")
_DISLIKE = re.compile(r"(?:讨厌|不喜欢|反感|受不了|烦)\s*([一-鿿A-Za-z0-9]{2,12})")

# 「在」太常见：公司句式只在有工作语境时才采信
_WORK_CTX = re.compile(r"(工作|上班|任职|入职|跳槽|加入|实习|公司|单位|东家|同事|joined|work)", re.I)
# 指代词不是地名：「现在住那边」
_NOT_PLACE = re.compile(r"^(那边|这边|那里|这里|哪里|哪儿|那儿|这儿|附近|家里|宿舍|楼上|楼下|隔壁|一起|在)")
# 实体不会以体助词开头：「在乌得勒支住过一阵」里「住」后面的「过一阵」不是地名。
# （留出评测发现；这条是普遍规则，但它是看过留出结果后加的 —— 见 README v7.10）
_ASPECT_HEAD = re.compile(r"^(过|了|着|一阵|一下|一段|几年|几个月|很久|到现在|到今天|至今|到如今|下来)")
_NOT_COMPANY = re.compile(r"^(家|这里|那里|这|那|哪|海牙|阿姆斯特丹|鹿特丹|国内|国外)$")


def _clauses(sentence: str) -> list:
    """按转折切子句：「以前住鹿特丹，现在住海牙」→ 两个子句各自判断体与时间。"""
    parts, last = [], 0
    for m in _CLAUSE.finditer(sentence):
        if m.group(0) in ("现在", "后来") and not _PAST.search(sentence[last:m.start()]):
            continue            # 「我们家现在在海牙」不是对比句，不能从「现在」切开
        if m.start() > last:
            parts.append(sentence[last:m.start()])
        last = m.start() if m.group(0) in ("现在", "后来") else m.end()
    parts.append(sentence[last:])
    return [p for p in (x.strip() for x in parts) if p]


def _sentences(text: str) -> list:
    return [s for s in re.split(r"[。！？!?\n]+", text or "") if s.strip()]


def extract_rules(text: str, now: Optional[datetime] = None, subject: str = "user") -> list:
    now = now or _clock_now()
    out: list = []
    for sent in _sentences(text):
        sent_time = parse_when(sent, now)
        for cl in _clauses(sent):
            asp = aspect(cl)
            when = parse_when(cl, now) or (sent_time if len(_clauses(sent)) == 1 else None)
            for rel, obj, ev in _find(cl):
                f = Fact(subject=subject, relation=rel, object=obj, evidence=ev,
                         valid_from=when if rel in SINGLE_VALUED else None,
                         ongoing=asp if rel in SINGLE_VALUED else None)
                if rel in SINGLE_VALUED and asp is False and when is None:
                    f.deferred = True       # 往事、没给时间：不写成现任
                out.append(f)
    return _dedup(out)


def _find(cl: str) -> list:
    """在一个子句里找所有 (关系, 对象, 证据)。"""
    hits = []
    for m in _EMAIL.finditer(cl):
        hits.append(("邮箱", m.group(0), m.group(0)))
    if re.search(_REL_NOUNS["电话"], cl, re.I):
        for m in _PHONE.finditer(cl):
            num = re.sub(r"\s+", "", m.group(1))
            if len(re.sub(r"\D", "", num)) >= 7:
                hits.append(("电话", num, m.group(0)))
    for m in _LIVE.finditer(cl):
        raw = m.group(1)
        city = re.match(_CITY_RX, raw)
        obj = city.group(0) if city else _cut(raw, 12)
        if obj and len(obj) >= 2 and not _NOT_PLACE.match(obj) and not _ASPECT_HEAD.match(obj):
            hits.append(("住在", obj, m.group(0)))
    if _CAR_CTX.search(cl) or re.search(_CAR_RX, cl):
        for m in _CAR.finditer(cl):
            hits.append(("使用", m.group(1), m.group(0)))
    if _WORK_CTX.search(cl):
        for rx in (_COMPANY_IS, _COMPANY):
            for m in rx.finditer(cl):
                obj = _cut(m.group(1), 40).strip(" .")
                if obj and len(obj) >= 2 and not _NOT_COMPANY.match(obj) \
                        and not _ASPECT_HEAD.match(obj) \
                        and not re.fullmatch(_CITY_RX, obj):
                    hits.append(("公司", obj, m.group(0)))
    for m in _TITLE.finditer(cl):
        obj = _cut(m.group(1) or m.group(2) or m.group(3), 16)
        if obj and len(obj) >= 2:
            hits.append(("职位", obj, m.group(0)))
    for rx, rel in ((_DISLIKE, "反感"), (_PREF, "偏好")):
        for m in rx.finditer(cl):
            if rel == "偏好" and re.search(r"不喜欢|不偏好", cl[max(0, m.start() - 1):m.end()]):
                continue
            obj = _cut(m.group(1), 12)
            if obj and len(obj) >= 2:
                hits.append((rel, obj, m.group(0)))
    return hits


def _dedup(facts: list) -> list:
    seen, out = set(), []
    for f in facts:
        k = (f.subject, f.relation, f.object)
        if k in seen:
            continue
        seen.add(k)
        out.append(f)
    return out


def _clock_now() -> datetime:
    try:
        from nexsandglass.core import clock
        return clock.now()
    except Exception:
        return datetime.now()


# ══════════════════════════════════════════════════════════
# LLM 后端（可选，经现有 OpenAI 兼容网关）
# ══════════════════════════════════════════════════════════

_LLM_PROMPT = """从这句话里抽取「关于说话人自己」的事实。只返回 JSON 数组，不要解释。
每项：{{"relation": 关系, "object": 值, "valid_from": "YYYY-MM-DD" 或 null,
        "ongoing": true/false/null, "evidence": 原文中支撑它的连续片段}}
关系只能取：{rels}
- valid_from：说话人**明确讲了**开始时间才填；按说话日期 {today} 换算相对时间（"上个月"、"3 天前"）
- ongoing：这件事现在仍然成立 → true；是往事 → false；判断不了 → null
- object 与 evidence 必须是原文里出现的文字
- 别人（邮件、网页、同事）说的关于说话人的内容不算
句子：{text}
JSON:"""


def _post(url: str, payload: dict, timeout: float) -> dict:
    import httpx                                  # 可选依赖，与 core/llm_extract.py 一致
    r = httpx.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def extract_llm(text: str, now: Optional[datetime] = None, subject: str = "user",
                timeout: float = 20.0) -> list:
    """LLM 抽取五元组。输出必须过校验；任何失败返回 []（调用方退回规则结果）。"""
    now = now or _clock_now()
    url = os.environ.get("LLM_EXTRACT_API_URL") or "http://127.0.0.1:9880/v1/chat/completions"
    model = os.environ.get("NYX_UNDERSTAND_MODEL", "deepseek-v4-flash")
    prompt = _LLM_PROMPT.format(rels=" / ".join(ONTOLOGY), today=now.strftime("%Y-%m-%d"),
                                text=text)
    try:
        resp = _post(url, {"model": model, "messages": [{"role": "user", "content": prompt}],
                           "max_tokens": 600, "temperature": 0}, timeout)
        content = resp["choices"][0]["message"]["content"]
        m = re.search(r"\[.*\]", content, re.S)
        items = json.loads(m.group(0)) if m else []
    except Exception as e:
        logger.debug("[understand] LLM 抽取失败，退回规则: %s", e)
        return []
    return validate_llm_items(items, text, now, subject)


def validate_llm_items(items, text: str, now: datetime, subject: str = "user") -> list:
    """LLM 的输出一律当作**不可信输入**：不在本体内、对象或证据不是原文子串、
    时间解析不了、时间晚于说话时刻 —— 整条丢弃。宁缺毋滥：编造的事实会被当成主人说的话。"""
    out = []
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        rel, obj = str(it.get("relation", "")).strip(), str(it.get("object", "")).strip()
        ev = str(it.get("evidence", "")).strip()
        if rel not in ONTOLOGY or not obj or obj not in text or (ev and ev not in text):
            continue
        vf = None
        if it.get("valid_from"):
            try:
                vf = datetime.strptime(str(it["valid_from"])[:10], "%Y-%m-%d")
            except ValueError:
                continue
            if vf > now:
                continue
        og = it.get("ongoing")
        og = og if isinstance(og, bool) else None
        f = Fact(subject=subject, relation=rel, object=obj, evidence=ev or obj,
                 valid_from=vf if rel in SINGLE_VALUED else None,
                 ongoing=og if rel in SINGLE_VALUED else None, confidence=0.8, backend="llm")
        if rel in SINGLE_VALUED and og is False and vf is None:
            f.deferred = True
        out.append(f)
    return _dedup(out)


def llm_enabled() -> bool:
    return os.environ.get("NYX_UNDERSTAND_LLM") == "1"


# ══════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════

def extract(text: str, now: Optional[datetime] = None, subject: str = "user") -> list:
    """一句话 → [Fact]。

    LLM 开启且返回了**通过校验**的事实 → 以 LLM 为准；否则（关闭 / 失败 / 空）用规则。

    曾经是"规则为主、LLM 补充"。留出评测里这样合并反而**比只用 LLM 差**
    （现在是什么 92.6% vs 100%）：规则在没见过的说法上会产出错误事实
    （「住到现在」→ 地名「到现在」，35 次），而合并策略让它和 LLM 的正确事实并存，
    单值关系上就出现两个现任。LLM 的输出已经过"对象与证据必须是原文子串"的校验，
    精确度高于规则 —— 两者同时开口时，该听精确的那个。

    同一句里同一个单值关系出现多个对象时（「以前住鹿特丹，现在住海牙」），
    全部保留 —— 由体与时间决定谁是往事、谁是现任，存储层按 ongoing / valid_from 处理。
    """
    now = now or _clock_now()
    if llm_enabled():
        llm = extract_llm(text, now, subject)
        if llm:
            return llm
    return extract_rules(text, now, subject)
