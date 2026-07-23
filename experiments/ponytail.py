#!/usr/bin/env python3
"""
Ponytail — Pre-check 模块（从 Thalamus 拆分而来）

在 AI 编码 Agent 接活前判断：用户的需求能不能用现成方案解决？
如果可以，拦截并给出建议（省 token + 省时间）。
如果不行，返回 None，走正常路由。

用法：
  from ponytail import precheck
  
  result = precheck("如何用 Python 解析日期字符串？")
  if result:
      print(f"✅ {result['category'].upper()}: {result['suggestion']}")
  else:
      print("需要写新代码")
"""

import json
import os
import re
import time
import urllib.request
from pathlib import Path

# ─── 配置 ───

DEFAULT_ENDPOINT = "https://api.deepseek.com/v1/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_KEY_ENV = "DEEPSEEK_API_KEY"
DEFAULT_MAX_TOKENS = 512
DEFAULT_TEMPERATURE = 0.3

# ─── 密钥解析 ───

KEYS = {}

def _load_keys():
    global KEYS
    keys_path = Path("/root/thalamus/keys.json")
    try:
        if keys_path.exists():
            with open(keys_path) as f:
                raw = json.load(f)
            KEYS = {}
            for alias, val in raw.items():
                if isinstance(val, dict):
                    KEYS[alias] = val
                else:
                    KEYS[alias] = {"key": val, "endpoint": DEFAULT_ENDPOINT}
    except Exception:
        KEYS = {}

def _resolve_key(key_env: str) -> str:
    if not key_env:
        return ""
    if key_env in KEYS:
        return KEYS[key_env]["key"]
    # 检查环境变量
    val = os.environ.get(key_env, "")
    if val:
        return val
    # 尝试从 .env 文件加载
    try:
        env_path = Path("/root/.hermes/.env")
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith(key_env) and "=" in line:
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""

def _resolve_endpoint(key_env: str) -> str:
    if not key_env:
        return ""
    if key_env in KEYS:
        return KEYS[key_env].get("endpoint", "")
    return ""

# ─── System Prompt ───

PRECHECK_SYSTEM_PROMPT = """你是 Preflight Checker，负责在 AI 编码 Agent 接活前拦住不必要的代码生成。

收到用户需求后，按以下顺序判断：

1. 标准库（stdlib）能搞定且不需要写自定义逻辑吗？ → Python/Node.js/Go 等语言自带的功能
2. 框架/平台内置了吗且不需要额外配置？ → React/Vue/Django 等框架自带功能
3. 有成熟的第三方包吗且不需要写包装代码？ → npm/pip 现成包

核心原则：只有用户的需求能通过**一行 import/require + 已有函数调用**解决时才拦截。
如果用户的需求需要**写完整的业务逻辑、多步处理、状态管理、定时任务、并发控制、自定义算法**等，不要拦截！

⚠️ 注意区分：
- "解析日期" → YES|stdlib （datetime.strptime 一行搞定）
- "写一个自动更新的token轮换服务" → NO （需要定时逻辑+状态管理+异常处理，不是一行 import 能搞定的）
- "用 Python 发 HTTP 请求" → YES|stdlib （urllib.request 一行）
- "写一个完整的 REST API 服务" → NO （需要路由/日志/错误处理等多步）

如果以上任一成立，回答格式：
  YES|{stdlib|framework|package}:{方案描述}
  例如：YES|stdlib: Python datetime.strptime 可以直接解析日期字符串

如果不成立，回答：
  NO|需求涉及自定义逻辑，需要写新代码

不要写代码！不要推荐要写代码的方案！只判断有没有现成的。"""


