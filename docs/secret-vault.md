# 密钥保险箱（方案 3）设计稿

> 状态：**设计稿，未实现**。目标版本 v7.15.0。依据：nyx v7.14.0 源码 + hermes-agent 0.19.0 源码（文中引用的 Hermes 行号都指这个版本）。
> 默认关；不开的话，行为和 v7.14.0 一个字节都不差。

## 0. 一句话

v7.14.0 起，聊天里贴的 token / API key / 密码在写进记忆之前就被换成占位符，原文丢掉。方案 3 加一个**主人自己开**的选项：
原文不丢，加密放进保险箱；主人明确要看时，经过人工确认交还给主人，**而且尽量不让模型服务商看到原文**。

## 1. 做什么，不做什么

**做**
- 主人亲口贴的密钥（只限主人；别人、机器人、助手、工具输出里的都不收）加密保存，占位符带编号：`[已隐去: Hugging Face token …ab12 #v3]`
- 主人问「我上次那个 HF token 是什么」时，agent 能找到是哪一条（编号、类别、末 4 位、保存时间、上下文），**不需要原文**
- 主人要原文时，三条路取回（§6），每次都要人工确认，有频率限制，有审计记录
- 遗忘联动：记忆被忘掉，保险箱里对应的那条跟着进隔离区；真删时一起真删（§8）

**不做**
- 不是密码管理器。重要凭据请用 1Password / Bitwarden / 系统钥匙串；这个保险箱是「聊天里顺手贴过、事后想找回」的兜底
- 不给 agent 「拿密钥去调用别的服务」的能力。agent 需要用的 key 请放 Hermes 的环境变量 / 密钥配置
- 不处理「原文已经泄露」的部分：贴进聊天的那一刻，原文已经发给了模型服务商、留在 Hermes 自己的会话记录和聊天平台里（§9）
- 不找回 v7.14.0 `scrub --apply` 已经抹掉的原文 —— 抹掉就是没了

## 2. 前提：先修好「这句话是谁说的」（现有缺陷）

保险箱只收主人的密钥、只把原文交给主人，所以必须先可靠地知道每一轮是谁说的。查 Hermes 0.19.0 源码发现，**nyx 现在识别发言人的方式在 Hermes 下从来没有生效过**：

- nyx 从 `on_turn_start(..., author_id=, author_name=, author_is_bot=)` 和 `sync_turn(..., turn_author=)` 取发言人（`core/memory_provider.py:618`、`:642`）
- Hermes 0.19.0 调 `on_turn_start` 时只传了轮次和消息（`agent/turn_context.py:791`），`sync_all` 只传 `session_id` 和 `messages`（`agent/memory_manager.py:666`）。整个 Hermes 包里搜不到 `turn_author` / `author_is_bot`
- 结果：`owner_ids` 配了也没用，群聊里所有人说的话都按「主人亲口说的」记。README 和 `nexsandglass_hermes/README.md` 里对 `owner_ids` 的说明在 Hermes 下不成立

Hermes 实际提供发言人信息的地方是会话上下文变量（`gateway/session_context.py`，网关每条消息绑定一次，`gateway/run.py:16375`）：

| 变量 | 内容 |
|---|---|
| `HERMES_SESSION_USER_ID` | 发这条消息的人在平台上的 ID（CLI 下为空） |
| `HERMES_SESSION_USER_NAME` | 显示名（可伪造，不用于判断主人） |
| `HERMES_SESSION_KEY` | `<ns>:<platform>:<chat_type>:…`，`chat_type` 是 `dm` / `group` / `channel` / `thread`（`gateway/session.py:904` 的 `build_session_key`） |
| `HERMES_SESSION_PLATFORM` | `telegram` / `discord` / …；CLI 下为空 |

修法（单独一个提交，保险箱之前合入）：

1. `on_turn_start` 在 agent 线程里用 `gateway.session_context.get_session_env` 读上面几个变量，连同消息的哈希记进一个小队列
2. `sync_turn` 在后台串行线程上执行，比下一轮的 `on_turn_start` 晚是常态 —— 所以不能用「最近一次的发言人」，而是按用户消息哈希去队列里配对；配不上就按**非主人**处理（对记忆：记为外部来源；对保险箱：不收）
3. 判断主人只认 ID，不认显示名（显示名在多数平台上谁都能改）。现有的 `owner_ids` 同时匹配 ID 和名字，保险箱这条路径只用 ID
4. 补测试：模拟网关绑定上下文变量 → 群聊里非主人的话不再按主人记

