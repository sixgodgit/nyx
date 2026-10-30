"""
NexSandglass MemoryProvider — Hermes Agent 官方记忆提供器接口实现
==================================================================
让 Hermes 使用 Nyx（NexSandglass）作为外置记忆，与 Honcho / Mem0 / Hindsight 走同一套
``agent.memory_provider.MemoryProvider`` 契约（v7.12 起按官方签名实现）。

零 API Key、零外部依赖——纯本地驱动。Nyx 独有、同类 provider 没有的：
  - 双时态事实：「现在是什么」与「第 K 天时我以为是什么」分开回答（nyx_belief）
  - 写入时绑定来源与信任：工具 / 委派 / 机器人的话不会被当成主人的话（宿主传 turn_author 时，再按 owner_ids 区分参与者；
    Hermes 目前不传发言人，那里按单人使用）
  - 两段式遗忘：agent 只能把记忆送进隔离区，保留期内可还原；永久擦除只留给主人（CLI）

生命周期（MemoryManager 驱动）：
  initialize → system_prompt_block（静态画像）→ 每轮 prefetch（按本轮问题召回）
  → sync_turn（MemoryManager 已放在串行后台线程里调用）→ 工具调用 → shutdown
"""
from __future__ import annotations

import functools
import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from nexsandglass.core import hints as _hints
from nexsandglass.core import secret_filter as _secret
from nexsandglass.core import secret_vault as _vault

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

from nexsandglass.core import settings as _settings
from nexsandglass.core.lang import t as _t

logger = logging.getLogger(__name__)

# 给模型看的文字一律英文（v7.16.0）；给人看的（hermes memory setup 的说明）走 core/lang.t
# 偏移方向标签（system_prompt_block / prefetch 共用）
_OFFSET_LABELS = {"frugal": "saving", "spend": "spending", "drift": "drifting"}


def _mood(entropy: float) -> str:
    return "steady" if entropy < 0.5 else ("variable" if entropy < 1.0 else "turbulent")

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
    _fn("sandglass_search", "Search the user's long-term memory (full-text, inverted index and entity search, fused). "
        "Results from sources other than the user carry an \"(unverified · source: …)\" tag.",
        {"query": {"type": "string", "description": "What to search for"},
         "limit": {"type": "integer", "default": 10}}, ["query"]),
    _fn("sandglass_recent", "The most recent N memories.",
        {"n": {"type": "integer", "default": 10}}),
    _fn("sandglass_offset", "The user's current decision drift (offset): which way recent decisions lean, as a percentage and direction.", {}),
    _fn("fact_store", "Structured fact store with trust scores. action=add stores a fact; search finds facts; "
        "probe lists trust scores for an entity; reason returns the best-supported memory for an entity.",
        {"action": {"type": "string", "enum": ["add", "search", "probe", "reason"]},
         "content": {"type": "string", "description": "The fact to store"},
         "category": {"type": "string", "default": "general"},
         "query": {"type": "string"},
         "entity": {"type": "string"}}, ["action"]),
    _fn("fact_feedback", "Mark a recalled memory as helpful or not (adjusts its trust score).",
        {"line_num": {"type": "integer"}, "helpful": {"type": "boolean"}},
        ["line_num", "helpful"]),
    _fn("sandglass_echo", "Recent sentiment trend in the user's messages (positive / negative / neutral).", {}),
    _fn("nyx_belief",
        "Bi-temporal facts about the user (where they live, what they use, employer, job title, email, phone, "
        "likes, dislikes). By default returns what is believed now; as_of=DATE → what was true on that day; "
        "known_at=DATE → what was believed at that time; history=true → how the fact and the belief changed. "
        "Use it for \"where do I live now / where did I work before / when did that change\".",
        {"subject": {"type": "string", "default": "user"},
         "relation": {"type": "string",
                      "enum": ["lives_in", "uses", "employer", "job_title", "email", "phone", "likes", "dislikes"]},
         "as_of": {"type": "string", "description": "YYYY-MM-DD"},
         "known_at": {"type": "string", "description": "YYYY-MM-DD"},
         "history": {"type": "boolean", "default": False}}),
    _fn("nyx_forget",
        "Forget memories: they move to a quarantine and can be restored with nyx_restore until the retention period "
        "ends (agents cannot erase permanently). Call once without confirm to preview, then again with confirm=true. "
        "Use only when the user explicitly asks to forget something.",
        {"contains": {"type": "string", "description": "Exact substring of the memory text (at least 2 characters)"},
         "mem_id": {"type": "string"},
         "reason": {"type": "string"},
         "confirm": {"type": "boolean", "default": False}}),
    _fn("nyx_restore", "Restore a forgotten memory from the quarantine, unchanged (while it is still there).",
        {"mem_id": {"type": "string"}}, ["mem_id"]),
    _fn("nyx_quarantine", "List the quarantine: forgotten memories that can still be restored (40-character preview) and when each expires.",
        {"limit": {"type": "integer", "default": 20}}),
    _fn("nyx_health",
        "Nyx self-check: hub vs journal consistency, full-text / inverted / semantic indexes, erasure integrity, "
        "quarantine expiry, rule poisoning. Each check reports ok and a reason. Call it when the user asks whether "
        "memory is working, or when the system prompt shows a Nyx health warning.",
        {"quick": {"type": "boolean", "default": False,
                   "description": "true = semantic index only (fast); false = everything (scans the whole journal)"}}),
]