def _search_sandglass(text: str, limit: int = 3) -> str:
    """查询沙漏，看看用户以前有没有做过类似需求"""
    try:
        import sys as _sys
        import os as _os
        _os.environ.setdefault("NEXSANDBASE_HOME", "/root/.hermes/nexsandglass")
        for _p in ["/root/nexsandglass", "/root/.hermes/NexSandglass"]:
            if _p not in _sys.path:
                _sys.path.insert(0, _p)
        from sandglass_sqlite import search as _sg_search
        
        _stopwords = {"用", "在", "的", "了", "吗", "吧", "呢", "啊", "什么", "怎么", "如何",
                      "哪个", "哪些", "可以", "能", "要", "是", "有", "给", "把", "被",
                      "from", "to", "in", "on", "at", "for", "of", "the", "a", "an",
                      "and", "or", "do", "is", "it", "with", "很", "太", "不", "没", "还",
                      "说", "话", "对", "那", "这", "我", "你", "他", "她"}
        
        _tech = [w.lower() for w in re.findall(r"[a-zA-Z0-9_]{2,}", text)
                 if w.lower() not in _stopwords]
        if _tech:
            _kw = " OR ".join(_tech[:3])
            _results = _sg_search(_kw, limit=limit)
        else:
            _words = [w for w in text.split() if len(w) > 1]
            _kw = " ".join(_words[:2]) if _words else ""
            _results = _sg_search(_kw, limit=limit) if _kw else []
        
        if _results:
            _lines = []
            for _rid, _ts, _rtext in _results[:limit]:
                _short = _rtext.replace("\n", " ").strip()[:100]
                _lines.append(f"  [{_ts}] {_short}")
            if _lines:
                return "\n📖 **之前类似需求的记录：**\n" + "\n".join(_lines)
    except Exception:
        pass
    return ""


def precheck(text: str, endpoint: str = None, model: str = None,
             key_env: str = None, max_tokens: int = None,
             temperature: float = None, sandglass_enabled: bool = True) -> dict | None:
    """
    执行 pre-check 判定。
    
    参数：
        text: 用户消息文本
        endpoint: API 端点（默认 DeepSeek）
        model: 模型名（默认 deepseek-v4-flash）
        key_env: 密钥环境变量名（默认 DEEPSEEK_API_KEY）
        max_tokens: 最大 token 数（默认 512）
        temperature: 温度（默认 0.3）
        sandglass_enabled: 是否查询沙漏历史
        
    返回：
        {
            "intercepted": True,
            "category": "stdlib|framework|package",
            "suggestion": "具体建议",
            "sandglass_hint": "沙漏历史记录（如有）",
            "latency": 0.5,
        }
        或 None（未拦截、配置失败）
    """
    if not text or not text.strip():
        return None
    
    ep = endpoint or DEFAULT_ENDPOINT
    md = model or DEFAULT_MODEL
    ke = key_env or DEFAULT_KEY_ENV
    mt = max_tokens or DEFAULT_MAX_TOKENS
    tp = temperature or DEFAULT_TEMPERATURE
    
    # 加载 keys（如果还没加载）
    _load_keys()
    
    # 解析密钥
    key = _resolve_key(ke)
    if not key:
        return None
    
    # 构建预检请求
    body = {
        "model": md,
        "messages": [
            {"role": "system", "content": PRECHECK_SYSTEM_PROMPT},
            {"role": "user", "content": text.strip()},
        ],
        "max_tokens": mt,
        "temperature": tp,
        "stream": False,
    }
    
    t0 = time.time()
    try:
        req = urllib.request.Request(
            ep,
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "User-Agent": "Ponytail/1.0",
            },
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read().decode())
        latency = time.time() - t0
        
        choice = data.get("choices", [{}])[0]
        reply = choice.get("message", {}).get("content", "").strip()
        
        # 解析结果
        if reply.startswith("YES|"):
            parts = reply.split("|", 2)
            if len(parts) >= 3:
                category = parts[1]
                suggestion = parts[2]
            elif len(parts) == 2 and ":" in parts[1]:
                sub = parts[1].split(":", 1)
                category = sub[0].strip()
                suggestion = sub[1].strip()
            else:
                category = "stdlib"
                suggestion = parts[1] if len(parts) > 1 else reply
            
            # 可选：查询沙漏
            sandglass_hint = ""
            if sandglass_enabled:
                sandglass_hint = _search_sandglass(text)
            
            return {
                "intercepted": True,
                "category": category,
                "suggestion": suggestion,
                "sandglass_hint": sandglass_hint,
                "latency": round(latency, 2),
            }
        
        return {"intercepted": False}
    
    except Exception:
        return None


def precheck_cli():
    """CLI 入口：python3 ponytail.py "你的问题" """
    import sys
    if len(sys.argv) < 2:
        print("用法: python3 ponytail.py '你的问题'")
        return
    text = sys.argv[1]
    result = precheck(text)
    if result and result.get("intercepted"):
        print(f"✅ {result['category'].upper()}: {result['suggestion']}")
        if result.get("sandglass_hint"):
            print(result["sandglass_hint"])
        print(f"(延迟: {result['latency']}s)")
    else:
        print("❌ 需要写新代码或无法判断")


if __name__ == "__main__":
    precheck_cli()
