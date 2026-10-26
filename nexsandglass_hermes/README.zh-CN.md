# Nyx 夜神 — Hermes Agent 记忆提供器

[English](https://github.com/sixgodgit/nyx/blob/main/nexsandglass_hermes/README.md) | **简体中文**

按 Hermes 官方 `agent.memory_provider.MemoryProvider` 契约实现，与 Honcho / Mem0 / Hindsight 同一套接口。
已对真实 Hermes 源码（`MemoryManager` + 插件加载器）跑通：`tests/integration/hermes_real_check.py`。

## 安装

```bash
hermes plugins install sixgodgit/nyx/hermes-plugin --enable
hermes config set memory.provider nyx        # 主线版 Hermes 装的时候已经设好了，再设一次也没关系
```

装完重启 Hermes（网关：`hermes gateway restart`）。这两条命令只用到 `hermes`，不需要先在系统 Python 里装任何东西。

Hermes 会把仓库里的 [`hermes-plugin/`](https://github.com/sixgodgit/nyx/tree/main/hermes-plugin) 放进 `$HERMES_HOME/plugins/nyx/`，再把它在 `plugin.yaml` 里声明的依赖 `nyx-memory` 装进 Hermes **自己的**环境。`hermes update` 之后，依赖会重新装上。Hermes 的文档明确要求这样做，不要用 pip 往它的环境里注入 provider：新版用自己的包管理器按锁文件同步环境，pip 装进去的东西会被同步掉。

要用语义检索的本地模型，就装 `sixgodgit/nyx/hermes-plugin-vector`：依赖是 `nyx-memory[vector]`，首次运行会下载约 470MB 的模型。

已在真实的 Hermes 上验证过：稳定版 v2026.9.24（uv 建的虚拟环境，里面没有 pip）和主线版（带包管理器）。两个版本上装完后，`hermes memory status` 都显示 `Provider: nyx`、可用。

### 老版本 Hermes

老版本指 `hermes plugins install --help` 里没有 `--no-deps` 的版本，比如 PyPI 上的 hermes-agent 0.19.x。它们不会装插件声明的依赖，也不认 pip 入口点：

```bash
pip install nyx-memory                       # 和 Hermes 同一个 Python
python3 -m nexsandglass_hermes install
```

`install` 新老版本都认：
- **新版**：它直接替你运行上面那两条 `hermes` 命令。在终端里运行时，Hermes 的依赖确认由你自己回答；不在终端里时，它会代为加上 `--yes-deps`。
- **老版本**：它从 `hermes` 命令找到 Hermes 用的 Python（包括 `#!/bin/sh` 包装脚本），缺 nyx-memory 就装进去，然后在 `plugins/nyx/` 放入口文件、设 `memory.provider: nyx`，最后用 Hermes 自己的加载器加载一次，确认能加载出来。

| 选项 | 作用 |
|---|---|
| `--vault` | 同时打开密钥保险箱：`hermes plugins enable nyx-reveal`，并在 `nyx.json` 里写 `vault: on` |
| `--vector` | 同时装语义检索（新版装 `hermes-plugin-vector`，老版本装 `[vector]`） |
| `--check` | 只检查，不改任何东西 |
| `--hermes-home DIR` | 指定 Hermes 的 home（有多个 profile 时，每个各装一次） |
| `--hermes-python PATH` | 指定 Hermes 用的 Python（老版本、找不到时用） |
| `--source` / `--legacy` | 新版从哪里装插件；强制走老版本的做法 |
| `--no-pip` / `--no-config` | 不往 Hermes 的 Python 里装 / 不改 Hermes 配置 |

以前用 v7.15.0 的安装命令装过，或者按更早的说明把整个目录拷进了 `plugins/nyx/`：再运行一次 `install`，会换成 Hermes 自己管理的安装。

## 配置（`hermes memory setup` 写入 `$HERMES_HOME/nyx.json`）

| 键 | 默认 | 说明 |
|---|---|---|
| `data_dir` | `~/.neurobase` | 记忆目录。默认与 nyx 的 MCP / CLI 共用一份记忆（一个人一份记忆）。`NEXSANDBASE_HOME` 优先。改动后重启 |
| `prefetch_tokens` | `600` | 每轮自动召回的预算；0 = 只注入偏移/情绪信号 |
| `semantic` | `auto` | 语义检索：`auto`（配了 API 用 API；装了 `nyx-memory[vector]` 用本地模型；都没有则关）/ `local` / `api` / `off` |
| `embedding_model` | 空 | 嵌入模型名；换模型后旧向量自动作废并后台重建 |
| `embedding_api_url` / `embedding_api_key` | 空 | OpenAI 兼容 `/embeddings`；密钥只进 `.env` |
| `rerank` | `off` | `local` = 召回后用本地多语言交叉编码器重排（需要 `[vector]`，首次下载约 470MB）。证据召回更准（LoCoMo +4.7 个点），但偏好类问题变差、每轮多 0.3–1.5 秒（CPU）。见主 README 的 Benchmark |
| `vault` | `off` | 密钥保险箱（v7.15.0）：聊天里贴的 token / API key / 密码加密存起来，主人要时经确认交还。Hermes 自己已带 cryptography，不用另装。见下面「密钥保险箱」 |
| `vault_reveal` | `direct` | `direct` = 原文不经过模型（要启用配套插件 `nyx-reveal`）；`cli` = 只在终端里取；`llm` = 原文交给模型转述 |
| `vault_kinds` | `all` | `tokens` = 不收密码 |
| `lang` | `auto` | 终端命令、`hermes memory setup` 用什么语言：`en` / `zh`；`auto` = 系统语言是中文就用中文（v7.16.0）。给模型看的文字一律英文 |
| `l3_llm` | `off` | `on` = 画像 / 偏移率 / 幽灵决策这些 L3 分析调用 DeepSeek 或 OpenRouter（`DEEPSEEK_API_KEY` / `OPENROUTER_API_KEY`，按当前 profile 取），会把相关的记忆片段、画像、近期决策发给它们。v7.15.2 以前不用打开、有 key 就用 |

## 终端命令：`hermes nyx …`（v7.15.2）

新版 Hermes 把 nyx-memory 装在它自己的隔离环境里，系统 Python 里没有 nyx，`python3 -m nexsandglass.…` 用不了。
nyx 是当前记忆插件时，Hermes 会多出 `hermes nyx` 这组命令（数据目录、保险箱开关按当前 profile 的 `nyx.json`）：

| 命令 | 作用 |
|---|---|
| `hermes nyx doctor` | 自检：每一项 ✅ / ❌ + 原因 |
| `hermes nyx vault list` / `show v3 [--copy]` / `delete v3` / `gc` / `rekey` / `audit` / `status` | 密钥保险箱 |
| `hermes nyx scrub [--apply] [--to-vault]` | 已经存下来的密钥 / 密码：找出来、原地隐去 |
| `hermes nyx repair [--apply]` | 中枢对不上日志时：按内容体检与修复 |
| `hermes nyx status` | 版本、Python、数据目录、保险箱 |

agent 转告给你的命令（自检警告、「在终端里取」）也会写成 `hermes nyx …`。

## 密钥保险箱（v7.15.0，默认关）

默认情况下，聊天里贴的密钥在写进记忆之前就被换成占位符，原文直接丢掉。打开 `vault` 后原文不丢，而是加密存进保险箱（主密钥在 `~/.config/nyx/vault.key`，不在数据目录里），占位符带上编号，例如 `[hidden: HF token …ab12 #v3]`（v7.16.0 以前是 `[已隐去: …]`，照样认）。

主人说「把我之前给你的 HF token 发我」时，agent 调用 `nyx_secret_reveal(v3)`，然后：

1. Hermes 弹出人工审批。如果审批被 `/yolo` 或 `approvals.mode: off` 关掉了，就改为让主人回复一句确认短语（如 `确认 v3 123456`；英文界面是 `confirm v3 123456`，两种都认）。
2. 模型拿到的只是一个句柄。在回复发给主人之前，配套插件把句柄换成原文，所以模型服务商看不到原文，Hermes 的会话记录里存的也是句柄。

启用（Hermes 自己已经带了 cryptography，不用另装）：

```bash
hermes plugins enable nyx-reveal      # 再在 hermes memory setup 里把 vault 设成 on，然后重启 Hermes
```

最稳妥的方式永远是在终端里取：`hermes nyx vault show v3`（不在 Hermes 里：`python3 -m nexsandglass.vault show v3`）。原文不经过任何模型或聊天平台。

保险箱防不住两件事：
- 密钥第一次贴进聊天的那一刻，原文已经发给了模型服务商，也留在了聊天平台上，所以贴过的密钥都应该换掉。
- 机器人在群聊里时，群里任何人都会被当成主人。

威胁模型和实测结果见 [docs/secret-vault.zh-CN.md](https://github.com/sixgodgit/nyx/blob/main/docs/secret-vault.zh-CN.md)。

## 每轮做什么

- **prefetch**：对本轮问题现做本地召回（每轮约 30–140ms），每条带「哪天说的」；问到住处 / 公司 / 职位等时，
  附上双时态的当前信念（「user lives in 上海 (since 2026-09-01)」）。默认注入 12 条；问「一共几次 / 隔了多久 / 哪个先 / 之前之后」时
  20 条（`NYX_BREADTH_LINES`，0 = 关闭）。界面上显示 `🧠 Nyx — recalled N memories`
- **sync_turn**：原文落沙（审计日志）+ 晋升门 + 事实抽取；按 `turn_author` 绑定来源
- **subagent / cron / flush 上下文只读**：不写记忆，写类工具直接拒绝

## 工具

| 工具 | 作用 |
|---|---|
| `sandglass_search` / `sandglass_recent` | 召回 / 最近记忆（非主人来源带 `(unverified · source: …)` 标注） |
| `nyx_belief` | 双时态事实：现在信什么；`as_of` 那天什么为真；`known_at` 那时我以为是什么；`history` 演变链 |
| `nyx_forget` | 遗忘 → **隔离区**（先预览、再 `confirm=true`；一次最多 20 条）。agent 无权永久擦除 |
| `nyx_restore` / `nyx_quarantine` | 从隔离区原样还原 / 查看隔离区 |
| `fact_store` / `fact_feedback` | 影子沙事实存储与信任反馈 |
| `sandglass_offset` / `sandglass_echo` | 主人决策偏移 / 情绪风向 |
| `nyx_health` | 自检（`quick=true` 只查语义索引）。系统提示里出现「⚠️ Nyx health」时调用 |

## 自检

语义索引等子系统出故障时不会拖垮对话（词法召回照常），但**不会再静默**：系统提示里会出现一行
「⚠️ Nyx health: …」，agent 会转告主人。主人在终端运行 `python3 -m nexsandglass.doctor` 看每一项。

## 已知局限

- 一个进程一份 nyx 记忆：网关里多个 Hermes profile 共进程时共用同一个 `data_dir`
- 语义检索需要嵌入后端（本地模型或 API）；没有时同义改写召不回来（问 company、原话是 joined Contoso）
- 从 v7.13.2 及更早版本升级后，首次启动会在后台回填全部历史记忆的向量（本地 CPU 模型几千条约几十分钟），期间语义召回逐步变全
- 隔离区到期不会自动真删：定期执行 `python3 -c "from nexsandglass.core import erasure; print(erasure.purge(apply=True))"`；到期没清时自检会提醒
- v3.x 的 `install.sh` 装过的机器：删掉 `~/.hermes/plugins/memory/nexsandglass` 与 `~/.hermes/plugins/sandglass`（旧的复制式插件，会让同一句话记两遍）