_WRITE_TOOLS = {"fact_feedback", "nyx_forget", "nyx_restore"}

# 密钥保险箱（v7.15.0，NYX_VAULT=on 才出现）。原文永远不进这两个工具的描述 / 列表；取回规则见 docs/secret-vault.md
_VAULT_TOOL_SCHEMAS = [
    _fn("nyx_secret_list",
        "List the secrets kept in the vault: id (v3), kind, last 4 characters, date saved, and the surrounding context. "
        "**Never includes the secret itself.** When the user asks for \"that token / key / password I gave you\", "
        "use this first to find which entry it is.",
        {"query": {"type": "string", "description": "Optional filter by kind or context, e.g. HF, GitHub, wifi"}}),
    _fn("nyx_secret_reveal",
        "Hand a stored secret back to the user. Call only when the user explicitly asked for it in this turn; "
        "the user has to confirm. When it returns queued, the secret does not pass through you: put the returned "
        "handle in your reply verbatim and nyx swaps it in before sending. Never guess, reconstruct or repeat a secret.",
        {"id": {"type": "string", "description": "Entry id, e.g. v3"}}, ["id"]),
]

# 主人这一轮的原话要像在要密钥（闸门 2）：防召回内容、网页、文件里埋的提示注入让 agent 自己去取
_ASK_VERB = re.compile(r"给我|告诉我|发我|发给我|显示|看看|看一下|查看|取回|拿回|找回|调出|是多少|是什么|多少|"
                       r"\breveal\b|\bshow\b|\btell me\b|\bgive me\b|\bwhat(?:'s| is)\b", re.I)
_ASK_NOUN = re.compile(r"token|key|密钥|秘钥|密码|口令|凭据|凭证|令牌|password|passwd|secret|#?\bv\d+\b", re.I)


def _asked_for_secret(message: str) -> bool:
    m = message or ""
    return bool(_ASK_VERB.search(m) and _ASK_NOUN.search(m))


def _clean(text: Optional[str]) -> str:
    """写入 / 发往外部服务之前：取回过的原文按字面隐去，其余按规则隐去。"""
    return _secret.clean(_vault.redact_revealed(text))


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


# nyx.json 的键 → 读设置时用的名字（同名环境变量设了的话优先）
_PROFILE_SETTINGS = (("semantic", "NYX_EMBED"), ("embedding_model", "NYX_EMBED_MODEL"),
                     ("embedding_api_url", "EMBEDDING_API_URL"), ("rerank", "NYX_RERANK"),
                     ("vault", "NYX_VAULT"), ("vault_reveal", "NYX_VAULT_REVEAL"), ("vault_kinds", "NYX_VAULT_KINDS"),
                     ("l3_llm", "NYX_L3_LLM"), ("lang", "NYX_LANG"))


