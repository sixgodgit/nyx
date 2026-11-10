"""待跟进（v7.23.0）：用户说过的、快到了的和刚过去还不知道结果的事，新会话开始时放进常驻块"""
import json
from datetime import datetime

import pytest

from tests.test_hermes_provider import env, _reindex, _provider  # noqa: F401  复用隔离目录

from nexsandglass.core import clock
from nexsandglass.core import followups as F


def _at(when: str) -> datetime:
    return datetime.strptime(when, "%Y-%m-%d %H:%M")


def _say(p, when, text, reply="好的。"):
    clock.set_global(_at(when))
    try:
        p.sync_turn(text, reply, session_id="s")
    finally:
        clock.set_global(None)


def _block(p, when):
    """新会话在 when 开始时，常驻块里待跟进那一段（没有就是空串）。"""
    clock.set_global(_at(when))
    try:
        b = p.system_prompt_block()
    finally:
        clock.set_global(None)
    i = b.find(F.HEADER)
    return b[i:].split("\n\n", 1)[0] if i >= 0 else ""


@pytest.fixture()
def switch_file(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "_path", lambda: str(tmp_path / "follow_ups.json"))


# ══════════════════════════════════════════════════════════
# 日子怎么换算
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("text,said,expect", [
    ("明天要考驾照科目一", "2026-09-13", ("2026-09-14", "2026-09-14")),
    ("后天要参加一个婚礼", "2026-09-15", ("2026-09-17", "2026-09-17")),
    ("大后天体检", "2026-10-01", ("2026-10-04", "2026-10-04")),
    ("下周三我有个腾讯的二面", "2026-10-02", ("2026-10-07", "2026-10-07")),        # 周五说的下周三
    ("下周三有个面试", "2026-10-04", ("2026-10-07", "2026-10-07")),                 # 周日说的下周三 = 三天后
    ("这周六要带我妈去医院复查", "2026-10-08", ("2026-10-10", "2026-10-10")),
    ("周日约了朋友去看演唱会", "2026-10-01", ("2026-10-04", "2026-10-04")),          # 只说星期几：下一个
    ("下下周一入职", "2026-10-01", ("2026-10-12", "2026-10-12")),
    ("10月8号要参加公司的晋升答辩", "2026-09-30", ("2026-10-08", "2026-10-08")),
    ("1月5号回国", "2026-12-20", ("2027-01-05", "2027-01-05")),                     # 年底说一月 = 明年
    ("13号去拔智齿", "2026-10-09", ("2026-10-13", "2026-10-13")),
    ("3号去拔智齿", "2026-10-09", ("2026-11-03", "2026-11-03")),                    # 这个月的 3 号过了
    ("下个月20号去日本旅行", "2026-10-04", ("2026-11-20", "2026-11-20")),
    ("下个月去日本旅行", "2026-10-04", ("2026-11-01", "2026-11-30")),
    ("月底要交稿", "2026-10-06", ("2026-10-25", "2026-10-31")),
    ("下周要出差", "2026-10-01", ("2026-10-05", "2026-10-11")),
    ("周末有个聚会", "2026-10-01", ("2026-10-03", "2026-10-04")),
    ("过两天去体检", "2026-10-01", ("2026-10-03", "2026-10-04")),
    ("三天后面试", "2026-10-01", ("2026-10-04", "2026-10-04")),
    ("面试推迟到下周五了", "2026-10-06", ("2026-10-16", "2026-10-16")),             # 改期：取改到的那天
    ("I have my driving test tomorrow morning", "2026-09-13", ("2026-09-14", "2026-09-14")),
    ("My final exam is the day after tomorrow", "2026-09-16", ("2026-09-18", "2026-09-18")),
    ("I have a job interview next Wednesday", "2026-10-02", ("2026-10-07", "2026-10-07")),
    ("My son has a swim meet this Friday", "2026-10-05", ("2026-10-09", "2026-10-09")),
    ("I have a dentist appointment on Monday", "2026-10-01", ("2026-10-05", "2026-10-05")),
    ("I'm defending my thesis on October 8th", "2026-09-30", ("2026-10-08", "2026-10-08")),
    ("Our wedding is on 3rd of November", "2026-10-01", ("2026-11-03", "2026-11-03")),
    ("Getting my wisdom teeth out on the 13th", "2026-10-09", ("2026-10-13", "2026-10-13")),
    ("Heading to a trade show in two days", "2026-10-10", ("2026-10-12", "2026-10-12")),
    ("My paper is due at the end of the month", "2026-10-06", ("2026-10-25", "2026-10-31")),
    ("We're moving to Austin next month", "2026-10-06", ("2026-11-01", "2026-11-30")),
    ("I have a concert this weekend", "2026-10-01", ("2026-10-03", "2026-10-04")),
    ("The interview was rescheduled to next Friday", "2026-10-06", ("2026-10-16", "2026-10-16")),
    ("昨天的面试改到下周五了", "2026-10-06", ("2026-10-16", "2026-10-16")),
    ("I have a marathon on April 17, 2027.", "2027-02-05", ("2027-04-17", "2027-04-17")),     # 说了年份
    ("2027年1月5号回国", "2026-12-20", ("2027-01-05", "2027-01-05")),
])
def test_dates(text, said, expect):
    got = F.extract(text, datetime.strptime(said, "%Y-%m-%d").replace(hour=20))
    assert [(a.isoformat(), b.isoformat()) for _, a, b, _ in got] == [expect]


