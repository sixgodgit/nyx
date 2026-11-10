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
| `prefetch_tokens` | `600` | 每轮自动召回的预算；0 = 不召回（偏移/情绪不平稳时只注入这一行信号） |
| `semantic` | `auto` | 语义检索：`auto`（配了 API 用 API；装了 `nyx-memory[vector]` 用本地模型；都没有则关）/ `local` / `api` / `off` |
| `embedding_model` | 空 | 嵌入模型名；换模型后旧向量自动作废并后台重建 |
| `embedding_api_url` / `embedding_api_key` | 空 | OpenAI 兼容 `/embeddings`；密钥只进 `.env` |
| `rerank` | `off` | `local` = 召回后用本地多语言交叉编码器重排（需要 `[vector]`，首次下载约 470MB）。证据召回更准（LoCoMo +4.7 个点），但偏好类问题变差、每轮多 0.3–1.5 秒（CPU）。见主 README 的 Benchmark |
| `vault` | `off` | 密钥保险箱（v7.15.0）：聊天里贴的 token / API key / 密码加密存起来，主人要时经确认交还。Hermes 自己已带 cryptography，不用另装。见下面「密钥保险箱」 |
| `vault_reveal` | `direct` | `direct` = 原文不经过模型（要启用配套插件 `nyx-reveal`）；`cli` = 只在终端里取；`llm` = 原文交给模型转述 |
| `vault_kinds` | `all` | `tokens` = 不收密码 |
| `lang` | `auto` | 终端命令、`hermes memory setup` 用什么语言：`en` / `zh`；`auto` = 系统语言是中文就用中文（v7.16.0）。给模型看的文字一律英文 |
| `l3_llm` | `off` | `on` = 画像 / 偏移率 / 幽灵决策这些 L3 分析调用 DeepSeek 或 OpenRouter（`DEEPSEEK_API_KEY` / `OPENROUTER_API_KEY`，按当前 profile 取），会把相关的记忆片段、画像、近期决策发给它们。v7.15.2 以前不用打开、有 key 就用 |
| `auto_purge` | `on` | 每天一次，把过了隔离期的遗忘内容（和它们的素材文件）真删掉；还能救回的一条不动（v7.22.0） |
| `follow_ups` | `on` | 系统提示里的待跟进：你说过的、两天内要发生的事，以及十天内已经发生、你还没说结果的事（面试、复查、出差），agent 可以祝你顺利或者问一句结果（v7.23.0） |

## 终端命令：`hermes nyx …`（v7.15.2）

新版 Hermes 把 nyx-memory 装在它自己的隔离环境里，系统 Python 里没有 nyx，`python3 -m nexsandglass.…` 用不了。
nyx 是当前记忆插件时，Hermes 会多出 `hermes nyx` 这组命令（数据目录、保险箱开关按当前 profile 的 `nyx.json`）：

| 命令 | 作用 |
|---|---|
| `hermes nyx doctor` | 自检：每一项 ✅ / ❌ + 原因 |
| `hermes nyx vault list` / `show v3 [--copy]` / `delete v3` / `gc` / `rekey` / `audit` / `status` | 密钥保险箱 |
| `hermes nyx scrub [--apply] [--to-vault]` | 已经存下来的密钥 / 密码：找出来、原地隐去 |
| `hermes nyx repair [--apply]` | 中枢对不上日志时：按内容体检与修复 |
| `hermes nyx import [--list] [--all] [--session ID…] [--since 日期]` | 把以前在 Hermes 里的对话导进来（v7.20.0，见下） |
| `hermes nyx search <词> [--since 日期] [--until 日期] [-n N]` | 找记忆，相关的在前，带日期和谁说的；日期写 YYYY-MM-DD、YYYY-MM 或 YYYY，含当天。不写词、只给日期范围 = 那段时间说了什么（v7.20.0） |
| `hermes nyx show <编号> [--around N]` | 看一条的全文：日期、谁说的、能不能信、在不在隔离区 / 擦没擦，以及前后几句（v7.20.0） |
| `hermes nyx forget <编号…> \| --contains 一段话 [--now] [--yes]` | 先列出来问你，再送进隔离区；`--now` 当场擦掉（输入 `erase` 确认）（v7.20.0） |
| `hermes nyx restore [<编号…>]` | 不写编号：隔离区里有什么、哪天到期；写编号：救回来（v7.20.0） |
| `hermes nyx edit <编号> [改正后的话]` | 改一条：新的按你说的、原来的日期记下，旧的进隔离区（v7.20.0） |
| `hermes nyx media add <文件> [--note 附言] [--when 时间]` / `list` / `show` / `understand [--pending]` / `backends` / `forget [--now]` / `restore` | 图片、声音、视频的记忆（v7.21.0，见下） |
| `hermes nyx status` | 版本、Python、数据目录、保险箱 |