这一步本身就修了一个真实缺陷，即使不做保险箱也该做。

## 3. 总体结构

```
写入（sync_turn，后台线程）
  主人的消息 ──► secret_filter.scrub(vault=收下)
                   │  每处匹配：原文 ──► vault.put() ──► 返回 v3
                   ▼
                 「…key 是 [已隐去: Hugging Face token …ab12 #v3]」 ──► 日志 / 中枢 / 索引 / 向量（同 v7.14.0）
                   原文只以密文存在 vault.db（或系统钥匙串）

取回
  ① 命令行   python3 -m nexsandglass.vault show v3          原文只出现在主人的终端
  ② 直达     nyx_secret_reveal(v3) ─► 模型只拿到句柄 ⟦nyx:v3⟧
             Hermes transform_llm_output 钩子 ─► 发给主人之前把句柄换成原文
  ③ 经模型   nyx_secret_reveal(v3) ─► 工具结果里就是原文（默认关）
```

## 4. 什么进保险箱

同时满足才收：

1. `NYX_VAULT=on`，且后端可用（§5）；否则和 v7.14.0 一样只隐去
2. 主 agent（`agent_context == primary`），不是子 agent / 委派 / 定时任务
3. 这条消息配对到的发言人是主人（§2）：CLI 本地会话；或网关上 `HERMES_SESSION_USER_ID ∈ owner_ids`。**网关上没配 `owner_ids` 就一律不收** —— 宁可少收，不能把别人的密钥收进主人的保险箱，也不能让别人取主人的
4. 完整匹配；被截断的 token（`[已隐去: 密钥（被截断）]`）不收
5. 档位：`NYX_SECRET_FILTER=tokens` 时密码本来就不隐去，也就不收；`NYX_VAULT_KINDS=tokens`（待定，见 §14）时只收 token / API key / 私钥

助手的回复、群里别人的话、工具输出、委派结果、Hermes 内置记忆写入（`on_memory_write`）里的密钥：照旧只隐去，不收。

**去重**：同一个值再贴一次，得到同一个编号（按 HMAC 查，§5）。助手回复里如果出现保险箱里已有的值（比如刚取回过），占位符也带上那个编号，但不新增条目。

**每条存什么**

| 字段 | 说明 |
|---|---|
| `id` | 自增，显示为 `v3` |
| `kind` / `label` / `last4` | 与占位符一致（`huggingface` / `Hugging Face token` / `ab12`；密码不留末 4 位） |
| `context` | 那条消息隐去后的前后 40 个字，例如「帮我配一下 Hugging Face，key 是 [已隐去: …]」。日志里本来就有这段，不比日志更敏感 |
| `mem_id`、`created_at` | 来源记忆与时间，用于遗忘联动与列表 |
| `mac` | HMAC-SHA256(去重子密钥, 原文)，唯一索引。用带密钥的 HMAC 而不是普通哈希：防止拿着 vault.db 离线猜短密码 |
| `nonce`、`ct` | AES-256-GCM 密文（file 后端）；keyring 后端这两列为空 |
| `held`、`revealed`、`last_revealed_at` | 隔离状态、取回次数、最后一次取回 |

## 5. 存储

**后端**（`NYX_VAULT_BACKEND`，默认 `auto`：钥匙串能用就用钥匙串，否则加密文件）

| 后端 | 原文在哪 | 依赖 | 适合 |
|---|---|---|---|
| `keyring` | 系统钥匙串（macOS Keychain / Windows 凭据管理器 / Linux Secret Service），服务名 `nyx-vault/<数据目录哈希>`，账户名 `v3` | 可选依赖 `keyring` | 桌面 |
| `file` | `vault.db` 里的密文 | 可选依赖 `cryptography` | 无桌面的服务器（多数 Hermes 网关跑在这种机器上） |

**没有明文后备**：两样都没有 → 打一条警告，按 `off` 处理（只隐去），`doctor` 里写明原因。