@pytest.mark.parametrize("text", [
    "如果下周有面试的话，穿什么比较好？",                           # 假设
    "If I get an interview next week, what should I wear?",
    "明天没有考试，可以睡懒觉",                                     # 否定
    "I don't have an interview tomorrow",
    "我同事明天面试，我帮他看了简历",                               # 别人的事
    "My colleague has an interview tomorrow",
    "周三面试挂了",                                                # 已经过去（只说了星期几，带「了」）
    "I had my interview on Monday",
    "昨天面试了",
    "今天面试过了，感觉还行",                                       # 今天 + 结果
    "明天的面试取消了",                                            # 取消：不是新的事
    "这周三面试",                                                  # 这周三已经过了
    "明天要给领导讲周报，帮我理个思路",                             # 不是要跟进的那类事
    "I need to present the weekly report to my boss tomorrow",
    "明天跟他见一面",                                              # 「一面」不是面试
    "帮我写个 third-party API 的调用，明天要用",
    # 以下来自对 LongMemEval / LCCC 的抽查（v7.23.0）：已经发生的、每周都有的、说的是对方的、一句祝福
    "I won my fourth video game tournament on Friday!",
    "I took Ethan to his first pediatrician appointment and he's doing great today!",
    "We just returned home from our road trip to Yellowstone today, and I'm still recovering.",
    "I'll take advantage of Black Friday sales to get a head start on my holiday shopping.",
    "I sat next to my friend Emily at the Sunday morning mass.",
    "Set a reminder for my conference call on Tuesdays and Thursdays.",
    "Good luck on your exam tomorrow!",
    "今天陪女朋友去考试了",
    "今天刚答辩了，就剩下一些手续了",
    "祝你明天下午的考试过关",
    "20岁生日快乐，过两天我也20了",
    "你也是4.9号生日？",
    "希望明天能收到面试邀请",
    "听说明天你去相亲，我该说点啥",
    "还在医院呆着，周一刚做完手术",
    "我今天不去医院",
    "I think I'll try to book a morning flight on a Saturday, since it's usually less busy.",
    "I'm so excited to hear that you're heading to Koenji this weekend!",
    "I just earned 10,000 miles after taking a round-trip flight from Boston to Atlanta today, by the way.",
    "I ordered it on the 15th of April for my best friend's birthday.",
    "I have some frozen veggies from my Costco trip last weekend that I can use this week.",
    "That was the graduation celebration at the office on June 15th, 2025.",
    "I've been to three concerts this year, including the Katy Perry concert today.",
    "I managed a personal best in the drag race at the Turbocharged Tuesday event.",
    "I just got back from my solo trip to Portland, Oregon today and I'm already thinking about planning my next trip.",
])
def test_not_follow_ups(text):
    assert F.extract(text, datetime(2026, 10, 9, 20)) == []


def test_the_nearest_day_belongs_to_the_event():
    got = F.extract("Remind me about my breakfast meeting on Wednesday and my doctor's appointment next week.",
                    datetime(2026, 10, 7, 9))
    assert [(a.isoformat(), b.isoformat()) for _, a, b, _ in got] == [("2026-10-12", "2026-10-18")]


# ══════════════════════════════════════════════════════════
# 常驻块里列什么
# ══════════════════════════════════════════════════════════

def test_what_is_listed(env):  # noqa: F811
    p = _provider()
    _say(p, "2026-09-13 20:00", "明天要考驾照科目一，有点慌。")            # 早就过去了
    _say(p, "2026-10-01 20:00", "下周四要去上海出差三天。")
    _say(p, "2026-10-02 20:00", "下周三我有个腾讯的二面，有点紧张。")
    _say(p, "2026-10-04 20:00", "下个月20号去日本旅行。")                  # 还早
    _say(p, "2026-10-06 20:00", "上海出差取消了，改成线上会。")             # 取消
    _say(p, "2026-10-07 20:00", "下周二要去医院体检。")                    # 明天
    _say(p, "2026-10-08 20:00", "这周六要带我妈去医院复查。")
    _say(p, "2026-10-11 20:00", "我妈复查结果出来了，一切正常。")           # 说了结果
    sec = _block(p, "2026-10-12 09:00")
    assert "二面" in sec and "体检" in sec
    for gone in ("科目一", "上海出差", "日本", "复查"):
        assert gone not in sec, gone
    assert "Coming up on Tue 2026-10-13: [2026-10-07] 下周二要去医院体检" in sec
    assert "Was on Wed 2026-10-07; nothing said about it since: [2026-10-02]" in sec
    assert sec.index("体检") < sec.index("二面")                           # 离今天近的在前
    assert F.GUIDANCE in sec


