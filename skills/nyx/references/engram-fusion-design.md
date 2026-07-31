# Nyx × EngramTide 融合设计

> 融合日期：2026-07-31
> 状态：v1 已实现（`nexsandglass/engram/`）

## 背景

EngramTide（videcy/EngramTide, MIT）是一个认知科学驱动的长期 AI Agent 记忆系统，
核心亮点：**Tulving 四类记忆 + Ebbinghaus 衰减 + Constitutional 记忆**。

Nyx 已有：沙漏（Sandglass）存储检索、织线知识图谱、Déjà Vu 感知、梦境系统、
偏移率/回音折、灵魂差分。

**融合目标**：吸取 EngramTide 的"记忆加工"层（分类、衰减、浮现、激活、宪法化），
接入 Nyx 的"记忆存储"层（沙漏/图谱/事实库），形成 存储→加工→呈现 的完整链路。

## 架构

```
┌────────────────────────────────────────────────────────────┐
│                    Nyx 记忆感知系统                          │
├────────────────────────────────────────────────────────────┤
│  加工层（新）neXsandglass.engram/                           │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐  │
│  │  types   │ │  decay   │ │  writer  │ │   context    │  │
│  │ 四类记忆 │ │ 衰减/浮现│ │差异化写入│ │ Constitutional│ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └──────┬───────┘  │
├───────┼────────────┼────────────┼──────────────┼──────────┤
│  存储层（已有）                                              │
│  ┌────▼────────────▼────────────▼──────────────▼───────┐   │
│  │ Sandglass 沙漏 │ Thread 织线 │ Fact Store │ Session │   │
│  └─────────────────────────────────────────────────────┘   │
├────────────────────────────────────────────────────────────┤
│  Hermes 集成（已有）：MCP 工具 / 梦境 / 偏移率 / 灵魂差分     │
└────────────────────────────────────────────────────────────┘
```

## 记忆类型（Tulving 模型）

| 类型 | 中文 | 特性 | 衰减 | 写入策略 | 典型内容 |
|------|------|------|------|----------|----------|
| semantic | 语义 | 稳定事实 | ❌ | 覆盖（≥0.85 标记 superseded） | 邮箱、住址、身份、偏好 |
| episodic | 情景 | 具体事件 | ✅ 指数衰减 | 直插，不可覆盖 | "7月14日 Odido 上门装光纤" |
| emotional | 情绪 | 带情绪色彩 | ✅ 慢衰减 | 强化（≥0.85 提升权重） | "用户对 Apple 账户安全紧张" |
| procedural | 规则 | 相处方式 | ❌ | 去重（≥0.90 只计访问） | "涉及个人数据必须先检索再回答" |

## 衰减引擎（Ebbinghaus 遗忘曲线）

```
multiplier = exp(-rate × hours_elapsed / (24 × (1 + ln(1 + access_count))))

  - episodic:   rate = 0.05
  - emotional:  rate = 0.05 × (1 - arousal × 0.7)   # 唤醒度越高衰减越慢
  - semantic / procedural: rate = 0（不衰减）
  - DECAY_FLOOR = 0.01（永不归零，"想不起来但隐约记得"）
  - 时钟回拨保护：hours_elapsed <= 0 → 1.0
```

## 浮现机制（分层优先级）

| 优先级 | 条件 | 说明 |
|--------|------|------|
| R1 | procedural | 规则永远浮现（写入去重保底） |
| R2 | emotional 且 arousal ≥ 0.7 | 高唤醒情绪优先 |
| R3 | unresolved | 未解决事项优先 |
| R4 | episodic 且 7 天内 | 近期事件 |

同规则内按 decay_weight 降序；`superseded_by` 非空的记忆不浮现（双保险）。

## 逐轮激活

```
new_weight = min(decay_weight + 0.05 × similarity, 1.0)
```
每轮用户输入后、检索之前，对相似度 > 0 的记忆做加法 boost；
相似度 ≥ 0.80 记为"强激活"，同时计访问（access_count +1，提升稳定性）。

## Constitutional 上下文

记忆不直接"朗读"给模型，而是融入交互宪法：

```
# 交互宪法
## 宪法第一条：自然无痕原则
- 严禁使用"根据我的记忆""数据库显示"等暴露机械记忆系统的表述
- 记忆应当像潜意识一样影响态度、语气和切入点

## 动态行为修正案
{procedural_memories}          ← 规则（最高优先级）

## 上下文记忆沙盒
### 基础用户信息   {semantic_memories}
### 近期生活事件   {episodic_memories}
### 历史情感沉淀   {emotional_memories}
```

组装按 procedural → semantic → episodic → emotional 顺序，
共享 token 预算（默认 1500），procedural 保底优先收录。

## 与 Nyx 已有能力的协同

| Nyx 能力 | 与 engram 的关系 |
|----------|-----------------|
| Sandglass 沙漏 | 沙粒 = 原始事件（episodic 源）；engram 从沙粒提炼四类记忆 |
| Thread 织线 | 织线三元组可标注类型；semantic/procedural 事实可入图谱 |
| Déjà Vu | 模糊感知（Bloom Filter）与 R3 unresolved 互补：查不到但"感觉聊过" |
| 梦境系统 | 夜间阶段可调用 apply_decay 批量衰减 + consolidate_similar 合并 |
| 偏移率/回音折 | emotional 记忆的 arousal/valence 可喂给情绪熵可视化 |
| 灵魂差分 | 导出时可携带 decay_weight 快照 |

## 接入路径（建议）

1. **写入**：沙漏写入沙粒时，同时用 `write_memory_classified` 提炼 semantic/
   emotional/procedural 记忆（episodic 直接用沙粒本身）
2. **检索前**：`compute_activations` 逐轮激活（需要 embedding）
3. **呈现**：检索结果 + `get_surfaced_memories` 浮现 → `build_constitutional_context`
   → 渲染进 system prompt
4. **夜间**：梦境 cron 调用 `apply_decay` + `consolidate_similar`

## 测试

`tests/test_engram_fusion.py` — 17 项测试，覆盖：
- 四类记忆定义与衰减行为（static 不衰减 / episodic 衰减 / emotional 慢衰减）
- 时钟回拨保护 / DECAY_FLOOR / 访问次数稳定性
- 差异化写入（semantic 覆盖 / emotional 强化 / procedural 去重 / episodic 直插）
- 浮现分层优先级（R1 procedural > R2 高唤醒 > R3 unresolved > R4 近期）
- 逐轮激活权重提升与封顶
- Constitutional 上下文组装与渲染
- 相似记忆合并（贪心配对）

运行：`python3 tests/test_engram_fusion.py` 或 `pytest tests/test_engram_fusion.py`
