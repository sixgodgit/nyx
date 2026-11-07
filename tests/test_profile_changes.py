"""常驻画像跟着改口（v7.22.0）：同一件事，新的说法顶替旧的；旧的列在「Changed since」"""
from datetime import datetime

from tests.test_hermes_provider import env, _reindex, _provider  # noqa: F401  复用隔离目录

from nexsandglass.core import profile


def _say(p, when, text):
    from nexsandglass.core import clock
    clock.set_global(datetime.strptime(when, "%Y-%m-%d"))
    try:
        p.sync_turn(text, "好的。", session_id="s")
    finally:
        clock.set_global(None)


def _block(p):
    b = p.system_prompt_block()
    b = b[: b.find("Nyx memory:")]
    current, _, changed = b.partition("Changed since")
    return current, changed


def test_newer_request_replaces_only_what_it_changes(env):  # noqa: F811
    p = _provider()
    _say(p, "2026-03-01", "以后回复我都用中文，不要用 emoji。")
    _say(p, "2026-03-05", "以后回答简短一点。")
    _say(p, "2026-09-01", "以后回复我用英文吧，我在练英语。")
    current, changed = _block(p)
    assert "用英文" in current and "都用中文" not in current
    assert "…不要用 emoji" in current                         # 同一句里没改的那件事还在
    assert "简短一点" in current                              # 别的话题不受影响
    assert "以后回复我都用中文 (changed 2026-09-01)" in changed


def test_diet_residence_and_likes_follow_changes(env):  # noqa: F811
    p = _provider()
    _say(p, "2026-03-01", "我是素食者，不吃肉。")
    _say(p, "2026-03-02", "我住在武汉，在一家银行上班。")
    _say(p, "2026-03-03", "我特别喜欢喝冰美式。")
    _say(p, "2026-03-04", "我对芒果过敏。")
    _say(p, "2026-09-01", "我现在不吃素了，开始吃肉了。")
    _say(p, "2026-09-02", "我上个月搬到深圳了。")
    _say(p, "2026-09-03", "最近不怎么喝冰美式了，改喝热拿铁。")
    current, changed = _block(p)
    assert "开始吃肉" in current and "我是素食者" not in current
    assert "搬到深圳" in current and "住在武汉" not in current
    assert "…在一家银行上班" in current                       # 只搬了家，工作那半句还算数
    assert "改喝热拿铁" in current and "喜欢喝冰美式" not in current
    assert "芒果过敏" in current
    assert changed.count("\n- [") == 3                       # 最多列三条，带改口的日期
    assert "(changed 2026-09-03)" in changed


def test_english_changes(env):  # noqa: F811
    p = _provider()
    _say(p, "2026-03-01", "Please always reply in English, and keep your answers short.")
    _say(p, "2026-03-02", "I'm vegan.")
    _say(p, "2026-03-03", "Call me Sam.")
    _say(p, "2026-03-04", "I love horror movies.")
    _say(p, "2026-09-01", "From now on answer in Spanish.")
    _say(p, "2026-09-02", "I'm not vegan anymore, I eat fish now.")
    _say(p, "2026-09-03", "Please call me Samantha from now on.")
    _say(p, "2026-09-04", "I don't really enjoy horror movies anymore, I prefer comedies.")
    current, _ = _block(p)
    assert "in Spanish" in current and "reply in English" not in current
    assert "keep your answers short" in current
    assert "not vegan anymore" in current and "] I'm vegan" not in current
    assert "Samantha" in current and "Call me Sam." not in current
    assert "prefer comedies" in current and "I love horror movies" not in current


def test_what_is_not_replaced(env):  # noqa: F811
    p = _provider()
    _say(p, "2026-03-01", "我妈住在青岛。")
    _say(p, "2026-03-02", "我喜欢猫。")
    _say(p, "2026-03-03", "我对花生过敏。")
    _say(p, "2026-09-01", "我妈搬到上海了。")                 # 家人的事不参与顶替
    _say(p, "2026-09-02", "我也喜欢狗。")                      # 不同的东西
    _say(p, "2026-09-03", "我对虾过敏。")                      # 不同的过敏原
    current, changed = _block(p)
    for s in ("我妈住在青岛", "我妈搬到上海", "喜欢猫", "喜欢狗", "花生过敏", "对虾过敏"):
        assert s in current, s
    assert not changed.strip()


def test_older_words_imported_later_do_not_replace_newer_ones(env):  # noqa: F811
    p = _provider()
    _say(p, "2026-09-01", "以后回复我用英文。")
    _say(p, "2026-03-01", "以后回复我都用中文。")             # 后写进来，但说得更早（导入的旧对话）
    current, changed = _block(p)
    assert "用英文" in current and "都用中文" not in current and "都用中文" in changed


def test_slots_are_per_clause():
    assert profile.slots("instruction", "以后回复我都用中文") == {"lang"}
    assert profile.slots("instruction", "不要用 emoji") == {"emoji"}
    assert profile.slots("identity", "换了家互联网公司") == {"job"}
    assert profile.slots("identity", "现在在上海上班") == set()        # 只说了在哪个城市上班：不算换工作
    assert profile.slots("likes", "I love horror movies") == profile.slots("likes", "I don't enjoy horror movie anymore")
    assert profile.slots("people", "我妈住在青岛") == set()
    assert profile.classify("我现在不吃素了，开始吃肉了。")[0] == "health"
    assert profile.classify("I've started eating meat again.")[0] == "health"
    assert profile.classify("最近不怎么喝冰美式了，改喝热拿铁。")[0] == "likes"
    assert profile.classify("这段代码我不太看得懂了") is None             # 改口的新规则不收工作上的话
