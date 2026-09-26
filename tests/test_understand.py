"""tests/test_understand.py — 话语理解：一句话 → (谁, 关系, 值, 从何时起, 是否至今仍真)（v7.10）

钉住的东西：
  - v7.9 纵向评测里端到端 0%：正则抽不出住在/公司/邮箱，读不出时间与"体"
  - 开发过程中踩到的真缺陷：邮箱正则的 \\w 吞掉整句中文；「住那边」当地名；
    「现在在海牙川菜馆上班」对象带出一个「在」；「住过一阵」抽出「过一阵」
  - 不猜：往事没给时间 → deferred，不写成现任
  - LLM 输出一律当不可信输入：不在原文里的对象/证据、本体外关系、未来日期 —— 丢弃
"""

from datetime import datetime

import pytest

from nexsandglass.core import understand as U

NOW = datetime(2026, 3, 10, 10, 0, 0)


def _facts(text, now=NOW):
    return [(f.relation, f.object, f.valid_from.date() if f.valid_from else None,
             f.ongoing, f.deferred) for f in U.extract(text, now)]


# ══════════════════════════════════════════════════════════
# 1. 时间表达
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("text,want", [
    ("2026-02-25 搬的", "2026-02-25"),
    ("2026年2月25日搬的", "2026-02-25"),
    ("2月25号搬的", "2026-02-25"),
    ("11月3号搬的", "2025-11-03"),          # 3 月说「11 月」→ 去年 11 月
    ("三天前搬的", "2026-03-07"),
    ("两周前换的", "2026-02-24"),
    ("昨天换的", "2026-03-09"),
    ("上个月搬的", "2026-02-01"),
    ("去年3月搬的", "2025-03-01"),
    ("从 2023 年起", "2023-01-01"),
    ("since 2025-06-01", "2025-06-01"),
    ("3 weeks ago", "2026-02-17"),
])
def test_parse_when(text, want):
    assert U.parse_when(text, NOW).strftime("%Y-%m-%d") == want


def test_parse_when_none_without_time():
    assert U.parse_when("我现在住在海牙", NOW) is None


# ══════════════════════════════════════════════════════════
# 2. 体
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("clause,want", [
    ("我现在住在海牙", True), ("我2月25号就换成吉利了", True), ("从2023年起住在代尔夫特", True),
    ("很早以前我开过比亚迪", False), ("以前住在鹿特丹", False), ("那时候在川菜馆工作过", False),
    ("我住在海牙", None),
])
def test_aspect(clause, want):
    assert U.aspect(clause) is want


# ══════════════════════════════════════════════════════════
# 3. 关系抽取
# ══════════════════════════════════════════════════════════

def test_the_v79_blind_spots():
    """v7.9 端到端 0% 的那几类：住在 / 公司 / 邮箱 / 带时间的换车。"""
    assert ("住在", "阿姆斯特丹", U.parse_when("上个月", NOW).date(), True, False) in \
        _facts("我上个月搬到阿姆斯特丹了")
    assert ("公司", "Delft Robotics", None, True, False) in _facts("我入职了 Delft Robotics")
    assert ("邮箱", "wen@nexsand.nl", None, True, False) in _facts("我的邮箱换成 wen@nexsand.nl 了")
    f = _facts("我2月25号就换成吉利了，现在还开着")
    assert ("使用", "吉利", datetime(2026, 2, 25).date(), True, False) in f


def test_contrast_sentence_splits_into_history_and_current():
    f = _facts("我以前住在鹿特丹，现在住在海牙")
    assert ("住在", "鹿特丹", None, False, True) in f, "往事没给时间应标 deferred"
    assert ("住在", "海牙", None, True, False) in f


def test_entity_boundaries():
    assert _facts("改用吉利了，特斯拉太贵")[0][:2] == ("使用", "吉利"), "v7.7 记录的「吉利了」"
    assert ("公司", "海牙川菜馆") in [x[:2] for x in _facts("我现在在海牙川菜馆上班")], \
        "「现在在」的第一个「在」属于「现在」"
    assert ("住在", "阿姆斯特丹") in [x[:2] for x in _facts("我们家现在在阿姆斯特丹")]


def test_email_regex_does_not_swallow_chinese():
    """Python 的 \\w 匹配汉字 —— 旧写法把「以后发邮件到a.wen@gmail.com」整句当成邮箱。"""
    assert [x[:2] for x in _facts("以后发邮件到a.wen@gmail.com")] == [("邮箱", "a.wen@gmail.com")]


