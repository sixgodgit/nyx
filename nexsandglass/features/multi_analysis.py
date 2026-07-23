#!/usr/bin/env python3
"""
Multi-Analysis — 多模型会诊模块（由 Thalamus 调度）

通过平行调用多个专业模型（代码/推理/创意），
从不同角度分析同一问题，返回综合报告。

不走独立入口，必须通过 Thalamus 的 /analysis 端点调用，
保证路由统一、审计统一、流程统一。
"""

import json
import os
import re
import threading
import time
import urllib.request
from pathlib import Path

# ─── 配置 ───

LOG_PATH = Path(os.environ.get("THALAMUS_LOG", "/root/.hermes/logs/thalamus.log"))

# 会诊角色（3 个视角互补）
PERSONAS = [
    {
        "name": "code",
        "provider": "xiaomi",
        "model_hint": "mimo",
        "system": "你是一名资深软件架构师，擅长分析系统设计和代码质量问题。用中文回答。",
        "description": "架构/代码视角 → MiMo",
    },
    {
        "name": "reasoning",
        "provider": "openrouter",
        "model_hint": "openrouter",
        "system": "你是一名逻辑分析师，擅长多角度推理和因果链分析。用中文回答。",
        "description": "逻辑推理视角 → OpenRouter",
    },
    {
        "name": "creative",
        "provider": "deepseek",
        "model_hint": "deepseek",
        "system": "你是一名创意顾问，擅长提出新颖视角和非常规解决方案。用中文回答。",
        "description": "创意发散视角 → DeepSeek",
    },
]

CACHE = {}
CACHE_MAX = 50


def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def _resolve_routes(routes: list, persona: dict) -> dict:
    """根据 persona 的 provider hint，从 routes 里找到对应的模型配置"""
    hint = persona.get("model_hint", "").lower()

    # 先精确匹配 provider
    for _pat, _model, _provider, _endpoint, _key_env, _label in routes:
        prov = (_provider or "").lower()
        if hint and hint in prov:
            return {"endpoint": _endpoint, "model": _model, "key_env": _key_env, "label": _label}

    # fallback: 匹配 endpoint 域名
    for _pat, _model, _provider, _endpoint, _key_env, _label in routes:
        ep = (_endpoint or "").lower()
        if hint and hint in ep:
            return {"endpoint": _endpoint, "model": _model, "key_env": _key_env, "label": _label}

    return None


def _resolve_key(key_env: str) -> str:
    """解析密钥，优先 keys.json，其次环境变量，最后 .env"""
    if not key_env:
        return ""
    # keys.json
    keys_path = Path("/root/thalamus/keys.json")
    try:
        if keys_path.exists():
            raw = json.loads(keys_path.read_text())
            for alias, val in raw.items():
                if alias == key_env:
                    if isinstance(val, dict):
                        return val["key"]
                    return val
    except Exception:
        pass
    # 环境变量
    val = os.environ.get(key_env, "")
    if val:
        return val
    # .env
    try:
        env_path = Path("/root/.hermes/.env")
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith(key_env) and "=" in line:
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


def analyze(prompt: str, routes: list, max_tokens: int = 4096,
            temperature: float = 0.7, default_endpoint: str = "",
            default_model: str = "", default_key_env: str = "") -> dict:
    """
    多模型平行会诊。

    参数：
        prompt: 用户问题
        routes: 路由列表（从 Thalamus 传入）
        max_tokens: 每模型最大 token
        temperature: 温度
        default_*: 兜底模型配置

    返回：
        {
            "prompt": "...",
            "perspectives": {"code": "...", "reasoning": "...", "creative": "..."},
            "errors": {"code": "..."},
            "count": 3,
            "error_count": 0,
            "routing": [{"name": "code", "model": "...", "latency": 1.2}, ...],
        }
    """
    # 缓存
    cache_key = prompt[:100]
    if cache_key in CACHE:
        log(f"ANALYSIS CACHE HIT: {cache_key[:40]}...")
        return CACHE[cache_key]

    perspectives = {}
    errors = {}
    routing_info = []
    lock = threading.Lock()

    def _call(name: str, cfg: dict):
        t0 = time.time()
        try:
            route = _resolve_routes(routes, cfg)
            if not route:
                # 兜底默认
                route = {
                    "endpoint": default_endpoint or "https://api.deepseek.com/v1/chat/completions",
                    "model": default_model or "deepseek-v4-flash",
                    "key_env": default_key_env or "DEEPSEEK_API_KEY",
                    "label": "default",
                }

            key = _resolve_key(route["key_env"])
            if not key:
                raise RuntimeError(f"Key not found: {route['key_env']}")

            body = {
                "model": route["model"],
                "messages": [
                    {"role": "system", "content": cfg["system"]},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": False,
            }

            req = urllib.request.Request(
                route["endpoint"],
                data=json.dumps(body).encode(),
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "User-Agent": "Thalamus/5.0",
                },
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=120)
            data = json.loads(resp.read().decode())
            latency = time.time() - t0

            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            with lock:
                perspectives[name] = content
                routing_info.append({
                    "name": name,
                    "model": route["model"],
                    "label": route["label"],
                    "latency_s": round(latency, 2),
                })
            log(f"ANALYSIS OK ({name}): {route['label']} | {latency:.2f}s")

        except Exception as e:
            latency = time.time() - t0
            with lock:
                errors[name] = str(e)
                routing_info.append({
                    "name": name,
                    "model": cfg.get("model_hint", "?"),
                    "label": f"ERROR: {str(e)[:50]}",
                    "latency_s": round(latency, 2),
                })
            log(f"ANALYSIS ERROR ({name}): {e}")

    # 并行执行
    threads = []
    for persona in PERSONAS:
        t = threading.Thread(target=_call, args=(persona["name"], persona), daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=120)

    result = {
        "prompt": prompt[:300],
        "perspectives": perspectives,
        "errors": errors,
        "count": len(perspectives),
        "error_count": len(errors),
        "routing": routing_info,
        "personas": [{"name": p["name"], "description": p["description"]} for p in PERSONAS],
    }

    # 缓存
    if len(CACHE) < CACHE_MAX:
        CACHE[cache_key] = result

    return result


def analyze_cli():
    """CLI 入口：python3 multi_analysis.py '你的问题'"""
    import sys
    if len(sys.argv) < 2:
        print("用法: python3 multi_analysis.py '你的问题'")
        return
    prompt = sys.argv[1]
    print(f"🔍 多模型会诊: {prompt[:60]}...")
    print()
    result = analyze(prompt, [], max_tokens=2048)
    for name, content in result.get("perspectives", {}).items():
        print(f"━━━ [{name}] ━━━")
        print(content[:500])
        print()
    if result.get("errors"):
        print(f"⚠️  失败: {result['errors']}")


if __name__ == "__main__":
    analyze_cli()
