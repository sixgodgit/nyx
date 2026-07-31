# Nyx 记忆自我演化 — 四闭环设计

> 设计日期：2026-07-31
> 状态：v1 已实现（`nexsandglass/engram/loops/` + `engram/evolve.py`）

## 背景

Nyx 功能数量已达边际收益递减点。下一阶段价值不在"加功能"，而在
**让模块之间形成反馈闭环**，使记忆能够自我演化：

```
之前：  功能多，但模块孤立（Thread 不感知 Fact 变化，Dream 只做总结……）
之后：  模块互为输入输出 → 记忆自我演化 → 核心竞争力从"功能很多"
        变为"记忆能够自我演化"
```

## 四个闭环

### 闭环 1：Thread ↔ Fact Store（事实 ↔ 图谱）

```
事实入库 ──fact_to_thread()──▶ 提取三元组 ──▶ 写入织线图谱
    ▲                                             │
    │         thread_validate_fact()              │ 冲突检测
    │◀────────────────────────────────────────────┘
    事实被图谱反向验证（confirm / conflict / unknown）
```

| 方向 | 函数 | 触发时机 |
|------|------|----------|
| 事实 → 图谱 | `fact_to_thread()` | 新事实入库后 |
| 图谱 → 事实 | `thread_validate_fact()` | 新事实入库前（冲突预防） |
| 合并入口 | `fact_pass()` | 事实写入统一走此函数 |

**冲突语义**：同一 (subject, relation) 已存在但 object 不同 →
报告 conflict，调用方决定是否覆盖（新事实胜出）或保留（图谱权威）。

### 闭环 2：Dream ↔ Engram（梦境 → 记忆加工）

```
梦境 cron
  │
  ├─ dream_reclassify()     episodic 高频重复 → semantic 稳定事实
  ├─ dream_consolidate()    相似 episodic/emotional → 合并分组
  └─ dream_relation_discovery()  记忆 → 新图谱关系
```

**重分类规则**：同一语义内容（去日期前缀后）出现 ≥3 次 →
提炼为 semantic 事实，旧 episodic 沉底。例：
"2026-07-01 用户去了海牙市政厅" × 3 次 → semantic："用户常去海牙市政厅"

### 闭环 3：Persona ↔ Context（画像 → 上下文）

```
画像条目
  │
  ├─ persona_weight_context()     画像确认事实 → semantic 桶加权（+0.3）
  └─ persona_trigger_rebuild()    画像字段变更 → 旧记忆标记重建候选
```

**加权规则**：画像中匹配度 ≥0.6 的 semantic/procedural 记忆 → decay_weight +0.3；
含"已改/不再/换成"等过时信号的记忆 → ×0.5 降权。

### 闭环 4：Recall ↔ Writer（召回 → 重要性）

```
检索命中
  │
  ├─ recall_feedback()   成功召回 → access_count+1, weight+0.05
  └─ age_and_demote()    定期维护 → 长期未访问加速衰减 / 归档候选
```

**双通道**：
- **短期重要性**：每次成功召回立即提升（access_count 驱动稳定性）
- **长期遗忘**：>30 天未访问 → 权重 ×0.5 加速衰减；
  >90 天从未召回且权重 <0.05 → 归档候选（移出活跃集）

## 演化协调器（evolve.py）

```python
run_evolution_pass(memories, persona_entries, thread_store, thread_query)
    # 一次完整演化回合（梦境/每日维护）：
    #   Loop 2: 重分类 + 合并 + 关系发现
    #   Loop 4: 老化降权 + 归档候选
    #   Loop 1: 新关系写入图谱（联动）
    #   Loop 3: 画像加权
    → (演化后的记忆列表, EvolutionReport)

recall_pass(memories, recalled_ids)   # 检索后：召回反馈（轻量）
fact_pass(fact_text, store, query)    # 事实写入：验证 + 图谱同步
```

## 与现有模块对接（适配器注入）

闭环保持**纯函数 + 注入回调**，对接现有存储：

| 现有模块 | 适配为 |
|----------|--------|
| `features/weavethread.py` | `thread_store` / `thread_query` 回调 |
| `features/shadow_sand.py` | 事实库读写（`shadow_index` / `shadow_search`） |
| `features/persona_l3.py` | 画像条目提供者 |
| `features/nightwatch.py` | 梦境 cron 触发 `run_evolution_pass` |

## 测试

`tests/test_engram_loops.py` — 15 项测试，覆盖：
- 三元组提取（含"用户邮箱是 X"无"的"结构）
- 事实→图谱存储 / 冲突检测 / 图谱反向验证
- 梦境重分类（≥3 次提炼）/ 合并 / 关系发现
- 画像加权 / 字段变更重建候选
- 召回反馈提升 / 老化降权 / 归档候选
- `run_evolution_pass` / `fact_pass` / `recall_pass` 协调器

运行：`python3 tests/test_engram_loops.py`

## 未来方向

- [ ] 闭环 2 → 1 自动联动已实现；闭环 3 变更 → 闭环 4 降权联动可深化
- [ ] 图谱关系置信度（多源佐证提升置信）
- [ ] 演化效果的量化指标（召回率变化、记忆瘦身率）
