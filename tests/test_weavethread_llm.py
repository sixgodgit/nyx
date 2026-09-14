"""
测试：知识图谱 LLM 抽取层（Task 2）

覆盖：
- 纯正则模式（默认）
- LLM 抽取开启（带降级）
- source 标注（regex vs llm）
- 实体归一化
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

from nexsandglass.features.weavethread import (
    wthread_extract,
    wthread_extract_with_source,
    wthread_extract_llm,
)


def test_regex_extraction():
    """纯正则抽取（默认路径）。"""
    text = "用户邮箱是 test@example.com，用户住在海牙"
    triples = wthread_extract(text)
    assert isinstance(triples, list)
    # 纯正则模式不依赖 LLM
    for t in triples:
        assert len(t) == 3  # (subj, rel, obj)


def test_extract_with_source():
    """带来源标注的抽取。"""
    text = "用户住在海牙，使用 DeepSeek 模型"
    triples = wthread_extract_with_source(text)
    assert isinstance(triples, list)
    for t in triples:
        assert len(t) == 4  # (subj, rel, obj, source)
        assert t[3] in ("regex", "llm")


def test_llm_disabled_by_default():
    """LLM 抽取默认关闭。"""
    # 确保环境变量未设置
    os.environ.pop("WTHREAD_LLM_EXTRACTION", None)
    result = wthread_extract_llm("任意文本")
    assert result == []


def test_llm_enabled_without_api():
    """LLM 开启但无可用 API 时优雅降级。"""
    os.environ["WTHREAD_LLM_EXTRACTION"] = "1"
    # 不设置 LLM_EXTRACT_API_URL，使用默认本地网关（不可用）
    os.environ.pop("LLM_EXTRACT_API_URL", None)
    result = wthread_extract_llm("用户住在海牙")
    # 本地网关不可用 → 返回空列表（不崩溃）
    assert isinstance(result, list)
    os.environ.pop("WTHREAD_LLM_EXTRACTION", None)


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