**加密文件的细节**
- 主密钥 32 字节随机数，放 `~/.config/nyx/vault.key`（遵守 `XDG_CONFIG_HOME`），文件 0600、目录 0700；**不放数据目录** —— 数据目录被备份 / 同步 / 拷走时密钥不跟着走。也可以用 `NYX_VAULT_KEY`（base64）给
- 密钥文件权限比 0600 宽（组或其他人可读）就拒绝使用，像 ssh 对私钥那样
- HKDF 从主密钥派生两把子密钥：加密用、去重 HMAC 用
- AES-256-GCM，96 位随机 nonce；附加数据 = `nyx-vault-v1|<id>|<kind>`，把密文绑死在这一行上（把别的行的密文挪过来解不开）
- `vault.db` 是 SQLite，删除后 VACUUM，并纳入 `erasure.wipe_free_space()`
- v7.14.0 的原始字节复查（`secret_scrub._bytes_scan`）照常扫 `vault.db` —— 里面只有密文，扫出来应该是 0，这就是「原文不落盘」的验收

**备份**：`backup_paths()` 带上 `vault.db`（只有密文），不带密钥。换机器恢复时没有密钥 → 列表照常、取回时提示「无法解密：这台机器上没有保险箱密钥」。文档里提醒主人把密钥另存（比如存进密码管理器）。

## 6. 取回的三条路

### 6.1 命令行（最安全，始终可用）

```
python3 -m nexsandglass.vault status            # 后端、条目数、密钥文件权限
python3 -m nexsandglass.vault list [关键词]      # 编号、类别、末 4 位、时间、上下文；不出原文
python3 -m nexsandglass.vault show v3           # 原文打到终端
python3 -m nexsandglass.vault show v3 --copy    # 放进剪贴板（pbcopy / wl-copy / xclip / clip.exe，有哪个用哪个），30 秒后清空
python3 -m nexsandglass.vault delete v3         # 真删
python3 -m nexsandglass.vault rekey             # 换主密钥，全部重新加密
```

原文不经过任何模型、任何聊天平台、任何 Hermes 会话记录。要求主人能登上这台机器。

### 6.2 直达（Hermes，推荐的工具路径）

思路：模型只拿到句柄，替换发生在 Hermes 把回复发给主人之前。

1. 模型调 `nyx_secret_reveal(id="v3")`，过完 §7 的闸门
2. 工具结果里**没有原文**：`{"status": "queued", "handle": "⟦nyx:v3⟧", "note": "原文不经过你。在回复里原样写上句柄，nyx 发出前替换；不要复述或猜测原文。"}`
3. nyx 在内存里记一条待发：`(session_id, v3, 10 分钟有效，只用一次)`
4. 回合结束时 Hermes 调插件钩子 `transform_llm_output(response_text, session_id, …)`（`agent/turn_finalizer.py:455`）。nyx 的钩子把回复里的句柄换成原文（放进代码块方便复制）；模型没写句柄就在末尾追加一行「🔑 v3（Hugging Face token，10 月 20 日存）：…」。别的会话的句柄不换
5. 网关在回复被改写过时会重发最终版本（`gateway/run.py:22061` 的注释），所以流式输出的平台上主人也能拿到替换后的那条

这样做的效果（都已在 0.19.0 源码里核对，实现时要用集成测试再确认一遍）：
- 模型服务商在这一轮看不到原文：工具结果里只有句柄
- Hermes 存的会话记录来自 `messages`（`gateway/run.py:13375` 一带），里面是句柄；之后几轮发给模型的历史也是句柄
- 改写后的回复会传给 nyx 自己的 `sync_turn`（`turn_finalizer.py:584`，在钩子之后）—— 被 v7.14.0 的过滤器隐去，不会以明文进记忆
- 聊天平台上会留下原文（这一条防不住，见 §9）

**要装一个配套插件**。Hermes 把记忆提供器当成「独占插件」单独加载，给它的 `register_hook` 是空函数（`plugins/memory/__init__.py:343`），所以钩子没法从记忆提供器里注册。做法：另带一个很小的普通插件 `nyx-reveal`（`nexsandglass_hermes_reveal/`，只有 `plugin.yaml` + 二十来行的 `register(ctx)`），通过 `python3 -m nexsandglass.vault hermes-install` 复制到 `$HERMES_HOME/plugins/nyx-reveal/`，再由主人执行 `hermes plugins enable nyx-reveal`（Hermes 的用户插件默认不启用）。注意：这个插件的源码里不能出现 `register_memory_provider` 这个字符串，否则 Hermes 会把它也当成记忆提供器（`hermes_cli/plugins.py:1594`）。

