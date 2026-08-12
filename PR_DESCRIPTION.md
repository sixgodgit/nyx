# Cognitive Memory OS — 地基（V3.5.0）

为 Cognitive Memory OS 打地基：引入 Canonical MemoryObject schema、生命周期状态机、双向映射 bridge 与稳定门面 facade。**不改对外产品行为**，默认路径完全兼容。

---

## 1. As-is 调用链（现状）

### 写入链（事件 → 记忆）
```
用户消息
  └─> core/sandglass_log.log_message(text, sender)     # 落沙：写 sandglass.txt 原始事件
        └─> features/shadow_sand                       # 影子沙索引（信任分/实体，SQLite shadow_sand.db）
              └─> engram/bridge.ingest(text)           # 分类 + 写 engram_store.jsonl（旧格式 {ts,content,type}）
                    └─> engram/writer.write_memory_classified  # 差异化写入（semantic覆盖/emotional强化/procedural去重/episodic直插）
                          └─> engram/decay.apply_decay          # Ebbinghaus 衰减
```

### 召回链（查询 → 上下文）
```
query
  └─> core/search_router.SearchRouter.search(query)    # 四路并发：shadow/FTS5/IDX/TF-IDF
        └─> sand_density + simhash_rerank              # 密度×trust + SimHash 融合
              └─> engram/context.build_constitutional_context  # 按类型分组 + token 预算
                    └─> engram/context.render_constitution    # 注入 system prompt（隐性）
```

### 梦境链（夜间演化）
```
cron 梦境
  └─> engram/evolve.run_evolution_pass()               # 四闭环：
        Loop2 dream_reclassify/consolidate/relation_discovery
        Loop4 age_and_demote（老化降权 + 归档候选）
        Loop1 fact_to_thread（事实 → 织线图谱）
        Loop3 persona_weight_context（画像加权）
```

### Provider 入口（Hermes 集成）
```
core/memory_provider.NexSandglassProvider
  ├─ system_prompt_block()   # 注入记忆上下文
  ├─ prefetch() / sync_turn()# 每轮对话落沙
  ├─ handle_tool_call()      # sandglass_search / fact_store 等
  └─ interfaces/sandglass_mcp.py  # MCP 工具暴露（gateway 运行）
```

---

## 2. 本 PR 改动

### 2.1 Canonical MemoryObject schema（新增）
`nexsandglass/engram/types.py`：
- 新增 `MemoryObject` dataclass —— 现有 `Memory` 的**超集**，含全部旧字段 + 新增字段：
  `structured(SPO)` / `importance` / `confidence` / `valid_from/until` / `status` / `lifecycle_state` / `supersedes` / `superseded_by` / `provenance` / `relations` / `token_estimate` / `updated_at`
- 类型全集 `OBJECT_MEMORY_TYPES`：现四种 + 预留 `preference|relational|temporal|meta`
- 双向转换：`from_memory(mem)` / `to_memory()`，旧数据可读，无 id 自动生成
- `SPO` 三元组载体

### 2.2 生命周期状态机（新增）
`nexsandglass/engram/types.py`：
- `LifecycleState` 枚举：`observed → candidate → validated → active → reinforced → consolidated → aging → archived → forgotten`
- `validate_transition(from, to)`：非法迁移返回 False
- `next_lifecycle(state)`：返回下一个合法状态，terminal 停滞
- `MemoryObject.advance_lifecycle()` / `advance_to(target)`：方法封装

### 2.3 bridge 双向映射（强化）
`nexsandglass/engram/bridge.py`：
- 保留旧接口：`ingest` / `classify_memory_type` / `stats` / `recent`（旧数据可读）
- 新增四向映射：
  - `sandglass_line_to_object` / `object_to_sandglass_line`（Sandglass 行）
  - `engram_row_to_object` / `object_to_engram_row`（旧 JSONL 行，兼容）
  - `shadow_row_to_object` / `object_to_shadow_row`（shadow 信任记录）
  - `memory_to_object` / `object_to_memory`（现有 Memory dataclass）

### 2.4 稳定门面（新增）
`nexsandglass/runtime/facade.py`（新目录）：
- `observe(event, mem_type?, source?) -> ObserveReport`
- `recall(query, context?, token_budget?) -> MemoryContext`（当前透传字符串列表）
- `feedback(outcome) -> dict`（委托 recall_feedback）
- `forget(selector) -> dict`（按 memory_id / source_id / all 删除）
- 内部委托现有函数，不重写 SearchRouter/Dream，不新增并行引擎

### 2.5 Provider feature flag（修改）
`nexsandglass/core/memory_provider.py`：
- `initialize()` 读取 `NYX_USE_FACADE`（默认 `0` 关闭）
- `sync_turn()`：启用时走 `facade.observe`，否则原路径
- `system_prompt_block()`：启用时追加一行 facade 标注
- **默认关闭**，对现有行为零影响

### 2.6 数据库移出版本控制
- `git rm --cached sandglass.db shadow_sand.db`（本地保留）
- `.gitignore` 追加 `*.db *.sqlite *.db-wal *.db-shm *.idx *.bin *.jsonl`

---

## 3. 测试

| 测试文件 | 结果 |
|---------|------|
| `tests/test_engram_cognitive.py`（新增） | **41 项通过**：schema / lifecycle / bridge 往返 / facade 路由 |
| `tests/test_engram_fusion.py`（现有回归） | 17/17 通过 |
| `tests/test_engram_dream.py`（现有回归） | 7/7 通过 |
| `tests/test_engram_loops.py`（现有回归） | 15/15 通过 |

**验收满足**：
- ✅ 旧路径默认可用（NYX_USE_FACADE 默认关）
- ✅ 新 schema + facade + 测试通过
- ✅ 不重写 SearchRouter / Dream
- ✅ 不新增并行记忆引擎
- ✅ 遵循现有代码风格

---

## 4. 后续（不在本 PR）
- MemoryContext 从字符串升级为结构化上下文（分组 + token 预算分配）
- lifecycle 与 evolve 循环的实际接线（advance 时机）
- facade 全面接管 Provider 读写（feature flag 转正式）
