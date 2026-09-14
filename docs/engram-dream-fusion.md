# hypnos × engram 梦境融合

> 融合日期：2026-07-31
> 状态：v1 已实现（`nexsandglass/engram/dream_pipeline.py` + `prompts/`）

## 背景

`hypnos-dream-system`（本地部署为 dream-system 技能）与 Nyx 梦境能力
存在大量重叠：

| 能力 | hypnos | Nyx 已有 | 结论 |
|------|--------|----------|------|
| 夜间总结 | Mnemosyne prompt | 梦境日志 + nightwatch | 重叠 |
| 知识三元组 | Epimetheus prompt | engram dream_relation_discovery | 重叠 |
| 用户画像 | Epimetheus | persona_l3 | 重叠 |
| 创意联结 | Prometheus prompt | 梦境灵感泡沫 | 重叠 |
| 重分类/合并/衰减 | ❌ 无 | engram 闭环 2/4 | Nyx 更强 |

**融合策略**：hypnos 保留"prompt 层"（三女神角色与输出格式），
Nyx engram 提供"确定性加工层"（重分类/合并/关系发现/衰减）。
一条管线，各取所长。

## 管线架构

```
day_log ──▶ Phase 1 Mnemosyne ──▶ Phase 2 Epimetheus ──▶ Phase 3 Prometheus ──▶ Phase 4 演化联动
           浅睡总结（prompt）      深睡内化（代码）       快速眼动（prompt）      老化降权+图谱
           └─ 已完成/未完成        └─ 重分类≥3次          └─ emotional→联想      └─ Loop 4 + Loop 1
             新知事实/待确认          合并/关系发现          跨域联结
```

### Phase 1：Mnemosyne（记忆女神）— 浅睡总结

- **prompt 层**（`prompts/01-mnemosyne-nrem.md`）：LLM 生成结构化总结
  （已完成/未完成/新知事实/待确认）
- **代码兜底**（`mnemosyne_summarize`）：规则式提取事实陈述
- 注入方式：`llm_extract` 回调

### Phase 2：Epimetheus（后见之神）— 深睡内化

- **代码层**（`epimetheus_internalize`）：完全确定性
  1. `dream_reclassify` — episodic 同内容 ≥3 次 → semantic 稳定事实
  2. `dream_consolidate` — 相似 episodic/emotional 合并（阈值 0.92）
  3. `dream_relation_discovery` — 提取图谱关系

### Phase 3：Prometheus（先见之神）— 快速眼动

- **prompt 层**（`prompts/03-prometheus-rem.md`）：LLM 跨域联想
- **代码兜底**（`prometheus_inspire`）：高唤醒 emotional → 联结句
- 注入方式：`llm_connect` 回调

### Phase 4：演化联动（engram 闭环）

- 关系发现结果 → 织线图谱（Loop 2 → Loop 1）
- `age_and_demote` — 老化降权 + 归档候选（Loop 4）

## 代码调用

```python
from nexsandglass.engram import run_dream_pipeline

evolved, report = run_dream_pipeline(
    memories,                # 当前活跃记忆
    day_log,                 # 今日对话记录
    persona_entries,         # 画像条目（可选）
    thread_store=wthread_add,  # 织线图谱写入回调
    llm_extract=fake_llm_summarize,  # 可选：LLM 总结
    llm_connect=fake_llm_inspire,    # 可选：LLM 联想
)
# report.summary: new_facts / reclassified / consolidated_groups /
#                 relations_found / inspirations / demoted / archive_candidates
```

## 迁移说明（hypnos → Nyx）

| hypnos 文件 | 去向 |
|-------------|------|
| `prompts/01-mnemosyne-nrem.md` | `engram/prompts/`（保留，prompt 层） |
| `prompts/02-epimetheus-sws.md` | `engram/prompts/`（保留，供参考） |
| `prompts/03-prometheus-rem.md` | `engram/prompts/`（保留，prompt 层） |
| `prompts/04-evolution.md` | 已被 engram 闭环 2/4 代码取代 |
| `prompts/05-dream-log.md` | 已被 Nyx 梦境日志格式取代 |
| Mnemosyne 总结逻辑 | `dream_pipeline.mnemosyne_summarize` |
| Epimetheus 三元组 | `dream_pipeline.epimetheus_internalize` + 织线 |
| Prometheus 灵感 | `dream_pipeline.prometheus_inspire` |

**建议**：dream-system 技能（hypnos 本地部署）可退役——
其全部能力已由 Nyx 梦境 + engram 管线覆盖；prompts/ 已作为资产并入 nyx 仓库。

## 测试

`tests/test_engram_dream.py` — 7 项测试：
- Mnemosyne 规则提取 / LLM 回调注入
- Epimetheus 重分类 + 关系发现
- Prometheus 代码模式 / LLM 模式
- 完整管线（含图谱写入联动）
- LLM 模式完整管线

运行：`python3 tests/test_engram_dream.py`
