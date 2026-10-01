# Nyx 夜神 — Hermes Agent 记忆提供器

按 Hermes 官方 `agent.memory_provider.MemoryProvider` 契约实现，与 Honcho / Mem0 / Hindsight 同一套接口。
已对真实 Hermes 源码（`MemoryManager` + 插件加载器）跑通：`tests/integration/hermes_real_check.py`。

## 安装

```bash
pip install nyx-memory          # entry point 自动注册：hermes_agent.memory_providers → nyx
```

Hermes 配置：

```yaml
memory:
  provider: nyx                 # 旧名 nexsandglass 同样可用
```

或者把本目录拷到 `$HERMES_HOME/plugins/nyx/`（仍需先 pip 装好 nyx-memory）。

## 配置（`hermes memory setup` 写入 `$HERMES_HOME/nyx.json`）

| 键 | 默认 | 说明 |
|---|---|---|
| `data_dir` | `~/.neurobase` | 记忆目录。默认与 nyx 的 MCP / CLI 共用一份记忆（一个人一份记忆）。`NEXSANDBASE_HOME` 优先。改动后重启 |
| `owner_ids` | 空 | 主人在聊天平台上的 ID / 名字（逗号分隔）。设置后只有主人说的话按「主人亲口说的」记；其他参与者一律记为外部来源（未经证实），不会变成关于主人的事实。留空 = 单人使用 |
| `bots_are_external` | `true` | 机器人发来的消息按外部来源记 |
| `prefetch_tokens` | `600` | 每轮自动召回的预算；0 = 只注入偏移/情绪信号 |

## 每轮做什么

- **prefetch**：对本轮问题现做本地召回（实测 10–50ms），每条带「哪天说的」；问到住处 / 公司 / 职位等时，
  附上双时态的当前信念（「user 住在 上海（自 2026-09-01）」）。界面上显示 `🧠 Nyx — recalled N memories`
- **sync_turn**：原文落沙（审计日志）+ 晋升门 + 事实抽取；按 `turn_author` 绑定来源
- **subagent / cron / flush 上下文只读**：不写记忆，写类工具直接拒绝

## 工具

| 工具 | 作用 |
|---|---|
| `sandglass_search` / `sandglass_recent` | 召回 / 最近记忆（非主人来源带「未经证实」标注） |
| `nyx_belief` | 双时态事实：现在信什么；`as_of` 那天什么为真；`known_at` 那时我以为是什么；`history` 演变链 |
| `nyx_forget` | 遗忘 → **隔离区**（先预览、再 `confirm=true`；一次最多 20 条）。agent 无权永久擦除 |
| `nyx_restore` / `nyx_quarantine` | 从隔离区原样还原 / 查看隔离区 |
| `fact_store` / `fact_feedback` | 影子沙事实存储与信任反馈 |
| `sandglass_offset` / `sandglass_echo` | 主人决策偏移 / 情绪风向 |

## 已知局限

- 一个进程一份 nyx 记忆：网关里多个 Hermes profile 共进程时共用同一个 `data_dir`
- 向量语义检索尚未接入召回主路径：同义改写（问 company、原话是 joined Contoso）召不回来
