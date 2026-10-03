"""
NexSandglass MemoryProvider — Hermes Agent 官方记忆提供器接口实现
==================================================================
让 Hermes 使用 Nyx（NexSandglass）作为外置记忆，与 Honcho / Mem0 / Hindsight 走同一套
``agent.memory_provider.MemoryProvider`` 契约（v7.12 起按官方签名实现）。

零 API Key、零外部依赖——纯本地驱动。Nyx 独有、同类 provider 没有的：
  - 双时态事实：「现在是什么」与「第 K 天时我以为是什么」分开回答（nyx_belief）
  - 写入时绑定来源与信任：多人会话里非主人说的话不会被当成主人的话（owner_ids）
  - 两段式遗忘：agent 只能把记忆送进隔离区，保留期内可还原；永久擦除只留给主人（CLI）

生命周期（MemoryManager 驱动）：
  initialize → system_prompt_block（静态画像）→ 每轮 prefetch（按本轮问题召回）
  → sync_turn（MemoryManager 已放在串行后台线程里调用）→ 工具调用 → shutdown
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# 条件导入——兼容赫姆斯环境和独立运行时
try:
    from agent.memory_provider import MemoryProvider
except ImportError:
    class MemoryProvider:  # 与官方 ABC 同名同签名的可选钩子默认值（独立运行 / 测试用）
        pre_compress_checkpoint_api_version = 1

        def unavailable_reason(self) -> str: return ""
        def system_prompt_block(self) -> str: return ""
        def prefetch(self, query, *, session_id=""): return ""
        def queue_prefetch(self, query, *, session_id=""): return None
        def recall_status(self): return None
        def sync_turn(self, user_content, assistant_content, *, session_id="",
                      messages=None, turn_author=None): return None
        def handle_tool_call(self, tool_name, args, **kwargs): raise NotImplementedError(tool_name)
        def shutdown(self): return None
        def on_turn_start(self, turn_number, message, **kwargs): return None
        def identity_signature(self): return {}
        def on_session_end(self, messages): return None
        def on_session_switch(self, new_session_id, *, parent_session_id="", reset=False,
                              rewound=False, **kwargs): return None
        def on_pre_compress(self, messages): return ""
        def on_delegation(self, task, result, *, child_session_id="", **kwargs): return None
        def get_config_schema(self): return []
        def save_config(self, values, hermes_home): return None
        def on_memory_write(self, action, target, content, metadata=None): return None
        def backup_paths(self): return []

try:
    from agent.memory_provider import RecallStatus
except ImportError:
    @dataclass(frozen=True)
    class RecallStatus:
        provider_label: str
        count: int
        glyph: str = "🧠"

try:
    from agent.memory_provider import is_trivial_prompt
except ImportError:
    _TRIVIAL = re.compile(
        r"^(yes|no|ok|okay|sure|thanks|thank you|y|n|hi|hey|hello|continue|go ahead|done|next|"
        r"好|好的|嗯|嗯嗯|行|可以|谢谢|收到|继续|对|是的|没问题)[\s!?.。！？~，,]*$", re.I)

    def is_trivial_prompt(text) -> bool:
        s = (text or "").strip()
        return not s or s.startswith("/") or bool(_TRIVIAL.match(s))

try:
    from tools.registry import tool_error
except ImportError:
    def tool_error(msg): return json.dumps({"error": msg}, ensure_ascii=False)

logger = logging.getLogger(__name__)

# 偏移方向中文标签（system_prompt_block / prefetch 共用）
_OFFSET_LABELS = {"frugal": "省钱", "spend": "愿投", "drift": "放弃"}

# agent 一次最多送进隔离区的条数；更大的范围交给主人用 CLI 做
_FORGET_MAX = 20
_PREFETCH_TOKENS_DEFAULT = 600
_WRITE_CONTEXTS = ("", "primary")


def _fn(name: str, description: str, properties: dict, required: list = None) -> dict:
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return {"name": name, "description": description, "parameters": schema}


# ══════════════════════════════════════════════════════════
# 工具方法——官方格式 {"name", "description", "parameters"}
# ══════════════════════════════════════════════════════════

_TOOL_SCHEMAS = [
    _fn("sandglass_search", "搜索 Nyx 记忆——投石问路（倒排索引）优先，五维权重排序；"
        "非主人来源的记忆会带「未经证实」标注。",
        {"query": {"type": "string", "description": "搜索关键词"},
         "limit": {"type": "integer", "default": 10}}, ["query"]),
    _fn("sandglass_recent", "获取最近 N 条记忆。",
        {"n": {"type": "integer", "default": 10}}),
    _fn("sandglass_offset", "计算当前偏移率——主人决策方向的趋势。返回偏移百分比和方向。", {}),
    _fn("fact_store", "影子沙事实存储。action=add/search/probe/reason。存储结构化事实，信任评分排序。",
        {"action": {"type": "string", "enum": ["add", "search", "probe", "reason"]},
         "content": {"type": "string", "description": "事实内容"},
         "category": {"type": "string", "default": "general"},
         "query": {"type": "string"},
         "entity": {"type": "string"}}, ["action"]),
    _fn("fact_feedback", "信任评分反馈。标记记忆是否有帮助。",
        {"line_num": {"type": "integer"}, "helpful": {"type": "boolean"}},
        ["line_num", "helpful"]),
    _fn("sandglass_echo", "读取回音折——最近的情感风向。", {}),
    _fn("nyx_belief",
        "查双时态事实（住在/使用/公司/职位/邮箱/电话/偏好/反感）。默认返回现在相信的；"
        "as_of=某日期 → 那天什么为真；known_at=某日期 → 那时我以为现在是什么；"
        "history=true → 演变链与看法变化时间线。回答「现在住哪 / 以前在哪工作 / 什么时候换的」时用它。",
        {"subject": {"type": "string", "default": "user"},
         "relation": {"type": "string",
                      "enum": ["住在", "使用", "公司", "职位", "邮箱", "电话", "偏好", "反感"]},
         "as_of": {"type": "string", "description": "YYYY-MM-DD"},
         "known_at": {"type": "string", "description": "YYYY-MM-DD"},
         "history": {"type": "boolean", "default": False}}),
    _fn("nyx_forget",
        "遗忘记忆（进隔离区，保留期内可 nyx_restore 还原；agent 无权永久擦除）。"
        "先不带 confirm 调一次看会删哪些，确认无误再带 confirm=true。"
        "只在主人明确要求忘掉某件事时使用。",
        {"contains": {"type": "string", "description": "正文精确子串（至少 2 个字）"},
         "mem_id": {"type": "string"},
         "reason": {"type": "string"},
         "confirm": {"type": "boolean", "default": False}}),
    _fn("nyx_restore", "把隔离区里的一条记忆原样还原（隔离期内有效）。",
        {"mem_id": {"type": "string"}}, ["mem_id"]),
    _fn("nyx_quarantine", "列出隔离区：被遗忘但还能还原的记忆（只含 40 字预览）与到期时间。",
        {"limit": {"type": "integer", "default": 20}}),
]

_WRITE_TOOLS = {"fact_feedback", "nyx_forget", "nyx_restore"}


def _as_bool(v, default: bool) -> bool:
    if v is None or v == "":
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y")


def _as_int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _said_on(objs: list) -> Dict[int, str]:
    """召回对象 → 说这句话的日期（按日志行号查中枢）。「上个月」「那天」这类问题离不开它。"""
    lines = {}
    for i, o in enumerate(objs):
        sid = getattr(o, "source_id", None)
        if sid is not None and str(sid).isdigit():
            lines[i] = int(sid)
    if not lines:
        return {}
    try:
        from nexsandglass.core import memid
        uniq = sorted(set(lines.values()))
        q = "SELECT line_start, ts FROM memories WHERE line_start IN (%s)" % ",".join("?" * len(uniq))
        ts = {r[0]: str(r[1])[:10] for r in memid.get_conn().execute(q, uniq)}
    except Exception:
        return {}
    return {i: ts[ln] for i, ln in lines.items() if ln in ts}


def _hermes_cfg():
    """nexsandglass_hermes 只用标准库；拿不到（旧式单文件部署）时退化为无配置。"""
    try:
        import nexsandglass_hermes as nh
        return nh
    except Exception:
        return None


class NexSandglassProvider(MemoryProvider):
    """Nyx 记忆提供器——纯本地零依赖，官方 MemoryProvider 契约。"""

    # 类级默认值：测试会用 __new__ 绕过 __init__ 直接调方法
    _config: dict = {}
    _lock = None
    _initialized = False
    _turn_count = 0
    _use_facade = False
    _session_id = ""
    _hermes_home: Optional[str] = None
    _agent_context = "primary"
    _owner_ids: frozenset = frozenset()
    _bots_external = True
    _prefetch_budget = _PREFETCH_TOKENS_DEFAULT
    _turn_author: Optional[dict] = None
    _last_recall: Optional[int] = None

    def __init__(self, config: dict = None):
        self._config = config or {}
        self._lock = threading.Lock()
        self._initialized = False
        self._turn_count = 0

    # ═══════ MemoryProvider 核心接口 ═══════

    @property
    def name(self) -> str:
        return "nexsandglass"

    def _data_dir(self) -> str:
        nh = _hermes_cfg()
        if nh is not None:
            return nh.resolve_data_dir(self._hermes_home)
        return os.path.abspath(os.environ.get("NEXSANDBASE_HOME")
                               or os.path.expanduser("~/.neurobase"))

    def is_available(self) -> bool:
        """零 API Key：只看记忆目录能不能写（不碰网络）。"""
        return not self.unavailable_reason()

    def unavailable_reason(self) -> str:
        d = self._data_dir()
        probe = d
        while probe and not os.path.exists(probe):
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent
        if probe and os.path.exists(probe) and not os.access(probe, os.W_OK):
            return f"Nyx 记忆目录不可写：{d}（设置 NEXSANDBASE_HOME 或 nyx.json 的 data_dir）"
        return ""

    def _load_settings(self) -> None:
        cfg = dict(self._config)
        nh = _hermes_cfg()
        if nh is not None:
            cfg = {**nh.load_config(self._hermes_home), **cfg}
        owners = cfg.get("owner_ids", os.environ.get("NYX_OWNER_IDS", ""))
        if isinstance(owners, str):
            owners = [o for o in re.split(r"[,，\s]+", owners) if o]
        self._owner_ids = frozenset(str(o) for o in (owners or []))
        self._bots_external = _as_bool(cfg.get("bots_are_external"), True)
        self._prefetch_budget = max(0, _as_int(cfg.get("prefetch_tokens"), _PREFETCH_TOKENS_DEFAULT))
        # 语义检索后端：配置只在环境变量没设时生效（环境变量 > nyx.json）
        for key, env in (("semantic", "NYX_EMBED"), ("embedding_model", "NYX_EMBED_MODEL"),
                         ("embedding_api_url", "EMBEDDING_API_URL")):
            if cfg.get(key) and not os.environ.get(env):
                os.environ[env] = str(cfg[key])

    def initialize(self, session_id: str = "", **kwargs) -> None:
        """设置沙漏路径、重建投石问路索引。

        kwargs（Hermes 注入）：hermes_home / platform / agent_context（非 primary 不写）/ ...
        """
        lock = self.__dict__.get("_lock") or threading.Lock()
        self._lock = lock
        with lock:
            self._session_id = session_id or ""
            self._hermes_home = kwargs.get("hermes_home") or self._hermes_home
            self._agent_context = str(kwargs.get("agent_context") or "primary")
            self._load_settings()
            if self._initialized:
                return
            nh = _hermes_cfg()
            if nh is not None:
                nh.apply_data_dir(self._hermes_home)
            # 确保 sandglass 模块可导入
            import sys
            nb = os.environ.get("NEXSANDBASE_HOME") or os.path.expanduser("~/.neurobase")
            nb_scripts = os.path.join(nb, "scripts")
            if nb_scripts not in sys.path:
                sys.path.insert(0, nb_scripts)

            from nexsandglass.features.sandglass_vault import rebuild_index
            from nexsandglass.core.sandglass_paths import validate
            validate()
            if self._can_write():
                rebuild_index()          # 子 agent / cron 不重建主 agent 的索引
                try:
                    from nexsandglass.core import semantic
                    semantic.warm_async()    # 后台：加载嵌入模型 + 回填历史向量（无后端时空操作）
                except Exception as e:
                    logger.debug("语义索引回填未启动: %s", e)
            # Cognitive Memory OS feature flag：NYX_USE_FACADE=1 时走稳定门面（默认关）
            self._use_facade = os.environ.get("NYX_USE_FACADE", "0") == "1"
            self._initialized = True
            logger.info("NexSandglass MemoryProvider initialized (context=%s, owners=%d, facade=%s)",
                        self._agent_context, len(self._owner_ids), self._use_facade)

    def _can_write(self) -> bool:
        return (self._agent_context or "") in _WRITE_CONTEXTS

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

            persona_parts = []
            if persona_text:
                persona_parts.append(persona_text)
            if scene_text:
                persona_parts.append(f"📍 {scene_text}")
            persona_layer = "\n".join(persona_parts)

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

            offset_layer = "\n".join(layer2[1:]) if len(layer2) > 1 else ""

            # ═══════ 第三层：你怎么变成这样 ═══════
            try:
                from nexsandglass.features.weavethread import wthread_stats, wthread_weave
                stats = wthread_stats()
                if stats["total_triples"] >= 20:
                    thread = wthread_weave(limit=3)
                    if thread and thread != "织线因果:":
                        thread_layer = thread[:200]
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
                open_loops_layer = "\n".join(layer4[1:]) if len(layer4) > 1 else ""
            else:
                open_loops_layer = ""

            # ═══════ B2：唯一出口 MemoryBundle → render ═══════
            # persona/offset/thread/open_loops 作为槽位输入；body 仅由 bundle.render() 产出
            thread_layer = locals().get("thread_layer", "")
            try:
                from nexsandglass.runtime.orchestrator import get_orchestrator
                orch = get_orchestrator()
                bundle = orch.recall_bundle(
                    "",
                    max_tokens=self._bundle_max_tokens(),
                    persona_layer=persona_layer,
                    offset_layer=offset_layer,
                    thread_layer=thread_layer,
                    open_loops=open_loops_layer,
                )
                body = bundle.render()
            except Exception as e:
                # 降级仅在 orch 真正不可用时触发，并打 warning
                logger.warning("system_prompt_block Bundle 路径失败，fallback: %s", e)
                fb = []
                if persona_layer: fb.append("【你是谁】\n" + persona_layer)
                if offset_layer: fb.append("【你在往哪走】\n" + offset_layer)
                if thread_layer: fb.append("【你怎么变成这样】\n" + thread_layer)
                if open_loops_layer: fb.append("【还没做完】\n" + open_loops_layer)
                body = "\n\n".join(fb).strip()

            tail = f"沙漏: {total}条 | 阶段: {stage}"
            return (body + "\n\n" + tail).strip()
        except Exception:
            logger.warning("system_prompt_block 整体失败", exc_info=True)
            return "NexSandglass记忆系统已就绪。使用sandglass_search搜索记忆。"

    def _signal_line(self) -> str:
        """偏移 + 情绪：主注入已有全貌，这里只给最动态的信号。"""
        try:
            from nexsandglass.features.sandglass_think import comprehensive_offset, _emotional_entropy
            off = comprehensive_offset()
            ent = _emotional_entropy()
            mood = "平稳" if ent < 0.5 else ("波动" if ent < 1.0 else "高熵")
            off_d = _OFFSET_LABELS.get(off.get('direction', ''), '平稳')
            return f"## 当前\n偏移: {off_d}({off.get('offset', 0):+d}%) | 情绪: {mood}\n"
        except Exception:
            return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """按**本轮**问题召回相关记忆 + 当前信号。

        官方建议「后台召回、这里返回缓存」是为远程 provider 设计的；Nyx 的召回是本地的
        （实测 10–30ms），直接对本轮问题召回，比用上一轮的问题预取更准。
        """
        self._last_recall = None           # recall_status 只反映最近这一次
        parts: List[str] = []
        if not is_trivial_prompt(query):
            try:
                from nexsandglass.runtime.orchestrator import get_orchestrator
                mc = get_orchestrator().recall(query, token_budget=self._prefetch_budget or 1)
                objs = [o for o in (mc.objects or []) if o.content and str(o.content).strip()]
                lines = [str(o.content) for o in objs] or \
                        [s for s in (mc.strings or []) if s and str(s).strip()]
                cap = max(12, self._prefetch_budget // 100)   # 600 → 12 条；4000 → 40 条
                if self._prefetch_budget and lines:
                    dates = _said_on(objs[:cap])
                    parts.append("## Nyx 记忆（与本轮相关，[日期]=说这句话的时间）")
                    for i, s in enumerate(lines[:cap]):
                        d = dates.get(i)
                        parts.append(f"- [{d}] {s[:300]}" if d else f"- {s[:300]}")
                    withheld = len(getattr(mc, "withheld", None) or [])
                    if withheld:
                        parts.append(f"（另有 {withheld} 条因来源可疑未注入）")
                    self._last_recall = min(len(lines), cap)
            except Exception as e:
                logger.debug("prefetch 召回失败: %s", e)
        sig = self._signal_line()
        if sig:
            parts.append(sig)
        return "\n".join(parts)

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """不预取：召回在 prefetch 里对本轮问题现做（见 prefetch 文档）。"""
        return None

    def recall_status(self):
        if self._last_recall:
            return RecallStatus(provider_label="Nyx", count=int(self._last_recall))
        return None

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        """记下这一轮是谁写的（多人会话里 sync_turn 可能拿不到 turn_author）。"""
        aid, aname = kwargs.get("author_id"), kwargs.get("author_name")
        if aid is None and aname is None and not kwargs.get("author_is_bot"):
            self._turn_author = None
        else:
            self._turn_author = {"id": aid, "name": aname, "is_bot": bool(kwargs.get("author_is_bot"))}

    def _user_source(self, turn_author: Optional[dict] = None) -> str:
        """用户一侧这句话算谁说的：主人 → user（principal）；机器人 / 非主人 → 外部来源。

        不配置 owner_ids = 单人会话，全部按主人记（与 v7.11 行为一致）。
        """
        a = turn_author if turn_author is not None else self._turn_author
        if not a:
            return "user"
        if a.get("is_bot") and self._bots_external:
            return "bot"
        if self._owner_ids:
            ident = {str(a.get("id") or ""), str(a.get("name") or "")} - {""}
            if not (ident & self._owner_ids):
                return "participant"
        return "user"

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "",
                  messages: Optional[List[Dict[str, Any]]] = None,
                  turn_author: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        """每轮对话后：raw 落沙（审计日志）+ 候选晋升（只写值得长期记住的）。

        同步执行是有意的：MemoryManager.sync_all 已经把它放在**串行**后台线程上，
        第 N 轮一定先于第 N+1 轮落盘，flush_pending 也等得到它；自己再起线程会破坏这两条。

          - 每条消息先落沙 sandglass（保留作审计日志），发送者按 turn_author 绑定来源
          - 用 PromotionEngine 判断是否晋升长期（Promote/SessionOnly/Drop）
          - 只对 promote 的候选经 orchestrator 写长期 engram/fact
        """
        if not self._can_write():
            return
        user_src = self._user_source(turn_author)
        pairs = [(user_content, user_src, user_src), (assistant_content, "agent", "assistant")]
        try:
            from nexsandglass.runtime.orchestrator import get_orchestrator
            from nexsandglass.runtime.promotion import PromotionEngine
            orch = get_orchestrator()
            eng = PromotionEngine(use_llm=False, timeout=2.0)

            for msg, sender, source in pairs:
                if not msg:
                    continue
                # 1. raw 落沙（审计日志，始终保留）
                try:
                    from nexsandglass.core.sandglass_log import log_message
                    log_message(msg, sender)
                except Exception as e:
                    logger.warning("sync_turn raw 落沙失败: %s", e)
                # 2. 候选晋升判断（raw 已在上方落过 → raw_already_logged=True 防双写）
                try:
                    cand = eng.observe(msg)
                    if cand.disposition == "promote":
                        orch.observe(msg, source=source, raw_already_logged=True)
                except Exception as e:
                    logger.warning("sync_turn 晋升失败: %s", e)
            self._turn_count += 1
            self._index_semantic()
        except Exception as e:
            # 降级：orchestrator 不可用时回退到直连落沙（保持可用性），打 warning
            logger.warning("sync_turn 主路径失败，fallback 直连落沙: %s", e)
            try:
                from nexsandglass.core.sandglass_log import log_message
                for msg, sender, _ in pairs:
                    if msg:
                        log_message(msg, sender)
                self._turn_count += 1
                self._index_semantic()
            except Exception as e2:
                logger.warning("sync_turn fallback 落沙失败: %s", e2)

    @staticmethod
    def _index_semantic() -> None:
        """给刚落沙的记忆补向量（v7.13）。sync_turn 已在 Hermes 的串行后台线程上，这里同步做。"""
        try:
            from nexsandglass.core import semantic
            if semantic.enabled():
                semantic.index_pending(limit=64)
        except Exception as e:
            logger.debug("语义索引跳过: %s", e)

    def _bundle_max_tokens(self) -> int:
        """Bundle 默认预算（NYX_BUNDLE_MAX_TOKENS 可配，默认 1500）。"""
        try:
            return int(os.environ.get("NYX_BUNDLE_MAX_TOKENS", "1500"))
        except ValueError:
            return 1500

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

    # ═══════ 双时态 / 遗忘 工具 ═══════

    def _handle_belief(self, args: dict) -> str:
        from nexsandglass.features import weavethread
        from nexsandglass.engram.loops import temporal_fact as tf
        db = weavethread._DB
        subject = args.get("subject") or "user"
        rel = args.get("relation") or None
        keep = ("subject", "relation", "object", "valid_from", "valid_until", "valid_basis",
                "recorded_at", "closed_at", "source_mem_id")
        slim = lambda rows: [{k: r.get(k) for k in keep} for r in rows]
        if _as_bool(args.get("history"), False):
            return json.dumps({"mode": "history", "facts": slim(tf.history_of(db, subject, rel)),
                               "timeline": tf.belief_timeline(db, subject, rel)},
                              ensure_ascii=False, default=str)
        if args.get("as_of"):
            rows = tf.as_of(db, args["as_of"], known_at=args.get("known_at"),
                            subject=subject, predicate=rel)
            return json.dumps({"mode": "as_of", "as_of": args["as_of"], "facts": slim(rows)},
                              ensure_ascii=False, default=str)
        if args.get("known_at"):
            rows = tf.get_current(db, subject, rel, known_at=args["known_at"])
            return json.dumps({"mode": "known_at", "known_at": args["known_at"], "facts": slim(rows)},
                              ensure_ascii=False, default=str)
        return json.dumps({"mode": "current", "facts": slim(tf.get_current(db, subject, rel))},
                          ensure_ascii=False, default=str)

    def _handle_forget(self, args: dict) -> str:
        from nexsandglass.runtime import facade
        sel: dict = {}
        if args.get("mem_id"):
            sel["mem_id"] = str(args["mem_id"])
        elif len(str(args.get("contains") or "").strip()) >= 2:
            sel["contains"] = str(args["contains"]).strip()
        else:
            return tool_error("nyx_forget 需要 mem_id，或至少 2 个字的 contains")
        preview = facade.forget({**sel, "dry_run": True})
        if not preview.get("ok"):
            return tool_error(f"nyx_forget 预览失败: {preview.get('error')}")
        n = int(preview.get("selected") or 0)
        if not _as_bool(args.get("confirm"), False) or n == 0:
            return json.dumps({"status": "preview", "would_forget": n,
                               "items": (preview.get("preview") or [])[:10],
                               "next": "确认后带 confirm=true 再调用；进入隔离区，保留期内可 nyx_restore"},
                              ensure_ascii=False, default=str)
        if n > _FORGET_MAX:
            return tool_error(f"一次选中 {n} 条，超过 agent 上限 {_FORGET_MAX}；"
                              "请缩小范围，或由主人用 CLI 处理")
        rep = facade.forget({**sel, "reason": str(args.get("reason") or "agent_forget")})
        return json.dumps({"status": "quarantined" if rep.get("ok") else "failed",
                           "forgotten": rep.get("removed", 0),
                           "mem_ids": [p.get("mem_id") for p in (preview.get("preview") or [])],
                           "recoverable": rep.get("recoverable", False),
                           "purge_after": rep.get("purge_after", ""),
                           "error": rep.get("error")}, ensure_ascii=False, default=str)

    def _handle_restore(self, args: dict) -> str:
        from nexsandglass.runtime import facade
        mid = str(args.get("mem_id") or "").strip()
        if not mid:
            return tool_error("nyx_restore 需要 mem_id")
        return json.dumps(facade.restore(mid), ensure_ascii=False, default=str)

    def _handle_quarantine(self, args: dict) -> str:
        from nexsandglass.core import erasure
        items = erasure.quarantine_list(limit=max(1, min(_as_int(args.get("limit"), 20), 200)))
        return json.dumps({"items": items}, ensure_ascii=False, default=str)

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """会话结束——偏移检查；宿主没调过 sync_turn 时才补落最后几轮（B1 兜底）。

        宿主正常调用 sync_turn 时，每一轮已按真实作者落过了；这里再按 role 落一遍，
        会把多人会话里别人说的话重新记成「主人说的」。
        """
        try:
            if self._can_write() and not self._turn_count:
                from nexsandglass.runtime.orchestrator import get_orchestrator
                orch = get_orchestrator()
                for msg in (messages or [])[-5:]:
                    role = msg.get("role", "user")
                    content = msg.get("content", "")
                    if content and role in ("user", "assistant"):
                        src = self._user_source() if role == "user" else "assistant"
                        orch.observe(str(content)[:500], source=src, raw_already_logged=True)

            # 触发偏移检查 + 织造
            from nexsandglass.features.sandglass_think import comprehensive_offset
            off = comprehensive_offset()
            if abs(off.get("offset", 0)) >= 30:
                logger.info(f"会话结束偏移: {off['offset']:+d}% ({off['direction']})")
        except Exception:
            pass

    def on_session_switch(self, new_session_id: str, *, parent_session_id: str = "",
                          reset: bool = False, rewound: bool = False, **kwargs) -> None:
        self._session_id = new_session_id or ""
        if reset:
            self._turn_author = None
            self._last_recall = None

    def on_delegation(self, task: str, result: str, *, child_session_id: str = "", **kwargs) -> None:
        """子 agent 的结果是 agent 推断，不是主人的话：以 agent 来源记入，经晋升门。"""
        if not self._can_write() or not (task or result):
            return
        try:
            from nexsandglass.runtime.orchestrator import get_orchestrator
            get_orchestrator().observe(f"委派：{str(task)[:200]} → 结果：{str(result)[:500]}",
                                       source="agent")
        except Exception as e:
            logger.debug("on_delegation 失败: %s", e)

    # ═══════ 工具暴露 ═══════

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [dict(s) for s in _TOOL_SCHEMAS]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any] = None, **kwargs) -> str:
        name = tool_name
        args = args or {}
        try:
            if name in _WRITE_TOOLS or (name == "fact_store" and args.get("action") == "add"):
                if not self._can_write():
                    return tool_error(f"{name}: 当前是 {self._agent_context} 上下文，Nyx 只读")

            if name == "sandglass_search":
                from nexsandglass.runtime.orchestrator import get_orchestrator
                orch = get_orchestrator()
                limit = _as_int(args.get("limit"), 10)
                rr = orch.recall(args.get("query", ""), token_budget=limit * 200)
                return json.dumps(
                    [{"text": t[:200], "id": rr.memory_ids[i] if i < len(rr.memory_ids) else t} for i, t in enumerate(rr.strings[:limit])],
                    ensure_ascii=False,
                )

            if name == "sandglass_recent":
                from nexsandglass.runtime.orchestrator import get_orchestrator
                orch = get_orchestrator()
                objs = orch.recent(_as_int(args.get("n"), 10))
                return json.dumps(
                    [{"id": o.memory_id, "text": o.content[:200]} for o in objs],
                    ensure_ascii=False,
                )

            if name == "sandglass_offset":
                from nexsandglass.features.sandglass_think import comprehensive_offset
                off = comprehensive_offset()
                return json.dumps(off, ensure_ascii=False, default=str)

            if name == "sandglass_echo":
                from nexsandglass.features.sandglass_think import _sentiment_wind
                wind = _sentiment_wind()
                return json.dumps({"wind": wind, "direction": "正面" if wind > 0 else ("负面" if wind < 0 else "中性")}, ensure_ascii=False)

            if name == "fact_store":
                return self._handle_fact_store(args)
            if name == "fact_feedback":
                return self._handle_fact_feedback(args)
            if name == "nyx_belief":
                return self._handle_belief(args)
            if name == "nyx_forget":
                return self._handle_forget(args)
            if name == "nyx_restore":
                return self._handle_restore(args)
            if name == "nyx_quarantine":
                return self._handle_quarantine(args)

            return tool_error(f"Unknown NexSandglass tool: {name}")

        except Exception as e:
            return tool_error(f"NexSandglass error: {e}")

    # ═══════ 可选钩子 ═══════

    def on_memory_write(self, action: str, target: str, content: str, metadata: dict = None) -> None:
        """镜像内置记忆写入——同步落沙（agent 的决定，来源记为 memory_write）。"""
        if not self._can_write():
            return
        try:
            from nexsandglass.core.sandglass_log import log_message
            text = f"[{action}] {target}: {(content or '')[:200]}"
            log_message(text, "memory_write")
        except Exception:
            pass

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """上下文压缩前提取关键记忆。经 orchestrator 统一召回。"""
        try:
            from nexsandglass.runtime.orchestrator import get_orchestrator
            orch = get_orchestrator()
            if messages:
                last = str(messages[-1].get("content", "") or "")[:100]
                if last:
                    rr = orch.recall(last, token_budget=600)
                    return "\n".join(t[:200] for t in rr.strings[:3])
        except Exception:
            pass
        return ""

    # ═══════ 配置 / 备份 / 身份 ═══════

    def get_config_schema(self) -> List[Dict[str, Any]]:
        nh = _hermes_cfg()
        default_dir = nh.DEFAULT_DATA_DIR if nh is not None else os.path.expanduser("~/.neurobase")
        return [
            {"key": "data_dir", "type": "text", "default": default_dir,
             "description": "Nyx 记忆目录。默认与 nyx 的 MCP / CLI 共用同一份记忆；"
                            "环境变量 NEXSANDBASE_HOME 优先。改动后重启生效。"},
            {"key": "owner_ids", "type": "text", "default": "",
             "description": "主人在聊天平台上的身份 ID / 名字（逗号分隔）。设置后只有这些人说的话按"
                            "「主人亲口说的」记，其他参与者一律记为外部来源（未经证实）。留空 = 单人使用。"},
            {"key": "bots_are_external", "type": "boolean", "default": True,
             "description": "机器人发来的消息按外部来源记（不当成主人的话）。"},
            {"key": "prefetch_tokens", "type": "integer", "default": _PREFETCH_TOKENS_DEFAULT,
             "minimum": 0, "maximum": 4000, "step": 100,
             "description": "每轮自动召回注入的预算（token）；0 = 只注入偏移/情绪信号。"},
            {"key": "semantic", "type": "text", "default": "auto", "choices": ["auto", "local", "api", "off"],
             "description": "语义检索（同义改写也能召回）。auto：配了 API 用 API，装了 "
                            "sentence-transformers（pip install 'nyx-memory[vector]'）用本地模型，都没有则关闭。"},
            {"key": "embedding_model", "type": "text", "default": "",
             "description": "嵌入模型名。本地默认 paraphrase-multilingual-MiniLM-L12-v2（中英混合）；"
                            "API 默认 text-embedding-3-small。换模型后旧向量自动作废并在后台重建。"},
            {"key": "embedding_api_url", "type": "text", "default": "",
             "description": "OpenAI 兼容 /embeddings 地址（选填；semantic=api 或 auto 时使用）。"},
            {"key": "embedding_api_key", "type": "text", "secret": True, "env_var": "EMBEDDING_API_KEY",
             "description": "嵌入 API 的密钥（存进 .env，不写 nyx.json）。"},
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        nh = _hermes_cfg()
        if nh is None:
            raise RuntimeError("nexsandglass_hermes 不可用，无法保存配置")
        known = {f["key"] for f in self.get_config_schema() if not f.get("secret")}   # 密钥只进 .env
        nh.save_config({k: v for k, v in (values or {}).items() if k in known}, hermes_home)
        if hermes_home and hermes_home == self._hermes_home:
            self._load_settings()

    def backup_paths(self) -> List[str]:
        """记忆目录在 HERMES_HOME 之外时交给 hermes backup（不需要 initialize）。"""
        d = self._data_dir()
        nh = _hermes_cfg()
        home = nh.hermes_home(self._hermes_home) if nh is not None else self._hermes_home
        if home:
            h = os.path.abspath(home)
            if d == h or d.startswith(h.rstrip(os.sep) + os.sep):
                return []
        return [d]

    def identity_signature(self) -> Dict[str, Any]:
        """owner_ids 变了，网关缓存的 agent 必须重建（来源绑定依赖它）。只读、廉价。"""
        nh = _hermes_cfg()
        owners = ""
        if nh is not None:
            owners = nh.load_config(self._hermes_home).get("owner_ids", "")
        owners = owners or os.environ.get("NYX_OWNER_IDS", "")
        if isinstance(owners, str):
            owners = [o for o in re.split(r"[,，\s]+", owners) if o]
        return {"nyx.owner_ids": sorted(str(o) for o in owners)}


# ── 插件自动发现入口 ──
def register(ctx) -> None:
    """Hermes 插件加载入口——注册 Provider。新部署用 nexsandglass_hermes（先定数据目录再 import）。"""
    provider = NexSandglassProvider()
    ctx.register_memory_provider(provider)