没装或没启用配套插件时，`nyx_secret_reveal` 不会退到 6.3，而是返回 `{"status": "use_cli", "command": "python3 -m nexsandglass.vault show v3"}`，让模型把命令告诉主人。

### 6.3 经模型（默认关，`NYX_VAULT_REVEAL=llm` 才开）

工具结果里直接是原文，由模型转述。原文会：发给模型服务商；进 Hermes 的会话记录（`~/.hermes` 下）；留在之后几轮的上下文里，直到被压缩（Hermes 压缩时会自己打码）。只给明确接受这些代价、又不想装配套插件的人。

## 7. 工具与闸门

只有 `NYX_VAULT=on` 时才出现在工具列表里（不给所有人加工具）。

| 工具 | 返回 | 说明 |
|---|---|---|
| `nyx_secret_list(query?)` | 编号、类别、末 4 位、保存日期、上下文、状态（可取回 / 在隔离区） | 永远不含原文 |
| `nyx_secret_reveal(id)` | `queued` / `value`（仅 6.3）/ `need_confirm` / `use_cli` / `denied`（带原因） | 原文只按 §6 交出 |

系统提示加一行（仅开启时）：「记忆里的 `#vN` 是保险箱编号。主人明确要看原文时调用 `nyx_secret_reveal`；不要猜测、拼凑或复述原文。」

**`nyx_secret_reveal` 的闸门**（按顺序，任何一道不过就 `denied`，不说原文存不存在以外的细节）

| # | 闸门 | 防什么 |
|---|---|---|
| 1 | 主 agent；不是子 agent、委派、定时任务（`agent_context`；定时任务看 `HERMES_CRON_SESSION`，与 Hermes 审批的判断方式相同，`tools/approval.py:241`） | 无人值守的流程替主人取 |
| 2 | 当前这一轮的发言人是主人（§2；网关上必须配了 `owner_ids` 且 ID 命中） | 群里别人让 agent 把主人的 key 发出来 |
| 3 | 不是群聊：`chat_type` 必须是 `dm`，或 CLI 本地 | 发在群里大家都看得见 |
| 4 | 主人**这一轮的原话**里有明确的取回意图（「给我 / 告诉我 / 显示 / 发我 … token / key / 密码 / v3」，或者是 §7 的确认短语） | 被召回的记忆、网页、文件里埋的提示注入让 agent 自己去取 |
| 5 | 人工确认（下面两种之一） | 以上都被绕过时的最后一道；模型伪造不了 |
| 6 | 频率：每小时 3 次、每天 10 次；确认码连错 3 次锁 15 分钟 | 反复试探 |
| 7 | 审计：`vault_audit.jsonl` 记时间、编号、取回路径、平台、会话类型、发言人 ID 的哈希、结果；**不记原文** | 事后能查 |

**第 5 道：人工确认**
- **Hermes 自带审批**（首选）：调 `tools.approval.request_tool_approval("nyx_secret_reveal", reason="显示保险箱 v3（Hugging Face token …ab12，10 月 20 日存）", rule_key="nyx_vault:<一次性随机数>")`（`tools/approval.py:2940`）。CLI 下是终端里的确认提示，网关下是聊天平台里的审批消息；默认 60 秒超时，超时 / 拒绝 / 出错一律当拒绝。这条路是 Hermes 专门留给插件「把某个工具调用升级到人工审批」的入口，模型跳不过去
  - `rule_key` 每次都用新的随机数：审批界面上有「本会话都允许 / 永远允许」，用固定 key 的话主人点一次「永远」，以后的取回就都不问了。代价是点「永远」会在 Hermes 的允许名单里留一条用不上的记录
  - **审批被整体关掉时不用它**：`--yolo`、会话里的 `/yolo`、`approvals.mode: off` 都会让这个审批直接放行（`tools/approval.py:2472` 的 `is_approval_bypass_active()`）。主人关掉命令审批，不等于同意 agent 随便交出密钥 —— 这时改走确认码
- **确认码**（审批不可用或被关掉时）：nyx 生成 6 位数字，要求主人在下一条消息里原样回复「确认 v3 482913」。走 6.2 时这句提示由钩子直接追加到回复里，模型看不到码；走 6.3 时由模型转述。下一轮 `on_turn_start`（agent 线程）核对：同一会话、同一发言人 ID、5 分钟内、完全一致 → 这一轮内 `nyx_secret_reveal(v3)` 放行一次