@pytest.mark.parametrize("text", [
    "我2025年4月8日搬到了鹿特丹，现在住那边",        # 「那边」不是地名
    "早年2024-04-03我在乌得勒支住过一阵",              # 「过一阵」不是地名
])
def test_no_garbage_places(text):
    places = [x[1] for x in _facts(text) if x[0] == "住在"]
    assert all(p not in ("那边", "过一阵") for p in places), places


def test_generic_usage_is_not_a_car_switch():
    """「用了 Python / Docker」不再进入单值关系「使用」。"""
    assert [x for x in _facts("用了 Python 写脚本，后来用了 Docker 部署") if x[0] == "使用"] == []


@pytest.mark.parametrize("text", ["我在家呢", "今天天气不错", "你住哪里？", "我们开会吧"])
def test_negatives(text):
    assert [x for x in _facts(text) if x[0] in U.SINGLE_VALUED] == []


def test_titles_and_preferences():
    assert ("职位", "机器人工程师") in [x[:2] for x in _facts("我入职了 Delft Robotics，做机器人工程师")]
    f = [x[:2] for x in _facts("我喜欢川菜，讨厌香菜")]
    assert ("偏好", "川菜") in f and ("反感", "香菜") in f


# ══════════════════════════════════════════════════════════
# 4. LLM 后端：输出一律当不可信输入
# ══════════════════════════════════════════════════════════

TEXT = "我2月25号就换成吉利了"


def test_llm_output_is_validated():
    items = [
        {"relation": "使用", "object": "吉利", "valid_from": "2026-02-25", "ongoing": True,
         "evidence": "2月25号就换成吉利"},
        {"relation": "使用", "object": "特斯拉", "valid_from": None, "ongoing": True,
         "evidence": "换成特斯拉"},                                   # 原文里没有
        {"relation": "银行账户", "object": "吉利", "evidence": "吉利"},  # 本体外
        {"relation": "使用", "object": "吉利", "valid_from": "2027-01-01", "evidence": "吉利"},  # 未来
        {"relation": "使用", "object": "吉利", "valid_from": "not a date", "evidence": "吉利"},
        "garbage",
    ]
    out = U.validate_llm_items(items, TEXT, NOW)
    assert [(f.relation, f.object, f.backend) for f in out] == [("使用", "吉利", "llm")]
    assert out[0].valid_from == datetime(2026, 2, 25) and out[0].ongoing is True


def test_llm_failure_falls_back_to_rules(monkeypatch):
    monkeypatch.setenv("NYX_UNDERSTAND_LLM", "1")

    def boom(*a, **k):
        raise ConnectionError("gateway down")

    monkeypatch.setattr(U, "_post", boom)
    f = U.extract(TEXT, NOW)
    assert [(x.relation, x.object, x.backend) for x in f] == [("使用", "吉利", "rules")]


def test_llm_supplements_rules(monkeypatch):
    """规则漏掉的说法（留出评测里的「供职于」）由 LLM 补上 —— 前提是过了校验。"""
    monkeypatch.setenv("NYX_UNDERSTAND_LLM", "1")
    text = "目前供职于Nexsand BV"
    reply = {"choices": [{"message": {"content":
        '[{"relation":"公司","object":"Nexsand BV","valid_from":null,"ongoing":true,'
        '"evidence":"供职于Nexsand BV"}]'}}]}
    monkeypatch.setattr(U, "_post", lambda *a, **k: reply)
    f = U.extract(text, NOW)
    assert [(x.relation, x.object, x.backend) for x in f] == [("公司", "Nexsand BV", "llm")]
    monkeypatch.delenv("NYX_UNDERSTAND_LLM")
    assert U.extract(text, NOW) == [], "关掉 LLM 时规则确实漏了这句（留出评测的已知盲区）"


# ══════════════════════════════════════════════════════════
# 5. 接入真实写入路径
# ══════════════════════════════════════════════════════════

@pytest.fixture()
def wt(monkeypatch, tmp_path):
    from nexsandglass.features import weavethread
    path = str(tmp_path / "shadow_sand.db")
    monkeypatch.setattr(weavethread, "_DB", path)
    monkeypatch.delenv("WTHREAD_LLM_EXTRACTION", raising=False)
    monkeypatch.delenv("NYX_UNDERSTAND_LLM", raising=False)
    return weavethread, path


