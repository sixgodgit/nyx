"""
engram dream_pipeline 测试 — hypnos × engram 融合

覆盖：
  1. Mnemosyne 浅睡总结（规则提取事实）
  2. Epimetheus 深睡内化（重分类/合并/关系发现）
  3. Prometheus 灵感联结（emotional → 联想）
  4. 完整管线 run_dream_pipeline
  5. LLM 回调注入（prompt 层可替换）
"""

import sys
import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
for _candidate in (
    _THIS_DIR,
    os.path.dirname(_THIS_DIR),
    os.path.dirname(os.path.dirname(_THIS_DIR)),
):
    if os.path.isdir(os.path.join(_candidate, "nexsandglass")):
        sys.path.insert(0, _candidate)
        break
if os.path.isdir(os.path.join(_THIS_DIR, "engram")):
    sys.path.insert(0, _THIS_DIR)

try:
    from engram.types import Memory, MemoryType
    from engram.dream_pipeline import (
        mnemosyne_summarize,
        epimetheus_internalize,
        prometheus_inspire,
        run_dream_pipeline,
        MnemosyneSummary,
    )
except ModuleNotFoundError:
    from nexsandglass.engram.types import Memory, MemoryType
    from nexsandglass.engram.dream_pipeline import (
        mnemosyne_summarize,
        epimetheus_internalize,
        prometheus_inspire,
        run_dream_pipeline,
        MnemosyneSummary,
    )


def _mem(mid, mtype, content, arousal=0.0, weight=1.0, created="2026-07-01T00:00:00+00:00"):
    return Memory(
        memory_id=mid,
        type=mtype,
        content=content,
        arousal=arousal,
        decay_weight=weight,
        created_at=created,
        last_accessed=created,
        access_count=0,
    )


def test_mnemosyne_extracts_facts():
    day_log = (
        "用户说：我住在海牙。\n"
        "用户说：我的邮箱是 enfys@hvh.expert。\n"
        "用户问：Odido 什么时候来装光纤？\n"
    )
    summary = mnemosyne_summarize(day_log)
    assert len(summary.new_facts) >= 1, "应提取到事实陈述"
    assert any("海牙" in f for f in summary.new_facts)


def test_mnemosyne_with_llm_callback():
    def fake_llm(day_log):
        return MnemosyneSummary(
            completed=["部署 nyx"],
            pending=["推送 github"],
            new_facts=["用户住在海牙"],
            to_confirm=["用户可能换邮箱"],
        )

    summary = mnemosyne_summarize("任意日志", llm_extract=fake_llm)
    assert summary.completed == ["部署 nyx"]
    assert summary.new_facts == ["用户住在海牙"]
    assert summary.to_confirm == ["用户可能换邮箱"]


def test_epimetheus_internalize():
    mems = [
        _mem("a1", "episodic", "2026-07-01 用户去了海牙市政厅"),
        _mem("a2", "episodic", "2026-07-08 用户去了海牙市政厅"),
        _mem("a3", "episodic", "2026-07-15 用户去了海牙市政厅"),
        _mem("s1", "semantic", "用户住在海牙"),
    ]
    report = epimetheus_internalize(mems, min_occurrence=3)
    assert len(report.reclassified) == 1, "高频 episodic 应重分类"
    assert len(report.relations_found) >= 1, "应发现图谱关系"


def test_prometheus_inspire_code_mode():
    mems = [
        _mem("m1", "emotional", "用户对 Apple 账户安全很紧张", arousal=0.9),
        _mem("m2", "emotional", "用户买新车很开心", arousal=0.3),
    ]
    inspirations = prometheus_inspire(mems)
    assert len(inspirations) >= 1, "应产生灵感联结"
    assert "Apple" in inspirations[0]


def test_prometheus_with_llm_callback():
    def fake_llm(mems):
        return ["灵感：海牙市政厅与 Odido 光纤都涉及文件签署"]

    mems = [_mem("m1", "emotional", "紧张", arousal=0.9)]
    inspirations = prometheus_inspire(mems, llm_connect=fake_llm)
    assert inspirations == ["灵感：海牙市政厅与 Odido 光纤都涉及文件签署"]


def test_run_dream_pipeline_full():
    mems = [
        _mem("a1", "episodic", "2026-07-01 用户去了海牙市政厅"),
        _mem("a2", "episodic", "2026-07-08 用户去了海牙市政厅"),
        _mem("a3", "episodic", "2026-07-15 用户去了海牙市政厅"),
        _mem("m1", "emotional", "用户对 Apple 账户安全很紧张", arousal=0.9),
        _mem("s1", "semantic", "用户住在海牙", weight=0.5),
    ]
    stored = []
    day_log = "用户说：我住在海牙。"
    evolved, report = run_dream_pipeline(
        mems,
        day_log=day_log,
        thread_store=lambda s, r, o: stored.append((s, r, o)),
    )
    assert report.summary["new_facts"] >= 1, "Mnemosyne 应提取事实"
    assert report.summary["reclassified"] == 1, "Epimetheus 应重分类"
    assert report.summary["inspirations"] >= 1, "Prometheus 应产生灵感"
    assert len(stored) >= 1, "关系应写入图谱"
    assert len(evolved) == len(mems)


def test_run_dream_pipeline_llm_mode():
    def fake_extract(day_log):
        return MnemosyneSummary(new_facts=["用户计划买车"], completed=["看车"])

    def fake_connect(mems):
        return ["灵感：Geely 与 ECAR 同属汽车生态"]

    mems = [_mem("s1", "semantic", "用户看了 Geely Starray")]
    evolved, report = run_dream_pipeline(
        mems,
        day_log="看车记录",
        llm_extract=fake_extract,
        llm_connect=fake_connect,
    )
    assert report.mnemosyne.new_facts == ["用户计划买车"]
    assert report.inspiration == ["灵感：Geely 与 ECAR 同属汽车生态"]


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in tests:
        try:
            fn()
            print(f"✅ {fn.__name__}")
            passed += 1
        except Exception:
            print(f"❌ {fn.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