## 8. 生命周期

| 事件 | 保险箱 |
|---|---|
| `nyx_forget` / `forget` 把来源记忆放进隔离区 | 对应条目 `held=1`：列表里显示「在隔离区」，不能取回 |
| `nyx_restore` | `held=0` |
| 隔离到期或 `purge` | 删行 + 删钥匙串条目 + VACUUM `vault.db`（走 `wipe_free_space`） |
| `vault delete v3` | 直接真删；来源记忆里的占位符保留，编号变成「已删除」 |
| `scrub --apply --to-vault`（新选项） | 处理 v7.14.0 之前存下、还没清理过的明文：日志里 `sender == user` 的那些先收进保险箱、占位符带编号，其余照旧只隐去。不加 `--to-vault` 时行为不变 |
| `doctor` / `nyx_health` | 新增一项 `vault`：后端是否可用、密钥文件权限、条目数、来源记忆已不存在的孤儿条目、配套插件是否已启用 |

## 9. 威胁模型

**防得住**
- 数据目录被拷走 / 同步 / 进了备份：里面只有密文，密钥不在数据目录
- 记忆被召回、被塞进提示词、被发给嵌入服务：那里只有占位符（同 v7.14.0）
- 群聊里别人、被召回内容里的提示注入、子 agent、定时任务让 agent 交出密钥：§7 闸门 1–5
- 走 6.2 时模型服务商在取回这一轮看到原文：工具结果里只有句柄
- 主人点一次「永远允许」之后再也不问：每次新的 `rule_key`
- 主人关掉了命令审批：识别出来，改走确认码

**防不住（要在文档里写清楚）**
- **原文第一次贴进聊天的那一刻**：已经发给了模型服务商，留在 Hermes 的会话记录（`~/.hermes/state.db` 等）和聊天平台里。nyx 管不到这些。凡是在聊天里贴过的密钥，都应该换掉
- 取回之后聊天平台上的那条消息（Telegram 等会一直留着）。要么取回后手动删掉那条，要么用命令行
- 6.3 经模型：原文进模型服务商和 Hermes 会话记录
- 6.2 下其它启用了的 Hermes 插件：`post_llm_call` 钩子拿到的是替换后的回复（`turn_finalizer.py:485`）
- 这台机器已经被攻破，或者同一个系统用户下有恶意程序：能读密钥文件就能解密
- 平台上的身份本身被冒用（主人的账号被盗）
- 旁边有人看屏幕

## 10. 配置

| 键（`nyx.json` / 环境变量） | 默认 | 取值 |
|---|---|---|
| `vault` / `NYX_VAULT` | `off` | `off` / `on` |
| `vault_backend` / `NYX_VAULT_BACKEND` | `auto` | `auto` / `keyring` / `file` |
| `vault_reveal` / `NYX_VAULT_REVEAL` | `direct` | `direct`（6.2，没装插件时提示走命令行）/ `cli`（工具只列不取）/ `llm`（6.3） |
| `vault_kinds` / `NYX_VAULT_KINDS` | 待定（§14） | `tokens` / `all` |
| `NYX_VAULT_KEY` | 无 | base64 主密钥，优先于密钥文件 |
| `owner_ids` | 空 | 网关上用保险箱**必须**填，且填平台 ID |

`hermes memory setup` 的配置项里加 `vault` 和 `vault_reveal` 两项（同 v7.14.0 加 `rerank` 的方式）。

## 11. 要改的文件

| 文件 | 改动 |
|---|---|
| `core/memory_provider.py` | §2 发言人识别；`sync_turn` 给主人的消息传 `vault=` 回调；两个工具与闸门；`on_turn_start` 核对确认码；系统提示一行；配置项 |
| `core/vault.py`（新） | 两个后端、加密、去重、`put / list / reveal / hold / unhold / delete / rekey / status`、待发句柄、确认码、频率限制、审计 |
| `core/secret_filter.py` | `scrub(..., vault=None)`：每处完整匹配调回调拿编号，占位符带 ` #vN`；`_PLACEHOLDER` 现有正则已能匹配带编号的形式 |
| `vault.py`（新，CLI） | §6.1 的子命令 + `hermes-install` |
| `nexsandglass_hermes_reveal/`（新） | 配套插件：`plugin.yaml`、`__init__.py` 注册 `transform_llm_output` |
| `core/erasure.py`、`core/quarantine.py` | 遗忘 / 还原 / 真删联动；`wipe_free_space` 带上 `vault.db` |
| `core/secret_scrub.py`、`scrub.py` | `--to-vault` |
| `doctor.py`、`core/memid.py` | `vault` 自检项 |
| `pyproject.toml` | 可选依赖 `vault = ["cryptography>=41"]`、`vault-keyring = ["keyring>=24"]` |
| `tests/conftest.py` | 清掉 `NYX_VAULT*`；`XDG_CONFIG_HOME` 指到临时目录（不碰开发者真实的密钥文件） |
| README、`nexsandglass_hermes/README.md`、CHANGELOG | 用法、威胁模型、§2 的更正 |

