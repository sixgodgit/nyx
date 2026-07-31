---
name: nyx
description: Nyx（夜神）记忆感知系统 — Hermes 的替代记忆层。Hermes 原生记忆（memory tool）已关闭，Nyx 接管全部跨会话记忆、事实存储、联想检索和 déjà vu 检测
tags:
  - nyx
  - memory
  - sandglass
  - dejavu
  - fact-store
  - session-search
  - knowledge-graph
---

# Nyx 记忆感知系统

## 📊 数据位置与表结构

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

部署到 `nyx.hvh.expert`（服务器 162.0.225.252）。详见 [`references/deployment-workflow.md`](references/deployment-workflow.md)。

**关键 pitfall**: CF API Key 在 Hermes 输出中被脱敏。完整 Key 在 `/root/.hermes/.credentials`，提取方式：
```bash
grep "^CLOUDFLARE_API_KEY=*** /root/.hermes/.credentials | cut -d= -f2-
```
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

## 强制执行的场景（不得使用 Hermes memory）

| 场景 | 以前的做法（已禁用） | 现在的做法（Nyx） |
|------|---------------------|-------------------|
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

| 错误 | 原因 | 纠正 |
|------|------|------|
| 调用了 `memory()` | 肌肉记忆 | 触发词检测：发现 `memory` → 自动替换为 `sandglass_search` |
| 不知道 QQ 邮箱 | 没走 déjà vu | 第一反应调 `sandglass_dejavu(action='check', query='QQ邮箱')` |
| 用了错误发件邮箱 | 没查偏好 | 默认用 `enfys@hvh.expert`，除非用户明确指定 |
| 附件发成 bin 文件 | MIME 编码问题 | Content-Type 强制设为 `application/pdf`，filename 用 UTF-8 编码 |
| 新会话进来忘记查上下文 | 没有自动触发 | 启动检查阶段全部做完再回用户第一条消息 |
