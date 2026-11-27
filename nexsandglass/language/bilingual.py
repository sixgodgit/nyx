"""Chinese/English profile rules. Original clauses remain evidence, not keys."""
from __future__ import annotations
import re
from typing import Optional
from .ontology import entity_key

KINDS = ("instruction", "health", "people", "identity", "likes")
LINE_CHARS = 160
_INSTRUCTION_MAX_LEN = 300

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
    from .segmentation import sentences
    return sentences(text, policy="profile")


# 句子说的是家人（开头就是「我妈 / 我老婆 / My mother …」）
_ABOUT_RELATIVE = re.compile(r"^\s*(我(的)?(女儿|儿子|孩子|闺女|宝宝|老婆|老公|妻子|丈夫|爱人|媳妇|对象|男朋友|女朋友|妈妈|母亲|爸爸|父亲|"
                             r"父母|爸妈|妈|爸|爷爷|奶奶|外公|外婆|姥姥|姥爷|哥哥|姐姐|弟弟|妹妹)|my (wife|husband|partner|"
                             r"girlfriend|boyfriend|son|daughter|kids?|child|baby|mom|mum|mother|dad|father|parents|"
                             r"brother|sister|grandma|grandmother|grandpa|grandfather)\b)", re.I)

# 对着助手说的评价（「I like the way you…」「你这个方案我喜欢」）是对这次回答的反馈，不是这个人的喜好
_TO_ASSISTANT = re.compile(r"\byou(r|'ve|'re|'d|'ll)?\b|你(的|这|写|说|改|给)", re.I)


def classify(text: str) -> Optional[tuple]:
    """(类别, 摘句) 或 None。按 instruction > health > people > identity > likes 的先后，取第一句命中的。"""
    from . import observation
    context = observation.current()
    if context and context.input_language.split("-", 1)[0] not in ("zh", "en", "mixed", "unknown"):
        return None
    text = str(text or "").strip()
    if not text or len(text) > _MAX_LEN:
        return None
    sents = _sentences(text)
    for kind in KINDS:
        if kind == "instruction" and len(text) > _INSTRUCTION_MAX_LEN:
            continue
        for i, s in enumerate(sents):
            from .assertions import assertion_state
            state = assertion_state(s)
            if state == "question" or (state in ("conditional", "uncertain", "ambiguous_negation", "historical") and kind != "health"):
                continue
            if kind in ("identity", "instruction", "likes"):
                from . import observation
                from .time import scan_times
                ctx = observation.current()
                if ctx:
                    times = scan_times(s, ctx)
                    if any(t.status != "resolved" or (t.start and t.start > ctx.local.date()) for t in times):
                        continue
            if kind == "likes" and _TO_ASSISTANT.search(s):
                continue
            if any(p.search(s) for p in _COMPILED[kind]):
                if kind == "health" and _ABOUT_RELATIVE.search(s):
                    kind = "people"          # 「我妈有糖尿病」是家人的事，放在「健康」下面像是用户自己的
                return kind, _excerpt(text, sents, i)
    return None


def _excerpt(text: str, sents: list, i: int) -> str:
    """Keep a complete matched sentence; final host budgets admit it atomically."""
    flat = " ".join(text.split())
    if len(flat) <= LINE_CHARS:
        return flat
    out = sents[i]
    if len(out) < 60 and i + 1 < len(sents):
        out = out + " " + sents[i + 1]
    out = " ".join(out.split())
    return out


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
_LIKE_ZH = re.compile(r"(?:最爱|爱吃|爱喝|爱看|爱玩|爱听|热爱|喜欢|讨厌|受不了|偏好|迷上|爱上|爱|改|(?:不再|不怎么)(?:吃|喝|看|玩|听|用))"
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
            out.add("like:" + entity_key(o, "preference"))
    for m in _LIKE_EN.finditer(clause):
        o = _EN_DET.sub("", m.group("o").strip().lower())
        o = " ".join(w for w in o.split() if w not in ("really", "actually", "just"))
        if o and o not in ("it", "that", "this", "them", "you"):
            key = entity_key(o, "preference")
            if key.startswith("literal:"):
                key = entity_key(" ".join(_en_stem(w) for w in o.split()), "preference")
            out.add("like:" + key)
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
        allergens = [entity_key((m.group(1) or m.group(2) or "").strip(), "allergen")
                     for m in _ALLERGY.finditer(c)]
        if allergens:
            # Generic coffee/diet topics do not authorize replacing an allergy.
            found = {"allergy:" + key for key in allergens}
    if kind == "likes":
        found |= _like_objects(c)
    return found


def _clauses(text: str) -> list:
    return [c.strip() for c in _CLAUSE.split(text or "") if c and c.strip()]


def comparison_slots(kind: str, clauses: list[str]) -> list[set]:
    """A topic match alone cannot erase a qualified medical statement.

    Only an unqualified self statement is eligible for the existing recency
    policy. Preserve compound/uncertain allergy statements in full until the
    richer polarity/condition policy in LANG-03 can interpret them.
    """
    from .assertions import assertion_state
    keys = [slots(kind, clause) if assertion_state(clause) == "affirmed" else set() for clause in clauses]
    if kind == "health" and any(k.startswith("allergy:") for row in keys for k in row):
        simple = len(clauses) == 1 and bool(re.fullmatch(
            r"(?:我)?(?:不再|不)?对[^，。！？\s]{1,8}过敏(?:了)?[。.!！]?"
            r"|i(?: am|'m) (?:not |no longer )?allergic to [a-z -]+[.!]?", clauses[0], re.I))
        if not simple:
            return [set() for _ in clauses]
    return keys


