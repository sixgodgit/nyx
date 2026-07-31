---
name: nyx
description: Nyx（夜神）记忆感知系统 — 存储层（沙漏/图谱/Déjà Vu/梦境）+ 加工层（Tulving 四类记忆/Ebbinghaus 衰减/差异化写入/Constitutional 上下文）+ 演化层（四闭环自我演化 + hypnos 梦境融合三女神管线）+ 语义层（真正的向量语义检索 + 可选 LLM 知识图谱抽取 + 时序事实冲突处理 + 量化评测框架）
tags:
  - nyx
  - memory
  - sandglass
  - dejavu
  - fact-store
  - session-search
  - knowledge-graph
  - engram
  - decay
  - constitutional
  - dream-pipeline
  - evolution
  - hypnos
---

# Nyx 记忆感知系统

> 版本：v3.4.0 | 仓库：https://github.com/sixgodgit/nyx | 协议：MIT

## 设计哲学：反馈回路 > 功能堆积

**核心洞察**：当功能数量达到边际收益递减点后，真正的价值跃迁来自**模块间反馈回路**——让记忆能够自我演化。

> 2026-08-01 代码审查发现语义检索、知识图谱抽取、文档脱节、评测缺失、时序冲突 5 类问题。审查报告与改进路线图见 [`references/code-review-2026-08-01.md`](references/code-review-2026-08-01.md)。已修复：真正的向量语义检索（embedding_provider + vector_store + vector_search + RRF 融合）、可选 LLM 知识图谱抽取（llm_extract + source 标注）、时序事实冲突处理（temporal_fact + valid_from/valid_until）。

Nyx 的架构演进遵循这一原则：

| 阶段 | 重点 | 局限 |
|------|------|------|
| v1 | 功能建设（沙漏/图谱/Déjà Vu/事实库） | 模块孤立，无反馈 |
| v2 | EngramTide 融合（四类记忆/衰减/浮现/加工） | 有加工层，但无闭环 |
| v3 | 四闭环自我演化（当前） | 模块互为输入输出 → 记忆活系统 |

**四个闭环**：
1. **Thread↔FactStore**：事实变化自动更新图谱，图谱关系反向验证事实
2. **Dream↔Engram**：梦境触发重分类/合并/关系发现
3. **Persona↔Context**：画像动态影响上下文组装
4. **Recall↔Writer**：成功召回提升重要性，长期无关记忆衰减

> 新功能在闭环上做（如新的浮现策略、新的合并规则），而非新增孤立模块。

## 数据位置与表结构

所有数据文件路径、数据库表结构、字段格式详见 [`references/data-locations.md`](references/data-locations.md)。

快速索引：
- **沙粒日志**: `/root/.hermes/nexsandglass/sandglass.txt`（`时间戳 | 发送者 | 文本`）
- **会话数据库**: `/root/.hermes/state.db`（sessions + messages 表）
- **记忆分层**: `...

---

## 📊 可视化设计规范（CRITICAL）

构建 Nyx 可视化时**必须**遵循 [`references/visualization-design-guide.md`](references/visualization-design-guide.md) 中的设计规范。违反会导致用户立即拒绝。

**核心规则**：
- 点阵连线构成沙漏（非 SVG、非几何三角）
- 物理驱动粒子（重力、湍流、阻尼、壁面反弹）
- 单元素居中（整个屏幕只有沙漏）
- 数据标注随沙流浮动
- 禁止常规仪表盘（Chart.js、metric cards、多面板）

## 🚀 生产部署

## 关键环境注意

- **CF API Key** 在 Hermes 输出中被脱敏。完整 Key 在 `/root/.hermes/.credentials`，提取方式：
```bash
grep "^CLOUDFLARE_API_KEY=" /root/.hermes/.credentials | cut -d= -f2-
```

- **⚠️ 输出压缩（ccr）**：终端和 `read_file`/`skill_view` 的大段内容会被环境压缩为 `<<ccr:...>>` 格式，无法直接阅读。
  - 小段输出（<300 字符）通常可读
  - 大段内容用 base64 编码、`head -c`、`sed -n`、Python 分块、或子代理（delegate_task）读取
  - 详情见 [`references/ccr-output-compression.md`](references/ccr-output-compression.md)
- **梦境日志**: `/root/.hermes/dreams/`（YYYY-MM-DD.md）
- **语义索引**: `/root/.hermes/nexsandglass/chroma_sand/`

## 硬性铁律：先查再答

**规则**：涉及任何具体事实、偏好、历史、用户信息、技术细节的问题，**在组织最终回答之前，必须先执行一次轻量检索**。不允许用"这次不需要"的理由跳过。

**铁律操作**（写在脑子里，不是问用户）：

```
每轮对话，如果用户的问题包含以下任何一种情况：
  - 问"我的xxx是什么/在哪里/是谁"
  - 说"之前/上次/以前/曾经"
  - 涉及个人数据（邮箱、地址、账号、密码、服务器IP、域名）
  - 涉及偏好（"我喜欢什么方式""我用什么xxx"）
  - 涉及决策记录（"为什么我选了xxx""上次怎么处理的"）
  - 涉及第三方配置（端口、域名、API key在哪）