agent 转告给你的命令（自检警告、「在终端里取」）也会写成 `hermes nyx …`。每个命令都有 `--json`；都不经过模型。

## 聊天里的 `/nyx`（v7.20.0）

斜杠命令只能由 Hermes 的普通插件注册（记忆提供器注册不了），所以它作为配套插件随 nyx-memory 一起装。启用一次：

```bash
hermes plugins enable nyx-commands
```

| 命令 | 作用 |
|---|---|
| `/nyx status` | 版本、记忆条数、在记录还是已暂停、画像里有什么 |
| `/nyx pause` | 暂停记录：每轮对话、会话结束的补记、子任务结果、内置记忆的镜像都不写。召回和明确的要求（「忘掉那条」）照常，agent 也会知道现在没在记。重启、换会话之后仍是暂停的 |
| `/nyx resume` | 恢复记录 |
| `/nyx forget-last [N]` | 把这个聊天最近 N 轮（默认 1，最多 20）送进隔离区；回显忘掉的是哪几句，附上救回的命令 |
| `/nyx follow-ups [on\|off]` | 看新对话开始时模型会看到的待跟进（快到了的、刚过去还没说结果的；v7.23.0）；`off` / `on` 关掉或打开（`nyx.json` 里 `follow_ups: off` 优先） |

命令在 Hermes 里直接执行，回复只留在这个聊天里，模型看不到。谁能用：本机（CLI、TUI、桌面）和私聊里都能用；群聊、频道里只能看
`status` —— 群里任何人都能打命令。配了 `owner_ids` 时，只有主人能暂停、恢复和遗忘（在群里也行）。机器人永远不能。

## 图片、声音、视频（v7.21.0，框架）

`hermes nyx media add <文件>` 登记一张图、一段录音或视频：按内容哈希复制进 `<数据目录>/media/blobs/`（`media_copy: off` 只记路径；超过 `media_max_mb`，默认 500，的不收），写一条记录（「Shared a photo: …」），`--note` 的附言算你说的话，日期用 `--when`（默认现在）。理解后端把内容变成文字 —— 看图描述、语音转写、视频描述 —— 按来源 `media` 存成普通记忆：搜得到，召回时带「未经证实」标注。默认一个后端都没有。内置的是每一类一个本机命令，在 `nyx.json` 里设：

| 键 | 例子 |
|---|---|
| `media_audio_cmd` | `whisper-cli -m ggml-base.bin -nt -f {path}` |
| `media_image_cmd` | 本机视觉模型的命令行，打印 `{path}` 的描述 |
| `media_video_cmd` | 抽帧再描述的脚本 |

`{path}` 换成文件路径（没写就加在最后），标准输出就是结果，不经过 shell。Python 包可以通过入口点组 `nyx.media_understanders` 加后端（一个有 `name`、`kinds`、`available()`、`understand(asset, path)` 返回 `Moment` 列表的对象）。`hermes nyx media understand --pending` 用现在能用的后端补上以前登记的。`forget` 把一个素材的所有记录送进隔离区，文件留到清理时；`--now` 一起擦掉。还没做：Hermes 聊天里收到的照片、语音自动登记，给 agent 的工具。

## 导入以前的对话：`hermes nyx import`（v7.20.0）

装 Nyx 之前就在用 Hermes 的话，你说过的话在 Hermes 的会话库里（`$HERMES_HOME/state.db`）。`hermes nyx import` **只读**打开它，
列出能导的会话，问你导哪些（`--all` 全导，`--session` 挑几个，`--list` 只看不导）：

