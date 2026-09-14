# RUNTIME.md — NexSandglass Memory Runtime 契约（B0）

唯一对外 API + 唯一内部对象模型。外部（Hermes / MCP / 认知内核）只依赖本契约。

## 公共 API

```python
from nexsandglass import runtime

runtime.observe(event, *, source="runtime", session_id=None, mem_type=None) -> ObserveReport
runtime.recall(query, *, context=None, token_budget=1500, session_id=None) -> MemoryContext
runtime.feedback(outcome: dict) -> dict
runtime.forget(selector: dict) -> dict
runtime.consolidate(*, tag="consolidation") -> dict   # 异步/维护入口
```

- `ObserveReport`: `ok, memory_id, memory_type, lifecycle_state, message`
- `MemoryContext`: `text, bundle, objects, strategy_used, reasons, traces, est_tokens, degraded`
- `MemoryObject`: 唯一 canonical（content / type / status / valid_from / valid_until / supersedes / ...）

## 数据流

```
Conversation → observe(Formation) → Store
Query → recall(Intent→Planner→Rank→Bundle→Context) → Agent
Cron → consolidate(Dream Proposal pipeline)
```

- `MemoryContext.text` = render 结果，**可直接进 system prompt**
- `MemoryObject` 贯穿写入 / 召回 / 晋升 / 时序

## 运行时开关

- `NYX_RUNTIME=1`（默认开启）：走 runtime 契约
- `NYX_RUNTIME=0`：紧急回滚旧路径（直连 sandglass/engram），打 deprecated 日志

## 约束

- 外部不直连内部表 / 不 import 内部模块
- 热路径可降级（degraded=True），失败不得毁掉对话
- 运行时 *.db / 密钥不提交进 git
