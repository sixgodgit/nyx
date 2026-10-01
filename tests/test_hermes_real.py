"""对真实 Hermes Agent 源码的集成检查（有 checkout 才跑）。

    git clone --depth 1 https://github.com/NousResearch/hermes-agent
    HERMES_AGENT_SRC=$PWD/hermes-agent python3 -m pytest tests/test_hermes_real.py
（Hermes 的依赖需要装在同一个解释器里；只跑记忆这条路径时实测只缺 ruamel.yaml）
"""
import os
import subprocess
import sys

import pytest

SRC = os.environ.get("HERMES_AGENT_SRC")


@pytest.mark.skipif(not SRC, reason="设置 HERMES_AGENT_SRC 指向 hermes-agent checkout 后运行")
def test_against_real_hermes():
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "integration", "hermes_real_check.py")
    r = subprocess.run([sys.executable, script], capture_output=True, text=True, timeout=600,
                       env=dict(os.environ, HERMES_AGENT_SRC=SRC))
    assert r.returncode == 0 and "HERMES REAL CHECK: PASS" in r.stdout, (r.stdout + r.stderr)[-3000:]