## 12. 测试与验收

测试里的 token 一律运行时拼出来（源码里不出现完整 token，避免 GitHub 推送保护，同 v7.14.0）。

**单元**
- 加解密往返；换密钥解不开；把一行的密文挪到另一行解不开（附加数据绑定）；密钥文件权限过宽拒绝；没装 `cryptography` 也没有钥匙串时按 `off` 处理且没有任何明文落盘；同值去重
- 收录范围：主人的收、带编号；参与者 / 机器人 / 助手 / 被截断的不收；`tokens` 档位下密码不收；占位符幂等（带编号的占位符再过一遍过滤器不变）
- §2：模拟网关绑定上下文变量，群聊里非主人的话按外部来源记；`sync_turn` 晚于下一轮 `on_turn_start` 时仍配对正确；配不上时按非主人
- 闸门逐条：子 agent、定时任务、非主人、只有显示名匹配、群聊、没有明确意图、审批拒绝 / 超时 / 出错、`/yolo` 下改走确认码、确认码错 3 次锁定、过期、频率限制
- 直达：句柄替换；模型没写句柄时追加；别的会话的句柄不换；句柄过期 / 只用一次；工具结果 JSON 里没有原文
- 生命周期：遗忘 → 不能取回；还原 → 能取回；真删 → 行、钥匙串条目都没了，`vault.db` VACUUM 后按原始字节扫不到
- 审计文件里没有原文

**端到端（验收标准）**
1. `NYX_VAULT=off`：现有全部测试不改一行照过
2. 开启 + 直达：用本地假模型服务（同 `tests/test_qa_eval.py` 的做法）跑一遍「贴 key → 若干轮 → 要回 key」，**记下发给假模型的每一个请求体：原文一次都不出现**（第一次贴的那一轮除外 —— 那是用户自己发出去的）；发给用户的最终回复里有原文
3. 整个数据目录按原始字节扫：原文 0 处
4. 用真实的 hermes-agent 0.19.0 加载配套插件，跑一轮 `turn_finalizer`，确认钩子被调用、`messages` 里是句柄（CI 里装 hermes-agent 做这一项；装不了就作为发布前的手动验收，记进 CHANGELOG）

## 13. 分期

| 期 | 内容 | 能单独发布吗 |
|---|---|---|
| A | §2 发言人识别修复 | 能，而且应该先发（修的是现有缺陷） |
| B | 保险箱核心 + 收录 + 命令行取回（6.1）+ 遗忘联动 + doctor | 能：开了以后，聊天里贴的密钥能在终端里找回 |
| C | `nyx_secret_list` + 直达取回（6.2）+ 配套插件 + 审批 / 确认码 | 依赖 A、B |
| D | 经模型取回（6.3）、`scrub --to-vault` | 可选 |

## 14. 需要你拍板

1. **默认取回路径**：建议 `direct`（装了配套插件就直达，没装就让 agent 把命令行告诉你）。另一个选择是 `cli`：agent 只帮你找是哪一条，原文永远只在终端
2. **密码收不收**：建议收（`all`）。只收 token 的话，「我 Wi-Fi 密码是多少」这种问题照旧答不上
3. **网关上没配 `owner_ids` 时**：建议一律不收、不取（本设计的写法）。另一个选择是「私聊默认算主人」—— 方便，但任何能私聊到你机器人的人都能取
4. **v7.14.0 之前存下、还没清理的明文**：要不要做 `--to-vault`（D 期）。已经 `scrub --apply` 过的找不回来
5. **先发 A 期**：建议先单独发一个小版本修发言人识别，再做保险箱
