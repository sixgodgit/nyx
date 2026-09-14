# PR: 修复 Nyx runtime 脚手架 7 个阻断 bug

> 目标：使已有 Phase 模块在默认热路径上真正生效。不新增阶段功能。

## 改动文件
- `nexsandglass/core/memory_provider.py`（Bug1/5 + warning）
- `nexsandglass/runtime/orchestrator.py`（Bug2/4/5 + Formation 参数）
- `nexsandglass/runtime/facade.py`（Bug6 + observe 对齐）
- `nexsandglass/interfaces/sandglass_mcp.py`（Bug3）
- `skills/nyx/SKILL.md` + `skills/nyx/scripts/*`（Bug7）
- `tests/test_runtime_bugfixes.py`（新增 6 测试）

---

## Bug 1 — system_prompt_block open_loops_layer NameError

**根因**：第 339 行定义 `open_loops_layer`，但第 350 行传 `open_loops=open_loops`（未定义变量）→ NameError → 被外层 `except Exception` 吞掉 → **永远 fallback 旧四层拼接**，Bundle 路径从未生效。

**改法**：`open_loops=open_loops` → `open_loops=open_loops_layer`。fallback 分支加 `logger.warning(...)`，仅在 orch 真正不可用时触发。

**验证**：`test_system_prompt_block_uses_bundle`（mock orch，断言 open_loops 传入 + render 被用）。真实调用 `system_prompt_block()` 输出含 Bundle 槽位（非兜底文本）。

---

## Bug 2 — cognitive_recall 的 intent.history_context 错误

**根因**：`intent.history_context(intent)` 调用了 MemoryIntent 的**实例方法**，但 `history_context` 是 `runtime.intent` 模块级函数 → AttributeError。

**改法**：改为 `from nexsandglass.runtime import intent as im; hctx = im.history_context(intent)`；history=True 时注入演变链；异常打 warning。

**验证**：`test_cognitive_recall_history_chain`（intent.history=True + cognitive_recall 不崩 + history_context 可调用）。

---

## Bug 3 — sandglass_mcp tools/list 未声明 memory_*

**根因**：`_handle_tool` 已支持 `memory_observe/recall/feedback/forget`，但 `tools/list` 的 tools 数组**未声明**这 4 个 → MCP 客户端 list 不到。

**改法**：tools/list 注册 4 个工具（完整 inputSchema），与 handler 一致。

**验证**：`test_mcp_lists_memory_tools`（断言 tools/list 含 4 个 memory_*）。

---

## Bug 4 — RecallPlanner._adapt_nyx 返回结构不匹配

**根因**：`_adapt_nyx` 读 `sense["familiar"]`、`hunt["matches"]`，但 `nyx_sense` 真实返回 `familiar_ratio/known_tokens/unknown_tokens/total`，`nyx_hunt` 返回 `phantoms`（list of dict: token/whisper/sightings）→ 永远读不到 → nyx 通道空。

**改法**：对齐真实结构——熟悉度写 content/confidence，known_tokens 转实体对象，phantoms 转 MemoryObject（whisper 文本 + sightings 置信度）。

**验证**：`test_adapt_nyx_reads_phantoms`（mock nyx，断言返回 phantom whisper + 熟悉度）。

---

## Bug 5 — sync_turn 双写 sandglass

**根因**：sync_turn 先 `log_message`（raw），promote 后再 `orch.observe` → FormationRouter **再次 log_message** → 同一消息写两次 sandglass。

**改法**（方案 b）：`FormationRouter.observe` / `NyxOrchestrator.observe` / `facade.observe` 增加 `raw_already_logged` 参数；sync_turn promote 时传 `raw_already_logged=True`。审计日志一行一次；长期记忆只经 promotion 后写入。

**验证**：`test_sync_turn_single_raw_log`（两条高信息量消息 → sandglass 增量精确 =2，无双写）。

---

## Bug 6 — facade.feedback 名存实亡

**根因**：`recall_feedback([], recalled_ids)` 传空 memories → 内部无匹配 → noop，从不改变任何存储权重。

**改法**：从 engram store 加载匹配 memory_id 的行 → 构造 Memory → `recall_feedback` 提升（helpful）或自定义降低（weaken）→ **持久化回写**（decay_weight/access_count 字段写回 jsonl）。

**验证**：`test_feedback_reinforces_persisted_memory`（临时 store：reinforce 提升 decay_weight≥1.0，weaken 降低 <1.0）。

---

## Bug 7 — skills/nyx/scripts 与主包分叉

**根因**：`skills/nyx/scripts/` 有 22 个 git 追踪文件（engram/types.py 等）与主包重复且**已分叉**（主包有 MemoryObject/lifecycle，scripts 是旧版），第二份逻辑无意义维护。

**改法**：git rm 整个 `skills/nyx/scripts/`（17 .py + 5 .md 都是主包副本，主包 engram/prompts/ 已有全部 prompt 资产）。SKILL.md 的 `scripts/engram` 引用改为 `nexsandglass/engram`（单一事实来源）。同步命令块更新。

**验证**：git ls-files 确认 scripts 全删；SKILL.md 无残留 scripts/ 引用；测试全绿（主包单一来源仍工作）。

---

## 额外一致性
- `facade.observe` 改为委托 orchestrator（FormationRouter），与 Formation 参数/返回/lifecycle 对齐
- 关键路径 `except: pass` 补 `logger.warning`（system_prompt_block fallback、sync_turn 晋升/raw 落沙）

## 测试
- `tests/test_runtime_bugfixes.py`：6 项测试，28 断言全绿
- 相关 pytest 子集：`test_runtime_bugfixes` 6 passed
- 全量独立脚本：12 个 test_engram_*.py 全绿，无回归

## 验收项
- ✅ Bundle 默认注入不再 NameError fallback（Bug1）
- ✅ MCP 客户端能 list 到 memory_*（Bug3）
- ✅ 一轮 sync_turn 沙粒 +1（每消息一次，无 +2 双写，Bug5）
- ✅ feedback 能改变存储权重（Bug6）
- ✅ nyx 策略能返回 phantom 文本（Bug4）
- ✅ 单一事实来源在 nexsandglass/（Bug7）