def _data_dir_in_use() -> Optional[str]:
    """这个进程已经定下的 nyx 数据目录（nexsandglass 已经 import 过才有）。"""
    import sys
    paths = sys.modules.get("nexsandglass.core.sandglass_paths")
    if paths is None:
        return None
    return os.environ.get("NEXSANDBASE_HOME") or getattr(paths, "_NB", None)


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
    _warm_thread = None
    _platform = ""
    _turn_no = 0
    _turn_message = ""
    _profile_env: Optional[dict] = None

    def __init__(self, config: dict = None):
        self._config = config or {}
        self._lock = threading.Lock()
        self._initialized = False
        self._turn_count = 0

    # ═══════ MemoryProvider 核心接口 ═══════

    @property
    def name(self) -> str:
        # 与插件目录、memory.provider 同名（v7.17.0；以前是 nexsandglass —— Hermes 只拿它做日志和去重，
        # 入口点与 memory.provider: nexsandglass 这个旧名照样认）
        return "nyx"

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
            return _t(f"Nyx data directory is not writable: {d} (set NEXSANDBASE_HOME, or data_dir in nyx.json)",
                      f"Nyx 记忆目录不可写：{d}（设置 NEXSANDBASE_HOME 或 nyx.json 的 data_dir）")
        in_use = _data_dir_in_use()
        if in_use and os.path.abspath(in_use) != os.path.abspath(d):
            # 一个进程只有一个 nyx 数据目录（模块在 import 时定下路径）。这个 profile 配的是另一个目录：
            # 不用，而不是悄悄写进别的 profile 的记忆里（v7.17.0）
            return _t(f"this Hermes process already serves the Nyx data directory {in_use} for another profile, and "
                      f"this profile's data_dir is {d}; Nyx keeps one data directory per process. Use the same "
                      "data_dir, or run this profile in its own process.",
                      f"这个 Hermes 进程已经在为另一个 profile 使用 Nyx 数据目录 {in_use}，而这个 profile 的 data_dir 是 {d}；"
                      "Nyx 一个进程只用一个数据目录。请用同一个 data_dir，或者让这个 profile 单独一个进程。")
        return ""

    def _read_config(self) -> dict:
        cfg = dict(self._config)
        nh = _hermes_cfg()
        if nh is not None:
            cfg = {**nh.load_config(self._hermes_home), **cfg}
        return cfg

    @staticmethod
    def _env_from(cfg: dict) -> dict:
        env = {e: str(cfg[k]) for k, e in _PROFILE_SETTINGS if cfg.get(k)}
        env["NYX_VIA_HERMES"] = "1"
        return env

    def _load_settings(self) -> None:
        cfg = self._read_config()
        owners = cfg.get("owner_ids", os.environ.get("NYX_OWNER_IDS", ""))
        if isinstance(owners, str):
            owners = [o for o in re.split(r"[,，\s]+", owners) if o]
        self._owner_ids = frozenset(str(o) for o in (owners or []))
        self._bots_external = _as_bool(cfg.get("bots_are_external"), True)
        self._prefetch_budget = max(0, _as_int(cfg.get("prefetch_tokens"), _PREFETCH_TOKENS_DEFAULT))
        # nyx.json 里的开关留在这个实例上，Hermes 每次调插件时绑到当前上下文（core/settings.py）。
        # v7.17.0 以前写进 os.environ：一个进程服务多个 profile 时，第一个 profile 的设置粘住所有 profile。
        # 读的时候仍然是环境变量 > nyx.json
        self._profile_env = self._env_from(cfg)

    def _settings_scope(self):
        """这个 profile 的设置。initialize 之前（Hermes 先问 is_available）按当前上下文的 profile 读 nyx.json，
        只算设置，不动 owner_ids 这些 —— 它们由 initialize 定。"""
        if self._profile_env is None:
            try:
                self._profile_env = self._env_from(self._read_config())
            except Exception as e:
                logger.warning("nyx: reading nyx.json failed (using defaults): %s", e)
                self._profile_env = {"NYX_VIA_HERMES": "1"}
        return _settings.scope(self._profile_env)

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
            self._platform = str(kwargs.get("platform") or "")
            self._load_settings()
            with _settings.scope(self._profile_env):     # 上面刚按 hermes_home 重读了设置
                self._initialize_once()

    def _initialize_once(self) -> None:
        if self._initialized:
            return
        nh = _hermes_cfg()
        if nh is not None:
            nh.apply_data_dir(self._hermes_home)
        from nexsandglass.core.sandglass_paths import validate
        validate()
        if self._can_write():
            # v7.13.2：不再同步全量重建倒排（15 万条记忆时启动卡 2.8 秒）。
            # 索引本来就按日志行数增量补齐；放到后台补，并顺手把索引和日志镜像读进内存，
            # 第一轮对话不用再现场加载（实测首轮 1.4 秒 → 与之后各轮相同）
            self._warm_async()
            try:
                from nexsandglass.core import semantic
                semantic.warm_async()    # 后台：加载嵌入模型 + 回填历史向量（无后端时空操作）
            except Exception as e:
                logger.debug("语义索引回填未启动: %s", e)
            try:
                from nexsandglass.core import rerank
                rerank.warm_async()      # NYX_RERANK 开着时后台加载重排模型（没开时空操作）
            except Exception as e:
                logger.debug("重排模型预热未启动: %s", e)
        # Cognitive Memory OS feature flag：NYX_USE_FACADE=1 时走稳定门面（默认关）
        self._use_facade = os.environ.get("NYX_USE_FACADE", "0") == "1"
        self._initialized = True
        logger.info("NexSandglass MemoryProvider initialized (context=%s, owners=%d, facade=%s)",
                    self._agent_context, len(self._owner_ids), self._use_facade)

    def _can_write(self) -> bool:
        return (self._agent_context or "") in _WRITE_CONTEXTS

    @staticmethod
    def _warm() -> None:
        """增量补齐索引 + 把倒排与日志镜像读进内存 + 一次预热召回（连接、Déjà Vu、时序表）。"""
        t0 = time.time()
        try:
            from nexsandglass.core import sandglass_sqlite, journal_mirror
            from nexsandglass.features import sandglass_vault
            sandglass_sqlite.sync_incremental()
            sandglass_vault._sync_index()
            journal_mirror.records(sandglass_vault._SANDGLASS)
            from nexsandglass.runtime.orchestrator import get_orchestrator
            get_orchestrator().recall("记忆预热", token_budget=200)
            logger.info("Nyx 预热完成：%.0f ms", (time.time() - t0) * 1000)
        except Exception as e:
            logger.warning("Nyx 预热失败（不影响使用，首轮查询会现场加载）: %s", e)

    def _warm_async(self) -> None:
        try:
            from agent.memory_provider import spawn_context_thread
            t = spawn_context_thread(self._warm, name="nyx-warm")
        except Exception:
            t = threading.Thread(target=self._warm, name="nyx-warm", daemon=True)
        t.start()
        self._warm_thread = t

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
            mood = _mood(ent)

            # 偏移方向
            off_label = _OFFSET_LABELS.get(off.get('direction', ''), 'steady')
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
                        scene_text = f"Current scene: {', '.join(scenes[:3])}"
                except Exception:
                    pass

            persona_parts = []
            if persona_text:
                persona_parts.append(persona_text)
            if scene_text:
                persona_parts.append(f"📍 {scene_text}")
            persona_layer = "\n".join(persona_parts)

            # ═══════ 第二层：你在往哪走 ═══════
            layer2 = ["[Where you are heading]"]
            if off_label != "steady":
                layer2.append(f"💰 leaning {off_label} ({off_pct:+d}%)")
            else:
                layer2.append("💰 decisions steady")

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
                layer2.append(f"📋 Recent: {'; '.join(decisions)}")

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

            if mood != "steady":
                layer2.append(f"🎭 Mood: {mood}")

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
                header = "[Unfinished]"
                if tasks:
                    layer4.append(header)
                    layer4.append("To do:")
                    layer4.extend(f"  {i+1}. {t}" for i, t in enumerate(tasks))
                if rules:
                    if not tasks:
                        layer4.append(header)
                    layer4.append("Rules:")
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
                if persona_layer: fb.append("[Who you are]\n" + persona_layer)
                if offset_layer: fb.append("[Where you are heading]\n" + offset_layer)
                if thread_layer: fb.append("[How you got here]\n" + thread_layer)
                if open_loops_layer: fb.append("[Unfinished]\n" + open_loops_layer)
                body = "\n\n".join(fb).strip()

            tail = f"Nyx memory: {total} entries | stage: {stage}"
            warn = self._health_warning()
            if _vault.level() == "on":
                tail += ("\n`#vN` in a memory is a secret-vault entry id. Call nyx_secret_reveal only when the user "
                         "explicitly asks to see the secret; never guess, reconstruct or repeat it.")
            return (body + "\n\n" + tail + (("\n" + warn) if warn else "")).strip()
        except Exception:
            logger.warning("system_prompt_block 整体失败", exc_info=True)
            return "Nyx memory is ready. Use sandglass_search to search it."

    @staticmethod
    def _health_warning() -> str:
        """语义索引自检失败时给 agent 的一行提醒（v7.13.3）。

        这一路曾经静默失效一个月：失败只进日志，没有人看日志。提醒放在系统提示里，
        agent 会看到并转告主人；全量自检较慢（要扫整份日志），这里只查最容易静默坏掉的那一项。
        """
        try:
            from nexsandglass.core import semantic
            hc = semantic.health_check()
        except Exception as e:
            return f"⚠️ Nyx health: could not read the semantic index status ({e}). Ask the user to run `{_hints.cmd('doctor')}`."
        if hc.get("ok"):
            return ""
        why = "; ".join(hc.get("problems") or [])[:200]
        return (f"⚠️ Nyx health: semantic search is unavailable ({why}). Lexical recall still works. "
                f"Ask the user to run `{_hints.cmd('doctor')}`, or call nyx_health for details.")

    def _signal_line(self) -> str:
        """偏移 + 情绪：主注入已有全貌，这里只给最动态的信号。"""
        try:
            from nexsandglass.features.sandglass_think import comprehensive_offset, _emotional_entropy
            off = comprehensive_offset()
            ent = _emotional_entropy()
            mood = _mood(ent)
            off_d = _OFFSET_LABELS.get(off.get('direction', ''), 'steady')
            return f"## Now\nDecision drift: {off_d} ({off.get('offset', 0):+d}%) | Mood: {mood}\n"
        except Exception:
            return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """按**本轮**问题召回相关记忆 + 当前信号。

        官方建议「后台召回、这里返回缓存」是为远程 provider 设计的；Nyx 的召回是本地的
        （实测 10–30ms），直接对本轮问题召回，比用上一轮的问题预取更准。
        """
        self._last_recall = None           # recall_status 只反映最近这一次
        parts: List[str] = []
        query = _clean(query)              # 召回链路里可能有外部服务（嵌入 API、LLM 理解）：密钥不出去（v7.14.0）
        if not is_trivial_prompt(query):
            # 预热还没做完就等它：同时现场加载同一份索引只会互相抢 —— 实测首轮从 1.1 秒变成 3 秒
            warm = getattr(self, "_warm_thread", None)
            if warm is not None and warm.is_alive():
                warm.join(_as_int(os.environ.get("NYX_WARM_WAIT_SECONDS"), 10))
            try:
                from nexsandglass.runtime.orchestrator import get_orchestrator
                mc = get_orchestrator().recall(query, token_budget=self._prefetch_budget or 1)
                objs = [o for o in (mc.objects or []) if o.content and str(o.content).strip()]
                lines = [str(o.content) for o in objs] or \
                        [s for s in (mc.strings or []) if s and str(s).strip()]
                cap = max(12, self._prefetch_budget // 100)   # 600 → 12 条；4000 → 40 条
                from nexsandglass.runtime import intent as _intent
                cap = max(cap, _intent.breadth_lines(query))   # 汇总 / 时长 / 先后：答案分散在几次对话里（v7.13.7）
                if self._prefetch_budget and lines:
                    dates = _said_on(objs[:cap])
                    parts.append("## Nyx memory (relevant to this turn; [date] = when it was said)")
                    for i, s in enumerate(lines[:cap]):
                        d = dates.get(i)
                        parts.append(f"- [{d}] {s[:300]}" if d else f"- {s[:300]}")
                    withheld = len(getattr(mc, "withheld", None) or [])
                    if withheld:
                        parts.append(f"({withheld} more withheld: their source looked unsafe)")
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
        """记下这一轮是谁写的（多人会话里 sync_turn 可能拿不到 turn_author）；核对保险箱的确认短语。"""
        self._turn_no = int(turn_number or 0)
        self._turn_message = message if isinstance(message, str) else ""
        if _vault.level() == "on":
            _vault.check_confirm(self._session_id, self._turn_message, self._turn_no)
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
        # 密钥 / 密码在进入任何存储之前隐去：日志、中枢、engram、影子沙、图谱、向量都只见到占位符（v7.14.0）
        user_content, assistant_content = self._scrub_turn(user_content, assistant_content)
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
    def _scrub_turn(user_content: str, assistant_content: str):
        """保险箱开着时：用户一侧的密钥收进保险箱（占位符带 #vN）；助手回复里只认已有的编号，不新收。"""
        user_content = _vault.redact_revealed(user_content)
        assistant_content = _vault.redact_revealed(assistant_content)
        if _vault.level() != "on":
            return _secret.clean(user_content), _secret.clean(assistant_content)
        col = _vault.Collector()
        user_content = _secret.scrub(user_content, vault=col).text
        col.finish(user_content)
        assistant_content = _secret.scrub(assistant_content, vault=_vault.Collector(only_lookup=True)).text
        return user_content, assistant_content

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
        from nexsandglass.core.understand import relation_key, RELATION_EN
        rel = relation_key(args.get("relation"))            # lives_in / 住在 都认（v7.16.0）
        keep = ("subject", "relation", "object", "valid_from", "valid_until", "valid_basis",
                "recorded_at", "closed_at", "source_mem_id")
        slim = lambda rows: [{**{k: r.get(k) for k in keep},
                              "relation": RELATION_EN.get(r.get("relation"), r.get("relation"))} for r in rows]
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
            return tool_error("nyx_forget needs mem_id, or contains with at least 2 characters")
        preview = facade.forget({**sel, "dry_run": True})
        if not preview.get("ok"):
            return tool_error(f"nyx_forget preview failed: {preview.get('error')}")
        n = int(preview.get("selected") or 0)
        if not _as_bool(args.get("confirm"), False) or n == 0:
            return json.dumps({"status": "preview", "would_forget": n,
                               "items": (preview.get("preview") or [])[:10],
                               "next": "call again with confirm=true; the memories move to the quarantine and nyx_restore can bring them back"},
                              ensure_ascii=False, default=str)
        if n > _FORGET_MAX:
            return tool_error(f"{n} memories selected, more than the agent limit of {_FORGET_MAX}; "
                              "narrow it down, or ask the user to do it from the CLI")
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
            return tool_error("nyx_restore needs mem_id")
        return json.dumps(facade.restore(mid), ensure_ascii=False, default=str)

    def _handle_quarantine(self, args: dict) -> str:
        from nexsandglass.core import erasure
        items = erasure.quarantine_list(limit=max(1, min(_as_int(args.get("limit"), 20), 200)))
        return json.dumps({"items": items}, ensure_ascii=False, default=str)

    # ═══════ 密钥保险箱（v7.15.0） ═══════

    def _vault_context_error(self) -> str:
        if not self._can_write():
            return f"only the primary agent can use the vault (this is {self._agent_context})"
        if _as_bool(os.environ.get("HERMES_CRON_SESSION"), False):
            return "the vault is not available in scheduled tasks (the user is not there)"
        return ""

    def _handle_secret_list(self, args: dict) -> str:
        why = _vault.unavailable_reason() or self._vault_context_error()
        if why:
            return json.dumps({"status": "unavailable", "reason": why}, ensure_ascii=False)
        items = [e.public() for e in _vault.entries(str(args.get("query") or ""))][-50:]
        return json.dumps({"items": items, "note": "No secrets here. To show one to the user, call nyx_secret_reveal(id)."},
                          ensure_ascii=False)

    @staticmethod
    def _hermes_approval(e) -> Optional[bool]:
        """Hermes 自带的人工审批（tools/approval.py:request_tool_approval，模型跳不过去）。

        None = 用不了（不在 Hermes 里 / 旧版本 / 主人用 --yolo、/yolo、approvals.mode: off 关掉了审批 ——
        关掉命令审批不等于同意交出密钥，这时改走确认码）。rule_key 每次都是新的：审批界面上有「本会话 / 永远允许」，
        固定 key 的话点一次「永远」，以后的取回就都不问了。
        """
        try:
            from tools import approval as ap
        except Exception:
            return None
        if not (hasattr(ap, "request_tool_approval") and hasattr(ap, "is_approval_bypass_active")):
            return None
        try:
            if ap.is_approval_bypass_active():
                return None
            tail = f" {e.public()['tail']}" if e.last4 else ""
            r = ap.request_tool_approval(
                "nyx_secret_reveal", _t(f"Show vault entry v{e.id} ({e.label}{tail}, saved {e.created_at[:10]})",
                                        f"显示保险箱 v{e.id}（{e.label}{tail}，{e.created_at[:10]} 存）的原文"),
                rule_key=f"nyx_vault:{secrets.token_hex(8)}")
            return bool((r or {}).get("approved"))
        except Exception as ex:
            logger.warning("Hermes 审批出错，按拒绝处理: %s", ex)
            return False

    def _handle_secret_reveal(self, args: dict) -> str:
        sid, plat = self._session_id, self._platform
        vid = _vault.parse_id(args.get("id"))
        mode = _vault.reveal_mode()

        def out(status: str, **kw) -> str:
            return json.dumps({"status": status, **kw}, ensure_ascii=False)

        def deny(reason: str) -> str:
            _vault.audit("reveal", vid, "denied", reason, mode, plat)
            return out("denied", reason=reason)

        why = _vault.unavailable_reason()
        if why:
            return out("unavailable", reason=why)
        if vid is None:
            return tool_error("nyx_secret_reveal needs an id such as v3 (find it with nyx_secret_list)")
        why = self._vault_context_error()
        if why:
            return deny(why)
        ok_code = _vault.confirmed(sid, vid, self._turn_no)
        if not ok_code and not _asked_for_secret(self._turn_message):
            return deny("the user did not ask to see the secret in this turn; call this only when they explicitly ask")
        e = _vault.entry(vid)
        if e is None:
            return deny(f"there is no v{vid} in the vault")
        if e.state != "live":
            return deny(e.public()["state"])
        if _vault.locked(sid):
            return deny("too many wrong confirmation phrases; try again in 15 minutes")
        ok, why = _vault.rate_ok()
        if not ok:
            return deny(why)
        cli = _hints.cmd("vault", f"show v{vid}")
        if mode == "cli" or (mode == "direct" and not _vault.hook_registered()):
            note = ("By configuration, secrets are shown only in the terminal. " if mode == "cli" else
                    "Direct reveal needs the companion plugin: hermes plugins enable nyx-reveal, then restart Hermes. "
                    "Until then, secrets are shown only in the terminal. ")
            _vault.audit("reveal", vid, "use_cli", note, mode, plat)
            return out("use_cli", command=cli, note=note + "Tell the user this command.")
        if not ok_code:
            approved = self._hermes_approval(e)
            if approved is False:
                return deny("the user did not approve (declined or timed out)")
            if approved is None:
                phrase = _vault.confirm_phrase(vid, _vault.new_code(sid, vid))
                _vault.audit("reveal", vid, "need_confirm", "", mode, plat)
                if mode == "direct":
                    _vault.queue_notice(sid, _t(f"🔐 To show v{vid} ({e.label}), reply within 5 minutes with: {phrase}",
                                                f"🔐 要显示 v{vid}（{e.label}），请在 5 分钟内回复：{phrase}"))
                    return out("need_confirm", note="The user has to confirm. nyx appends the confirmation phrase to the end "
                                                    "of your reply (you cannot see the code); ask the user to reply with "
                                                    "that phrase. Never make up a code.")
                return out("need_confirm", phrase=phrase,
                           note="The user has to confirm: show them this phrase exactly and ask them to reply with it "
                                "within 5 minutes. Never reply on their behalf.")
        _vault.consume_confirmation(sid, vid)
        try:
            value = _vault.get(vid)
        except _vault.VaultError as ex:
            return deny(str(ex))
        _vault.mark_revealed(vid)
        _vault.remember_revealed(vid, e.label, value)
        _vault.audit("reveal", vid, "ok", "", mode, plat)
        if mode == "direct":
            h = _vault.queue_reveal(sid, e, value)
            return out("queued", handle=h,
                       note="The secret does not pass through you. Put this handle in your reply verbatim; nyx replaces "
                            "it with the secret before the reply reaches the user. Never repeat or guess the secret.")
        return out("value", id=f"v{vid}", label=e.label, value=value,
                   note="Pass it to the user, and remind them that this value also went to the model provider and "
                        "stays in the session log.")

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
                    content = _clean(str(msg.get("content", "") or ""))   # 先隐去再截断：截断会切开 token
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
            task, result = _clean(str(task or "")), _clean(str(result or ""))
            get_orchestrator().observe(f"Delegated: {task[:200]} → result: {result[:500]}", source="agent")
        except Exception as e:
            logger.debug("on_delegation 失败: %s", e)

    # ═══════ 工具暴露 ═══════

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        extra = _VAULT_TOOL_SCHEMAS if _vault.level() == "on" else []
        return [dict(s) for s in _TOOL_SCHEMAS + extra]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any] = None, **kwargs) -> str:
        name = tool_name
        args = args or {}
        try:
            if name in _WRITE_TOOLS or (name == "fact_store" and args.get("action") == "add"):
                if not self._can_write():
                    return tool_error(f"{name}: Nyx is read-only in the {self._agent_context} context")

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
                return json.dumps({"wind": wind, "direction": "positive" if wind > 0 else ("negative" if wind < 0 else "neutral")}, ensure_ascii=False)

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
            if name == "nyx_secret_list":
                return self._handle_secret_list(args)
            if name == "nyx_secret_reveal":
                return self._handle_secret_reveal(args)
            if name == "nyx_health":
                if _as_bool(args.get("quick"), False):
                    from nexsandglass.core import semantic
                    return json.dumps({"checks": {"semantic_index": semantic.health_check()}},
                                      ensure_ascii=False, default=str)
                from nexsandglass import doctor
                return json.dumps(doctor.run(), ensure_ascii=False, default=str)

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
            text = f"[{action}] {target}: {_clean(content or '')[:200]}"
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
             "description": _t("Where Nyx keeps its memory. By default the same data the Nyx MCP server and CLI use; "
                               "the NEXSANDBASE_HOME environment variable wins. Restart after changing it.",
                               "Nyx 记忆目录。默认与 nyx 的 MCP / CLI 共用同一份记忆；"
                               "环境变量 NEXSANDBASE_HOME 优先。改动后重启生效。")},
            {"key": "prefetch_tokens", "type": "integer", "default": _PREFETCH_TOKENS_DEFAULT,
             "minimum": 0, "maximum": 4000, "step": 100,
             "description": _t("Token budget for the memories recalled into each turn; 0 = only the drift / mood signal.",
                               "每轮自动召回注入的预算（token）；0 = 只注入偏移/情绪信号。")},
            {"key": "semantic", "type": "text", "default": "auto", "choices": ["auto", "local", "api", "off"],
             "description": _t("Semantic search (finds paraphrases too). auto: an embedding API if configured, else a "
                               "local model if sentence-transformers is installed (pip install 'nyx-memory[vector]'), "
                               "else off.",
                               "语义检索（同义改写也能召回）。auto：配了 API 用 API，装了 "
                               "sentence-transformers（pip install 'nyx-memory[vector]'）用本地模型，都没有则关闭。")},
            {"key": "embedding_model", "type": "text", "default": "",
             "description": _t("Embedding model. Local default paraphrase-multilingual-MiniLM-L12-v2 (multilingual); "
                               "API default text-embedding-3-small. Changing it rebuilds the vectors in the background.",
                               "嵌入模型名。本地默认 paraphrase-multilingual-MiniLM-L12-v2（中英混合）；"
                               "API 默认 text-embedding-3-small。换模型后旧向量自动作废并在后台重建。")},
            {"key": "embedding_api_url", "type": "text", "default": "",
             "description": _t("OpenAI-compatible /embeddings URL (optional; used when semantic is api or auto).",
                               "OpenAI 兼容 /embeddings 地址（选填；semantic=api 或 auto 时使用）。")},
            {"key": "embedding_api_key", "type": "text", "secret": True, "env_var": "EMBEDDING_API_KEY",
             "description": _t("Key for the embedding API (kept by Hermes, not written to nyx.json).",
                               "嵌入 API 的密钥（存进 .env，不写 nyx.json）。")},
            {"key": "rerank", "type": "text", "default": "off", "choices": ["off", "local"],
             "description": _t("Rerank recalled memories with a local multilingual cross-encoder (needs [vector]; about "
                               "470 MB on first run). +4.7 points LoCoMo evidence recall, but worse on preference "
                               "questions and 0.3–1.5 s slower per turn on CPU. Off by default.",
                               "召回后用本地多语言交叉编码器重排（需要 [vector]，首次下载约 470MB）。LoCoMo 证据召回 +4.7 个点，"
                               "但偏好类问题变差、每轮召回多 0.3–1.5 秒（CPU）。默认关。")},
            {"key": "vault", "type": "text", "default": "off", "choices": ["off", "on"],
             "description": _t("Secret vault: tokens, API keys and passwords pasted in chat are stored encrypted and "
                               "handed back after the user confirms (needs pip install 'nyx-memory[vault]'). "
                               "Off = only hidden, the value is dropped. See docs/secret-vault.md.",
                               "密钥保险箱：聊天里贴的 token / API key / 密码加密存起来，主人要时经确认交还"
                               "（需要 pip install 'nyx-memory[vault]'）。默认关 = 只隐去、原文丢掉。见 docs/secret-vault.md。")},
            {"key": "vault_reveal", "type": "text", "default": "direct", "choices": ["direct", "cli", "llm"],
             "description": _t("How secrets come back: direct = the value never passes through the model (needs the "
                               "companion plugin: hermes plugins enable nyx-reveal); cli = terminal only; llm = the "
                               "model relays it (the provider and the session log see it).",
                               "怎么取回：direct = 原文不经过模型（要启用配套插件：hermes plugins enable nyx-reveal）；"
                               "cli = 只在终端里取；llm = 原文交给模型转述（模型服务商和会话记录都会看到）。")},
            {"key": "vault_kinds", "type": "text", "default": "all", "choices": ["all", "tokens"],
             "description": _t("What to keep: all = tokens / API keys / private keys / passwords; tokens = no passwords.",
                               "收哪些：all = token / API key / 私钥 / 密码；tokens = 不收密码。")},
            {"key": "l3_llm", "type": "text", "default": "off", "choices": ["off", "on"],
             "description": _t("Let the L3 analyses (profile, decision drift, ghost decisions) call DeepSeek or "
                               "OpenRouter (DEEPSEEK_API_KEY / OPENROUTER_API_KEY). Sends related memory snippets, the "
                               "profile and recent decisions to them. Off by default; before v7.15.2 a key alone was enough.",
                               "画像 / 偏移率 / 幽灵决策这些 L3 分析调用 DeepSeek 或 OpenRouter（用 DEEPSEEK_API_KEY / "
                               "OPENROUTER_API_KEY）。会把相关的记忆片段、画像、近期决策发给它们。默认关；v7.15.2 以前有 key 就用。")},
            {"key": "lang", "type": "text", "default": "auto", "choices": ["auto", "en", "zh"],
             "description": _t("Language of Nyx's terminal output and these settings: auto = follow the system locale, "
                               "en = English, zh = Chinese. What the model sees is always English.",
                               "Nyx 终端输出与这些设置说明的语言：auto = 跟随系统语言，en = 英文，zh = 中文。"
                               "给模型看的始终是英文。")},
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        nh = _hermes_cfg()
        if nh is None:
            raise RuntimeError("nexsandglass_hermes is not available; cannot save the config")
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



# Hermes 调进来的每个入口都在这个 profile 的设置里运行（core/settings.py）。后台线程用 spawn_context_thread
# 起，带着同一份上下文；Hermes 的预取 / 同步线程也是。
_PROFILE_SCOPED = ("is_available", "unavailable_reason", "initialize", "system_prompt_block", "prefetch",
                   "queue_prefetch", "recall_status", "on_turn_start", "sync_turn", "shutdown", "on_session_end",
                   "on_session_switch", "on_delegation", "get_tool_schemas", "handle_tool_call", "on_memory_write",
                   "on_pre_compress", "get_config_schema", "save_config", "backup_paths", "identity_signature")


def _in_profile_scope(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._settings_scope():
            return fn(self, *args, **kwargs)
    return wrapper


for _m in _PROFILE_SCOPED:
    setattr(NexSandglassProvider, _m, _in_profile_scope(getattr(NexSandglassProvider, _m)))
del _m

# ── 插件自动发现入口 ──
def register(ctx) -> None:
    """Hermes 插件加载入口——注册 Provider。新部署用 nexsandglass_hermes（先定数据目录再 import）。"""
    provider = NexSandglassProvider()
    ctx.register_memory_provider(provider)