→ 拒绝凭"我觉得我记得"来回答
→ 无条件先跑一次 Nyx 检索链：
   1. sandglass_search(query=问题关键词, limit=5)   ← 精确匹配
   2. 如果结果不够 → mcp_pre_gateway_dispatch_sandglass_semantic(query=问题, limit=3)  ← 语义联想
   3. 如果还没找到 → mcp_pre_gateway_dispatch_sandglass_dejavu(action='check', query=问题) ← 模糊感知
   4. 如果全空 → session_search(query=问题, limit=3) ← 翻会话原文
   5. 最后才是 "我不确定，需要你告诉我"
```

**例**：用户说"发到我的 QQ 邮箱" → 不能凭记忆回答 → 直接调 sandglass_search / dejavu → 查到 10537543@qq.com

---

## 启动检查（每次会话开始时自动执行）

新会话开始后的前 3 条消息之内，**无条件执行以下启动流程**，不依赖用户问题是否触发：

```python
# Step 1: 健康检查
mcp_pre_gateway_dispatch_sandglass_ping()

# Step 2: 获取最近记忆（恢复上下文）
mcp_pre_gateway_dispatch_sandglass_recent(limit=5)
# 如果最近有 active_tasks、pending_items → 主动告知用户"上次还有这些没做完"

# Step 3: 获取当前画像
mcp_pre_gateway_dispatch_sandglass_persona()

# Step 4: 获取回音折（近期情感风向，用来判断用户状态）
sandglass_echo()

# Step 5: 检查 Déjà Vu 系统状态
sandglass_dejavu(action="stats")

## 📋 发布检查清单（CRITICAL）

每次向 nyx 仓库推送前，**必须**执行以下检查：

### 1. 确认目标仓库
```bash
cd /root/nyx-repo && git remote -v
# 必须是 sixgodgit/nyx，不是 sixgodgit/hermes-memory-system（已归档只读）
```

### 2. 同步技能层副本
```bash
# 仓库 → 技能
cp -r nexsandglass/engram skills/nyx/scripts/
cp tests/test_engram_*.py skills/nyx/scripts/
# 技能 → 仓库
cp -r /root/.hermes/skills/memory/nyx/scripts/engram nexsandglass/
cp /root/.hermes/skills/memory/nyx/scripts/test_engram_*.py tests/
```

### 3. 更新 README
- 新增模块 → 包结构树加入路径
- 新增能力 → 核心能力表加入行
- 版本变更 → 更新日志加入条目
- 新功能 → 新增对应工具/接口说明

### 4. 更新 pyproject.toml 版本号
```toml
[project]
version = "X.Y.Z"  # 与更新日志一致
```

### 5. 验证 + 推送
```bash
python3 tests/test_engram_fusion.py   # 融合层
python3 tests/test_engram_loops.py    # 闭环层
git add -A && git status              # 确认变更范围
git commit -m "..."
git push origin main
```

### Pitfalls（本次会话踩过的坑）
- ❌ 推送到 `hermes-memory-system`（归档仓库，403）→ ✅ 推送到 `nyx`
- ❌ 忘记更新 README → ✅ 每次功能变更都更新
- ❌ 忘记更新 pyproject.toml 版本号 → ✅ 与更新日志同步

---

## 🏗️ 系统架构概览

