# NexSandglass / Nyx（夜神）

> **夜神 Nyx — Hermes Agent 的跨会话记忆感知系统**

NexSandglass 是 Hermes Agent 的记忆基础设施，在 Hermes 原生 memory 工具关闭时接管全部跨会话记忆、事实存储、联想检索和 déjà vu 检测。

![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python) ![License](https://img.shields.io/badge/License-MIT-green) ![Version](https://img.shields.io/badge/version-3.0.0-blue)

---

## 📋 核心能力

| 能力 | 模块 | 说明 |
|------|------|------|
| 🧠 **沙漏 Sandglass** | `core/sandglass_sqlite.py` | 长期记忆存储、全文搜索、语义搜索 |
| 🕸️ **织线 Thread** | `features/weavethread.py` | 知识图谱（实体关系三元组），支持时间窗口查询 |
| 👻 **Ghost 回放** | `l3/emotion_l3.py` | 决策回放——"如果选另一个选项会怎样"的幽灵推演（entropy_ghost） |
| 🏜️ **影子沙 Fact Store** | `features/shadow_sand.py` | 结构化事实存储（带信任评分） |
| 📊 **情绪/画像** | `core/emotion_vocab.py`, `l3/persona_l3.py` | 用户状态追踪、偏移率计算、回音折 |
| 🌙 **梦境 Dream** | `dream/` | 夜间多阶段复盘：记忆整理、反思成长、创造联结 |
| 🔌 **MCP 接口** | `interfaces/sandglass_mcp.py`, `interfaces/nyx.py` | MCP 工具接入 Hermes / Claude |
| 🔍 **语义检索** | `core/embedding_provider.py`, `core/vector_search.py` | 真正的向量语义检索（Task 1，本地免费模型 + RRF 混合） |
| 🧬 **记忆加工** | `engram/types.py`, `engram/decay.py` | Tulving 四类记忆 + Ebbinghaus 衰减（EngramTide 融合） |
| 🧬 **差异化写入** | `engram/writer.py` | semantic 覆盖 / emotional 强化 / procedural 去重 / episodic 直插 |
| 🧬 **Constitutional** | `engram/context.py` | 记忆融入 system prompt 隐性影响（自然无痕） |
| 🔄 **记忆自我演化** | `engram/evolve.py`, `engram/loops/` | 四闭环：事实↔图谱、梦境→加工、画像→上下文、召回→重要性 |
| 🌙 **梦境管线** | `engram/dream_pipeline.py`, `engram/prompts/` | hypnos 三女神融合：浅睡总结→深睡内化→灵感联结 |
| 🤖 **LLM 抽取** | `core/llm_extract.py`, `features/weavethread.py` | 可选 LLM 知识图谱补充抽取（Task 2，可降级） |

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
│   ├── nyx.py                       # Nyx 适配层（记忆读写接口）
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

## 🚀 安装

### 方式一：直接安装

```bash
# Linux / macOS
./install.sh

# Windows
install.bat
```

### 方式二：Docker

```bash
docker compose up -d
```

### 方式三：作为 Hermes 技能

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
| `sandglass_ghost` | 决策回放——"如果选另一个选项"的幽灵推演 |
| `sandglass_thread` | 织线知识图谱查询 |
| `sandglass_thread_graph` | 实体子图展开 |
| `sandglass_thread_weave` | 因果链摘要 |
| `sandglass_dream` | 幽灵决策（"如果选另一个选项会怎样"） |
| `sandglass_export/import` | 记忆迁移 |
| `soul_export/merge` | 灵魂差分导出/合并 |
| `fact_store` | 事实增删查（信任评分） |
| `fact_feedback` | 信任评分反馈 |

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
| Ghost 回放 | 内存计算（无持久化文件） | 决策推演 |
| 梦境日志 | `~/.hermes/dreams/` | Markdown |

---

## 📝 更新日志

### v3.4.1 (2026-08-01) — 评测框架补全

- 📊 评测测试集扩充至 **60 条**（fact_exact 10 / fact_semantic 15 / cross_session 10 / emotional 8 / procedural 8 / tech 9）
- 📊 run_eval.py 词法对比真实接入（token 重叠检索），产出**具体数字**：
  - 首轮评测：60 条测试集词法召回率 **75%**，衰减系数 0.223（30 天 episodic）
  - 报告模板：`tests/eval/latest_report.md`
- 混合检索对比支持注入向量组件后自动启用（RRF 融合）

### v3.4.0 (2026-08-01) — 审查问题修复

- 🔍 **真正的语义检索**（Task 1）：新增 `core/embedding_provider.py`（本地 sentence-transformers + 外部 API 可插拔）、`core/vector_store.py`（JSON/sqlite-vec 后端）、`core/vector_search.py`（向量检索 + RRF 融合）；SearchRouter 新增第五路向量检索，与现有四路做混合排序；`engram/writer.py` 写入时自动计算 embedding
- 🤖 **LLM 知识图谱抽取**（Task 2）：新增 `core/llm_extract.py`（可选接口）；`features/weavethread.py` 新增 `wthread_extract_with_source`（regex/llm 来源标注）和 `wthread_extract_llm`（可降级 LLM 补充抽取）；环境变量 `WTHREAD_LLM_EXTRACTION=1` 开启，关闭时纯正则
- 📝 **文档修复**（Task 3）：README 包结构替换虚构路径（core/dejavu.py→interfaces/nyx.py, core/thread_*.py→features/weavethread.py, core/fact_store.py→features/shadow_sand.py）；明确 Ghost 回放功能（entropy_ghost 是决策推演，非模糊检索）

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
