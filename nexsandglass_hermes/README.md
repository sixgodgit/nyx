# Nyx 夜神 — Hermes Agent 记忆提供器

按 Hermes 官方 `agent.memory_provider.MemoryProvider` 契约实现，与 Honcho / Mem0 / Hindsight 同一套接口。
已对真实 Hermes 源码（`MemoryManager` + 插件加载器）跑通：`tests/integration/hermes_real_check.py`。

## 安装

```bash
pip install nyx-memory
python3 -m nexsandglass_hermes install       # 然后重启 Hermes
```

`install` 做四件事：
1. 找到 Hermes 用的 Python（`hermes` 命令第一行的 `#!`）。官方安装脚本把 Hermes 装在单独的虚拟环境里，那里没有 nyx-memory 的话就装进去（同一个版本）。
2. 在 `$HERMES_HOME/plugins/nyx/` 放一个入口文件，转到 pip 装的代码。以后 pip 升级不用再动它。
3. `hermes config set memory.provider nyx`。
4. 用 Hermes 自己的加载器加载一次，确认能加载出来。

为什么要这一步：hermes-agent 0.19.0 只按目录找记忆插件（`plugins/memory/__init__.py` 的 `find_provider_dir`），不看 pip 入口点。光 `pip install` 的话 Hermes 找不到 nyx，而且只在 debug 日志里留一行。

| 选项 | 作用 |
|---|---|
| `--vault` | 同时打开密钥保险箱：装 `[vault]`、`hermes plugins enable nyx-reveal`、`nyx.json` 里 `vault: on` |
| `--vector` | 同时装语义检索 `[vector]` |
| `--check` | 只检查，不改任何东西 |
| `--hermes-home DIR` | 指定 Hermes 的 home（多个 profile 时每个装一次） |
| `--hermes-python PATH` | 指定 Hermes 用的 Python（找不到 `hermes` 命令时） |
| `--no-pip` / `--no-config` | 不往 Hermes 的 Python 里装 / 不改 Hermes 配置 |

以前按旧说明把本目录整个拷进 `plugins/nyx/` 的，运行一次 `install` 会换成入口文件；否则升级之后那份旧拷贝还在跑旧代码。

## 配置（`hermes memory setup` 写入 `$HERMES_HOME/nyx.json`）

| 键 | 默认 | 说明 |
|---|---|---|
| `data_dir` | `~/.neurobase` | 记忆目录。默认与 nyx 的 MCP / CLI 共用一份记忆（一个人一份记忆）。`NEXSANDBASE_HOME` 优先。改动后重启 |
| `owner_ids` | 空 | 主人在聊天平台上的 ID / 名字（逗号分隔）。设置后只有主人说的话按「主人亲口说的」记；其他参与者一律记为外部来源（未经证实），不会变成关于主人的事实。留空 = 单人使用 |
| `bots_are_external` | `true` | 机器人发来的消息按外部来源记 |
| `prefetch_tokens` | `600` | 每轮自动召回的预算；0 = 只注入偏移/情绪信号 |
| `semantic` | `auto` | 语义检索：`auto`（配了 API 用 API；装了 `nyx-memory[vector]` 用本地模型；都没有则关）/ `local` / `api` / `off` |
| `embedding_model` | 空 | 嵌入模型名；换模型后旧向量自动作废并后台重建 |
| `embedding_api_url` / `embedding_api_key` | 空 | OpenAI 兼容 `/embeddings`；密钥只进 `.env` |
| `rerank` | `off` | `local` = 召回后用本地多语言交叉编码器重排（需要 `[vector]`，首次下载约 470MB）。证据召回更准（LoCoMo +4.7 个点），但偏好类问题变差、每轮多 0.3–1.5 秒（CPU）。见主 README 的 Benchmark |
| `vault` | `off` | 密钥保险箱（v7.15.0）：聊天里贴的 token / API key / 密码加密存起来，主人要时经确认交还。需要 `pip install 'nyx-memory[vault]'`。见下一节 |
| `vault_reveal` | `direct` | `direct` = 原文不经过模型（要启用配套插件 `nyx-reveal`）；`cli` = 只在终端里取；`llm` = 原文交给模型转述 |
| `vault_kinds` | `all` | `tokens` = 不收密码 |

## 密钥保险箱（v7.15.0，默认关）

默认情况下，聊天里贴的密钥在写进记忆之前就被换成占位符，原文直接丢掉。打开 `vault` 后原文不丢，而是加密存进保险箱（主密钥在 `~/.config/nyx/vault.key`，不在数据目录里），占位符带上编号，例如 `[已隐去: HF token …ab12 #v3]`。

主人说「把我之前给你的 HF token 发我」时，agent 调用 `nyx_secret_reveal(v3)`，然后：

1. Hermes 弹出人工审批。如果审批被 `/yolo` 或 `approvals.mode: off` 关掉了，就改为让主人回复一句确认短语。
2. 模型拿到的只是一个句柄。在回复发给主人之前，配套插件把句柄换成原文，所以模型服务商看不到原文，Hermes 的会话记录里存的也是句柄。

启用（装 `[vault]`、启用配套插件、打开开关，一条命令）：

```bash
python3 -m nexsandglass_hermes install --vault      # 然后重启 Hermes
```

最稳妥的方式永远是在终端里取：`python3 -m nexsandglass.vault show v3`。原文不经过任何模型或聊天平台。

保险箱防不住两件事：
- 密钥第一次贴进聊天的那一刻，原文已经发给了模型服务商，也留在了聊天平台上，所以贴过的密钥都应该换掉。
- 机器人在群聊里时，群里任何人都会被当成主人。

威胁模型和实测结果见 [docs/secret-vault.md](../docs/secret-vault.md)。

## 每轮做什么

- **prefetch**：对本轮问题现做本地召回（每轮约 30–140ms），每条带「哪天说的」；问到住处 / 公司 / 职位等时，
  附上双时态的当前信念（「user 住在 上海（自 2026-09-01）」）。默认注入 12 条；问「一共几次 / 隔了多久 / 哪个先 / 之前之后」时
  20 条（`NYX_BREADTH_LINES`，0 = 关闭）。界面上显示 `🧠 Nyx — recalled N memories`
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
| `nyx_health` | 自检（`quick=true` 只查语义索引）。系统提示里出现「⚠️ Nyx 自检」时调用 |

## 自检

语义索引等子系统出故障时不会拖垮对话（词法召回照常），但**不会再静默**：系统提示里会出现一行
「⚠️ Nyx 自检：…」，agent 会转告主人。主人在终端运行 `python3 -m nexsandglass.doctor` 看每一项。

## 已知局限

- 一个进程一份 nyx 记忆：网关里多个 Hermes profile 共进程时共用同一个 `data_dir`
- 语义检索需要嵌入后端（本地模型或 API）；没有时同义改写召不回来（问 company、原话是 joined Contoso）
- 从 v7.13.2 及更早版本升级后，首次启动会在后台回填全部历史记忆的向量（本地 CPU 模型几千条约几十分钟），期间语义召回逐步变全
- 隔离区到期不会自动真删：定期执行 `python3 -c "from nexsandglass.core import erasure; print(erasure.purge(apply=True))"`；到期没清时自检会提醒
- v3.x 的 `install.sh` 装过的机器：删掉 `~/.hermes/plugins/memory/nexsandglass` 与 `~/.hermes/plugins/sandglass`（旧的复制式插件，会让同一句话记两遍）