```
Nyx 记忆感知系统
├── 存储层（已有）
│   ├── Sandglass 沙漏 — 长期记忆 / 全文+语义检索
│   ├── Thread 织线 — 知识图谱（实体三元组）
│   ├── Déjà Vu — Bloom Filter 模糊感知
│   ├── Session Search — 跨会话 FTS5 检索
│   ├── Fact Store — 结构化事实（信任评分）
│   └── Dream System — 夜间 8 阶段复盘
│
├── 加工层（EngramTide 融合，v3.1.0）
│   ├── engram/types.py       — Tulving 四类记忆（semantic/episodic/emotional/procedural）
│   ├── engram/decay.py       — Ebbinghaus 指数衰减 + 分层浮现 + 逐轮激活
│   ├── engram/writer.py      — 差异化写入（覆盖/强化/去重/直插）
│   └── engram/context.py    — Constitutional 上下文组装
│
├── 演化层（四闭环，v3.2.0）
│   ├── evolve.py                  — run_evolution_pass / recall_pass / fact_pass
│   └── loops/
│       ├── fact_thread.py         — 闭环1: Thread ↔ Fact Store
│       ├── dream_engram.py        — 闭环2: Dream ↔ Engram
│       ├── persona_ctx.py         — 闭环3: Persona ↔ Context
│       └── recall_writer.py       — 闭环4: Recall ↔ Writer
│
└── Hermes 集成
    ├── MCP 工具（write_memory / retrieve / run_decay / consolidate）
    ├── 梦境 cron（每日 8 点推送）
    └── 灵魂差分导出/迁移
```

详细设计文档见 references/ 目录。

---

## 故障排查与参考

### 相关技能关系
### 设计文档索引
- **EngramTide 融合设计** → [`references/engram-fusion-design.md`](references/engram-fusion-design.md)（v3.1.0 加工层）
- **演化闭环设计** → [`references/engram-evolution-design.md`](references/engram-evolution-design.md)（v3.2.0 四闭环）
- **Hypnos 重叠分析** → [`references/hypnos-overlap-analysis.md`](references/hypnos-overlap-analysis.md)（梦境系统定位）
- **梦境融合设计** → [`references/engram-dream-fusion.md`](references/engram-dream-fusion.md)（v3.3.0 三女神管线）
- **GitHub 推送避坑** → [`references/github-push-pitfall.md`](references/github-push-pitfall.md)（正确仓库 vs 归档仓库）
- **输出压缩处理** → [`references/ccr-output-compression.md`](references/ccr-output-compression.md)（大文件读取策略）
- **部署工作流** → [`references/deployment-workflow.md`](references/deployment-workflow.md)

- **模型链路验证**：当需要确认当前运行的模型版本（如判断是 preview 还是正式版）时，参见 [`references/model-chain-verification.md`](references/model-chain-verification.md)。核心结论：DeepSeek 正式版模型 API 名不变（仍为 `deepseek-v4-flash`），通过 `/v1/responses` 端点是否可用来判断后端是否已切换到正式版。详细检测方法 + GPT-5.6 价格对比见 [`references/deepseek-v4-formal-release.md`](references/deepseek-v4-formal-release.md)。
- **GitHub 推送流程**：推送前必须检查 remote 地址和 repo 是否 archived。正确 nyx 仓库是 `sixgodgit/nyx`（Python 包），而非 `sixgodgit/hermes-memory-system`（已归档）。详见 `references/github-push-workflow.md`。


- [DeepSeek V4 版本验证](references/deepseek-v4-verification.md) — 如何通过 Responses API 检测正式版
- [输出压缩（ccr）环境_workarounds](references/output-compression-workarounds.md) — 工具输出被压缩时的读取技巧

## 关键环境注意

