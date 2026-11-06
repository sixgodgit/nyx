# Nyx 夜神 · nyx-memory

[English](https://github.com/sixgodgit/nyx/blob/main/README.md) | **简体中文**

> **给 agent 一份跟着你一辈子的记忆** —— 单用户、本地、写入不调用大模型。记得住，找得到，信得过，忘得掉。

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python) ![License](https://img.shields.io/badge/License-MIT-green) ![Version](https://img.shields.io/badge/version-7.20.1-blue) ![Deps](https://img.shields.io/badge/runtime%20deps-0-brightgreen)

Nyx 是 agent 的长期记忆层：接进 [Hermes](https://github.com/NousResearch/hermes-agent)（作为记忆插件）、
Claude Desktop / Claude Code 等任意 MCP 客户端，或者你自己的 Python 程序。
每句话原样留档，按需找回带日期的原话与「现在信什么」，数据全部是你机器上的普通文件。

## 为什么用 Nyx

| | 怎么做到的 | 实测 |
|---|---|---|
| **找得到** | 全文、倒排、TF-IDF、实体与（可选）语义检索多路融合；长记忆按长度打折，不会把说了答案的短消息挤下去；问「一共几次 / 隔了多久」时多给 agent 几条 | LongMemEval-S（470 道计分题）：答案所在的那次对话进前 5 条 **98.7%**；LoCoMo 证据召回 0.573（BM25 0.498）；BEAM 10 万–100 万 token 的长对话：证据 recall@10 **0.454**（BM25 0.333）；1000 万 token 时 0.326（BM25 0.218）。每轮召回约 30–140 ms |
| **认得你** | 你说过的关于自己的事 —— 对 agent 的要求（「以后用中文、别用 emoji」）、过敏和饮食、家人、住处工作、喜好 —— 用原话放在每个会话的系统提示里；只靠「什么 / 帮我 / what」和问题撞上的记忆不注入；家人的事记在家人名下 | 8 个个人故事（v7.19.0）：agent 该看到的事实从 25–38% 到 **100%**，每个问题的无关注入从 9 条降到 1–2 条，无关问题 88% 一条都不注入（以前 13–25%） |
| **分得清时间** | 双时态事实：「何时为真」和「何时得知」分开记 | 「我 2 月就换成吉利了」记下的是 2 月，不是说这句话的那天；能回答「现在信什么」「三月时我以为是什么」 |
| **信得过** | 写入时绑定来源与信任：只有主人亲口说的能成为规则；别人转述的、网页抄来的，召回时带「未经证实」标注 | 投毒演练：16 条攻击 0 条无标注进入 agent 提示，主人的 8 条正常信息 0 误伤 |
| **你说了算** | 任何聊天里 `/nyx pause` 暂停记录、`/nyx resume` 恢复，`/nyx forget-last` 收回上一轮；终端里 `hermes nyx search / show / forget / restore / edit` 查、看、忘、救回、改，不经过模型；`hermes nyx import` 把装 Nyx 之前在 Hermes 里说过的话导进来（v7.20.0） | 用 Hermes 自己的插件加载器和命令分发验证过，按终端和消息网关两种调用方式；导入的旧话「我住在杭州」不会盖过后来说的「我搬到上海了」 |
| **忘得掉、救得回** | 遗忘先进隔离区（检索立刻读不到），保留期（默认 30 天）内原样还原；到期后清理即真删，并验收向量、索引、图谱都清干净了 | 误删 150 条，逐字节救回；真删不可逆 |
| **便宜、私密** | 写入零 LLM 调用、零 API key、零运行时依赖；默认不联网（[例外](#privacy)） | 全部数据在 `~/.neurobase` |
| **密钥不进记忆** | 聊天里贴的 token、API key、私钥、密码，在写进任何地方之前换成 `[hidden: HF token …a1b2]` | LongMemEval-S 近 20 万轮真实对话只触发 19 轮（0.01%），全是贴出来的代码配置；普通句子 0 误伤 |
| **坏了看得见** | `python3 -m nexsandglass.doctor` 一条命令查每一项；Hermes 里自检失败会让 agent 转告你 | 语义索引、全文索引、遗忘完整性、规则投毒…… 每项 ✅ / ❌ + 原因 |

> 上面的检索数字是**检索**指标（零 LLM、可复现），不是 Mem0 / Zep 们报的「大模型作答 + 大模型裁判」的问答准确率，
> 不能直接比较。测法与全部数字见 [Benchmark](#benchmark)。

---

## 🚀 快速开始

### Hermes（推荐）

```bash
hermes plugins install sixgodgit/nyx/hermes-plugin --enable
hermes config set memory.provider nyx
```

重启 Hermes（网关：`hermes gateway restart`）就好。只用到 `hermes` 命令，不需要先在系统 Python 里装任何东西：
Hermes 把插件放进 `~/.hermes/plugins/nyx/`，再把它声明的依赖 nyx-memory 装进**它自己的**环境（`hermes update` 之后会重新装上）。
要语义检索（同义改写、跨语言也找得到；首次运行下载约 470MB 模型）就装 `sixgodgit/nyx/hermes-plugin-vector`。
[密钥保险箱](https://github.com/sixgodgit/nyx/blob/main/docs/secret-vault.zh-CN.md)：再 `hermes plugins enable nyx-reveal`，在 `hermes memory setup` 里把 `vault` 设成 `on`。

两个可选步骤（v7.20.0）：

```bash
hermes plugins enable nyx-commands    # 任何聊天里都能用 /nyx status · pause · resume · forget-last
hermes nyx import                     # 把以前在 Hermes 里的对话导进来（先列出来，问过你再导）
```

<details>
<summary>老版本 Hermes（<code>hermes plugins install --help</code> 里没有 <code>--no-deps</code>，比如 PyPI 上的 hermes-agent 0.19.x）</summary>

老版本不会装插件声明的依赖，也不认 pip 入口点。把 nyx-memory 装进 Hermes 所在的 Python，再放入口文件：

```bash
pip install nyx-memory                       # 和 Hermes 同一个 Python
python3 -m nexsandglass_hermes install       # 也认新版：新版上它就是替你跑上面那两条命令
```

`install` 会从 `hermes` 命令找到 Hermes 用的 Python（包括 `#!/bin/sh` 包装脚本），缺 nyx-memory 就装进去，
在 `plugins/nyx/` 放入口文件，设 `memory.provider: nyx`，最后用 Hermes 自己的加载器加载一次确认。`--check` 只检查不改。
</details>

之后每轮对话自动记忆、自动召回；agent 还多了 `sandglass_search`（带日期，可限定日期范围）、`nyx_read`（看一条的全文和前后几句）、`nyx_belief`（现在 / 当时信什么）、`nyx_forget` / `nyx_restore`（遗忘与还原）、`nyx_health`（自检）等工具。终端里：
`hermes nyx search 花生 --since 2026-03`、`hermes nyx show <编号>`、`hermes nyx forget / restore / edit <编号>`、`hermes nyx doctor` 等。完整说明见 [`nexsandglass_hermes/README.zh-CN.md`](https://github.com/sixgodgit/nyx/blob/main/nexsandglass_hermes/README.zh-CN.md)。

### MCP（Claude Desktop / Claude Code / 任意 MCP 客户端）

```bash
pip install nyx-memory
claude mcp add nyx -- python3 -m nexsandglass.interfaces.sandglass_mcp     # Claude Code
```

其他客户端在配置文件里加：

```json
{ "mcpServers": { "nyx": { "command": "python3", "args": ["-m", "nexsandglass.interfaces.sandglass_mcp"] } } }
```

27 个工具，见 [MCP 工具](#mcp-tools)。

### Python

```python
from nexsandglass.runtime import observe, recall, forget, restore

observe("我上个月从杭州搬到了上海", source="user")   # source="user" = 主人亲口说的
print(recall("我现在住哪").text)                       # → user lives in 上海 (since 2026-09-01)

r = forget({"contains": "搬到了上海", "dry_run": True})  # 先预览
r = forget({"contains": "搬到了上海"})                 # 进隔离区：召回立刻读不到
print(r["recoverable"], r["purge_after"])             # True 2026-11-04 …
restore(r["preview"][0]["mem_id"])                     # 隔离期内原样还原
```

装完运行 `python3 -m nexsandglass.doctor`，全部 ✅ 就说明一切正常。

---

## ⚙️ 它怎么工作

**写入**（每一轮对话之后，不调用大模型）

1. 原话追加到 `sandglass.txt`，拿到唯一编号 `mem_id` —— 这份日志是唯一真相来源，其余一切都能从它重建
2. 绑定来源与信任：主人亲口说的 / 机器人 / 群里其他人 / 外部内容，写入时定下，之后改正文也改不了
3. 晋升门：值得记的进入加工层（事实、图谱、engram）；闲聊、验证码不晋升（原话仍在日志里，照样搜得到）
4. 话语理解：「2 月 25 号就换成吉利了」→（user, 车, 吉利, 自 2 月 25 日起, 至今仍真），写成双时态事实；旧事实不被覆盖，而是记下「到何时为止」。很早以前说的、现在才写进来的话（导入的旧对话、改正的一条）只补成历史，不会盖过后来知道的（v7.20.0）
5. 全文、倒排、（可选）语义索引增量更新

**每个会话**（系统提示，v7.19.0）

用户说过的关于自己的事，用原话、新的在前：对 agent 的长期要求（「以后用中文、别用 emoji」）、饮食和健康（过敏、吃素）、
家人伴侣宠物、住在哪做什么、喜欢和讨厌什么。按规则从主人自己的消息里挑（不调大模型），被遗忘的立刻消失。
问「今晚吃什么」和「我对花生过敏」一个词都对不上，靠的就是这一段。

**召回**（每一轮对话之前，本地完成）

1. 全文（FTS5）、倒排、TF-IDF、实体、（可选）语义向量多路检索
2. 融合排序：字面完整命中在前，语义作为辅助（权重减半、只认明显高于背景的命中）；超过 300 字的记忆按长度打折，长回复挤不掉说了事实的那条短消息
3. 补上当前信念（「user lives in 上海 (since 2026-09-01)」）；家人朋友的事记在他们自己名下（「user's mother lives in 青岛」）
4. 信任门：来源被污染的不出库，未经证实的带标注、不能当规则
5. 相关门（v7.19.0）：至少和问题有一个实词对得上才注入 —— 只靠「什么 / 帮我 / 几个 / what / how」撞上的不算；什么都不相关就一条都不注入
6. 带日期注入 agent：默认最多 12 条；问「一共几次 / 隔了多久 / 哪个先 / 之前之后」时最多 20 条

---

## 📋 能力一览

| 能力 | 模块 | 说明 |
|---|---|---|
| 🆔 **原文日志 + ID 中枢** | `core/memid.py`、`core/sandglass_log.py` | 每条记忆一个 `mem_id` 与行号跨度；墓碑阻止已删记忆在重建时复活 |
| 🔍 **多路检索** | `core/search_router.py`、`core/semantic.py` | FTS5 / 倒排 / TF-IDF / 影子沙 + 可选语义（本地多语言模型或 OpenAI 兼容 API），RRF 融合 |
| 🪪 **常驻画像** | `core/profile.py` | 用户亲口说的关于自己的事（对 agent 的要求、饮食健康、家人、住处工作、喜好）每个会话都在系统提示里；只收主人的、来源可信的，跟着遗忘走（v7.19.0） |
| 🚪 **相关门** | `core/relevance.py` | 注入前：只靠虚词和问题撞上的不注入；什么都不相关就一条都不注入（v7.19.0） |
| 🕰️ **双时态事实** | `engram/loops/temporal_fact.py`、`core/understand.py` | `as_of` 问世界、`known_at` 问当时的信念、`belief_timeline` 问看法怎么变的；假设的时间不冒充陈述的时间 |
| 🛡️ **来源信任** | `core/provenance.py` | trusted / unverified / tainted；只有主人亲口说的能成为规则（防 OWASP ASI06 记忆投毒） |
| 🕯️ **遗忘隔离区** | `core/quarantine.py`、`core/erasure.py` | 两段式遗忘：隔离 → 还原或到期擦除；擦除贯通日志、索引、图谱、向量，可独立验收 |
| 🔑 **密钥不进记忆** | `core/secret_filter.py`、`scrub.py` | 写入前隐去 token / API key / 私钥 / 连接串 / 密码；已经存下的用 `scrub` 原地隐去，按原始字节复查 |
| 🔐 **密钥保险箱**（可选） | `core/secret_vault.py`、`vault.py` | 隐去的原文加密存起来，主人要时经人工确认交还；直达取回时模型只拿到句柄、看不到原文（真实 Hermes 实测） |
| 🩺 **自检** | `doctor.py` | 中枢与日志一致性、各索引、遗忘完整性、隔离区到期、规则投毒，逐项给出原因 |
| 🧯 **体检与修复** | `repair.py`、`core/forensics.py` | 中枢对不上日志时按内容逐条核对：补缺失、挪错位、认回被覆盖的记忆、撤下合成记录及其派生「事实」；先备份，不改日志 |
| 🔌 **Hermes 记忆提供器** | `nexsandglass_hermes/`、`core/memory_provider.py` | 按 Hermes 的 `MemoryProvider` 接口实现；`hermes plugins install sixgodgit/nyx/hermes-plugin --enable` 装进 Hermes |
| ⏸️ **聊天里的 `/nyx`** | `nexsandglass_hermes_commands/`、`core/recording.py` | `status`、`pause` / `resume`（暂停 / 恢复记录，召回照常）、`forget-last [N]`（把这个聊天最近几轮送进隔离区，回显给你核对）。在 Hermes 里直接执行，不经过模型；群聊里只有主人能改（v7.20.0） |
| 🗂️ **查找和改正** | `memory_cli.py`、`core/lookup.py` | `hermes nyx search`（`--since` / `--until` 限定日期）、`show`（全文和前后几句）、`forget`（`--now` 当场擦掉）、`restore`、`edit`（改正后的话替换旧的，日期不变）；agent 的 `sandglass_search` / `nyx_read` 用同一套代码（v7.20.0） |
| 📥 **从 Hermes 导入** | `history_import.py` | 只读打开当前 profile 的 Hermes 会话库，把你自己的对话（本机和私聊；不含群聊、子 agent、定时任务）按原来的时间导进来；Nyx 已有的跳过；可以断点续导（v7.20.0） |
| 🔌 **MCP 服务** | `interfaces/sandglass_mcp.py` | stdio，纯标准库，27 个工具 |
| 👻 **Déjà Vu** | `dejavu/`（独立子包） | 分清「没聊过」和「聊过但找不到」，见下一节 |
| 🕸️ **知识图谱** | `features/weavethread.py` | 实体关系三元组 + 类型化关系（belongs_to / evolved_from / contradicts …）+ PPR 图增强 |

<details>
<summary><b>更多模块</b>（记忆加工、梦境、画像、可视化…）</summary>

| 能力 | 模块 | 说明 |
|---|---|---|
| 🧬 记忆加工 | `engram/` | Tulving 四类记忆 + Ebbinghaus 衰减；semantic 覆盖 / emotional 强化 / procedural 去重 / episodic 直插 |
| 🌱 候选晋升 | `runtime/promotion.py` | 「什么值得记住」：Observation → Extract → Score → Type → Promote / Session / Drop |
| 🧭 意图召回 | `runtime/intent.py` | MemoryIntent：实体 / 时间 / 领域 / 关系感知排序 |
| 📦 记忆 Bundle | `runtime/bundle.py` | 10 个槽位的 system prompt 组装，按「相关度 / token 成本」取舍 |
| 🌙 梦境与巩固 | `engram/dream_pipeline.py`、`runtime/consolidation.py` | 浅睡总结 → 深睡内化 → 灵感联结；所有变更先成提案、过校验，可快照回滚 |
| 🔄 记忆自我演化 | `engram/evolve.py`、`engram/loops/` | 事实↔图谱、梦境↔加工、画像↔上下文、召回↔重要性四个闭环 |
| 📊 情绪与画像 | `core/emotion_vocab.py`、`l3/` | 状态追踪、偏移率、回音折、待办 |
| 🧩 技能蒸馏 | `features/skill_distiller.py` | 反复出现的工作流 → 可复用的 skill 草案 |
| 🤖 LLM 图谱抽取 | `core/llm_extract.py` | 可选的 LLM 知识图谱补充抽取与实体归一化（失败降级为规则） |
| 📚 记忆书 NyxBook | `nyx-web/`、`nyx_server.py` | 可视化浏览、写日记、盖章（FastAPI + Vue3，可选） |

开发历程（Phase 0–7 的设计与取舍）见 [`CHANGELOG.md`](https://github.com/sixgodgit/nyx/blob/main/CHANGELOG.md) 与 [`docs/`](https://github.com/sixgodgit/nyx/tree/main/docs)。
</details>

---

## 👻 Déjà Vu：分清「没聊过」和「聊过但找不到」

用户说「我们聊过的那家川菜馆」，检索返回空 —— 是真没聊过，还是当时的说法和现在不一样？
所有主流检索器在这两种情况下返回的是同一个东西：空。

| | 向量检索 | BM25 / FTS | **Nyx Déjà Vu** |
|---|---|---|---|
| 没发生过 | top-k 非空但全是噪音 | 0 命中 | **陌生**（明确信号） |
| 发生过但找不到 | top-k 非空但全是噪音 | 0 命中 | **熟悉 + 找回痕迹** |

```python
from nexsandglass.dejavu import DejaVu      # 零依赖，stdlib + sqlite3

dv = DejaVu("./my_memory")
dv.imprint("msg-1", "上周三和张老板聊了川菜馆的事")
dv.sense("我们聊过的那家川菜馆")   # -> 熟悉 0.67，见过「川菜」「菜馆」（普通检索在这里返回空）
dv.sense("上次说的那篇量子计算论文")  # -> 陌生 0.0
dv.hunt("川菜馆")                 # -> [Phantom(token='川菜', refs=['msg-1'], ...), ...]
```

两层结构：**Veil**（Bloom Filter，128 KiB）回答「这个词见过没有」，**Mist**（SQLite）记录它何时、何地、几次出现过。
如实说明：它不提高召回率，只把 miss 分成两类 —— **分类有效，寻回有限**。常用词不算 token，不会让一个问题变得「熟悉」：英文的 the、about、talked……（v7.16.1），中文的我们、聊过和「的那」这类碎片（v7.16.2）；记得久了普通实词也都见过，所以要看见过的是哪个词 —— 少见的名字是强证据，普通名词是弱证据。
实测见 [`benchmarks/dejavu_bench.py`](https://github.com/sixgodgit/nyx/blob/main/benchmarks/dejavu_bench.py)，设计取舍见 [`docs/dejavu.zh-CN.md`](https://github.com/sixgodgit/nyx/blob/main/docs/dejavu.zh-CN.md)。

---

## 📦 安装（详细）

| 方式 | 命令 | 说明 |
|---|---|---|
| pip | `pip install nyx-memory` | 零运行时依赖；Hermes 插件与 MCP 服务都在里面 |
| pip + 语义检索 | `pip install "nyx-memory[vector]"` | 装 sentence-transformers；首次运行把模型（约 470MB）下载到 `~/.cache/nexsandglass/models` |
| 从源码 | `./install.sh [--vector]`（Windows：`install.bat [--vector]`） | 检查 Python 版本、安装、提示清理旧版留下的插件文件 |
| Docker | `docker build -t nyx-memory .` | MCP 服务走 stdio：在 MCP 客户端里配置 `docker run -i --rm -v nyx-data:/data nyx-memory`；加语义检索用 `--build-arg EXTRAS="[vector]"` |

包名 `nyx-memory`（PyPI），Python 模块名 `nexsandglass`。需要 Python 3.10+。

**从旧版本升级**：`pip install -U nyx-memory`（用了语义检索就是 `"nyx-memory[vector]"`），重启 Hermes，再跑一次 `doctor`。
- 第一次启动会在后台给全部历史记忆补向量（本地 CPU 模型几千条约几十分钟），期间语义召回逐步变全，`doctor` 显示「正在回填」
- 用 v3.x 的 `install.sh` 装过的机器：删掉 `~/.hermes/plugins/memory/nexsandglass` 与 `~/.hermes/plugins/sandglass`。
  那是旧的复制式插件，留着会和现在的插件同时生效，同一句话可能被记两遍

---

## 🔧 配置

**自检**：`python3 -m nexsandglass.doctor`（Hermes 里是 `hermes nyx doctor`；`--json` 机读；有项目失败时退出码 1）。升级后、或者觉得「记得不对」时先跑它。

**Hermes 用户**：`hermes memory setup` 写入 `$HERMES_HOME/nyx.json`（`data_dir`、`prefetch_tokens`、
语义检索的 `semantic` / `embedding_model` / `embedding_api_url` / `embedding_api_key`；密钥只进 `.env`），
键见 [`nexsandglass_hermes/README.zh-CN.md`](https://github.com/sixgodgit/nyx/blob/main/nexsandglass_hermes/README.zh-CN.md)。

**环境变量**（所有入口通用；Hermes 下环境变量优先于 `nyx.json`）：

| 变量 | 默认 | 作用 |
|---|---|---|
| `NEXSANDBASE_HOME` | `~/.neurobase` | 记忆数据目录 |
| `NYX_LANG` | `auto` | 给人看的输出（终端命令、`hermes memory setup`、安装）用什么语言：`en` / `zh`；`auto` = 系统语言是中文就用中文。Hermes 里是 `lang`。给模型看的文字（工具说明、召回标签）一律英文 |
| `NYX_EMBED` | `auto` | 语义检索后端：`auto` / `local` / `api` / `off` |
| `NYX_EMBED_MODEL` | 本地 `paraphrase-multilingual-MiniLM-L12-v2`；API `text-embedding-3-small` | 嵌入模型 |
| `EMBEDDING_API_URL` / `EMBEDDING_API_KEY` | 空 | OpenAI 兼容 `/embeddings` |
| `NYX_EMBED_Z` | `3` | 语义命中要比背景高出多少（稳健 z 分数）才算证据 |
| `NYX_EMBED_WEIGHT` | `0.5` | 融合时语义这一票的权重（词法一票是 1） |
| `NYX_EMBED_CACHE` | `~/.cache/nexsandglass/models` | 本地嵌入模型的存放目录 |
| `NYX_RERANK` | `off` | `local` = 召回后用本地交叉编码器重排（需要 `[vector]`）。更准、更慢，偏好类问题变差，见 [Benchmark](#benchmark)。`NYX_RERANK_MODEL` / `_DEPTH`（50）/ `_MODE`（rrf）/ `_MAXLEN`（128）/ `_BUDGET_MS`（2000）可调 |
| `NYX_SECRET_FILTER` | `on` | 密钥 / 密码写入前隐去：`on`（token + 密码 + `<private>`）/ `tokens`（不动密码）/ `off` |
| `NYX_L3_LLM` | 关 | `1` = L3 分析（画像 / 偏移率 / 幽灵决策）调用 DeepSeek / OpenRouter（`DEEPSEEK_API_KEY` / `OPENROUTER_API_KEY`）。v7.15.2 以前有 key 就用 |
| `NYX_VAULT` | `off` | `on` = 隐去的原文加密存进保险箱（要 `pip install 'nyx-memory[vault]'`）。`NYX_VAULT_REVEAL`（`direct` / `cli` / `llm`）、`NYX_VAULT_KINDS`（`all` / `tokens`）、`NYX_VAULT_BACKEND`（`auto` / `file` / `keyring`）、`NYX_VAULT_KEY`，见 [docs/secret-vault.zh-CN.md](https://github.com/sixgodgit/nyx/blob/main/docs/secret-vault.zh-CN.md) |
| `NYX_BREADTH_LINES` | `20` | 问「一共几次 / 隔了多久 / 哪个先 / 之前之后」时召回与注入的条数；`0` = 不放宽 |
| `NYX_UNDERSTAND_LLM` | 关 | `1` = 事实抽取走 LLM（结果必须过校验；失败退回规则） |
| `WTHREAD_LLM_EXTRACTION` | 关 | 知识图谱补充抽取走 LLM（需要 `LLM_EXTRACT_API_URL`） |
| `LLM_EXTRACT_API_URL` / `NYX_UNDERSTAND_MODEL` | 空 / `deepseek-v4-flash` | 抽取用的 OpenAI 兼容网关与模型 |
| `NYX_FORGET_RETENTION_DAYS` | `30` | 遗忘隔离期 |
| `NYX_BUNDLE_MAX_TOKENS` | `1500` | system prompt 注入预算 |
| `NYX_JOURNAL_MIRROR_MB` | `32` | 日志内存镜像上限（日志文件大小，MB）；超过则退回逐行扫描。约占日志 3.5 倍内存 |
| `NYX_WARM_WAIT_SECONDS` | `10` | Hermes 首轮对话最多等后台预热多久 |
| `NYX_RUNTIME` | `1` | `0` = 紧急回滚到旧写入路径 |
| `NYX_EMBED_PROVIDER` | 空 | 自定义嵌入后端（`包.模块:工厂函数`） |
| `DEEPSEEK_API_KEY` / `OPENROUTER_API_KEY` | 空 | 只在 `NYX_L3_LLM=1` 时使用：画像 / 偏移率 / 幽灵决策等 L3 分析调用 DeepSeek / OpenRouter，见[数据与隐私](#privacy) |

---

<a id="mcp-tools"></a>

## 🛠️ MCP 工具

启动（stdio）：`python3 -m nexsandglass.interfaces.sandglass_mcp`

| 工具 | 功能 |
|------|------|
| `memory_observe` | 统一写入（经晋升门与来源绑定） |
| `memory_recall` | 统一召回（意图自适应 + 信任门） |
| `memory_feedback` | 召回反馈（强化 / 弱化） |
| `memory_forget` | 遗忘：`memory_id` / `contains` / `dry_run`；**默认进隔离区**，保留期内可还原；什么都没删时返回 `ok=false` |
| `sandglass_search` / `sandglass_semantic` | 搜索，每条带编号、日期和谁说的，`since` / `until` 限定日期范围（v7.20.0）/ 同义词 + SimHash + TF-IDF 搜索 |
| `nyx_read` | 看一条的全文：什么时候说的、谁说的、前后几句（v7.20.0） |
| `sandglass_recent` / `sandglass_ping` | 按日期最近的 N 条，可限定日期范围 / 健康检查（语义索引坏了时 `status=degraded`） |
| `sandglass_persona` / `sandglass_tasks` | 主人画像 / 待办 |
| `sandglass_offset` / `sandglass_echo` / `sandglass_chart` | 偏移率 / 情感风向 / 情绪熵图 |
| `sandglass_dejavu` | Déjà Vu 模糊感知 |
| `sandglass_thread` / `sandglass_thread_graph` / `sandglass_thread_weave` / `sandglass_thread_add` | 知识图谱查询 / 子图 / 因果链 / 手动补三元组 |
| `sandglass_dream` | 幽灵决策（「如果选另一个会怎样」） |
| `sandglass_export` / `sandglass_import` / `sandglass_migrate` | 导出 / 导入（含 ChatGPT、Claude 对话导出）/ 打包迁移 |
| `sandglass_soul_export` / `sandglass_soul_merge` | 灵魂差分导出 / 合并 |
| `fact_store` / `fact_feedback` | 事实存储 / 信任反馈 |

> MCP 目前没有还原工具；被遗忘的记忆用 `scripts/nyx_quarantine.py restore <mem_id>` 或 Python 的 `restore()` 还原。
> 双时态查询（现在 / 当时以为 / 演变）与隔离区工具目前只在 Hermes 插件里提供（`nyx_belief` / `nyx_restore` / `nyx_quarantine`）。

---

<a id="privacy"></a>

## 🔒 数据与隐私

默认目录 `~/.neurobase`（`NEXSANDBASE_HOME` 可改）。**`sandglass.txt` 是唯一真相来源**，其余都可以从它重建。

| 文件 | 内容 |
|------|------|
| `sandglass.txt` | 原始日志：`时间 \| 发送者 \| 正文`，逐条追加 |
| `nyx.db` | ID 中枢（mem_id ↔ 行号）、墓碑、来源与信任、遗忘隔离区 |
| `sandglass.db` / `sandglass.idx` | FTS5 全文索引 / 倒排索引 |
| `shadow_sand.db` | 影子沙（信任评分、实体、标签）+ 双时态事实 `wthread_triples` |
| `semantic.db` | 语义索引（表 `embeddings`；v7.13.3 起。没有嵌入后端时不创建）。旧版的 `vectors.db` 不再使用 |
| `engram_store.jsonl` | engram 加工层（四类记忆 + 衰减） |
| `veil.bin` / `mist.db` | Déjà Vu（Bloom Filter / 出现记录） |
| `persona/`、`archive/` | 画像与决策日志 / 冷归档 |

Hermes 用户：数据目录在 `HERMES_HOME` 之外时，`hermes backup` 会自动把它带上（插件的 `backup_paths`）。

**密钥和密码不进记忆**（v7.14.0）：聊天里贴的 token（Hugging Face、GitHub、OpenAI / DeepSeek / Anthropic / OpenRouter、
PyPI、AWS、Google、Slack、Stripe、Telegram、JWT…）、私钥、带密码的连接串、`XXX_API_KEY=…` 这类赋值、「密码是 …」，
以及 `<private>…</private>` 包住的内容，**在写进任何地方之前**就换成占位符：token 留末 4 位（`[hidden: HF token …a1b2]`，
agent 还能说出「你给过一个 HF token」），密码一个字符不留。日志、中枢、索引、影子沙、图谱、向量里都只有占位符；
发给嵌入 API 的问句也先隐去。`NYX_SECRET_FILTER=tokens` 只管 token，`off` 关闭。v7.16.0 以前存下的占位符是 `[已隐去: …]`，照样认、不改写。
Hermes 自己的会话记录（`~/.hermes` 下）不归 Nyx 管。

**想让 agent 之后还能把 key 告诉你？**（v7.15.0，默认关）打开密钥保险箱：`NYX_VAULT=on`（Hermes 里是 `vault: on`），
`pip install 'nyx-memory[vault]'`。隐去的原文加密存起来（主密钥在 `~/.config/nyx/vault.key`，不在数据目录里），占位符带编号
（`[hidden: HF token …a1b2 #v3]`）。你说「把我之前给你的 HF token 发我」，agent 调 `nyx_secret_reveal`：先经人工确认
（Hermes 的审批；审批被 `/yolo` 关掉时改成回复一句确认短语），再由配套插件在回复发出前把句柄换成原文 ——
**模型服务商看不到原文**，Hermes 的会话记录里也只有句柄（`hermes plugins enable nyx-reveal` 启用一次）。
最稳的是在终端里取：`python3 -m nexsandglass.vault show v3`。贴进聊天的那一刻原文已经发给了模型服务商，所以贴过的 key 还是该换掉；
机器人在群里的话，群里任何人都会被当成主人。威胁模型与实测见 [docs/secret-vault.zh-CN.md](https://github.com/sixgodgit/nyx/blob/main/docs/secret-vault.zh-CN.md)。

**删了就是没了**：遗忘到期清理（或 `mode="purge"`）之后，Nyx 会合并全文索引并 VACUUM 每个库 ——
SQLite 删行后原文还留在文件的空闲页里，FTS5 删掉的词元连 VACUUM 都清不掉（v7.14.0 以前就是这样）。

**什么时候会联网**：默认什么都不配，Nyx 不发起任何对外请求。以下情况例外 —— 括号里是会被发出去的内容：
- 装了 `[vector]` 后第一次运行：从 HuggingFace 下载嵌入模型（不发送你的数据）
- 设了 `EMBEDDING_API_URL`：语义检索改用你指定的嵌入 API（记忆原文与问句）
- `NYX_UNDERSTAND_LLM=1` 或 `WTHREAD_LLM_EXTRACTION` 并配了 `LLM_EXTRACT_API_URL`：事实 / 图谱抽取走你指定的 LLM（被抽取的那句话）
- `NYX_L3_LLM=1`（Hermes 里是 `l3_llm: on`）并且有 `DEEPSEEK_API_KEY` 或 `OPENROUTER_API_KEY`：画像、偏移率、幽灵决策这些
  L3 分析调用 DeepSeek / OpenRouter（相关的记忆片段、画像、近期决策）。**v7.15.2 以前不用打开，环境里有 key 就用** ——
  在 Hermes 里主模型多半就走 OpenRouter，于是每次搭系统提示，画像和近期决策都被发了出去。现在默认关

---

## ❓ 常见问题

**语义检索没生效？** 跑 `doctor` 看 `semantic_index` 一项。常见原因：没装 `[vector]`；模型还没下载完（首次运行）；
网络连不上 HuggingFace 的下载服务器（受限网络里可试 `HF_HUB_DISABLE_XET=1`，或在能联网的机器上下载后把
`~/.cache/nexsandglass/models` 拷过来，用 `NYX_EMBED_CACHE` 指定位置）。语义坏了不影响词法召回，也不会静默：`doctor` 报 ❌，Hermes 里 agent 会提醒你。

**同一句话被记了两遍？** 多半是 v3.x 的旧插件文件还在，见上面「从旧版本升级」。

**想删掉一条记忆？** 终端里先 `hermes nyx search …` 找到，再 `hermes nyx forget <编号>`（先列出来问你；`--now` 当场擦掉）。聊天里 `/nyx forget-last` 收回上一轮（需要启用 `nyx-commands`）；agent 的 `nyx_forget` 先预览、再确认。Python 用 `forget()`。
后悔了：`hermes nyx restore` 列出隔离区，`hermes nyx restore <编号>` 救回（或 `nyx_restore` / `restore()`）。

**记错了？** `hermes nyx edit <编号> "正确的说法"`：改正后的话算你说的、日期还是原来那天，旧的进隔离区（由它得出的事实一起撤掉）。

**想让它暂时别记？** 聊天里 `/nyx pause`（帮别人问事、聊私事），`/nyx resume` 恢复。暂停期间什么都不写（每轮对话、会话结束的补记、子任务结果）；召回和你明确要求的照常，agent 也会知道现在没在记。重启之后仍是暂停的。

**装 Nyx 之前就在用 Hermes？** `hermes nyx import` 列出以前的对话，导你挑的（`--all` 全导），按原来的时间。只导你说的话和 agent 的回复 —— 不导群聊、子 agent、定时任务，也不导 Hermes 注入的记忆文本；私聊里有好几个人时先配 `owner_ids`。Hermes 的库只读打开，再跑一次只补新的。
隔离期（默认 30 天）过了**不会自动删**：执行
`python3 -c "from nexsandglass.core import erasure; print(erasure.purge(apply=True))"` 真删
（源码仓库里也可以 `python3 scripts/nyx_quarantine.py purge --apply`），建议放进每天的 cron；到期没清时 `doctor` 会报 ❌。

**想让召回更准？** 装了 `[vector]` 的话可以设 `NYX_RERANK=local`（Hermes 里是 `rerank: local`）：
LoCoMo 证据召回 +4.7 个点，代价是每轮召回多 0.3–1.5 秒、偏好类问题变差（[Benchmark](#benchmark)）。

**注入的记忆太多、太费 token？** Hermes 里调小 `prefetch_tokens`（默认 600 ≈ 12 条）；`NYX_BREADTH_LINES=0` 关掉「汇总 / 时间类问题多给 10 条」。

**多个 agent / 多台机器共用一份记忆？** 指向同一个 `NEXSANDBASE_HOME` 即可（同一台机器上多个进程同时读写是安全的：写入有跨进程锁）。
跨机器请用 `sandglass_export` / `sandglass_migrate` 打包迁移，不要用网盘同步正在写的数据目录。

**以前在聊天里贴过 token / 密码？** v7.14.0 之后的写入会自动隐去；之前存下来的，跑 `python3 -m nexsandglass.scrub`（Hermes 里是 `hermes nyx scrub`）
看有几处、在哪（只读，不输出原文），停掉 Hermes / MCP 服务后 `--apply` 原地隐去。它会重建索引、VACUUM，
最后按原始字节把整个数据目录扫一遍。旧的修复备份（`backups/` 下的 `.db`）不会被改，会列出来，确认没用就删掉。
**另外记得去对应的平台把那个 token 吊销** —— 它已经发给过你用的大模型服务了，Nyx 删掉的只是自己存的那份。

**系统提示里出现「⚠️ Nyx health: …」？** 某个子系统坏了（比如语义索引），召回照常但不完整。跑 `doctor` 看具体原因。

**`doctor` 里 `hub_vs_journal` / `fts_index` / `erasure_integrity` 红了？** 中枢和日志对不上了（比如测试或别的程序往数据目录里写过东西）。先跑 `python3 -m nexsandglass.repair`：只读体检，按内容分出缺失、错位、被覆盖、合成孤儿、真假「泄漏」，只给计数和 seq 范围。然后停掉 Hermes 网关 / MCP 服务，`python3 -m nexsandglass.repair --apply`（先自动备份到 `<数据目录>/backups/`，日志一个字节不改），修完重启服务。**不要按 `erasure_integrity` 报的 seq 去 `forget`**：墓碑可能指错了位置，抹掉的会是真实记忆。体检里「遗忘没做完」的记录（日志正文还在）要 `--finish-erasures` 才会在日志里抹掉；日志里也有测试数据的发送者时，确认后可以加 `--forget-senders golden,e2e`（进隔离区，30 天内可还原）。

---

## ⚠️ 已知局限

- **单用户设计**：一个人一份记忆；一个进程同一时刻只服务一个数据目录
- **还没用强模型测端到端问答**：[Benchmark](#benchmark) 里是检索指标、agent 实际看到的内容，以及 PersonaMem 上一个本地小模型（1.5B）的四选一；Mem0 / Zep 们报的是大模型作答、大模型裁判的问答准确率
- **还弱的题型**：LoCoMo 多跳题 agent 看到的证据只有 41%（开重排 48%）；LongMemEval 偏好题 0.71（开重排反而更低）；BEAM 的「总结」「事件先后」题要用到散在长对话里的十几条消息，agent 只看到其中 6–8%
- **英文事实抽取较少**：规则抽取以中文为主，英文主要靠检索原文 + 日期；可开 `NYX_UNDERSTAND_LLM` 用 LLM 抽取。不装 `[vector]` 时，和记忆没有共同词的英文改写问法找不到（「which editor do I use」找不到「I switched from VS Code to Zed」）
- **Déjà Vu 在很长的历史上**：常用词已经不算了（英文 v7.16.1、中文 v7.16.2），但普通实词会越积越多。10 个不相干的测试问题里，记了 2000 轮英文之后仍有 10 个靠 teacher、workshop 这类词判成熟悉（20 轮时 0 个、200 轮时 3 个；`benchmarks/dejavu_english_real.py`）；记了 2000 句中文闲聊之后有 3 个（企鹅、玻璃、老师；`benchmarks/dejavu_chinese_real.py`）
- **常驻画像和相关门是规则，不是理解**（v7.19.0）：画像只认常见说法（「我对…过敏」「以后…别…」「我女儿…」「I'm allergic to…」
  「from now on…」）；只是流露出来的偏好（「又是香菜，烦死了」）进不去。相关门是词法的：不装 `[vector]` 时，和问题一个实词都
  对不上的相关记忆不会注入（装了的话语义命中照样放行）
- **默认嵌入模型偏小**：本地多语言 MiniLM 胜在小而快；换更强的 API 模型时，融合权重可能需要重新调（`NYX_EMBED_Z` / `NYX_EMBED_WEIGHT`）

---

<a id="benchmark"></a>

## 📊 Benchmark

每个评测都逐轮走 Hermes 插件真实的写入路径。前两个（v7.19.0）看一个个人 agent 关于它的用户能看到什么；后面三个公开基准
只测**检索**（零 LLM、可复现）。语义检索用默认的本地模型 `paraphrase-multilingual-MiniLM-L12-v2`
（`pip install "nyx-memory[vector]"`）。

**个人场景**（`benchmarks/personal_eval.py`，故事在 `benchmarks/personal_cases.py`；结果 `benchmarks/results/personal_eval.json`）。
8 个故事，中英文各 4 个：一个人和自己的 agent 相处 40 天，300 轮工作和闲聊之间说过饮食、过敏、对 agent 的长期要求
（「以后用中文、别用 emoji」）、家人、住处和搬家。之后开一个新会话问 62 个问题：个人问题（「今晚吃什么」「感冒发烧能吃什么药」）、
2 个工作问题、16 个无关问题（「地球到月球多远」）。按 agent 实际看到的打分 —— 这个会话的系统提示常驻块 + 这一轮注入的记忆。
4 个故事在开发时用，另外 4 个留到最后才跑。纯词法（默认安装）。

| | 开发集 v7.18.1 | 开发集 v7.19.0 | 留出集 v7.18.1 | 留出集 v7.19.0 |
|---|---|---|---|---|
| 该看到的事实里看到了多少 | 38% | **100%** | 25% | **100%** |
| 个人问题里该看到的全看到了 | 33% | **100%** | 18% | **100%** |
| 每个问题注入的无关条目 | 9.0 | **0.8** | 8.6 | **1.6** |
| 无关问题一条都没注入 | 25% | **88%** | 13% | **88%** |
| 每个问题注入的字数 | 721 | **89** | 742 | **204** |
| 常驻块里助手的话 | 17 行 | **0** | 17 行 | **0** |
| 常驻块里写死的内容（不是这个用户的） | 4 处 | **0** | 5 处 | **0** |
| 家人的事记到了用户头上 | 1 | **0** | 3 | **0** |

- 大头来自常驻画像：看到的事实里 79% 是在常驻块里看到的 —— 它们和问题一个词都对不上
- 故事和画像规则是一起写的，写规则时 8 个故事都看过；留出集对相关门和主语归属算留出，对画像规则不完全算。
  PersonaMem（下面）才是独立的检验
- 还剩的问题（留出集）：常见英文动词还会放进不相关的条目 ——「Where do I work now?」带进 11 条「why does X work in staging…」，
  「How do I look after succulents?」带进 20 条「I looked at it…」。2 个工作问题仍会带进讲线上环境、数据迁移的工作对话，
  算不算有用见仁见智
- v7.20.0：上面每个数都没变。常驻块平均多 6 个字（634 → 640）：家人的健康情况（「我妈有糖尿病」「我老婆怀孕了」）现在列在家人下面，不再算用户自己的饮食健康

**PersonaMem**（`benchmarks/personamem_eval.py`；Jiang et al. 2025, "Know Me, Respond to Me"，MIT；数据不放进仓库：
https://huggingface.co/datasets/bowen-upenn/PersonaMem-v1）。20 个虚构用户和聊天机器人聊很多段，偏好会变；32K 档 589 题，
每题是对话中途用户的一句话和四个候选回答，选最适合这个用户的（记得用户说过的事、知道偏好变了、按现在的偏好推荐……共 7 类）。
nyx 按真实写入路径收下提问之前的每一轮 —— 数据集里的 system 消息是给生成器看的人物设定，真实的 agent 没有它，不给 nyx ——
再取 agent 这时会看到的记忆。结果 `benchmarks/results/personamem.json`。

| | v7.18.1 | v7.19.0 |
|---|---|---|
| 常驻块字数（中位数） | 5,971 | **916** |
| 每轮注入字数（中位数） | 3,737 | 3,685 |
| 本地模型选对回答，200 题（不给记忆：0.370） | 0.425 | **0.445** |
| 零 LLM 参照，全部 589 题（瞎猜 0.25） | 0.380 | **0.396** |

- 本地模型是 Qwen2.5-1.5B-Instruct（llama.cpp，temperature 0），只读 nyx 给的记忆、用户这句话和四个回答；200 题在整个 32K 档里
  均匀抽。v7.19.0 对比不给记忆：不给记忆时答错、给了答对 21 题，反过来 6 题（符号检验 p = 0.006）；v7.18.1 对比不给记忆 23 / 12
  （p = 0.09）。v7.19.0 对比 v7.18.1：12 / 8（p = 0.50）—— 没有显著差别，常驻块短了 6.5 倍
- 零 LLM 参照分题型：7 类里 4 类变好（偏好为什么变了 0.455 → 0.556、偏好演变 0.182 → 0.236、用户分享过的事 0.319 → 0.370、
  新场景 0.351 → 0.386），3 类变差（按偏好推荐 0.509 → 0.382、提新点子 0.565 → 0.505、用户提到过的事实 0.706 → 0.471，17 题）。
  PersonaMem 的对话开头通常是用户自我介绍，旧常驻块（最早 10 条消息）恰好装着这些；这个参照按共同词打分，偏向字多的一方。
  模型作答在这几类上没有同样的下降（按偏好推荐两版都是 0.667，提新点子两版都是 0.176）
- v7.20.0：589 题里 agent 看到的和 v7.19.0 完全一样（参照分 0.396），只有常驻块里的条数现在数的是记忆条数、不是日志行数。有一次在负载很高的机器上跑，19 题不同：召回某一路超过时限（1.5–3 秒）就不等它了；重跑一次完全一致
- 零 LLM 参照：每个回答「只有它有」的词，在记忆里出现的比例，最高的算选中，打平按随机选算期望
- 不能和别处报的 PersonaMem 分数比（TencentDB Agent Memory 报 48% → 76%）：模型和设置都不同。这里是 CPU 上的 1.5B 小模型，
  还偏爱选 (c)；看的是两个版本之间的差别


**BEAM**（`benchmarks/beam_eval.py`；Tavakoli 等，ICLR 2026，「Beyond a Million Tokens」：100 段 10 万到 1000 万 token
的长对话，题分 10 类，每道题都标注了答案出自哪几条消息；「拒答」的 200 题没有这种标注，不计。只开词法检索 —— 默认安装就是这样。
参照是同一批消息上最朴素的 BM25；结果 `benchmarks/results/beam_retrieval.json`）

| 对话长度 | 段数 | 每段消息数 | nyx R@10 | BM25 R@10 | agent 看到的证据 | 召回 p50 | 每条写入 | 磁盘 |
|---|---|---|---|---|---|---|---|---|
| 10 万 token | 20 | 287 | **0.532** | 0.417 | 0.559 | 43 ms | 11 ms | 5 MB |
| 50 万 token | 35 | 1,087 | **0.494** | 0.368 | 0.515 | 54 ms | 10 ms | 23 MB |
| 100 万 token | 35 | 2,132 | **0.368** | 0.251 | 0.389 | 66 ms | 10 ms | 47 MB |
| 1000 万 token | 10 | 20,870 | **0.326** | 0.218 | 0.350 | 149 ms | 12 ms | 500 MB |

| 题型（10 万–100 万） | 题数 | nyx R@5 | BM25 R@5 | nyx R@10 | BM25 R@10 | agent 看到的证据 |
|---|---|---|---|---|---|---|
| 矛盾处理 | 180 | **0.681** | 0.568 | **0.765** | 0.644 | 0.781 |
| 事件先后 | 180 | **0.034** | 0.004 | **0.053** | 0.009 | 0.074 |
| 信息抽取 | 180 | **0.479** | 0.379 | **0.569** | 0.466 | 0.593 |
| 遵守指令 | 180 | **0.189** | 0.057 | **0.234** | 0.104 | 0.242 |
| 信息更新 | 179 | **0.678** | 0.445 | **0.770** | 0.588 | 0.791 |
| 跨会话推理 | 180 | **0.384** | 0.212 | **0.483** | 0.305 | 0.529 |
| 遵守偏好 | 178 | **0.234** | 0.113 | **0.312** | 0.192 | 0.314 |
| 总结 | 172 | 0.034 | **0.044** | 0.064 | **0.078** | 0.079 |
| 时间推理 | 180 | **0.705** | 0.485 | **0.815** | 0.603 | 0.863 |
| **总体** | 1609 | **0.382** | 0.257 | **0.454** | 0.333 | 0.476 |

- R@k = 标注的那几条消息在前 k 条里找到了多少；「agent 看到的证据」= 注入给 agent 的那些条（12 条；问「一共 / 多久 / 先后」时 20 条）里
  找到了多少；磁盘约为写入文字的 10 倍
- 写一条消息的时间不随记忆变多而变长：从 10 万到 1000 万 token 都是每条 10 ms 左右（v7.18.1；v7.18.0 时从 12 ms 涨到
  108 ms，一次导入 1000 万 token 要 40 分钟左右，现在 4 分钟左右）
- v7.18.0 以前 nyx 在这里**不如** BM25（10 万–100 万 R@10 0.211）：BEAM 的消息平均约 2100 个字符，助手的长回复把说了事实的
  那条用户短消息挤了下去。见 v7.18.0 更新日志
- 「总结」和「事件先后」的答案要用到散在整段对话里的十几条消息，前 10 条怎么排都覆盖不了（BM25 也一样）。这是最弱的地方：
  这两类题 agent 只看到所需内容的 6–8%
- 这是检索；BEAM 论文报的是大模型作答再打分，口径不同
- 相关门（v7.19.0）在这里没有代价：门前门后 agent 看到的证据都是 0.476（10 万–100 万档）。但它对 180 道拒答题也没用 ——
  没有一道做到一条都不注入：这些是和对话共享实词的长英文问题，词法的门分不出来。排序没变，recall@k 与 v7.18.1 完全相同

**召回的记忆怎么写进提示词**（`benchmarks/beam_format_eval.py`，v7.18.0）。注入的每一条是记忆的前 300 个字符。
我们在同一批召回结果上试了最直接的另一种写法 —— 长记忆给覆盖问题里词最多的那 300 个字符 —— 用评分要点是短事实的
720 道 BEAM（10 万–100 万）题来比。评分要点出现在注入的同一条记忆里的比例：

| 题型 | 题数 | 取开头 300 字（保留） | 取与问题相关的那段（试过） | 整条全文（参照） |
|---|---|---|---|---|
| 信息抽取 | 180 | 0.539 | 0.555 | 0.557 |
| 信息更新 | 180 | 0.781 | 0.781 | 0.797 |
| 跨会话推理 | 180 | 0.310 | 0.314 | 0.349 |
| 时间推理 | 180 | 0.338 | 0.343 | 0.354 |
| **总体**（注入字符数） | 720 | 0.492（4358） | 0.498（4305） | 0.514（21165） |

18 题变好、11 题变差（符号检验 p = 0.26）；1000 万 token 档 0.498 对 0.492。让本地模型（Qwen2.5-1.5B-Instruct，llama.cpp，
temperature 0）只读注入的文字作答，随机抽信息抽取 60 题、信息更新 60 题：答案含评分要点的比例两种写法
0.336 对 0.338，8 题变好、8 题变差。就连整条全文也只多 2 个点：BEAM 里事实多半在用户的短消息里，或在回复的前面。所以写法不改，
瓶颈在召回排序。

**LongMemEval-S**（`benchmarks/longmemeval_eval.py`，500 题、每题约 50 个会话 / 490 轮；按官方定义算会话级召回，
「该拒答」的 30 题不计；结果 `benchmarks/results/longmemeval_s_retrieval.json`）

| 题型 | 题数 | 前 5 命中 | 前 5 全中 | 前 10 全中 | agent 看到的含答案轮次 | 纯词法 前 5 全中（v7.18） |
|---|---|---|---|---|---|---|
| 信息更新 | 72 | 1.000 | 1.000 | 1.000 | 0.944 | 0.931 |
| 多会话汇总 | 121 | 0.992 | 0.793 | 0.901 | 0.804 | 0.711 |
| 助手说过的话 | 56 | 1.000 | 1.000 | 1.000 | 0.982 | 0.982 |
| 用户偏好 | 30 | 0.900 | 0.900 | 0.900 | 0.706 | 0.600 |
| 用户说过的话 | 64 | 1.000 | 1.000 | 1.000 | 0.961 | 1.000 |
| 时间推理 | 127 | 0.984 | 0.779 | 0.866 | 0.804 | 0.772 |
| **总体** | 470 | **0.987** | **0.881** | **0.932** | **0.862** | 0.826 |

v7.19.0（纯词法）：排序没变；agent 看到的含答案轮次，不过相关门 0.847、过了 0.849；30 道拒答题里只有 1 道一条都没注入
（门后中位数还剩召回的 22 条里的 18 条：问题长，和会话共享实词）。

「前 5 命中」= 前 5 条召回里至少有一个答案所在会话；「前 5 全中」= 答案分散在几个会话时，前 5 条把它们全找到了；
「agent 看到的含答案轮次」= 注入给 agent 的那些记录（默认 12 条，问「一共 / 多久 / 先后」这类问题时 20 条）里，
数据集标注的含答案的轮次找到了多少 —— 最接近 agent 能不能答对（v7.13.7）。

开语义检索的几列是 v7.17.0 的全量结果。v7.18.0（长记忆按长度打折）开语义检索在每 4 题取 1 题上重跑（计分 116 题），
两个版本用同一份库：前 5 命中 0.983 → 0.991、前 5 全中 0.888 → 0.922、前 10 全中 0.931 → 0.966、agent 看到的含答案轮次
0.863 → 0.930。纯词法全量：前 5 全中 0.781 → 0.826、agent 看到的含答案轮次 0.738 → 0.847、前 5 命中 0.955 → 0.945。

**LoCoMo**（`benchmarks/locomo_eval.py`，10 段两人长对话、5882 轮、跨数月；计分 1536 题，对抗题 446、无证据标注 4 题按惯例不计；
指标是标注的证据句在前 k 条里找到了多少；参照是同一批句子上最朴素的 BM25；结果 `benchmarks/results/locomo_retrieval.json`）

| 类别 | 题数 | nyx R@5 | 纯词法 R@5 | BM25 R@5 | nyx R@10 | BM25 R@10 | agent 看到的证据 |
|---|---|---|---|---|---|---|---|
| 多跳 | 282 | **0.291** | 0.281 | 0.180 | **0.398** | 0.264 | 0.414 |
| 时间 | 321 | **0.680** | 0.660 | 0.611 | **0.741** | 0.678 | 0.756 |
| 开放域推理 | 92 | **0.273** | 0.271 | 0.243 | **0.352** | 0.272 | 0.358 |
| 单跳 | 841 | **0.660** | 0.615 | 0.590 | **0.720** | 0.655 | 0.727 |
| **总体** | 1536 | **0.573** | 0.542 | 0.498 | **0.643** | 0.565 | 0.654 |

v7.19.0（纯词法）：排序没变；agent 看到的证据过不过相关门都是 0.626；446 道对抗题仍然都有注入（它们问的人和事在对话里出现过）。

怎么读这两张表：
- 这是**检索**，不是别人报的分数。Mem0、Zep、Hindsight 报的 LongMemEval / LoCoMo 分数是「大模型读召回结果作答、
  再由大模型裁判」的问答准确率，口径不同，不能拿上表去比；端到端问答还没跑（需要大模型 API）
- 两个基准差别很大：LongMemEval 里大部分会话是与问题无关的填充对话，语义很好区分；LoCoMo 是同样两个人
  从头聊到尾，句句都像，靠人名与关键词。nyx 的融合（v7.13.6）按两个基准各取一半选参数、在另一半上验证，
  两边都不比纯词法差（可以设 `NYX_EMBED_Z=2 NYX_EMBED_WEIGHT=1` 回到旧融合）
- 问「一共几次 / 隔了多久 / 哪个先 / 之前之后」时多给 agent 10 条（v7.13.7）：LongMemEval 多会话汇总题
  agent 看到的含答案轮次 0.701 → 0.804、时间推理 0.715 → 0.804（`NYX_BREADTH_LINES=0` 关闭）
- 短板：LoCoMo 多跳题证据全部进前 10 只有 18%、agent 看到的只有 41%；LongMemEval 偏好题 0.71
- v7.13.4 及以前，LoCoMo 的 nyx R@5 是 **0.046**（召回聚合的排序缺陷，见 v7.13.5 更新日志）

**可选：交叉编码器重排**（`NYX_RERANK=local`，v7.14.0，默认关）。路由先取 50 条，本地多语言交叉编码器
`mmarco-mMiniLMv2-L12` 逐对打分（与原排名各投一票），再取原来的条数。参数只在两个基准各一半的题上选，
下表是**另一半**（留出）上的结果：

| 留出半 | 不重排 | 重排 |
|---|---|---|
| LoCoMo：agent 看到的证据 | 0.681 | **0.728** |
| LoCoMo：R@5 | 0.610 | **0.674** |
| LongMemEval-S：agent 看到的含答案轮次 | 0.855 | **0.867** |
| LongMemEval-S：前 5 全中 | 0.875 | **0.907** |
| LongMemEval-S：其中偏好题 | **0.694** | 0.500 |

代价：每轮召回多 0.3–1.5 秒（CPU；短记忆在 4 线程上约 0.2 秒，长记忆约 1 秒），模型约 470MB。偏好题（「给我推荐几个…」）
变差：交叉编码器学的是「哪段话在回答这个问题」，会把通用的推荐排到用户自己说过的偏好前面。所以默认关。

```bash
pip install "nyx-memory[vector]"     # 首次运行把模型下载到 ~/.cache/nexsandglass/models（NYX_EMBED_CACHE 可改）
# LongMemEval（MIT）：https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned
python3 benchmarks/longmemeval_eval.py longmemeval_s_cleaned.json --workers 8 --json benchmarks/results/longmemeval_s.json
python3 benchmarks/longmemeval_eval.py longmemeval_s_cleaned.json --workers 8 --no-semantic --json longmemeval_s_lexical.json
# LoCoMo（CC BY-NC 4.0，仓库里不放）：https://github.com/snap-research/locomo
python3 benchmarks/locomo_eval.py locomo10.json --workers 10 --json benchmarks/results/locomo.json
# BEAM（CC BY-SA 4.0，仓库里不放）：https://huggingface.co/datasets/Mohammadta/BEAM 与 Mohammadta/BEAM-10M
pip install pyarrow
python3 benchmarks/beam_eval.py 100K.parquet 500K.parquet 1M.parquet --workers 4 --no-semantic --json beam.json --views views.jsonl
python3 benchmarks/beam_eval.py 10M-00000-of-00002.parquet 10M-00001-of-00002.parquet --workers 4 --no-semantic --json beam_10m.json
python3 benchmarks/beam_format_eval.py views.jsonl            # 第 2 步加 --llm，并设 NYX_EVAL_API_URL / NYX_EVAL_MODEL
# 个人场景（故事在仓库里）与 PersonaMem（MIT，仓库里不放）
python3 benchmarks/personal_eval.py --show
python3 benchmarks/personamem_eval.py questions_32k.csv shared_contexts_32k.jsonl --views views.jsonl
python3 benchmarks/personamem_eval.py questions_32k.csv --answer views.jsonl --llm --limit 200    # NYX_EVAL_API_URL / NYX_EVAL_MODEL
```

语义后端开着、索引却没建成时，两个评测框架都直接报错（v7.13.6）—— 不会把纯词法的结果记成「语义开」。

**仓库里可复现的评测**（结果在 `benchmarks/results/`）：

| 评测 | 测什么 | 版本 |
|---|---|---|
| `benchmarks/personal_eval.py` | 个人场景：新会话里 agent 能不能看到它需要的用户原话、带进来多少无关内容、家人的事有没有记到对的人头上（见上） | v7.19.0、v7.20.0 |
| `benchmarks/personamem_eval.py` | PersonaMem：每道题 agent 看到的记忆，再让本地模型从四个回答里选，对照不给记忆（见上） | v7.19.0、v7.20.0 |
| `benchmarks/beam_eval.py` | BEAM 检索证据召回，10 万到 1000 万 token（见上），附 BM25 参照、写入速度与磁盘占用 | v7.18.0 |
| `benchmarks/beam_format_eval.py` | 召回结果怎么写进提示词：答案看不看得见、模型用不用得上 | v7.18.0 |
| `benchmarks/locomo_eval.py` | LoCoMo 检索证据召回（见上），附 BM25 参照 | v7.13.5 |
| `benchmarks/longitudinal_eval.py` | 一个人半年的生活：现在 / 当时以为 / 过去某刻，三个问题分开打分；含留出句式 | v7.9–v7.11 |
| `benchmarks/poisoning_drill.py` | 记忆投毒：16 条攻击 0 条无标注进 prompt，主人 8 条 0 误伤 | v7.8 |
| `benchmarks/forget_restore_drill.py` | 误删 150 条 → 逐字节还原；真删仍不可逆 | v7.6 |
| `tests/integration/hermes_multiprofile_check.py` | 一个 Hermes 进程、三个 profile：设置按 profile、`data_dir` 不同的被拒绝、`os.environ` / `sys.path` 不被改 | v7.17.0 |
| `tests/integration/hermes_real_check.py` | 对真实 Hermes 源码跑插件全链路（需 `HERMES_AGENT_SRC`） | v7.12 |
| `benchmarks/dejavu_bench.py` | Déjà Vu「熟悉 / 陌生」分类（E2 节：英文） | v7.5、v7.16.1 |
| `benchmarks/dejavu_english_real.py` | Déjà Vu 在真实英文对话（LongMemEval-S）上的误报 | v7.16.1 |
| `benchmarks/dejavu_chinese_real.py` | Déjà Vu 在真实中文闲聊（LCCC）上的误报 | v7.16.2 |
| `benchmarks/recall_latency.py` | 3 万 / 15 万条记忆下 Hermes 插件的启动、首轮、每轮召回耗时；conversation 模式按真实对话节奏（写一句、查一句） | v7.13.2–v7.13.3 |

> 旧版这里放过 `tests/eval/run_eval.py` 的「词法检索召回率 75%」。那个脚本用自带的简易分词给一个内存列表打分，
> **不经过 nyx 的检索链路**，不代表 nyx 的召回质量，已撤下。

---

## 📝 最近更新

- **v7.20.1**：就是 PyPI 上的 v7.20.0（v7.20.0 的发布检查只认一个配套插件、实际有两个，没有发布出去）
- **v7.20.0**：记什么由你说了算。任何聊天里 `/nyx pause` / `resume` / `forget-last` / `status`（`hermes plugins enable nyx-commands`）；终端里 `hermes nyx search / show / forget / restore / edit`，不经过模型；`hermes nyx import` 把装 Nyx 之前的对话按原来的时间导进来，后导入的旧话不会盖过新的。agent 的搜索结果带日期和谁说的、能限定日期范围，`nyx_read` 看一条的全文和前后几句（MCP 也有）。修好：从隔离区救回的记忆可能一直不回常驻画像。个人场景和 PersonaMem 评测里 agent 看到的没有变化
- **v7.19.0**：为个人 agent 而做。每个会话的系统提示先放用户说过的关于自己的事（对 agent 的要求、饮食健康、家人、住处工作、
  喜好），用原话；只靠「什么 / 帮我 / what」这类词和问题撞上的记忆不再注入，什么都不相关就一条都不注入；家人的事记在家人名下
  （「我妈住在青岛」不再变成用户住青岛）。8 个个人故事上，agent 该看到的事实从 25–38% 到 100%，每个问题的无关注入从 9 条
  降到 1–2 条（留出集 1.6）。公开基准：检索不变，相关门没有挡掉证据
- **v7.18.1**：记忆越多写得越慢的问题修好了：从 10 万到 1000 万 token 每条写入都在 10 ms 左右（以前从 12 ms 涨到 108 ms；BEAM），召回不变
- **v7.18.0**：长记忆不再把说了答案的短消息挤下去（BEAM recall@10 0.211 → 0.454，超过 BM25 的 0.333；LongMemEval-S agent 看到的含答案轮次 0.738 → 0.847，纯词法）；新增 BEAM 基准，最长 1000 万 token；试了「长记忆注入与问题相关的那段、不取开头 300 字」，没有可测的差别，写法不改
- **v7.17.0**：Hermes 里设置按 profile 生效 —— 一个进程服务多个 profile 时，一个 profile 的 `nyx.json`（比如 `l3_llm: on`）不再作用到别的 profile；Nyx 不再改 `os.environ` 和 `sys.path`；`data_dir` 不同的 profile 会被拒绝而不是共用；插件名改成 `nyx`（Hermes 插件目录评审提出）
- **v7.16.2**：Déjà Vu 不再把中文虚词和跨词碎片（我们、聊过、的那、过的……）算作见过 —— 以前只记 20 句闲聊，10 个不相干的中文问题就有 6 个「熟悉」，现在 0 个
- **v7.16.1**：Déjà Vu 不再把英文常用词（the、about、talked……）算作见过 —— 以前只要记过一句英文，任何不相干的英文问题都是「熟悉」
- **v7.16.0**：英文优先 —— 文档、终端命令、`hermes memory setup` 默认英文（`NYX_LANG=zh` 或 Hermes 里 `lang: zh` 用中文，系统语言是中文时自动中文）；给模型看的文字（工具说明、召回标签、占位符）统一英文；已存的数据不改写，旧的中文占位符和标注照样认
- **v7.15.4**：召回快了约一半（1 万轮真实对话，prefetch p50 从约 88 ms 到约 47 ms），结果逐字不变
- **v7.15.3**：新版 Hermes 认得出 Nyx 是记忆插件，`hermes plugins install … --enable` 会设好 `memory.provider`；安装后的说明改成英文在前
- **v7.15.2**：L3 分析调用 DeepSeek / OpenRouter 改成要主动打开（`l3_llm: on`）—— 以前在 Hermes 里几乎总是默认发出画像和近期决策；新增 `hermes nyx …` 终端命令
- **v7.15.1**：Hermes 用自己的插件命令装：`hermes plugins install sixgodgit/nyx/hermes-plugin --enable`（v7.15.0 的安装命令在新版 Hermes 上装不上）
- **v7.15.0**：可选的密钥保险箱 —— 聊天里贴过的 key 之后还能让 agent 交还给你（人工确认；直达取回时模型服务商看不到原文，真实 Hermes 实测）；Hermes 安装方式修好
- **v7.14.0**：密钥和密码不进记忆（写入前隐去；已存下的 `scrub` 原地隐去）；真删之后磁盘上也没了（以前全文库里还留着）；可选的交叉编码器重排（LoCoMo 证据召回 +4.7 个点，默认关）；端到端问答评测框架
- **v7.13.9**：测试再也写不进真实数据目录（会话级隔离 + 运行时守卫）；新增 `python3 -m nexsandglass.repair` —— 中枢被污染时按内容体检与修复，真实记忆一条不少、被遗忘的不复活、合成「事实」连同派生数据撤下
- **v7.13.8**：README 重写（快速开始、工作原理、隐私、常见问题、已知局限）；全新安装时 `doctor` 不再误报 ❌；安装脚本与 Docker 修好（旧脚本会装一个让每句话记两遍的网关插件）
- **v7.13.7**：问「一共几次 / 隔了多久 / 哪个先 / 之前之后」时多给 agent 10 条 —— 多会话汇总题 agent 看到的含答案轮次 0.701 → 0.804，时间推理 0.715 → 0.804
- **v7.13.6**：第一次用真实嵌入模型测公开基准：LongMemEval-S 前 5 命中 98.1%；语义融合按分半验证重新定参
- **v7.13.5**：第一次在公开基准真实数据上测，查出召回排序缺陷：LoCoMo recall@5 0.046 → 0.546
- **v7.13.4**：多个线程 / 进程同时第一次开库不再撞锁；每个 PR 在 Python 3.10–3.13 上跑全量测试
- **v7.13.3**：语义索引静默失效一个月的根治；并发写入不再丢记忆；`doctor` 自检

全部版本、每次改动的实测数字与取舍：[`CHANGELOG.md`](https://github.com/sixgodgit/nyx/blob/main/CHANGELOG.md)。

---

## 🚢 发布（维护者）

`.github/workflows/release.yml`：推送 `v*` 标签 → Python 3.10–3.13 全量测试 → 构建 → 校验（标签必须等于
`__version__`、`twine check --strict`、干净环境安装后 Hermes 入口可用、包里没有测试与数据文件）→ 发布到 PyPI。
用 PyPI Trusted Publishing（OIDC），仓库里**不存任何 token**。在 Actions 页面手动运行只测试和构建，不发布。

一次性设置：PyPI 项目 `nyx-memory` → Publishing → Add a new publisher → GitHub，填
Owner `sixgodgit` / Repository `nyx` / Workflow `release.yml` / Environment `pypi`。

发版：改 `nexsandglass/core/sandglass_paths.py` 的 `__version__` 并合并到 main，然后在
[GitHub Releases](https://github.com/sixgodgit/nyx/releases/new) 新建 release（tag `v<版本>`、target `main`），
或者 `git tag v<版本> && git push origin v<版本>`。标签与 `__version__` 不一致时流水线直接失败。

---

## 🌱 起源

Nyx 始于 NexLSL 的开源项目 **NexSandglass**（MIT 许可，2026 年 6 月，v1.0–v2.9.9）。沙漏日志与 FTS5 全文库、
多路检索（`search_router`）、影子沙、织线知识图谱，画像 / 偏移率 / 场景 / 情绪这些 L3 分析，以及 MCP 服务和最早的
Hermes 插件，设计与第一版代码都来自那里；Python 包名 `nexsandglass` 也沿用至今。

v3.0（2026 年 7 月）起由 sixgodgit 维护：重构为模块化包，之后加入了 runtime 编排与候选晋升、ID 中枢、遗忘隔离区与
可验证的真删除、双时态事实、来源信任、话语理解、Hermes 插件改为按官方 `MemoryProvider` 接口实现、语义检索、
LongMemEval / LoCoMo 评测、密钥过滤与保险箱等（见 [CHANGELOG.md](CHANGELOG.md)）。完整的提交历史都保留在仓库里。

感谢 NexLSL 和 NexSandglass 的贡献者。

---

## 📄 License

MIT，见 [LICENSE](LICENSE)。版权人：NexSandglass（原始项目）与 sixgodgit（v3.0 起）。

**Made with 🧠 by Nyx — 夜神记忆感知系统**
