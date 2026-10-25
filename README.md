# Nyx 夜神 · nyx-memory

> **给 agent 一份跟着你一辈子的记忆** —— 单用户、本地、写入不调用大模型。记得住，找得到，信得过，忘得掉。

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python) ![License](https://img.shields.io/badge/License-MIT-green) ![Version](https://img.shields.io/badge/version-7.15.2-blue) ![Deps](https://img.shields.io/badge/runtime%20deps-0-brightgreen)

Nyx 是 agent 的长期记忆层：接进 [Hermes](https://github.com/NousResearch/hermes-agent)（作为记忆插件）、
Claude Desktop / Claude Code 等任意 MCP 客户端，或者你自己的 Python 程序。
每句话原样留档，按需找回带日期的原话与「现在信什么」，数据全部是你机器上的普通文件。

## 为什么用 Nyx

| | 怎么做到的 | 实测 |
|---|---|---|
| **找得到** | 全文、倒排、TF-IDF、实体与（可选）语义检索多路融合；问「一共几次 / 隔了多久」时多给 agent 几条 | LongMemEval-S（470 道计分题）：答案所在的那次对话进前 5 条 **98.7%**；LoCoMo 证据召回 0.575（BM25 0.498）。每轮召回约 30–140 ms |
| **分得清时间** | 双时态事实：「何时为真」和「何时得知」分开记 | 「我 2 月就换成吉利了」记下的是 2 月，不是说这句话的那天；能回答「现在信什么」「三月时我以为是什么」 |
| **信得过** | 写入时绑定来源与信任：只有主人亲口说的能成为规则；别人转述的、网页抄来的，召回时带「未经证实」标注 | 投毒演练：16 条攻击 0 条无标注进入 agent 提示，主人的 8 条正常信息 0 误伤 |
| **忘得掉、救得回** | 遗忘先进隔离区（检索立刻读不到），保留期（默认 30 天）内原样还原；到期后清理即真删，并验收向量、索引、图谱都清干净了 | 误删 150 条，逐字节救回；真删不可逆 |
| **便宜、私密** | 写入零 LLM 调用、零 API key、零运行时依赖；默认不联网（[例外](#privacy)） | 全部数据在 `~/.neurobase` |
| **密钥不进记忆** | 聊天里贴的 token、API key、私钥、密码，在写进任何地方之前换成 `[已隐去: HF token …a1b2]` | LongMemEval-S 近 20 万轮真实对话只触发 19 轮（0.01%），全是贴出来的代码配置；普通句子 0 误伤 |
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
[密钥保险箱](docs/secret-vault.md)：再 `hermes plugins enable nyx-reveal`，在 `hermes memory setup` 里把 `vault` 设成 `on`。

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

之后每轮对话自动记忆、自动召回；agent 还多了 `nyx_belief`（现在 / 当时信什么）、`nyx_forget` / `nyx_restore`
（遗忘与还原）、`nyx_health`（自检）等工具；终端里有 `hermes nyx doctor` / `hermes nyx vault …` 等命令。完整说明见 [`nexsandglass_hermes/README.md`](https://github.com/sixgodgit/nyx/blob/main/nexsandglass_hermes/README.md)。

### MCP（Claude Desktop / Claude Code / 任意 MCP 客户端）

```bash
pip install nyx-memory
claude mcp add nyx -- python3 -m nexsandglass.interfaces.sandglass_mcp     # Claude Code
```

其他客户端在配置文件里加：

```json
{ "mcpServers": { "nyx": { "command": "python3", "args": ["-m", "nexsandglass.interfaces.sandglass_mcp"] } } }
```

26 个工具，见 [MCP 工具](#mcp-tools)。

### Python

```python
from nexsandglass.runtime import observe, recall, forget, restore

observe("我上个月从杭州搬到了上海", source="user")   # source="user" = 主人亲口说的
print(recall("我现在住哪").text)                       # → user 住在 上海（自 2026-09-01）

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
4. 话语理解：「2 月 25 号就换成吉利了」→（user, 车, 吉利, 自 2 月 25 日起, 至今仍真），写成双时态事实；旧事实不被覆盖，而是记下「到何时为止」
5. 全文、倒排、（可选）语义索引增量更新

**召回**（每一轮对话之前，本地完成）

1. 全文（FTS5）、倒排、TF-IDF、实体、（可选）语义向量多路检索
2. 融合排序：字面完整命中在前，语义作为辅助（权重减半、只认明显高于背景的命中）
3. 补上当前信念（「user 住在 上海（自 2026-09-01）」）
4. 信任门：来源被污染的不出库，未经证实的带标注、不能当规则
5. 带日期注入 agent：默认 12 条；问「一共几次 / 隔了多久 / 哪个先 / 之前之后」时 20 条

---

## 📋 能力一览

| 能力 | 模块 | 说明 |
|---|---|---|
| 🆔 **原文日志 + ID 中枢** | `core/memid.py`、`core/sandglass_log.py` | 每条记忆一个 `mem_id` 与行号跨度；墓碑阻止已删记忆在重建时复活 |
| 🔍 **多路检索** | `core/search_router.py`、`core/semantic.py` | FTS5 / 倒排 / TF-IDF / 影子沙 + 可选语义（本地多语言模型或 OpenAI 兼容 API），RRF 融合 |
| 🕰️ **双时态事实** | `engram/loops/temporal_fact.py`、`core/understand.py` | `as_of` 问世界、`known_at` 问当时的信念、`belief_timeline` 问看法怎么变的；假设的时间不冒充陈述的时间 |
| 🛡️ **来源信任** | `core/provenance.py` | trusted / unverified / tainted；只有主人亲口说的能成为规则（防 OWASP ASI06 记忆投毒） |
| 🕯️ **遗忘隔离区** | `core/quarantine.py`、`core/erasure.py` | 两段式遗忘：隔离 → 还原或到期擦除；擦除贯通日志、索引、图谱、向量，可独立验收 |
| 🔑 **密钥不进记忆** | `core/secret_filter.py`、`scrub.py` | 写入前隐去 token / API key / 私钥 / 连接串 / 密码；已经存下的用 `scrub` 原地隐去，按原始字节复查 |
| 🔐 **密钥保险箱**（可选） | `core/secret_vault.py`、`vault.py` | 隐去的原文加密存起来，主人要时经人工确认交还；直达取回时模型只拿到句柄、看不到原文（真实 Hermes 实测） |
| 🩺 **自检** | `doctor.py` | 中枢与日志一致性、各索引、遗忘完整性、隔离区到期、规则投毒，逐项给出原因 |
| 🧯 **体检与修复** | `repair.py`、`core/forensics.py` | 中枢对不上日志时按内容逐条核对：补缺失、挪错位、认回被覆盖的记忆、撤下合成记录及其派生「事实」；先备份，不改日志 |
| 🔌 **Hermes 记忆提供器** | `nexsandglass_hermes/`、`core/memory_provider.py` | 按 Hermes 的 `MemoryProvider` 接口实现；`hermes plugins install sixgodgit/nyx/hermes-plugin --enable` 装进 Hermes |
| 🔌 **MCP 服务** | `interfaces/sandglass_mcp.py` | stdio，纯标准库，26 个工具 |
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
dv.sense("我们聊过的那家川菜馆")   # -> 熟悉 0.22（普通检索在这里返回空）
dv.hunt("川菜馆")                 # -> [Phantom(token='川菜馆', refs=['msg-1'], ...)]
```

两层结构：**Veil**（Bloom Filter，128 KiB）回答「这个词见过没有」，**Mist**（SQLite）记录它何时、何地、几次出现过。
如实说明：它不提高召回率，只把 miss 分成两类 —— **分类有效，寻回有限**。
实测见 [`benchmarks/dejavu_bench.py`](https://github.com/sixgodgit/nyx/blob/main/benchmarks/dejavu_bench.py)，设计取舍见 [`docs/dejavu.md`](https://github.com/sixgodgit/nyx/blob/main/docs/dejavu.md)。

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
键见 [`nexsandglass_hermes/README.md`](https://github.com/sixgodgit/nyx/blob/main/nexsandglass_hermes/README.md)。

**环境变量**（所有入口通用；Hermes 下环境变量优先于 `nyx.json`）：

| 变量 | 默认 | 作用 |
|---|---|---|
| `NEXSANDBASE_HOME` | `~/.neurobase` | 记忆数据目录 |
| `NYX_EMBED` | `auto` | 语义检索后端：`auto` / `local` / `api` / `off` |
| `NYX_EMBED_MODEL` | 本地 `paraphrase-multilingual-MiniLM-L12-v2`；API `text-embedding-3-small` | 嵌入模型 |
| `EMBEDDING_API_URL` / `EMBEDDING_API_KEY` | 空 | OpenAI 兼容 `/embeddings` |
| `NYX_EMBED_Z` | `3` | 语义命中要比背景高出多少（稳健 z 分数）才算证据 |
| `NYX_EMBED_WEIGHT` | `0.5` | 融合时语义这一票的权重（词法一票是 1） |
| `NYX_EMBED_CACHE` | `~/.cache/nexsandglass/models` | 本地嵌入模型的存放目录 |
| `NYX_RERANK` | `off` | `local` = 召回后用本地交叉编码器重排（需要 `[vector]`）。更准、更慢，偏好类问题变差，见 [Benchmark](#benchmark)。`NYX_RERANK_MODEL` / `_DEPTH`（50）/ `_MODE`（rrf）/ `_MAXLEN`（128）/ `_BUDGET_MS`（2000）可调 |
| `NYX_SECRET_FILTER` | `on` | 密钥 / 密码写入前隐去：`on`（token + 密码 + `<private>`）/ `tokens`（不动密码）/ `off` |
| `NYX_L3_LLM` | 关 | `1` = L3 分析（画像 / 偏移率 / 幽灵决策）调用 DeepSeek / OpenRouter（`DEEPSEEK_API_KEY` / `OPENROUTER_API_KEY`）。v7.15.2 以前有 key 就用 |
| `NYX_VAULT` | `off` | `on` = 隐去的原文加密存进保险箱（要 `pip install 'nyx-memory[vault]'`）。`NYX_VAULT_REVEAL`（`direct` / `cli` / `llm`）、`NYX_VAULT_KINDS`（`all` / `tokens`）、`NYX_VAULT_BACKEND`（`auto` / `file` / `keyring`）、`NYX_VAULT_KEY`，见 [docs/secret-vault.md](docs/secret-vault.md) |
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
| `DEEPSEEK_API_KEY` / `OPENROUTER_API_KEY` | 空 | **设了就会**让画像 / 偏移率 / 幽灵决策等 L3 分析调用 DeepSeek / OpenRouter，见[数据与隐私](#privacy) |

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
| `sandglass_search` / `sandglass_semantic` | 关键词搜索 / 同义词 + SimHash + TF-IDF 搜索 |
| `sandglass_recent` / `sandglass_ping` | 最近 N 条 / 健康检查（语义索引坏了时 `status=degraded`） |
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
| `consolidation_snapshots/` | 巩固（consolidation，默认关）运行前的回滚快照，含记忆全文；遗忘和 `scrub` 都会处理 |
| `veil.bin` / `mist.db` | Déjà Vu（Bloom Filter / 出现记录） |
| `persona/`、`archive/` | 画像与决策日志 / 冷归档 |

Hermes 用户：数据目录在 `HERMES_HOME` 之外时，`hermes backup` 会自动把它带上（插件的 `backup_paths`）。

**密钥和密码不进记忆**（v7.14.0）：聊天里贴的 token（Hugging Face、GitHub、OpenAI / DeepSeek / Anthropic / OpenRouter、
PyPI、AWS、Google、Slack、Stripe、Telegram、JWT…）、私钥、带密码的连接串、`XXX_API_KEY=…` 这类赋值、「密码是 …」，
以及 `<private>…</private>` 包住的内容，**在写进任何地方之前**就换成占位符：token 留末 4 位（`[已隐去: HF token …a1b2]`，
agent 还能说出「你给过一个 HF token」），密码一个字符不留。日志、中枢、索引、影子沙、图谱、向量里都只有占位符；
发给嵌入 API 的问句也先隐去。`NYX_SECRET_FILTER=tokens` 只管 token，`off` 关闭。
Hermes 自己的会话记录（`~/.hermes` 下）不归 Nyx 管。

**想让 agent 之后还能把 key 告诉你？**（v7.15.0，默认关）打开密钥保险箱：`NYX_VAULT=on`（Hermes 里是 `vault: on`），
`pip install 'nyx-memory[vault]'`。隐去的原文加密存起来（主密钥在 `~/.config/nyx/vault.key`，不在数据目录里），占位符带编号
（`[已隐去: HF token …a1b2 #v3]`）。你说「把我之前给你的 HF token 发我」，agent 调 `nyx_secret_reveal`：先经人工确认
（Hermes 的审批；审批被 `/yolo` 关掉时改成回复一句确认短语），再由配套插件在回复发出前把句柄换成原文 ——
**模型服务商看不到原文**，Hermes 的会话记录里也只有句柄（`hermes plugins enable nyx-reveal` 启用一次）。
最稳的是在终端里取：`python3 -m nexsandglass.vault show v3`。贴进聊天的那一刻原文已经发给了模型服务商，所以贴过的 key 还是该换掉；
机器人在群里的话，群里任何人都会被当成主人。威胁模型与实测见 [docs/secret-vault.md](docs/secret-vault.md)。

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

**想删掉一条记忆？** agent 里用 `nyx_forget`（先预览、再确认，进隔离区）；Python 用 `forget()`。
后悔了用 `nyx_restore` / `restore()` 或 `python3 scripts/nyx_quarantine.py restore <mem_id>`。
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

**系统提示里出现「⚠️ Nyx 自检：…」？** 某个子系统坏了（比如语义索引），召回照常但不完整。跑 `doctor` 看具体原因。

**`doctor` 里 `hub_vs_journal` / `fts_index` / `erasure_integrity` 红了？** 中枢和日志对不上了（比如测试或别的程序往数据目录里写过东西）。先跑 `python3 -m nexsandglass.repair`：只读体检，按内容分出缺失、错位、被覆盖、合成孤儿、真假「泄漏」，只给计数和 seq 范围。然后停掉 Hermes 网关 / MCP 服务，`python3 -m nexsandglass.repair --apply`（先自动备份到 `<数据目录>/backups/`，日志一个字节不改），修完重启服务。**不要按 `erasure_integrity` 报的 seq 去 `forget`**：墓碑可能指错了位置，抹掉的会是真实记忆。体检里「遗忘没做完」的记录（日志正文还在）要 `--finish-erasures` 才会在日志里抹掉；日志里也有测试数据的发送者时，确认后可以加 `--forget-senders golden,e2e`（进隔离区，30 天内可还原）。

---

## ⚠️ 已知局限

- **单用户设计**：一个人一份记忆；一个进程同一时刻只服务一个数据目录
- **端到端问答准确率还没测**：[Benchmark](#benchmark) 里都是检索指标；Mem0 / Zep 们报的是问答准确率（需要大模型作答与裁判）
- **还弱的题型**：LoCoMo 多跳题 agent 看到的证据只有 41%（开重排 48%）；LongMemEval 偏好题 0.71（开重排反而更低）
- **英文事实抽取较少**：规则抽取以中文为主，英文主要靠检索原文 + 日期；可开 `NYX_UNDERSTAND_LLM` 用 LLM 抽取
- **默认嵌入模型偏小**：本地多语言 MiniLM 胜在小而快；换更强的 API 模型时，融合权重可能需要重新调（`NYX_EMBED_Z` / `NYX_EMBED_WEIGHT`）

---

<a id="benchmark"></a>

## 📊 Benchmark

两个公开基准都只测**检索**（零 LLM、可复现），逐轮走 Hermes 插件真实的写入路径。
语义检索用默认的本地模型 `paraphrase-multilingual-MiniLM-L12-v2`（`pip install "nyx-memory[vector]"`）。

**LongMemEval-S**（`benchmarks/longmemeval_eval.py`，500 题、每题约 50 个会话 / 490 轮；按官方定义算会话级召回，
「该拒答」的 30 题不计；结果 `benchmarks/results/longmemeval_s_retrieval.json`）

| 题型 | 题数 | 前 5 命中 | 前 5 全中 | 前 10 全中 | agent 看到的含答案轮次 | 纯词法 前 5 全中 |
|---|---|---|---|---|---|---|
| 信息更新 | 72 | 1.000 | 1.000 | 1.000 | 0.944 | 0.889 |
| 多会话汇总 | 121 | 0.992 | 0.793 | 0.901 | 0.804 | 0.603 |
| 助手说过的话 | 56 | 1.000 | 1.000 | 1.000 | 0.982 | 0.982 |
| 用户偏好 | 30 | 0.900 | 0.900 | 0.900 | 0.706 | 0.700 |
| 用户说过的话 | 64 | 1.000 | 1.000 | 1.000 | 0.961 | 0.984 |
| 时间推理 | 127 | 0.984 | 0.779 | 0.866 | 0.804 | 0.693 |
| **总体** | 470 | **0.987** | **0.881** | **0.932** | **0.862** | 0.774 |

「前 5 命中」= 前 5 条召回里至少有一个答案所在会话；「前 5 全中」= 答案分散在几个会话时，前 5 条把它们全找到了；
「agent 看到的含答案轮次」= 注入给 agent 的那些记录（默认 12 条，问「一共 / 多久 / 先后」这类问题时 20 条）里，
数据集标注的含答案的轮次找到了多少 —— 最接近 agent 能不能答对（v7.13.7）。

**LoCoMo**（`benchmarks/locomo_eval.py`，10 段两人长对话、5882 轮、跨数月；计分 1536 题，对抗题 446、无证据标注 4 题按惯例不计；
指标是标注的证据句在前 k 条里找到了多少；参照是同一批句子上最朴素的 BM25；结果 `benchmarks/results/locomo_retrieval.json`）

| 类别 | 题数 | nyx R@5 | 纯词法 R@5 | BM25 R@5 | nyx R@10 | BM25 R@10 | agent 看到的证据 |
|---|---|---|---|---|---|---|---|
| 多跳 | 282 | **0.301** | 0.289 | 0.180 | **0.397** | 0.264 | 0.413 |
| 时间 | 321 | **0.674** | 0.666 | 0.611 | **0.744** | 0.678 | 0.756 |
| 开放域推理 | 92 | 0.273 | **0.282** | 0.243 | **0.352** | 0.272 | 0.358 |
| 单跳 | 841 | **0.661** | 0.615 | 0.590 | **0.725** | 0.655 | 0.732 |
| **总体** | 1536 | **0.575** | 0.546 | 0.498 | **0.646** | 0.565 | 0.656 |

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
```

语义后端开着、索引却没建成时，两个评测框架都直接报错（v7.13.6）—— 不会把纯词法的结果记成「语义开」。

**仓库里可复现的评测**（结果在 `benchmarks/results/`）：

| 评测 | 测什么 | 版本 |
|---|---|---|
| `benchmarks/locomo_eval.py` | LoCoMo 检索证据召回（见上），附 BM25 参照 | v7.13.5 |
| `benchmarks/longitudinal_eval.py` | 一个人半年的生活：现在 / 当时以为 / 过去某刻，三个问题分开打分；含留出句式 | v7.9–v7.11 |
| `benchmarks/poisoning_drill.py` | 记忆投毒：16 条攻击 0 条无标注进 prompt，主人 8 条 0 误伤 | v7.8 |
| `benchmarks/forget_restore_drill.py` | 误删 150 条 → 逐字节还原；真删仍不可逆 | v7.6 |
| `tests/integration/hermes_real_check.py` | 对真实 Hermes 源码跑插件全链路（需 `HERMES_AGENT_SRC`） | v7.12 |
| `benchmarks/dejavu_bench.py` | Déjà Vu「熟悉 / 陌生」分类 | v7.5 |
| `benchmarks/recall_latency.py` | 3 万 / 15 万条记忆下 Hermes 插件的启动、首轮、每轮召回耗时；conversation 模式按真实对话节奏（写一句、查一句） | v7.13.2–v7.13.3 |

> 旧版这里放过 `tests/eval/run_eval.py` 的「词法检索召回率 75%」。那个脚本用自带的简易分词给一个内存列表打分，
> **不经过 nyx 的检索链路**，不代表 nyx 的召回质量，已撤下。

---

## 📝 最近更新

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

## 📄 License

MIT

**Made with 🧠 by Nyx — 夜神记忆感知系统**