1. **推送代码前**：始终检查 `git remote -v` + 仓库归档状态（`curl api.github.com/repos/... | python3 -c "...archived..."`）
2. **模型版本**：模型自称版本不可靠，用 Responses API 端点或官方 changelog 验证
3. **输出压缩**：大文件/输出会被 ccr 压缩，用分块输出、grep/sed 精确提取、或 tempfile 分段读取
mcp_pre_gateway_dispatch_sandglass_dejavu(action='stats')
```

上述 5 步加起来耗时 < 2 秒，tokens < 200。**不允许跳过。**

---

## 会话快照（Session Handoff）——自动转存

每次会话中，以下事件**自动触发写 sandglass**，由工具调用结果直接落地，不需要我手动判断：

| 触发事件 | 写什么 | 工具 |
|---------|--------|------|
| 完成一个明确的任务 | `[session] task_done: [任务名] → [关键结果]` | `sandglass_search` 用于确认，或用 `fact_store` 存储 |
| 用户做出一次决策 | `[session] decision: [决策内容]` | `fact_store(action='add', category='decisions', ...)` |
| 用户给出一个偏好/指令 | `[session] preference: [偏好内容]` | `fact_store(action='add', category='preferences', ...)` |
| 获取到一个关键数据(IP/密码/账号) | `[session] data: [数据类型] = [脱敏值]` | `fact_store(action='add', category='credentials', ...)` |
| 用户说"记住""记一下""别忘了" | `[session] remember: [内容]` | `fact_store(action='add', category='important', ...)` + `sandglass_search` |

---

## 核心原则

**Hermes 的 `memory` 工具已禁用**，永远不要尝试调用它。Nyx 接管以下四个维度：

| 维度 | 工具 | 用途 |
|------|------|------|
| **沙漏（长期记忆）** | `sandglass_search` / `sandglass_recent` / `sandglass_semantic` | 主记忆库，跨会话持久化 |
| **Déjà Vu（模糊感知）** | `mcp_pre_gateway_dispatch_sandglass_dejavu` | 想不起来但感觉聊过的内容 |
| **事实存储（结构化）** | `fact_store` / `fact_feedback` | 偏好、配置、关键事实 |
| **织线（知识图谱）** | `mcp_pre_gateway_dispatch_sandglass_thread` / `_thread_graph` / `_thread_add` | 实体关系推理 |
| **Session（会话回溯）** | `session_search` | 翻当前及近期会话原文 |

---

## 触发规则 — 什么情况用什么

### 1. 用户提到"还记得吗""之前说过""你忘了" → 优先走 **Déjà Vu**

```
mcp_pre_gateway_dispatch_sandglass_dejavu(action='check', query='...')
```

如果 déjà vu 返回 ghost 结果 → 再用 `sandglass_search` 或 `session_search` 细查。

### 2. 需要查具体事实/历史 → 走 **Sandglass 搜索**

```python
# 关键词搜索（精确）
sandglass_search(query="用户偏好 邮箱", limit=5)

# 语义搜索（模糊联想）
mcp_pre_gateway_dispatch_sandglass_semantic(query="邮件发送偏好", limit=5)
```

### 3. 需要查近期对话上下文 → 走 **Session Search**

```
session_search(query="发件邮箱 enfys", limit=3)
```

### 4. 需要存储一个新事实 → 走 **Fact Store**

```
fact_store(action='add', content='默认发件邮箱是 enfys@hvh.expert', category='preferences')
```

### 5. 需要关联实体关系 → 走 **织线图谱**

```
mcp_pre_gateway_dispatch_sandglass_thread(entity='enfys', relation='used_for')
mcp_pre_gateway_dispatch_sandglass_thread_add(subject='enfys', relation='send_email', object='10537543@qq.com')
```

---

## 🧬 EngramTide 融合层（记忆加工）

> 设计文档见 [`references/engram-fusion-design.md`](references/engram-fusion-design.md)。
> 代码模块：`scripts/engram/`（types / decay / writer / context），测试 `scripts/test_engram_fusion.py`。

Nyx 已融合 EngramTide 的认知科学记忆机制：**Tulving 四类记忆 + Ebbinghaus 衰减 + Constitutional 上下文**。

### 记忆四分类（Tulving 模型）

| 类型 | 含义 | 衰减 | 写入策略 |
|------|------|------|----------|
| **semantic** | 稳定事实/偏好/身份 | ❌ | 覆盖（相似 ≥0.85 旧记忆标记 superseded） |
| **episodic** | 具体事件 | ✅ 指数衰减 | 直插，不可覆盖 |
| **emotional** | 情绪色彩经历 | ✅ 慢衰减（唤醒度↑衰减↓） | 强化（相似 ≥0.85 提升权重） |
| **procedural** | 相处规则/铁律 | ❌ | 去重（相似 ≥0.90 只计访问） |

### 写记忆时的分类原则

涉及以下内容时，按类型写入（对应存储位置不变，但**标注类型**）：

- **semantic** → `fact_store(action='add', category='identity'/'preferences'/'facts')`
  - 例：邮箱、住址、身份、长期偏好
- **episodic** → `sandglass` 沙粒（事件日志）
  - 例：昨天去了市政厅、Odido 7月14日上门装光纤
- **emotional** → `fact_store(category='emotions')`，标注 arousal（0~1）
  - 例：用户对 Apple 账户安全很紧张（arousal 0.8）
- **procedural** → `fact_store(category='rules')`
  - 例：涉及个人数据必须先检索再回答

### 衰减与浮现（自动，无需手动触发）

- **衰减**：episodic/emotional 记忆随时间指数衰减（`exp(-rate×time)`），但 **DECAY_FLOOR=0.01 永不归零**。semantic/procedural 不衰减。
- **浮现优先级**：R1 procedural 规则 > R2 高唤醒 emotional > R3 unresolved > R4 近期 episodic
- **逐轮激活**：当前输入相似度高的记忆被 boost（权重 ↑，封顶 1.0）

### Constitutional 记忆（CRITICAL：自然无痕）

记忆影响回复时，**严禁暴露机械记忆系统**：

- ❌ "根据我的记忆""你上次说过""数据库显示""检索结果显示"
- ✅ 记忆像潜意识一样影响态度、语气、切入点，自然融入

四类记忆的呈现位置：
- procedural → 动态行为修正案（最高优先级）
- semantic → 基础用户信息
- episodic → 近期生活事件
- emotional → 历史情感沉淀

### 代码调用示例

```python
# 从脚本层使用（路径：scripts/engram/）
from engram import write_memory_classified, compute_decay_multiplier, build_constitutional_context

