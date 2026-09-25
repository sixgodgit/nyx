# RUNTIME.md — NexSandglass Memory Runtime 契约（B0）

唯一对外 API + 唯一内部对象模型。外部（Hermes / MCP / 认知内核）只依赖本契约。

## 公共 API

```python
from nexsandglass import runtime

runtime.observe(event, *, source="runtime", session_id=None, mem_type=None) -> ObserveReport
runtime.recall(query, *, context=None, token_budget=1500, session_id=None) -> MemoryContext
runtime.feedback(outcome: dict) -> dict
runtime.forget(selector: dict) -> dict
runtime.restore(mem_id: str) -> dict                  # 隔离期内撤回一次 forget
runtime.purge_forgotten(*, apply=False, mem_ids=None) -> dict   # 到期物理擦除（cron）
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

## 遗忘是两段的（v7.6）

```
forget(selector)                  检索侧立刻读不到；原文进隔离区，默认留 30 天
  ├─ restore(mem_id)              隔离期内：逐字节还原（日志/中枢/索引/影子/图谱）
  └─ purge_forgotten(apply=True)  到期：物理擦除 + VACUUM，此后不可恢复
forget({"purge_now": True})       不留隔离区，当场不可逆
```

- `forget` 的返回值带 `recoverable` 与 `purge_after` —— 调用方必须能区分
  「删了」和「删了但还能救」
- `verify_erasure` 返回三个字段：`clean`（检索侧摸不到）/ `in_quarantine`（隔离区还有）/
  `fully_purged`（磁盘上真的没了）。**不要把它们合并成一个布尔值**
- 隔离期由 `NYX_FORGET_RETENTION_DAYS` 配置；0 = 不留隔离区
- cron 必须跑 `purge_forgotten(apply=True)`（或 `scripts/nyx_quarantine.py purge --apply`），
  否则 `memid.health()` 的 `pending_purge` 项会报红：承诺的保留期变成了永久留着

## 约束

- 外部不直连内部表 / 不 import 内部模块
- 热路径可降级（degraded=True），失败不得毁掉对话
- 运行时 *.db / 密钥不提交进 git
