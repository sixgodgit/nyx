"""
NexSandglass MemoryProvider — MemoryProvider for Hermes
========================================================
让 Hermes 使用 NexSandglass 作为记忆后端，替代 Holographic。

零API Key、零外部依赖——纯本地驱动。投石问路（倒排索引）优先、
五维权重排序、偏移率感知、回音折情绪追踪、影子灵魂预测。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

# 条件导入——兼容赫姆斯环境和独立运行时
try:
    from agent.memory_provider import MemoryProvider
except ImportError:
    class MemoryProvider:
        name = "nexsandglass"
        def is_available(self): return True
        def initialize(self): pass
        def shutdown(self): pass
        def get_tool_schemas(self): return []
        def handle_tool_call(self, name, args): return ""
        def system_prompt_block(self): return ""
        def prefetch(self, query): return None
        def sync_turn(self, user_msg, assistant_msg): pass

try:
    from tools.registry import tool_error
except ImportError:
    def tool_error(msg): return json.dumps({"error": msg})

logger = logging.getLogger(__name__)

# 偏移方向中文标签（system_prompt_block / prefetch 共用）
_OFFSET_LABELS = {"frugal": "省钱", "spend": "愿投", "drift": "放弃"}

# ══════════════════════════════════════════════════════════
# 工具方法——把 sandglass 函数暴露给 Hermes 模型调用
# ══════════════════════════════════════════════════════════

_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "sandglass_search",
            "description": "搜索沙漏记忆——投石问路（倒排索引）优先，五维权重排序。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sandglass_recent",
            "description": "获取最近 N 条记忆。",
            "parameters": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer", "default": 10},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sandglass_offset",
            "description": "计算当前偏移率——主人决策方向的趋势。返回偏移百分比和方向。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fact_store",
            "description": "影子沙事实存储。action=add/search/probe/reason。存储结构化事实，信任评分排序。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "search", "probe", "reason"]},
                    "content": {"type": "string", "description": "事实内容"},
                    "category": {"type": "string", "default": "general"},
                    "query": {"type": "string"},
                    "entity": {"type": "string"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fact_feedback",
            "description": "信任评分反馈。标记记忆是否有帮助。",
            "parameters": {
                "type": "object",
                "properties": {
                    "line_num": {"type": "integer"},
                    "helpful": {"type": "boolean"},
                },
                "required": ["line_num", "helpful"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sandglass_echo",
            "description": "读取回音折——最近的情感风向。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


class NexSandglassProvider(MemoryProvider):
    """NexSandglass 记忆提供器——替代 Holographic，纯本地零依赖。"""

    def __init__(self, config: dict = None):
        self._config = config or {}
        self._lock = threading.Lock()
        self._initialized = False
        self._turn_count = 0

    # ═══════ MemoryProvider 核心接口 ═══════

    @property
    def name(self) -> str:
        return "nexsandglass"

    def is_available(self) -> bool:
        """始终可用——零API Key，纯本地。"""
        return True

    def initialize(self, session_id: str = "", **kwargs) -> None:
        """设置沙漏路径、重建投石问路索引。"""
        with self._lock:
            if self._initialized:
                return
            # 确保 sandglass 模块可导入
            import sys
            nb = os.environ.get("NEXSANDBASE_HOME") or os.path.expanduser("~/.neurobase")
            nb_scripts = os.path.join(nb, "scripts")
            if nb_scripts not in sys.path:
                sys.path.insert(0, nb_scripts)

            from nexsandglass.features.sandglass_vault import rebuild_index
            from nexsandglass.core.sandglass_paths import validate
            validate()
            rebuild_index()
            # Cognitive Memory OS feature flag：NYX_USE_FACADE=1 时走稳定门面（默认关）
            self._use_facade = os.environ.get("NYX_USE_FACADE", "0") == "1"
            self._initialized = True
            if self._use_facade:
                logger.info("NexSandglass MemoryProvider initialized (facade mode: ON)")
            else:
                logger.info("NexSandglass MemoryProvider initialized")

    def system_prompt_block(self) -> str:
        """V2.9.8: 四层问答式注入 — 你是谁→往哪走→怎么变成这样→还没做完"""
        try:
            from nexsandglass.features.sandglass_vault import count
            from nexsandglass.features.sandglass_think import comprehensive_offset, _current_stage
            from nexsandglass.features.sandglass_think import _emotional_entropy, search_filter

            total = count()
            off = comprehensive_offset()
            stage = _current_stage()
            ent = _emotional_entropy()
            mood = "平稳" if ent < 0.5 else ("波动" if ent < 1.0 else "高熵")

            # 偏移方向
            off_label = _OFFSET_LABELS.get(off.get('direction', ''), '平稳')
            off_pct = off.get('offset', 0)

            blocks = []

            # ═══════ 第一层：你是谁 ═══════
            persona_text = ""
            scene_text = ""
            try:
                sf = search_filter("")
                if sf.get("persona_context"):
                    raw = sf["persona_context"][:250]
                    # 保留完整结构（含日期/沙子来源→精准定位），截到段落边界
                    cut = raw.rfind("\n\n")
                    if cut > 80:
                        raw = raw[:cut]
                    persona_text = raw.strip()
                if sf.get("scene_context"):
                    raw_scene = sf["scene_context"]
                    if "：" in raw_scene:
                        raw_scene = raw_scene.split("：", 1)[1]
                    scene_text = raw_scene
            except Exception:
                logger.debug("search_filter 失败", exc_info=True)

            # 场景如果在画像中已出现，不重复
            if scene_text and persona_text and any(s in persona_text for s in scene_text.split("、")):
                scene_text = ""

            # fallback: scene_current
            if not scene_text:
                try:
                    from nexsandglass.l3.scene_l3 import scene_current
                    scenes = scene_current()
                    if scenes:
                        scene_text = f"当前场景：{'、'.join(scenes[:3])}"
                except Exception:
                    pass

            if persona_text or scene_text:
                layer1 = ["【你是谁】"]
                if persona_text:
                    layer1.append(persona_text)
                if scene_text:
                    layer1.append(f"📍 {scene_text}")
                blocks.append("\n".join(layer1))

            # ═══════ 第二层：你在往哪走 ═══════
            layer2 = ["【你在往哪走】"]
            if off_label != "平稳":
                layer2.append(f"💰 {off_label}倾向({off_pct:+d}%)")
            else:
                layer2.append(f"💰 决策平稳")

            # 最近决策
            decisions = []
            try:
                from nexsandglass.core.sandglass_paths import _NB
                dlog = os.path.join(_NB, "persona", "decision-log.jsonl")
                if os.path.exists(dlog):
                    with open(dlog, "r", encoding="utf-8") as f:
                        all_lines = f.readlines()
                    recent = [json.loads(l) for l in all_lines[-10:]]
                    recent = [d for d in recent if d.get("decision")]
                    seen_d, unique_d = set(), []
                    for d in reversed(recent):
                        if d["decision"] not in seen_d:
                            seen_d.add(d["decision"])
                            unique_d.append(d)
                        if len(unique_d) >= 2:
                            break
                    unique_d.reverse()
                    decisions = [d['decision'][:60] for d in unique_d]
                    # 子串去重：短的被长的包含→去掉短的
                    if len(decisions) == 2 and decisions[0] in decisions[1]:
                        decisions = [decisions[1]]
                    elif len(decisions) == 2 and decisions[1] in decisions[0]:
                        decisions = [decisions[0]]
            except Exception:
                pass
            if decisions:
                layer2.append(f"📋 最近：{'；'.join(decisions)}")

            # 矛盾检测
            try:
                from nexsandglass.l3.weave_l3 import weave_contradiction
                contra = weave_contradiction()
                if contra.get("conflicts"):
                    c0 = contra["conflicts"][0]
                    if c0.get("conflict"):
                        layer2.append(f"⚠️ {c0['conflict'][:100]}")
            except Exception:
                logger.debug("矛盾检测失败", exc_info=True)

            if mood != "平稳":
                layer2.append(f"🎭 情绪：{mood}")

            blocks.append("\n".join(layer2))

            # ═══════ 第三层：你怎么变成这样 ═══════
            try:
                from nexsandglass.features.weavethread import wthread_stats, wthread_weave
                stats = wthread_stats()
                if stats["total_triples"] >= 20:
                    thread = wthread_weave(limit=3)
                    if thread and thread != "织线因果:":
                        blocks.append(f"【你怎么变成这样】\n{thread[:200]}")
            except Exception:
                logger.debug("织线失败", exc_info=True)

            # ═══════ 第四层：还没做完 ═══════
            layer4 = []

            # 待办
            tasks = []
            try:
                from nexsandglass.l3.l3_tasks import task_pending
                tp = task_pending()
                if tp:
                    tasks = [t['task'][:80] for t in tp[:3]]
            except Exception:
                pass

            # 纪律
            rules = []
            try:
                from nexsandglass.utils.discipline import iron_rules_with_counts
                raw_rules = iron_rules_with_counts(3)
                if raw_rules:
                    if any(c > 0 for _, c in raw_rules):
                        rules = [f"{r} ×{c}" for r, c in raw_rules]
                    else:
                        rules = [r for r, _ in raw_rules]
            except Exception:
                pass

            if tasks or rules:
                header = "【还没做完】"
                if tasks:
                    layer4.append(header)
                    layer4.append("待办：")
                    layer4.extend(f"  {i+1}. {t}" for i, t in enumerate(tasks))
                if rules:
                    if not tasks:
                        layer4.append(header)
                    layer4.append("纪律：")
                    layer4.extend(f"  {i+1}. {r}" for i, r in enumerate(rules))
                blocks.append("\n".join(layer4))

            # ═══════ 合并两套出口：四层块 → MemoryBundle 槽位 ═══════
            # layer1(你是谁)/layer2(往哪走)/layer4(还没做完) 作为 Bundle 槽位
            persona_layer = "\n".join(layer1) if layer1 else ""
            offset_layer = "\n".join(layer2) if layer2 else ""
            open_loops_layer = "\n".join(layer4) if layer4 else ""

            # 经 orchestrator 构建 MemoryBundle（记忆核心 + Provider 槽位合一）
            try:
                from nexsandglass.runtime.orchestrator import get_orchestrator
                orch = get_orchestrator()
                bundle = orch.recall_bundle(
                    "",
                    max_tokens=1500,
                    persona_layer=persona_layer,
                    offset_layer=offset_layer,
                    open_loops=open_loops,
                )
                body = bundle.render()
            except Exception:
                # 降级：仅 Provider 层块（保可用性）
                body = "\n\n".join(blocks).strip()

            tail = f"沙漏: {total}条 | 阶段: {stage}"
            return (body + "\n\n" + tail).strip()
        except Exception:
            logger.warning("system_prompt_block 整体失败", exc_info=True)
            return "NexSandglass记忆系统已就绪。使用sandglass_search搜索记忆。"

    def prefetch(self, query: str) -> str:
        """每轮对话前注入织布机摘要——偏移+情绪+场景。主注入已有全貌，这里只给最动态的信号。"""
        try:
            from nexsandglass.features.sandglass_think import comprehensive_offset, _current_stage, _emotional_entropy
            off = comprehensive_offset()
            ent = _emotional_entropy()
            mood = "平稳" if ent < 0.5 else ("波动" if ent < 1.0 else "高熵")
            off_d = _OFFSET_LABELS.get(off.get('direction', ''), '平稳')
            return (
                f"## 当前\n"
                f"偏移: {off_d}({off.get('offset',0):+d}%) | 情绪: {mood}\n"
            )
        except Exception:
            return ""

    def queue_prefetch(self, query: str) -> None:
        """后台预热——搜索足够快，不需要预热。保持接口兼容。"""
        pass

    def sync_turn(self, user_msg: str, assistant_msg: str, **kwargs) -> None:
        """每轮对话后：raw 落沙（审计日志）+ 候选晋升（只写值得长期记住的）。

        替代默认整轮 dump：
          - 每条消息先落沙 sandglass（保留作审计日志）
          - 用 PromotionEngine 判断是否晋升长期（Promote/SessionOnly/Drop）
          - 只对 promote 的候选经 orchestrator 写长期 engram/fact
        """
        try:
            from nexsandglass.runtime.orchestrator import get_orchestrator
            from nexsandglass.runtime.promotion import PromotionEngine
            orch = get_orchestrator()
            eng = PromotionEngine(use_llm=False, timeout=2.0)

            for msg in (user_msg, assistant_msg):
                if not msg:
                    continue
                # 1. raw 落沙（审计日志，始终保留）
                try:
                    from nexsandglass.core.sandglass_log import log_message
                    log_message(msg, "user" if msg == user_msg else "agent")
                except Exception:
                    pass
                # 2. 候选晋升判断
                try:
                    cand = eng.observe(msg)
                    if cand.disposition == "promote":
                        orch.observe(msg, source="user" if msg == user_msg else "assistant")
                except Exception:
                    pass
            self._turn_count += 1
        except Exception:
            # 降级：orchestrator 不可用时回退到直连落沙（保持可用性）
            try:
                from nexsandglass.core.sandglass_log import log_message
                if user_msg:
                    log_message(user_msg, "user")
                if assistant_msg:
                    log_message(assistant_msg, "agent")
                self._turn_count += 1
            except Exception:
                pass

    def shutdown(self) -> None:
        """清理。"""
        logger.info("NexSandglass MemoryProvider shutdown")

    # ═══════ fact_store / fact_feedback ═══════

    def _handle_fact_store(self, args: dict) -> str:
        try:
            from nexsandglass.features.shadow_sand import shadow_search as _ss
            action = args.get("action", "search")

            if action == "add":
                from nexsandglass.runtime.orchestrator import get_orchestrator
                orch = get_orchestrator()
                fr = orch.observe(args.get("content", ""), source="fact_store")
                return json.dumps({"status": "added" if fr.ok else "failed", "id": fr.memory_id})

            if action == "search":
                from nexsandglass.runtime.orchestrator import get_orchestrator
                orch = get_orchestrator()
                rr = orch.recall(args.get("query", ""), token_budget=2000)
                return json.dumps({
                    "results": [{"text": t[:200]} for t in rr.strings[:10]],
                }, ensure_ascii=False)

            if action == "probe":
                entity = args.get("entity", "")
                results = _ss(entity, limit=20)
                return json.dumps([{"line": ln, "trust": score} for score, ln in results], ensure_ascii=False)

            if action == "reason":
                entity = args.get("entity", "")
                results = _ss(entity, limit=5)
                if results:
                    ln = results[0][1]
                    from nexsandglass.features.sandglass_vault import search as vs
                    r = vs(str(ln), limit=1)
                    if r:
                        return json.dumps({"line": ln, "text": r[0][2][:300]}, ensure_ascii=False)
                return json.dumps({"status": "no results"})

            return tool_error(f"Unknown fact_store action: {action}")
        except Exception as e:
            return tool_error(f"fact_store error: {e}")

    def _handle_fact_feedback(self, args: dict) -> str:
        try:
            from nexsandglass.features.shadow_sand import shadow_feedback
            result = shadow_feedback(args["line_num"], args.get("helpful", True))
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return tool_error(f"fact_feedback error: {e}")

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """会话结束——蒸馏 + 偏移检查。"""
        try:
            # 落最后一轮对话
            for msg in messages[-5:]:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if content:
                    from nexsandglass.core.sandglass_log import log_message
                    log_message(str(content)[:500], role)

            # 触发偏移检查 + 织造
            from nexsandglass.features.sandglass_think import comprehensive_offset
            off = comprehensive_offset()
            if abs(off.get("offset", 0)) >= 30:
                logger.info(f"会话结束偏移: {off['offset']:+d}% ({off['direction']})")

        except Exception:
            pass

    # ═══════ 工具暴露 ═══════

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return _TOOL_SCHEMAS

    def handle_tool_call(self, name: str, args: Dict[str, Any]) -> str:
        try:
            if name == "sandglass_search":
                from nexsandglass.runtime.orchestrator import get_orchestrator
                orch = get_orchestrator()
                rr = orch.recall(args.get("query", ""), token_budget=args.get("limit", 10) * 200)
                return json.dumps(
                    [{"text": t[:200], "id": rr.memory_ids[i] if i < len(rr.memory_ids) else t} for i, t in enumerate(rr.strings[: args.get("limit", 10)])],
                    ensure_ascii=False,
                )

            if name == "sandglass_recent":
                from nexsandglass.features.sandglass_vault import recent
                results = recent(args.get("n", 10))
                return json.dumps(
                    [{"line": ln, "ts": ts, "text": txt[:200]} for ln, ts, txt in results],
                    ensure_ascii=False,
                )

            if name == "sandglass_offset":
                from nexsandglass.features.sandglass_think import comprehensive_offset
                off = comprehensive_offset()
                return json.dumps(off, ensure_ascii=False)

            if name == "sandglass_echo":
                from nexsandglass.features.sandglass_think import _sentiment_wind
                wind = _sentiment_wind()
                return json.dumps({"wind": wind, "direction": "正面" if wind > 0 else ("负面" if wind < 0 else "中性")}, ensure_ascii=False)

            if name == "fact_store":
                return self._handle_fact_store(args)

            if name == "fact_feedback":
                return self._handle_fact_feedback(args)

            return tool_error(f"Unknown NexSandglass tool: {name}")

        except Exception as e:
            return tool_error(f"NexSandglass error: {e}")

    # ═══════ 可选钩子 ═══════

    def on_memory_write(self, action: str, target: str, content: str, metadata: dict = None) -> None:
        """镜像内置记忆写入——同步落沙。"""
        try:
            from nexsandglass.core.sandglass_log import log_message
            text = f"[{action}] {target}: {content[:200]}"
            log_message(text, "memory_write")
        except Exception:
            pass

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> Optional[str]:
        """上下文压缩前提取关键记忆。经 orchestrator 统一召回。"""
        try:
            from nexsandglass.runtime.orchestrator import get_orchestrator
            orch = get_orchestrator()
            if messages:
                last = messages[-1].get("content", "")[:100]
                if last:
                    rr = orch.recall(last, token_budget=600)
                    return "\n".join(t[:200] for t in rr.strings[:3])
        except Exception:
            pass
        return None


# ── 插件自动发现入口 ──
def register(ctx) -> None:
    """Hermes 插件加载入口——接收 config 上下文并注册 Provider。"""
    provider = NexSandglassProvider()
    ctx.register_memory_provider(provider)