# 分类写入：semantic 覆盖 / emotional 强化 / procedural 去重 / episodic 直插
action, report, target = write_memory_classified(
    "用户住在海牙", "semantic", existing_memories
)

# 衰减计算（Ebbinghaus）
multiplier = compute_decay_multiplier("episodic", arousal=0.0, access_count=3, hours_elapsed=72)

# Constitutional 上下文组装
ctx = build_constitutional_context(retrieved, surfaced=surfaced_list)
```

---

## 🔄 GitHub 推送工作流

**正确仓库**: `sixgodgit/nyx`（Python 包，`nexsandglass/` 目录）

**推送前必检（三步）**:
```bash
# 1. 确认 remote 指向正确仓库
git remote -v
# 必须显示: origin  https://github.com/sixgodgit/nyx.git

# 2. 确认 repo 未归档（归档 = 只读，推送会 403）
gh repo view sixgodgit/nyx --json name,archived 2>/dev/null | grep archived
# 必须返回: "archived": false

# 3. 推送
git push origin main
```

**⚠️ 已知陷阱**:
- `sixgodgit/hermes-memory-system` 是**归档仓库**（只读），推它会报 `403: This repository was archived`
- Nyx 技能层（`skills/nyx/`）和仓库代码（`nexsandglass/`）双向同步：修改任一侧都要 `cp` 到另一侧
推送前先 `git add -A && git status` 确认变更范围。

**⚠️ GitHub 推送避坑（CRITICAL）**：

| 正确 | 错误 |
|------|------|
| `cd /root/nyx-repo && git push origin main` | 推到 `sixgodgit/hermes-memory-system`（已归档，403） |

- 正确仓库：`sixgodgit/nyx`（Python 包）
- 归档仓库：`sixgodgit/hermes-memory-system`（只读，禁止推送）
- 推送前**必须**确认 `git remote -v` 输出为 `sixgodgit/nyx.git`
- 详情见 [`references/github-push-pitfall.md`](references/github-push-pitfall.md)

详细工作流见 [`references/deployment-workflow.md`](references/deployment-workflow.md)。

## 本技能参考文档

| 文件 | 内容 |
|------|------|
| [`references/constitutional-memory-principles.md`](references/constitutional-memory-principles.md) | Constitutional 记忆原则（自然无痕、四类记忆呈现位置） |
| [`references/github-workflow.md`](references/github-workflow.md) | 双目录同步、推送前检查、ccr 输出压缩规避 |
| [`references/formulas-and-parameters.md`](references/formulas-and-parameters.md) | 衰减公式、浮现阈值、写入阈值、召回反馈参数 |
| [`references/engram-fusion-design.md`](references/engram-fusion-design.md) | EngramTide 融合设计（v3.1.0） |
| [`references/engram-evolution-design.md`](references/engram-evolution-design.md) | 四闭环自我演化设计（v3.2.0） |

## 强制执行的场景（不得使用 Hermes memory）

| 场景 | 以前的做法（已禁用） | 现在的做法（Nyx） |
|------|---------------------|------------------|
> 设计文档见 [`references/engram-evolution-design.md`](references/engram-evolution-design.md)。
> 代码：`scripts/engram/loops/`（四个闭环）+ `scripts/engram/evolve.py`（协调器）。
> 测试：`scripts/test_engram_loops.py`（15 项）。

Nyx 核心竞争力：**记忆能够自我演化**。四个闭环让模块互为输入输出：

### 闭环 1：Thread ↔ Fact Store（事实 ↔ 图谱）

- **事实变化自动更新图谱**：新事实入库 → 提取三元组 → 写入织线
- **图谱反向验证事实**：新事实入库前校验 → confirm / conflict / unknown
- 使用：写入事实时走 `fact_pass`（先验证再同步图谱）

### 闭环 2：Dream ↔ Engram（梦境 → 记忆加工）

- **重分类**：同一 episodic 事件出现 ≥3 次 → 提炼为 semantic 稳定事实
- **合并**：相似 episodic/emotional 记忆合并（贪心配对）
- **关系发现**：从记忆中提取新图谱关系写入织线
- 使用：梦境 cron 调用 `run_evolution_pass`

### 闭环 3：Persona ↔ Context（画像 → 上下文）

- **画像加权**：画像确认的 semantic 事实 → Context 组装时权重 +0.3
- **变更重建**：画像字段变更（如邮箱）→ 旧记忆标记重建候选
- 使用：组装 Constitutional 上下文前调用 `persona_weight_context`

### 闭环 4：Recall ↔ Writer（召回 → 重要性）

- **召回反馈**：成功召回的记忆 access_count+1、权重 +0.05（短期重要性）
- **老化降权**：>30 天未访问 → 加速衰减；>90 天从未召回且权重 <0.05 → 归档候选
- 使用：每次检索命中后调用 `recall_pass`；每日维护调用 `age_and_demote`

### 演化协调器（唯一入口）

```python
# 梦境/每日维护：一次完整演化回合
run_evolution_pass(memories, persona_entries, thread_store, thread_query)
# 检索后：召回反馈
recall_pass(memories, recalled_ids)
# 事实写入：验证 + 图谱同步
fact_pass(fact_text, thread_store, thread_query)
```

### 🌙 梦境管线（hypnos × engram 融合）

> 设计文档见 [`references/engram-dream-fusion.md`](references/engram-dream-fusion.md)。
> 代码：`scripts/engram/dream_pipeline.py` + `scripts/engram/prompts/`（三女神 prompt）。
> 测试：`scripts/test_engram_dream.py`（7 项）。

hypnos-dream-system 的三女神流程已并入 Nyx 梦境，消除重叠：

| 阶段 | 女神 | 职责 | 实现层 |
|------|------|------|--------|
| Phase 1 | **Mnemosyne** 记忆女神 | 浅睡总结（已完成/未完成/新知事实/待确认） | prompt（`prompts/01-mnemosyne-nrem.md`）+ 规则兜底 |
| Phase 2 | **Epimetheus** 后见之神 | 深睡内化：重分类≥3次 / 合并 / 关系发现 | **确定性代码**（engram 闭环 2） |
| Phase 3 | **Prometheus** 先见之神 | 快速眼动：emotional → 跨域灵感联结 | prompt（`prompts/03-prometheus-rem.md`）+ 代码兜底 |
| Phase 4 | 演化联动 | 关系写图谱 + 老化降权 | engram 闭环 1/4 |

**调用**：

```python
from nexsandglass.engram import run_dream_pipeline