- **导**：本机会话（CLI、TUI、桌面）和私聊 —— 你说的话和 agent 的回复，按原来的时间，走正常的写入路径（密钥照样隐去、照样抽事实、画像跟着更新）
- **不导**：群聊和频道（别人说的）、子 agent 和定时任务（那里的「用户」是 agent 自己）、API / 批处理 / 测试会话；工具调用；回退掉的消息、压缩摘要、系统插入的续写；以及 Hermes 当时注入给模型的记忆文本 —— 只导你打的字
- 配了 `owner_ids` 时只导主人的私聊。没配、而私聊来自好几个人时，`--all` 会被拒绝：Nyx 分不清哪个是你
- Nyx 已经有的（一天之内的同一句）跳过；每个会话导到哪记着，再跑一次只补新的
- 旧话不会盖过新话：1 月说的「我住在杭州」在你 10 月说「搬到上海了」之后才导进来，会记成以前住的地方

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

## 每个会话、每轮做什么

- **系统提示**（每个会话一次）：`[About the user]` —— 用户说过的关于自己的事，用原话、新的在前：对 agent 的长期要求、
  饮食和健康、家人伴侣宠物、住处和工作、喜好（v7.19.0，`core/profile.py`）。同一件事后来说法改了，新的顶替旧的，被顶替的列在「Changed since」里（v7.22.0）。然后是待跟进：用户说过的、两天内要发生的事，和十天内已经发生、之后没人再提的事，附一句怎么问的分寸（v7.23.0，`core/followups.py`）。下面是有内容时才出现的：用户自己生成的画像、
  决策偏移、待办和纪律
- **prefetch**：对本轮问题现做本地召回（每轮约 30–140ms），每条带「哪天说的」；问到住处 / 公司 / 职位等时，
  附上双时态的当前信念（「user lives in 上海 (since 2026-09-01)」）。只注入和问题至少有一个实词对得上的，不相关的问题
  一条都不注入（v7.19.0）。默认最多 12 条；问「一共几次 / 隔了多久 / 哪个先 / 之前之后」时最多 20 条
  （`NYX_BREADTH_LINES`，0 = 关闭）。界面上显示 `🧠 Nyx — recalled N memories`
- **sync_turn**：原文落沙（审计日志）+ 晋升门 + 事实抽取；按 `turn_author` 绑定来源
- **subagent / cron / flush 上下文只读**：不写记忆，写类工具直接拒绝

## 工具

| 工具 | 作用 |
|---|---|
| `sandglass_search` / `sandglass_recent` | 搜索 / 最近的记忆。每条带编号、说的日期和谁说的；`since` / `until`（YYYY-MM-DD、YYYY-MM 或 YYYY，含当天）限定时间段；超过 300 字的截断（`more: true`）。主人和 agent 以外的来源带 `(unverified · source: …)` 标注（日期和时间段 v7.20.0） |
| `nyx_read` | 看一条的全文和前后几句（`around`，默认 3）。忘掉的、疑似注入的不给正文（v7.20.0） |
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

- 一个 Hermes 进程里有多个 profile（网关、桌面端）：每个 profile 的 `nyx.json` 设置（`l3_llm`、`vault`、`semantic`、`lang`……）只对这个 profile 的调用生效（v7.17.0；以前第一个 profile 的设置被复制进进程环境变量，对所有 profile 生效）。数据目录一个进程一个：`data_dir` 相同的 profile 共用记忆；配了别的 `data_dir` 的 profile 在这个进程里报不可用 —— 改成同一个 `data_dir`，或者让它单独一个进程。共用 `data_dir` 就是共用一份记忆：打开了 `l3_llm` 或嵌入 API 的 profile，在它自己的调用里发出去的内容来自这份共用的记忆，可能包括在另一个 profile 里说过的话。要彼此隔开的 profile 需要各自的 `data_dir`（也就各自一个进程）
- 语义检索需要嵌入后端（本地模型或 API）；没有时同义改写召不回来（问 company、原话是 joined Contoso）
- 从 v7.13.2 及更早版本升级后，首次启动会在后台回填全部历史记忆的向量（本地 CPU 模型几千条约几十分钟），期间语义召回逐步变全
- 隔离区到期不会自动真删：定期执行 `python3 -c "from nexsandglass.core import erasure; print(erasure.purge(apply=True))"`；到期没清时自检会提醒
- v3.x 的 `install.sh` 装过的机器：删掉 `~/.hermes/plugins/memory/nexsandglass` 与 `~/.hermes/plugins/sandglass`（旧的复制式插件，会让同一句话记两遍）