def test_once_asked_or_answered_it_is_dropped(env):  # noqa: F811
    p = _provider()
    _say(p, "2026-10-02 20:00", "下周三我有个面试，有点紧张。")
    _say(p, "2026-10-07 08:00", "面试的衣服准备好了。")                     # 当天早上：还没有结果
    assert "有点紧张" in _block(p, "2026-10-08 09:00")
    _say(p, "2026-10-08 09:05", "早", reply="早！对了，昨天的面试怎么样了？")   # 助手问过了
    assert _block(p, "2026-10-09 09:00") == ""

    _say(p, "2026-10-09 20:00", "周五要去体检。")
    assert "体检" in _block(p, "2026-10-12 09:00")
    _say(p, "2026-10-12 20:00", "体检报告出来了，都正常。")                  # 用户说了结果
    assert _block(p, "2026-10-13 09:00") == ""


def test_too_old_and_too_early(env):  # noqa: F811
    p = _provider()
    _say(p, "2026-10-02 20:00", "下周三我有个面试。")                       # 10-07
    assert _block(p, "2026-10-04 09:00") == ""                             # 还有三天：还早
    assert "Coming up" in _block(p, "2026-10-05 09:00")                    # 后天
    assert "Was on" in _block(p, "2026-10-17 09:00")                       # 十天之内
    assert _block(p, "2026-10-18 09:00") == ""                             # 过了十天：不翻旧账


def test_rescheduled(env):  # noqa: F811
    p = _provider()
    _say(p, "2026-10-02 20:00", "下周三我有个面试。")
    _say(p, "2026-10-06 20:00", "面试推迟到下周五了。")                     # 10-16
    assert _block(p, "2026-10-08 09:00") == ""                             # 10-07 那场改期了；10-16 还早
    assert "Coming up on Fri 2026-10-16: [2026-10-06] 面试推迟到下周五了" in _block(p, "2026-10-15 09:00")


def test_other_news_of_a_related_kind(env):  # noqa: F811
    """「在诊所的面试」也沾「看病」的边：之后「我爸体检结果不错」不能算这场面试的结果。"""
    p = _provider()
    _say(p, "2026-10-02 20:00", "I have a job interview at a clinic next Wednesday, pretty nervous.")
    _say(p, "2026-10-01 20:00", "I'm flying to Seattle next Thursday for a conference.")
    _say(p, "2026-10-06 20:00", "The Seattle trip got cancelled, they moved the conference online.")
    _say(p, "2026-10-08 20:00", "Taking my dad to his checkup this Saturday.")
    _say(p, "2026-10-11 20:00", "Dad's checkup went fine.")
    sec = _block(p, "2026-10-12 09:00")
    assert "job interview" in sec
    assert "Seattle" not in sec and "checkup" not in sec


def test_only_the_users_own_words(env):  # noqa: F811
    p = _provider()
    _say(p, "2026-10-08 20:00", "帮我看看这个接口", reply="好的。另外提醒一下，你明天下午有个面试。")
    assert _block(p, "2026-10-09 09:00") == ""


def test_at_most_three(env):  # noqa: F811
    p = _provider()
    for day, text in (("01", "下周一要去体检。"), ("02", "下周二有个面试。"), ("03", "下周三要考科目二。"),
                      ("04", "下周四有个答辩。")):
        _say(p, f"2026-10-{day} 20:00", text)
    sec = _block(p, "2026-10-09 09:00")
    assert sec.count("\n- ") == 3 and "答辩" in sec and "体检" not in sec     # 最近的三件


def test_forgotten_words_take_the_follow_up_with_them(env):  # noqa: F811
    from nexsandglass.core import lookup
    from nexsandglass.runtime import facade
    p = _provider()
    _say(p, "2026-10-02 20:00", "下周三我有个面试。")
    mid = lookup.containing("下周三我有个面试")[0]["mem_id"]
    json.loads(p.handle_tool_call("nyx_forget", {"mem_id": mid, "confirm": True}))
    assert _block(p, "2026-10-08 09:00") == ""                 # 扫描时它在隔离区里：水位线越过了它
    assert facade.restore(mid)["ok"]
    assert "下周三我有个面试" in _block(p, "2026-10-08 09:00")   # 救回来时补收


def test_switches(env, switch_file, monkeypatch):  # noqa: F811
    from nexsandglass_hermes_commands import handle
    p = _provider()
    _say(p, "2026-10-02 20:00", "下周三我有个面试。")
    clock.set_global(_at("2026-10-08 09:00"))
    try:
        listed = handle("follow-ups")
        assert "on, 1 due" in listed and "下周三我有个面试" in listed
        assert "follow-ups: on, 1 due" in handle("status")
        assert "off" in handle("follow-ups off")
        assert "already off" in handle("followups off")
    finally:
        clock.set_global(None)
    assert _block(p, "2026-10-08 09:00") == ""
    assert "on" in handle("follow-ups on")
    assert "下周三我有个面试" in _block(p, "2026-10-08 09:00")
    monkeypatch.setenv("NYX_FOLLOW_UPS", "off")                 # nyx.json 的 follow_ups: off 同理
    assert _block(p, "2026-10-08 09:00") == ""
    assert "still keeps them off" in handle("follow-ups on")
    assert "Unknown" in handle("follow-ups maybe")