evolved, report = run_dream_pipeline(
    memories, day_log,
    thread_store=wthread_add,     # 织线写入回调
    llm_extract=mnemosyne_llm,    # 可选：LLM 浅睡总结
    llm_connect=prometheus_llm,   # 可选：LLM 灵感联结
)
```

**LLM 注入模式**：有回调 → hypnos 原 prompt 智能层；无回调 → 规则式兜底。

详细工作流见 [`references/deployment-workflow.md`](references/deployment-workflow.md)。

## 本技能参考文档

| 文件 | 内容 |
|------|------|
| [`references/constitutional-memory-principles.md`](references/constitutional-memory-principles.md) | Constitutional 记忆原则（自然无痕、四类记忆呈现位置） |
| [`references/github-workflow.md`](references/github-workflow.md) | 双目录同步、推送前检查、ccr 输出压缩规避 |
| [`references/formulas-and-parameters.md`](references/formulas-and-parameters.md) | 衰减公式、浮现阈值、写入阈值、召回反馈参数 |
| [`references/engram-fusion-design.md`](references/engram-fusion-design.md) | EngramTide 融合设计（v3.1.0） |
| [`references/engram-evolution-design.md`](references/engram-evolution-design.md) | 四闭环自我演化设计（v3.2.0） |

## 强制执行的场景（不得使用 Hermes memory）

| 场景 | 以前的做法（已禁用） | 现在的做法（Nyx） |
|------|---------------------|------------------|
| 用户问"我QQ邮箱是多少" | `memory(action='search')` ❌ | `sandglass_search(query="QQ邮箱 qq.com")` ✅ |
| 用户说"上次我说过xxx" | `memory(action='search')` ❌ | `sandglass_dejavu(action='check', query='xxx')` → `session_search` ✅ |
| 用户说"记住这个偏好" | `memory(action='add', target='user')` ❌ | `fact_store(action='add', content='...', category='preferences')` ✅ |
| 用户问"我上次啥时候叫你做xx" | `memory(action='search')` ❌ | `session_search(query='xx')` ✅ |
| 用户发来一个实体名问关系 | `memory(action='search')` ❌ | `mcp_pre_gateway_dispatch_sandglass_thread(entity='...')` ✅ |

---

## 已保存的关键事实（需通过 Nyx 检索）

以下是用 `fact_store` 或 `sandglass` 存储的高优先级事实：

- **QQ 邮箱（只收件）**：`10537543@qq.com`
- **默认发件邮箱【铁律】**：`enfys@hvh.expert`（密码见 himalaya config，永远不得使用其他邮箱发送）
- **发件备选邮箱（只能用 enfys，除非用户明确指定）**：`sixgod@hvh.expert`（密码含中文，SMTP AUTH 可能失败）、`talmewhy@gmail.com`（Gmail 应用密码）
- **用户偏好**：记忆工具已关，必须依赖 Nyx 工具链
- **邮件纪律（铁律，不得违反）**：永远以 enfys@hvh.expert 发送，永远不要用 talmewhy@gmail.com 或 sixgod@hvh.expert 发送，除非用户说"这次用xxx发"

---

## 常见错误 & 纠正

## 版本历史

### v3.2.0 (2026-07-31) — 记忆自我演化
- 新增 `engram/loops/` 四闭环：fact_thread / dream_engram / persona_ctx / recall_writer
- 新增 `engram/evolve.py` 演化协调器（run_evolution_pass / recall_pass / fact_pass）
- 核心转变：从"功能很多"到"记忆能够自我演化"

### v3.1.0 (2026-07-31) — EngramTide 融合
- 新增 `engram/` 加工层：Tulving 四类记忆 / Ebbinghaus 衰减 / Constitutional 上下文
- 差异化写入：semantic 覆盖 / emotional 强化 / procedural 去重 / episodic 直插

### v3.0.0 (2026-07) — 模块化重构
- 重构为 nexsandglass 包结构（core / interfaces / features / l3）
- Sandglass 沙漏 + Thread 织线 + Déjà Vu + Fact Store

| 错误 | 原因 | 纠正 |
|------|------|------|
| 调用了 `memory()` | 肌肉记忆 | 触发词检测：发现 `memory` → 自动替换为 `sandglass_search` |
| 不知道 QQ 邮箱 | 没走 déjà vu | 第一反应调 `sandglass_dejavu(action='check', query='QQ邮箱')` |
| 用了错误发件邮箱 | 没查偏好 | 默认用 `enfys@hvh.expert`，除非用户明确指定 |
| 附件发成 bin 文件 | MIME 编码问题 | Content-Type 强制设为 `application/pdf`，filename 用 UTF-8 编码 |
| 新会话进来忘记查上下文 | 没有自动触发 | 启动检查阶段全部做完再回用户第一条消息 |