def test_store_writes_stated_time_and_aspect(wt):
    from nexsandglass.core import clock
    from nexsandglass.engram.loops.temporal_fact import get_current
    weavethread, path = wt
    with clock.frozen("2026-03-01 10:00:00"):
        weavethread.wthread_store("我现在开的是特斯拉", 1)
    with clock.frozen("2026-03-10 10:00:00"):
        weavethread.wthread_store("我2月25号就换成吉利了，现在还开着", 2)
    cur = get_current(path, "user", "使用")
    assert [r["object"] for r in cur] == ["吉利"], "陈述起点早于现任的假设起点 —— ongoing 必须生效"
    assert cur[0]["valid_basis"] == "stated" and cur[0]["valid_from"].startswith("2026-02-25")
    assert [r["object"] for r in get_current(path, "user", "使用", known_at="2026-03-05")] == ["特斯拉"]


def test_store_does_not_write_undated_history_as_current(wt):
    from nexsandglass.engram.loops.temporal_fact import get_current
    weavethread, path = wt
    weavethread.wthread_store("我现在住在海牙", 1)
    weavethread.wthread_store("我以前住在鹿特丹", 2)
    assert [r["object"] for r in get_current(path, "user", "住在")] == ["海牙"]


def test_store_keeps_generic_usage_as_multi_valued(wt):
    """旧正则泛化的「用了 X」降为多值关系「用过」：信息保留，但不顶掉座驾。"""
    from nexsandglass.engram.loops.temporal_fact import get_current
    weavethread, path = wt
    weavethread.wthread_store("我现在开的是特斯拉", 1)
    weavethread.wthread_store("这周用了Docker部署服务", 2)
    assert [r["object"] for r in get_current(path, "user", "使用")] == ["特斯拉"]
    used = [r["object"] for r in get_current(path, "user", "用过")]
    assert any("Docker" in o for o in used), f"「用了 X」的信息丢了: {used}"


def test_extractor_crash_falls_back_to_legacy(wt, monkeypatch):
    """理解层出错时，旧正则的结果仍然写进去（不能整条丢掉）。"""
    from nexsandglass.engram.loops.temporal_fact import get_current
    weavethread, path = wt

    def boom(*a, **k):
        raise RuntimeError("regex bug")

    monkeypatch.setattr(U, "extract", boom)
    assert weavethread.wthread_store("我偏好 Python，这周用了Docker部署服务", 1) >= 1
    assert get_current(path, "user", "用过"), "退回正则后的结果丢了"


def test_llm_takes_precedence_over_rules(monkeypatch):
    """回归：「规则为主、LLM 补充」时，规则的错误事实会和 LLM 的正确事实并存。

    留出评测：「搬进了莱顿的新房子，住到现在」→ 规则抽出地名「到现在」、LLM 抽出「莱顿」，
    单值关系出现两个现任；合并后的成绩（92.6%）反而低于只用 LLM（100%）。
    """
    monkeypatch.setenv("NYX_UNDERSTAND_LLM", "1")
    text = "8天之前那天我们搬进了莱顿的新房子，住到现在"
    reply = {"choices": [{"message": {"content":
        '[{"relation":"住在","object":"莱顿","valid_from":"2026-03-02","ongoing":true,'
        '"evidence":"搬进了莱顿的新房子"}]'}}]}
    monkeypatch.setattr(U, "_post", lambda *a, **k: reply)
    f = U.extract(text, NOW)
    assert [(x.relation, x.object, x.backend) for x in f] == [("住在", "莱顿", "llm")]


def test_until_now_is_not_a_place():
    """「住到现在」的「到现在」不是地名（留出评测发现，35 次）。"""
    places = [x[1] for x in _facts("那天我们搬进了莱顿的新房子，住到现在") if x[0] == "住在"]
    assert "到现在" not in places


def test_replay_recording_matches_real_prompt_shape():
    """录音回放靠解析真实 prompt 取回 (日期, 原句)。prompt 改了而回放没跟上，
    评测会静默地全部"未录到"然后退回规则 —— 这里钉住 prompt 里的两个锚点。"""
    p = U._LLM_PROMPT.format(rels="x", today="2026-03-10", text="原句")
    assert "按说话日期 2026-03-10" in p and p.rstrip().endswith("句子：原句\nJSON:")
