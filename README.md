# NexSandglass / Nyx（夜神）

> **夜神 Nyx — Hermes Agent 的跨会话记忆感知系统**

NexSandglass 是 Hermes Agent 的记忆基础设施，在 Hermes 原生 memory 工具关闭时接管全部跨会话记忆、事实存储、联想检索和 déjà vu 检测。

![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python) ![License](https://img.shields.io/badge/License-MIT-green) ![Version](https://img.shields.io/badge/version-7.3-blue)

---

## 📋 核心能力

| 能力 | 模块 | 说明 |
|------|------|------|
| 🧠 **沙漏 Sandglass** | `core/sandglass_sqlite.py` | 长期记忆存储、全文搜索、语义搜索 |
| 🕸️ **织线 Thread** | `features/weavethread.py` | 知识图谱（实体关系三元组），支持时间窗口查询 |
| 👻 **Déjà Vu (Veil)** | `interfaces/nyx.py` | 模糊感知——"感觉聊过但检索不到"的 Bloom Filter 检测 + 寻回（nyx_hunt） |
| 🏜️ **影子沙 Fact Store** | `features/shadow_sand.py` | 结构化事实存储（带信任评分） |
| 📊 **情绪/画像** | `core/emotion_vocab.py`, `l3/persona_l3.py` | 用户状态追踪、偏移率计算、回音折 |
| 🌙 **梦境 Dream** | `dream/` | 夜间多阶段复盘：记忆整理、反思成长、创造联结 |
| 🔍 **语义检索** | `core/embedding_provider.py`, `core/vector_search.py` | 真正的向量语义检索（本地多语言模型 + RRF 混合，v3.4.0） |
| 🤖 **LLM 图谱抽取** | `core/llm_extract.py`, `features/weavethread.py` | 可选 LLM 知识图谱补充抽取 + 实体归一化（可降级，v3.4.0） |
| 🔌 **MCP 接口** | `interfaces/sandglass_mcp.py`, `interfaces/nyx.py` | MCP 工具接入 Hermes / Claude |
| 🧬 **记忆加工** | `engram/types.py`, `engram/decay.py` | Tulving 四类记忆 + Ebbinghaus 衰减（EngramTide 融合） |
| 🧬 **差异化写入** | `engram/writer.py` | semantic 覆盖 / emotional 强化 / procedural 去重 / episodic 直插 |
| 🧬 **Constitutional** | `engram/context.py` | 记忆融入 system prompt 隐性影响（自然无痕） |
| 🔄 **记忆自我演化** | `engram/evolve.py`, `engram/loops/` | 四闭环：事实↔图谱、梦境→加工、画像→上下文、召回→重要性 |
| 🌙 **梦境管线** | `engram/dream_pipeline.py`, `engram/prompts/` | hypnos 三女神融合：浅睡总结→深睡内化→灵感联结 |
| 🎛️ **热路径收编** | `runtime/orchestrator.py` | NyxOrchestrator：写入/召回统一入口，包住底层引擎 |
| 🧭 **意图召回** | `runtime/intent.py` | MemoryIntent 自适应召回（v5.0），语义/时间/领域/关系感知排序 |
| 📦 **记忆 Bundle** | `runtime/bundle.py` | MemoryBundle 合并两套出口（Constitutional + system_prompt），10 槽位 |
| 🌱 **候选晋升** | `runtime/promotion.py` | "什么值得记住"——Observation→Extract→Score→Type→Promote/Session/Drop |
| 🕰️ **时序事实** | `engram/loops/temporal_fact.py` | current_only / as_of / history_of 演变链，不静默覆盖 |
| 🌙 **Consolidation Engine** | `runtime/consolidation.py` | Dream 生产化：Proposal→Validator→Apply/Quarantine + 快照回滚 |
| 🧭 **Cognitive OS 端到端** | `runtime/eval.py`, `orchestrator.cognitive_recall` | Formation→Store→Dream→Intent→Bundle→Context→Agent 全链路 |

---

## ✨ 新增能力（Phase 0-7, v4-v7）

> 在原有记忆基础设施之上，新增的"认知记忆"层次，解决 **What to remember / What to recall now / How memory changed** 三问。

### 🎛️ 稳定门面 + 热路径收编（Phase 0-1）
- `runtime/facade.py`：四个稳定操作 `observe / recall / feedback / forget`，对外不暴露实现细节
- `runtime/orchestrator.py`：`NyxOrchestrator` 收编所有热路径——写入经 `FormationRouter`，读取经 `RecallPlanner`
- 禁止新代码直写内部表（dev escape hatch 打日志）；MCP 工具仍可暴露但内部转 orchestrator

### 🧭 意图感知自适应召回（Phase 4, v5.0）
- `runtime/intent.py`：`MemoryIntent` 解析 entities / temporal / domain / relation / types / current|history
- 策略路由：ownership+vehicle+previous → 多源；熟悉但搜不到 → nyx_hunt；generic → SearchRouter 混合
- 多因子排序：semantic / temporal_validity / importance / confidence / recency / lifecycle_penalty / trust
- 意图失败自动降级 generic_semantic；**包住而非替换** SearchRouter

### 📦 MemoryBundle 合并出口（Phase 3）
- `runtime/bundle.py`：把 engram Constitutional + Provider system_prompt 两套出口统一为 10 槽位 Bundle
- `core_facts / preferences / current_thread / historical_events / related_memories / contradictions / confidence_summary / temporal_state / persona_layer / offset_layer / open_loops`
- 流水线：candidates → score → temporal → budget → compress → render；`utility = relevance/token_cost`

### 🌱 候选晋升（Phase 4 记忆选择）
- `runtime/promotion.py`：替代默认"整轮 dump"，只晋升值得长期记住的
- 复用 engram.writer 差异化策略（override/reinforce/dedup/insert）
- 重复→reinforce 不双写；冲突→conflict candidate 不静默覆盖；闲聊/OTP 不晋升

### 🕰️ 时序事实（Phase 5, v6.0）
- `engram/loops/temporal_fact.py`：`get_current / as_of / history_of / evolution_chain`
- 写入新值 → 旧值 valid_until=now + historical，新值 active + 链接；禁止静默覆盖
- Golden：2025 Tesla → 2026 Geely，现在 vs 以前答案不同

### 🌙 Dream 生产化（Phase 6, v6.5）
- `runtime/consolidation.py`：所有变更 → DreamProposal → Validator → Apply/Quarantine
- 保留 provenance（source memory ids）；destructive merge 可回滚快照
- 压缩/抽象/关系/冲突/衰减；冲突写入 Bundle.contradictions；异步调度不阻塞热路径

### 🧭 Cognitive OS 端到端（Phase 7, v7.0）
- `orchestrator.cognitive_recall()`：Formation→Store/Graph→Dream async→Intent Recall→Bundle→Context→Agent
- `runtime/eval.py`：formation P/R、temporal accuracy、token utility、p95 latency
- MCP 统一入口 `memory_observe / memory_recall / memory_feedback / memory_forget`（旧工具保留作适配层）

---

## 📦 包结构

```
nexsandglass/
├── core/                        # 基础设施
│   ├── embedding_provider.py        # 语义向量后端（本地/外部 API，Task 1）
│   ├── vector_store.py              # 向量存储（JSON/sqlite-vec，Task 1）
│   ├── vector_search.py             # 向量语义检索 + RRF 融合（Task 1）
│   ├── search_router.py             # 检索路由器（五路混合排序）
│   ├── llm_extract.py               # 可选 LLM 抽取接口（Task 2）
│   ├── sandglass_sqlite.py          # SQLite 沙漏存储
│   ├── sandglass_paths.py           # 数据路径管理
│   ├── sandglass_archive.py         # 记忆归档
│   ├── l0_buffer.py                 # L0 缓冲层
│   ├── emotion_vocab.py             # 情绪词库
├── interfaces/                    # 对外接口
│   ├── nexsandglass.py              # 主接口
│   ├── nyx.py                       # Nyx 适配层（含 Veil Bloom Filter + Déjà Vu 寻回）
│   ├── sandglass_mcp.py             # MCP 工具
│   └── plugin.py                    # 插件接口
├── features/                      # 特性模块
│   ├── weavethread.py               # 知识图谱（正则 + 可选 LLM 抽取，Task 2）
│   ├── shadow_sand.py               # 结构化事实存储（信任评分）
│   ├── sandglass_vault.py           # 沙漏索引检索
│   ├── soul_diff.py                 # 灵魂差分导出/迁移
│   ├── nightwatch.py                # 夜间值守
│   └── ...                          # decision_particles/multi_analysis/pulse/think
├── l3/                          # L3 层（高级记忆）
│   ├── persona_l3.py                # 画像构建
│   ├── l3_search_core.py            # SimHash/搜索核心
│   └── ...                          # tasks/emotion/scene/offset/weave/arch
├── engram/                      # 🧬 EngramTide 融合层（记忆加工）
│   ├── types.py                     # Tulving 四类记忆
│   ├── decay.py                     # Ebbinghaus 衰减 + 浮现 + 激活
│   ├── writer.py                    # 差异化写入 + embedding 计算（Task 1）
│   ├── context.py                   # Constitutional 上下文
│   ├── evolve.py                    # 演化协调器
│   ├── dream_pipeline.py            # 🌙 梦境管线（hypnos 融合）
│   ├── prompts/                     # 三女神 prompt（Mnemosyne/Epimetheus/Prometheus）
│   └── loops/                       # 四闭环（事实↔图谱/梦境↔加工/画像↔上下文/召回↔重要性）
└── utils/                       # 工具
    ├── heartbeat.py                 # 心跳
    └── discipline.py                # 纪律约束```

---

## 📊 Benchmark

基于 `tests/eval/run_eval.py` 运行的记忆系统评测（测试集 60 条，top-k=5）：

| 指标 | 结果 |
|------|------|
| 词法检索召回率 | 75.00% |
| Episodic 记忆 30 天衰减后权重 | 22.31% |
| 评测脚本 | `tests/eval/run_eval.py` |

>注：混合检索（词法+RRF向量）需要安装 `[vector]` 或 `[chroma]` 额外依赖后运行。

---

## 🚀 安装

### 方式一：pip 安装（推荐）

```bash
pip install nyx-memory
```

带 MCP 支持：

```bash
pip install "nyx-memory[mcp]"
```

带向量语义检索：

```bash
pip install "nyx-memory[vector,chroma]"
```

> 包名 `nyx-memory`（PyPI）；Python 模块名仍为 `nexsandglass`（`import nexsandglass`）。

### 方式二：直接安装

```bash
# Linux / macOS
./install.sh

# Windows
install.bat
```

### 方式三：Docker

```bash
docker compose up -d
```

### 方式四：作为 Hermes 技能

技能文件位于 `skills/nyx/`，复制到 Hermes 技能目录即可自动加载：

```bash
cp -r skills/nyx ~/.hermes/skills/memory/
```

---

## 🔧 配置

`memory_bus_config.yaml`（或 Hermes `config.yaml`）示例：

```yaml
memory:
  provider: nexsandglass
  memory_enabled: true
  user_profile_enabled: true
  nyx:
    sandglass:
      backend: tfidf        # 或 chromadb
      auto_consolidate: true
    dream:
      enabled: true
      cron: "0 3 * * *"
    dejavu:
      enabled: true
      sensitivity: 0.7
```

---

## 🛠️ MCP 工具

| 工具 | 功能 |
|------|------|
| `sandglass_search` | 倒排索引关键词搜索 |
| `sandglass_semantic` | 语义搜索（TF-IDF / ChromaDB 后端） |
| `sandglass_recent` | 最近 N 条记忆 |
| `sandglass_persona` | 当前主人画像 |
| `sandglass_offset` | 当前偏移率（决策趋势） |
| `sandglass_echo` | 回音折（情感风向） |
| `sandglass_chart` | 情绪熵 ASCII 可视化 |
| `sandglass_dejavu` | Déjà Vu 模糊感知（check/stats/save_bf/awaken） |
| `sandglass_thread` | 织线知识图谱查询 |
| `sandglass_thread_graph` | 实体子图展开 |
| `sandglass_thread_weave` | 因果链摘要 |
| `sandglass_dream` | 幽灵决策（"如果选另一个选项会怎样"） |
| `sandglass_export/import` | 记忆迁移 |
| `soul_export/merge` | 灵魂差分导出/合并 |
| `fact_store` | 事实增删查（信任评分） |
| `fact_feedback` | 信任评分反馈 |
| `memory_observe` | 🆕 统一记忆写入入口（经 PromotionEngine 晋升） |
| `memory_recall` | 🆕 统一记忆召回入口（经 MemoryIntent 自适应） |
| `memory_feedback` | 🆕 统一反馈入口 |
| `memory_forget` | 🆕 统一遗忘入口 |

---

## 📖 使用示例

```python
# 搜索记忆
sandglass_search(query="荷兰 BV 公司", limit=10)

# 语义搜索
sandglass_semantic(query="如何优化网络延迟", backend="tfidf")

# 查看主人画像
sandglass_persona()

# 查询知识图谱
sandglass_thread(entity="用户", relation="偏好", limit=5)

# 展开知识子图
sandglass_thread_graph(entity="Xian Delicious Foods", depth=2)

# 幽灵决策
sandglass_dream(question="如果选择另一个方案会怎样")
```

---

## 📊 数据存储

| 数据 | 位置 | 格式 |
|------|------|------|
| 沙漏记忆 | `~/.hermes/sandglass/` | SQLite / JSONL |
| 织线图谱 | `~/.hermes/sandglass/thread/` | JSON（三元组） |
| 事实存储 | `~/.hermes/sandglass/facts/` | SQLite |
| Déjà Vu | `~/.hermes/sandglass/dejavu.bf` | Bloom Filter 持久化 |
| 梦境日志 | `~/.hermes/dreams/` | Markdown |

---

## 📝 更新日志

### v7.3 (2026-08-27) — 3 个引擎缺陷修复（Windows 沙盒测试发现）

- 🛠️ **缺陷1** `shadow_sand` 批提交策略导致同库直写死锁：`_maybe_commit` 每累积 3 次写才 commit，
  持久连接在操作间隙仍持有 `shadow_sand.db` 写锁，阻塞同库其他连接（`resolve_temporal_conflict`
  直连 `weavethread._DB`）→ `database is locked`、写入被吞。改为**每次写操作结束立即 commit**
  + `_get_conn` 加 `timeout=10` + `PRAGMA journal_mode=WAL`，移除 `_commit_pending` 计数器
- 🛠️ **缺陷2** `_Mist` 连接跨线程崩坏导致 phantom 数据静默丢失：`_drift()` 建持久连接未设
  `check_same_thread=False`，跨线程用（SearchRouter/MCP）在 Python 3.11+ 抛 `ProgrammingError`
  被 `except` 吞掉。改为 `check_same_thread=False` + `_Mist` 加 `threading.RLock` 保护
  `haunt/stalk/census` 及 `nyx_forget/cleanup/reindex` 全部 DB 操作
- 🛠️ **缺陷3** `temporal_fact` 隐藏依赖外部建表：`wthread_triples` 的 DDL 只在 `weavethread._ensure_table()`
  定义，第三方直接使用 temporal API（全新空库/未触发 weavethread）→ `no such table` 静默失败、
  读路径直接崩溃。新增参数化 `_ensure_table(db_path)` 并在 `resolve_temporal_conflict` 与
  `ensure_temporal_columns` 内调用，使模块**自足建表**；并为 `TEMPORAL_RELATIONS` 补语义 docstring
- ✅ 3 项均以独立 ad-hoc 验证脚本复现+验证（缺陷1 5/5、缺陷2 4/4、缺陷3 6/6）；全量 19 测试文件逐文件独立运行全绿

### v7.2 (2026-08-18) — DoD 收尾：5 golden scenarios + 单一事实来源

- ✅ **DoD#7** 新增 5 个 golden scenarios 自动化验收（`tests/test_engram_cognitive_os.py`）：
  A. "还是按照之前那个方案" → 找回决策/约束/结论；B. 跨 session 偏好演化（Tesla→Geely）；
  C. 闲聊不污染长期记忆（Formation 门禁）；D. token_budget 受限保留铁律/关键事实；E. 单源失败降级不崩
- ✅ 每个 golden 场景使用独立临时数据目录（`NEXSANDBASE_HOME`），不污染生产记忆、场景间不串扰
- ✅ **DoD#9** `skills/memory/nyx/SKILL.md` 收敛单一事实来源：删除历史 `scripts/engram` 副本残留引用，
  代码路径统一指向 `nexsandglass/engram`，移除"双向同步拷贝"铁律
- ✅ `test_engram_cognitive_os.py` 全绿（21 项检查 PASS）
- ✅ 9 条 Definition of Done 全部达标：唯一 API / 经 Promotion / Intent-Rank 召回 / Bundle 唯一出口 /
  temporal current vs history / Dream Proposal / golden scenarios / 无双写与 NameError / 无第二套实现

### v7.1 (2026-08-12) — runtime 脚手架修复（B0 契约 + 7 阻断 bug）

- 🛠️ **Bug1** `system_prompt_block` open_loops NameError → 不再静默 fallback 旧拼接（改用 `open_loops_layer`）
- 🛠️ **Bug2** `cognitive_recall` 误调 `intent.history_context` 实例方法 → 改用模块级函数
- 🛠️ **Bug3** `sandglass_mcp` tools/list 补声明 `memory_observe/recall/feedback/forget` 四工具
- 🛠️ **Bug4** `RecallPlanner._adapt_nyx` 对齐 `nyx_sense/hunt` 真实返回结构（phantom whisper + 熟悉度）
- 🛠️ **Bug5** `sync_turn` 与 FormationRouter 双写 sandglass → 加 `raw_already_logged` 参数，审计日志一行一次
- 🛠️ **Bug6** `facade.feedback` 传空 memories 名存实亡 → 加载匹配行并回写权重（helpful 提升 / weaken 降低）
- 🛠️ **Bug7** 删除 `skills/nyx/scripts/` 与主包分叉的 22 个副本，单一事实来源归 `nexsandglass/`
- 📄 新增 `RUNTIME.md`（B0 唯一对外 API + MemoryContext/MemoryObject 契约）+ `PR_BUGFIX.md`
- ✅ 新增 `tests/test_runtime_{api,b1,b2,b3,bugfixes}.py`；全量 19 个测试文件独立运行全绿

### v7.0 (2026-08-12) — Cognitive Memory OS 全链路

- 🧭 **Cognitive OS 端到端**：Formation→Store/Graph→Dream async→Intent Recall→Bundle→Context→Agent
- 🧭 **评测框架** `runtime/eval.py`：formation P/R、temporal accuracy、token utility、p95 latency
- 🎛️ MCP 统一入口 `memory_observe/recall/feedback/forget`（旧工具保留适配层）

### v6.5 (2026-08-12) — Dream 生产化

- 🌙 `runtime/consolidation.py`：DreamProposal→Validator→Apply/Quarantine + 快照回滚
- 🌙 冲突写入 Bundle.contradictions；异步调度不阻塞 observe/recall

### v6.0 (2026-08-12) — 时序事实

- 🕰️ `engram/loops/temporal_fact.py` 融合查询 API：current_only / as_of / history_of / evolution_chain
- 🕰️ 不静默覆盖：旧值 historical + 新值 active + supersedes 链接

### v5.0 (2026-08-12) — 意图自适应召回

- 🧭 `runtime/intent.py`：MemoryIntent 解析 + 策略路由 + 多因子排序
- 🧭 意图失败降级 generic_semantic；包住 SearchRouter

### v4.0 (2026-08-12) — 候选晋升

- 🌱 `runtime/promotion.py`：Observation→Extract→Score→Type→Promote/SessionOnly/Drop
- 🌱 重复→reinforce；冲突→conflict candidate；闲聊/OTP 不晋升

### v3.5.0 (2026-08-12) — 稳定门面 + 热路径收编

- 🎛️ `runtime/facade.py`：observe/recall/feedback/forget 稳定门面
- 🎛️ `runtime/orchestrator.py`：NyxOrchestrator 收编热路径（FormationRouter/RecallPlanner）
- 📦 `runtime/bundle.py`：MemoryBundle 合并两套出口
- 🧬 `engram/types.py`：MemoryObject canonical schema + lifecycle 状态机

### v3.4.1 (2026-08-01) — 评测框架补全

- 📊 评测测试集扩充至 **60 条**（fact_exact 10 / fact_semantic 15 / cross_session 10 / emotional 8 / procedural 8 / tech 9）
- 📊 run_eval.py 词法对比真实接入（token 重叠检索），产出**具体数字**：
  - 首轮评测：60 条测试集词法召回率 **75%**，衰减系数 0.223（30 天 episodic）
  - 报告模板：`tests/eval/latest_report.md`
- 混合检索对比支持注入向量组件后自动启用（RRF 融合）

### v3.4.0 (2026-08-01) — 审查问题修复

- 🔍 **真正的语义检索**（Task 1）：新增 `core/embedding_provider.py`（本地 sentence-transformers + 外部 API 可插拔）、`core/vector_store.py`（JSON/sqlite-vec 后端）、`core/vector_search.py`（向量检索 + RRF 融合）；SearchRouter 新增第五路向量检索，与现有四路做混合排序；`engram/writer.py` 写入时自动计算 embedding
- 🤖 **LLM 知识图谱抽取**（Task 2）：新增 `core/llm_extract.py`（可选接口）；`features/weavethread.py` 新增 `wthread_extract_with_source`（regex/llm 来源标注）和 `wthread_extract_llm`（可降级 LLM 补充抽取）；环境变量 `WTHREAD_LLM_EXTRACTION=1` 开启，关闭时纯正则
- 📝 **文档修复**（Task 3）：README 包结构替换虚构路径（core/thread_*.py→features/weavethread.py, core/fact_store.py→features/shadow_sand.py, core/persona.py→l3/persona_l3.py）；修正 Déjà Vu 描述（明确实现在 interfaces/nyx.py 的 _Veil Bloom Filter + nyx_hunt 寻回）

### v3.3.0 (2026-07-31) — 🌙 hypnos 梦境融合

- 🌙 新增 `engram/dream_pipeline.py`：hypnos 三女神流程并入 Nyx 梦境
- 🌙 Mnemosyne 浅睡总结（prompt + 规则兜底）/ Epimetheus 深睡内化（确定性代码）/ Prometheus 灵感联结
- 🌙 新增 `engram/prompts/`：hypnos 三女神 prompt 资产入库
- 📝 README/SKILL.md 更新：梦境管线章节 + 融合设计文档

### v3.2.0 (2026-07-31) — 🔄 记忆自我演化

- 🔄 新增 `engram/loops/` 四闭环 + `engram/evolve.py` 演化协调器
- 🔄 闭环1 Thread↔FactStore：事实→图谱自动更新，图谱反向验证冲突
- 🔄 闭环2 Dream↔Engram：梦境触发重分类（≥3次提炼）/合并/关系发现
- 🔄 闭环3 Persona↔Context：画像确认事实加权，字段变更触发重建
- 🔄 闭环4 Recall↔Writer：成功召回提升重要性，长期无关加速衰减
- 📝 README/SKILL.md 更新：演化闭环章节 + 设计文档

### v3.1.0 (2026-07-31) — 🧬 EngramTide 融合

- 🧬 新增 `nexsandglass/engram/` 记忆加工层（types / decay / writer / context）
- 🧠 Tulving 四类记忆：semantic / episodic / emotional / procedural 差异化写入
- ⏳ Ebbinghaus 指数衰减：episodic/emotional 衰减，DECAY_FLOOR 永不归零
- 📤 分层浮现 R1-R4：规则 > 高唤醒情绪 > 未解决 > 近期事件
- ⚡ 逐轮激活：当前输入相似记忆 boost（封顶 1.0）
- 📜 Constitutional 上下文：记忆隐性影响回复，严禁暴露机械检索
- 📝 README 更新：包结构 + 核心能力表加入融合层

### v3.0.0 (2026-07-31)

- 🔄 重构为模块化包结构（core / interfaces / utils / features / l3）
- 🧠 技能层同步：`skills/nyx/` 完整纳入仓库（SKILL.md + 14 篇参考文档）
- 📝 README 重写：新增架构说明、MCP 工具表、使用示例
- 🐛 修复 `_fail_open` 装饰器循环导入问题
- 🔀 合并远端文件（nyx.py、PROTOCOL.md、demo/）

### 早期版本

- v2.9.9：雷军 TOCTOU 修复（Lock + 元组原子赋值）
- v2.9.9：词库自生长修复（马云 + 托尼双审）
- 基础沙漏记忆存储与检索

---

## 📄 License

MIT License

---

**Made with 🧠 by Nyx — 夜神记忆感知系统**
