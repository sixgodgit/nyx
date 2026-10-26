# 密钥保险箱（方案 3）

[English](secret-vault.md) | **简体中文**

> 状态：**已实现，v7.15.0**。依据 nyx 源码和 hermes-agent 0.19.0 源码；文中引用的 Hermes 行号都指 0.19.0。
> 默认关闭。不打开时，行为和 v7.14.0 完全一样，一个字节都不差。
> 按个人使用设计：只有一个用户（主人），不区分发言人（见 §2）。

## 0. 一句话

从 v7.14.0 起，聊天里贴的 token、API key、密码在写进记忆之前就会被换成占位符，原文直接丢掉。

保险箱是一个由主人自己打开的选项：原文不丢，而是加密存进保险箱。主人明确要看时，经过人工确认再交还给主人，**并且尽量不让模型服务商看到原文**。

## 1. 做什么，不做什么

**做**
- 加密保存主人贴的密钥。只收用户一侧的消息，助手回复、工具输出里的不收。占位符带编号，例如 `[hidden: HF token …ab12 #v3]`（v7.16.0 以前存下的是 `[已隐去: …]`，照样认）。
- 主人问「我上次那个 HF token 是什么」时，agent 不需要原文就能找到是哪一条：编号、类别、末 4 位、保存时间、当时的上下文。
- 主人要原文时，有三种取回方式（§6）。每次都要人工确认，有频率限制，有审计记录。
- 遗忘联动：来源记忆进了隔离区，这一条就取不回来；来源记忆真删了，这一条也跟着真删（§8）。

**不做**
- 它不是密码管理器。重要凭据请用 1Password、Bitwarden 或系统钥匙串。保险箱只是「聊天里顺手贴过、事后想找回」时的兜底。
- 不给 agent「拿密钥去调别的服务」的能力。agent 自己要用的 key，请放进 Hermes 的环境变量或密钥配置。
- 管不了已经泄露出去的部分。密钥贴进聊天的那一刻，原文已经发给了模型服务商，也留在了 Hermes 自己的会话记录和聊天平台里（§9）。
- 找不回 v7.14.0 `scrub --apply` 已经抹掉的原文。抹掉就是没了。

## 2. 前提：个人使用，不验证发言人

nyx 是个人记忆，用户一侧的每条消息都算主人说的，保险箱不判断「这句话是谁说的」。所以 §7 的闸门管的是「**是不是主人自己要取**」（靠明确的请求加人工确认），而不是「是不是主人」。

由此有一条使用约束，已写进文档：开了保险箱，就别把机器人拉进群聊，否则群里任何人都会被当成主人（§9）。

顺带记一笔，这件事与保险箱无关，另行处理：README 里说 `owner_ids` 能在群聊里区分主人和别人，但 Hermes 0.19.0 从来不把发言人信息传给记忆提供器。
- `on_turn_start` 只传轮次和消息（`agent/turn_context.py:791`）。
- `sync_all` 只传 `session_id` 和 `messages`（`agent/memory_manager.py:666`）。
- 所以 nyx 读的 `author_id` / `turn_author` 永远是空的，这个选项在 Hermes 下不起作用。

## 3. 总体结构

```
写入（sync_turn，Hermes 的串行后台线程）
  用户一侧的消息 ──► secret_filter.scrub(vault=Collector())
                      │  每处匹配：原文 ──► secret_vault.put() ──► 返回 3
                      ▼
                    「…key 是 [hidden: HF token …ab12 #v3]」 ──► 日志 / 中枢 / 索引 / 向量（同 v7.14.0）
                      原文只以密文的形式存在 vault.db（或系统钥匙串）里

取回
  ① 命令行   python3 -m nexsandglass.vault show v3          原文只出现在主人的终端里
  ② 直达     nyx_secret_reveal(v3) ─► 模型只拿到句柄 ⟦nyx:v3⟧
             nyx-reveal 插件的 transform_llm_output 钩子 ─► 回复发给主人之前，把句柄换成原文
  ③ 经模型   nyx_secret_reveal(v3) ─► 工具结果里直接是原文（默认关）
```

代码位置：
- `core/secret_vault.py`：存储、加密、引用状态、审计、频率限制、直达句柄、确认码。
- `core/secret_filter.py` 的 `scrub(vault=...)`：给占位符加上编号。
- `core/memory_provider.py`：两个工具和闸门。
- `nexsandglass_hermes_reveal/`：Hermes 配套插件。
- `nexsandglass/vault.py`：命令行。

模块名不叫 `vault`，因为 `features/sandglass_vault.py` 是倒排索引，避免撞名。

## 4. 什么进保险箱

以下条件同时满足才收：

1. `NYX_VAULT=on`，并且后端可用（§5）。否则和 v7.14.0 一样，只隐去不保存。
2. 是主 agent（`agent_context == primary`）在写入，不是子 agent。子 agent 本来就不写记忆。
3. 是用户一侧的消息（`sync_turn` 的 `user_content`）。
4. 是完整匹配。被截断的 token（`[hidden: secret (truncated)]`）不收，`<private>…</private>` 也不收（它的意思是「别记」）。
5. 档位允许：`NYX_SECRET_FILTER=tokens` 时密码本来就不隐去，自然也不收；`NYX_VAULT_KINDS=tokens` 时只收 token、API key、私钥，不收密码。

助手回复、工具输出、委派结果、Hermes 内置记忆写入（`on_memory_write`）里的密钥，照旧只隐去，不收。

**去重**：同一个值再贴一次，还是同一个编号（按 HMAC 查，见 §5）。助手回复里如果出现保险箱里已有的值，占位符也带上那个编号，但不新增条目。

**取回过的原文会按字面再隐去一次**。直达取回时，钩子把原文追加在回复里，Hermes 再把改写后的回复交给 `sync_turn`（`turn_finalizer.py:584`，在钩子之后执行）。像「（密码）：`hunter2`」这种写法，过滤规则不一定认得出来。所以取回过的值会在内存里记一小时：之后它再出现在要写入的文本里（模型复述、主人贴回来），都会换成带编号的占位符（`secret_vault.redact_revealed`）。

**每条存什么**（`vault.db` 的 `secrets` 表）

| 字段 | 说明 |
|---|---|
| `id` | 自增编号，显示为 `v3`。**删掉的编号不复用**，所以旧记忆里的 `#v3` 永远不会指向另一把密钥 |
| `kind` / `label` / `last4` | 与占位符一致，例如 `huggingface` / `HF token` / `ab12`。密码不留末 4 位 |
| `context` | 那条消息隐去之后、占位符前后的一段文字，例如「帮我配一下 Hugging Face，key 是 [hidden: …]」。日志里本来就有这段，不比日志更敏感 |
| `created_at` | 保存时间 |
| `mac` | HMAC-SHA256(去重子密钥, 原文)，建唯一索引。用带密钥的 HMAC 而不是普通哈希，是为了防止有人拿着 vault.db 离线猜短密码 |
| `backend`、`nonce`、`ct` | 存在哪个后端。用加密文件时这里是 AES-256-GCM 密文；用钥匙串时 `ct` 为空 |
| `revealed`、`last_revealed_at` | 取回次数、最后一次取回时间 |

**状态不存在表里，按引用实时计算**：
- 有尚未被遗忘的记忆提到 `#v3` → 可取回。
- 只有隔离区里的记忆提到它 → 不能取回。
- 哪里都没有提到 → 由 `gc()` 删掉（§8）。

这样，同一个值出现在两条记忆里时，忘掉其中一条不会影响另一条。

## 5. 存储

**后端**（`NYX_VAULT_BACKEND`，默认 `auto`）

| 后端 | 原文放在哪 | 依赖 | auto 下何时用 |
|---|---|---|---|
| `file` | `vault.db` 里的密文 | `pip install 'nyx-memory[vault]'`（cryptography） | 装了能用的 cryptography 时 |
| `keyring` | 系统钥匙串：macOS Keychain、Windows 凭据管理器、Linux Secret Service / KWallet。服务名 `nyx-vault:<库路径哈希>`，账户名 `v3` | `pip install 'nyx-memory[vault-keyring]'` | 没有 cryptography、但有可信钥匙串时 |

auto 先选加密文件，不先选钥匙串，有两个原因：
- 网关多半跑在没有桌面的机器上，那里通常没有钥匙串。
- 在桌面上，后台线程访问钥匙串可能弹出授权框，把写入卡住。

钥匙串只认真正的系统钥匙串。`keyrings.alt` 的 PlaintextKeyring 之类会把「密钥」明文写进家目录，这种不认。

**没有明文后备**：两种后端都不可用时，按 `off` 处理（只隐去），`doctor` 里会写明原因。cryptography 装坏了也算没装。实测：系统包里的 cryptography 缺 `_cffi_backend` 时，import 抛的是 pyo3 的 `PanicException`，它继承自 `BaseException`，`except Exception` 接不住。

**加密文件的细节**
- 主密钥是 32 字节随机数，存在 `~/.config/nyx/vault.key`（遵守 `XDG_CONFIG_HOME`）。文件权限 0600，目录 0700。它**不放在数据目录里**，这样数据目录被备份、同步或拷走时，密钥不会跟着走。也可以用环境变量 `NYX_VAULT_KEY`（base64）提供。
- 密钥文件权限比 0600 宽（组或其他人可读）时拒绝使用，和 ssh 对待私钥的方式一样。
- 用 HKDF 从主密钥派生出两把子密钥，一把加密用，一把去重 HMAC 用。`meta` 表里存一个密钥校验值，换错密钥时会报「密钥不对」，而不是解出乱码。
- 加密用 AES-256-GCM，96 位随机 nonce。附加数据是 `nyx-vault-v1|<id>|<kind>`，把密文绑死在这一行上：把别的行的密文挪过来也解不开。
- `vault.db` 是 SQLite，文件权限 0600。删除条目后会 VACUUM，`erasure.wipe_free_space()` 也覆盖它。
- v7.14.0 的原始字节复查（`secret_scrub._bytes_scan`）照常扫描 `vault.db`。里面只有密文，扫出来是 0 处，这就是「原文不落盘」的验收。

**备份**：`backup_paths()` 会带上 `vault.db`（里面只有密文），但不带密钥。换机器恢复时如果没有密钥，列表照常显示，取回时提示「这台机器上没有保险箱密钥」。`vault status` 会提醒主人把密钥另存一份，比如存进密码管理器。

## 6. 三种取回方式

### 6.1 命令行（最安全，始终可用）

```
python3 -m nexsandglass.vault status            # 开没开、后端、密钥文件（含权限）、条目数、配套插件
python3 -m nexsandglass.vault list [关键词]      # 编号、类别、末 4 位、时间、上下文、状态；不出原文
python3 -m nexsandglass.vault show v3           # 把原文打到终端
python3 -m nexsandglass.vault show v3 --copy    # 放进剪贴板（pbcopy / wl-copy / xclip / xsel / clip.exe，有哪个用哪个），30 秒后清空
python3 -m nexsandglass.vault delete v3         # 真删
python3 -m nexsandglass.vault gc                # 删掉来源记忆已不存在的条目
python3 -m nexsandglass.vault rekey             # 换主密钥，全部重新加密
python3 -m nexsandglass.vault audit             # 最近的取回 / 删除记录（不含原文）
```

原文不经过任何模型、任何聊天平台、任何 Hermes 会话记录。前提是主人能登上这台机器。

在新版 Hermes 里，nyx-memory 装在 Hermes 自己的隔离环境里，系统 Python 里没有 nyx，所以要用 `hermes nyx vault …`（v7.15.2 起）。子命令和上面一样，比如 `hermes nyx vault show v3`。agent 转告给你的命令也会写成这个形式（`core/hints.py`）。

### 6.2 直达（Hermes 下的默认方式）

思路：模型只拿到一个句柄，换成原文这一步发生在 Hermes 把回复发给主人之前。

1. 模型调用 `nyx_secret_reveal(id="v3")`，先过 §7 的闸门。
2. 工具结果里**没有原文**，只有：`{"status": "queued", "handle": "⟦nyx:v3⟧", "note": "The secret does not pass through you…"}`。
3. nyx 在进程内存里记下一条待发：会话 id、编号、原文。10 分钟内有效，只能用一次。
4. 回合结束时，Hermes 调用插件钩子 `transform_llm_output(response_text, session_id, …)`（`agent/turn_finalizer.py:455`）。
   - nyx 的钩子把回复里的句柄换成原文，放进代码块方便复制。
   - 如果模型没写句柄，就在末尾追加一行「🔑 v3 (HF token, saved 2026-10-20): …」（中文界面是「🔑 v3（HF token，2026-10-20 存）：…」）。
   - 别的会话的句柄不换。
5. 回复被改写过时，网关会重发最终版本（`gateway/run.py:22061` 的注释）。所以在流式输出的平台上，主人也能收到替换后的那条。

**配套插件 `nyx-reveal`**：Hermes 把记忆提供器当作「独占插件」单独加载，交给它的 `register_hook` 是个空函数（`plugins/memory/__init__.py:343`），所以钩子没法从记忆提供器里注册，只能另带一个普通插件。
- 它通过 pip 入口点 `hermes_agent.plugins → nyx-reveal` 随 nyx-memory 一起安装（`hermes_cli/plugins.py:1658` 会扫描这个入口点组）。
- Hermes 的用户插件默认不启用。`python3 -m nexsandglass_hermes install --vault` 会顺带执行 `hermes plugins enable nyx-reveal`；也可以自己执行一次，再重启 Hermes。
- 插件源码里不能出现 `register_memory_provider` 或 `MemoryProvider`。否则 Hermes 会把它也当成记忆提供器，不加载它的钩子（`hermes_cli/plugins.py:1594`）。测试会检查这一点。

没装或没启用配套插件时，`nyx_secret_reveal` 不会退到 6.3，而是返回 `{"status": "use_cli", "command": "python3 -m nexsandglass.vault show v3", ...}`，让模型把命令告诉主人。

**在真实 Hermes 里验证过**（`benchmarks/vault_hermes_e2e.py`，hermes-agent 0.19.0）。用真的 AIAgent、真的插件加载、真的终端审批、真的会话库，只把模型换成本地假服务，并记下它收到的每一个请求体：
- 发给模型 7 个请求，含原文的 0 个。
- 确认码在主人打出来之前，没发给过模型。
- Hermes 会话库（state.db）9 行，带句柄的 4 行（正向对照：确实存了），带原文的 0 行。
- 按原始字节扫 Hermes 目录和 nyx 数据目录，都找不到原文。

### 6.3 经模型（默认关，设 `NYX_VAULT_REVEAL=llm` 才开）

工具结果里直接是原文，由模型转述。代价是原文会：
- 发给模型服务商；
- 进入 Hermes 的会话记录（`~/.hermes` 下）；
- 留在之后几轮的上下文里，直到被压缩（Hermes 压缩时会自己打码）。

只适合明确接受这些代价、又不想装配套插件的人。nyx 自己的记忆里，原文仍然会按字面隐去（§4）。

## 7. 工具与闸门

这两个工具只在 `NYX_VAULT=on` 时才出现在工具列表里，不给所有人都加工具。

| 工具 | 返回 | 说明 |
|---|---|---|
| `nyx_secret_list(query?)` | 编号、类别、末 4 位、保存日期、上下文、状态（可取回 / 来源在隔离区 / 来源已不存在）、取回次数 | 永远不含原文 |
| `nyx_secret_reveal(id)` | `queued` / `value`（仅 6.3）/ `need_confirm` / `use_cli` / `denied`（附原因）/ `unavailable` | 原文只按 §6 交出 |

开启时，系统提示里加一行（给模型看的，一律英文）：「`#vN` in a memory is a secret-vault entry id. Call nyx_secret_reveal only when the user explicitly asks to see the secret; never guess, reconstruct or repeat it.」

**`nyx_secret_reveal` 的闸门**：按顺序检查，任何一道没过就返回 `denied`。

| # | 闸门 | 防什么 |
|---|---|---|
| 1 | 必须是主 agent，不能是子 agent 或定时任务。看 `agent_context`；定时任务看 `HERMES_CRON_SESSION`，与 Hermes 审批的判断方式相同（`tools/approval.py:241`） | 无人值守的流程替主人取 |
| 2 | 主人**这一轮的原话**里有明确的取回意图：既有「给我 / 告诉我 / 发我 / 显示 / 是什么……」这类动词，又有「token / key / 密钥 / 密码 / v3……」这类名词。主人刚回复了确认短语也算 | 被召回的记忆、网页、文件里埋的提示注入，让 agent 自己去取 |
| — | 条目存在，且来源记忆没被遗忘 | |
| 3 | 人工确认（见下面两种） | 以上都被绕过时的最后一道；模型伪造不了 |
| 4 | 频率：经工具取回每小时最多 3 次、每天最多 10 次（命令行不算）；确认短语连错 3 次锁 15 分钟 | 反复试探 |
| 5 | 审计：记在 `vault.db` 的 `audit` 表（`vault audit` 可查），包括时间、编号、动作、取回方式、平台、结果、原因；**不记原文** | 事后能查 |

**第 3 道：人工确认**
- **首选 Hermes 自带的审批**：调用 `tools.approval.request_tool_approval("nyx_secret_reveal", reason="Show vault entry v3 (HF token …ab12, saved 2026-10-20)", rule_key="nyx_vault:<一次性随机数>")`（`tools/approval.py:2940`）。
  - CLI 下是终端里的确认提示，网关下是聊天平台里的审批消息。默认 60 秒超时；超时、拒绝、出错，一律当作拒绝。
  - 这是 Hermes 专门留给插件、把某个工具调用升级为人工审批的入口，模型跳不过去。审批提示里只有编号、类别、末 4 位和日期，实测不含原文。
  - `rule_key` 每次都用新的随机数。审批界面上有「本会话都允许」和「永远允许」，如果用固定的 key，主人点一次「永远」，以后的取回就再也不问了。代价是点「永远」会在 Hermes 的允许名单里留下一条用不上的记录。
  - **审批被整体关掉时不走这条**。`--yolo`、会话里的 `/yolo`、`approvals.mode: off` 都会让这个审批直接放行（`tools/approval.py:2472` 的 `is_approval_bypass_active()`）。主人关掉命令审批，不等于同意 agent 随便交出密钥，所以这时改用确认码。
- **确认码**：在 Hermes 审批用不了或被关掉时使用。
  - nyx 生成 6 位数字，要求主人在下一条消息里原样回复，例如「确认 v3 482913」（英文界面是「confirm v3 482913」，两种写法都认）。
  - 走 6.2 时，这句提示由钩子直接追加到回复末尾，模型看不到码；走 6.3 时由模型转述。
  - 下一轮 `on_turn_start`（agent 线程）核对：同一会话、5 分钟内、完全一致，就在这一轮里放行一次 `nyx_secret_reveal(v3)`。
  - 模型在工具参数里自称「主人已经确认」没用，确认只认主人这一轮的原话（有测试覆盖）。

## 8. 生命周期

| 事件 | 保险箱 |
|---|---|
| `nyx_forget` / `forget` 把来源记忆放进隔离区 | 只剩隔离区里提到它，状态变为「来源记忆在隔离区」，不能取回 |
| `nyx_restore` | 又有在世的记忆提到它，可以取回 |
| 隔离区 `purge`（到期或指定） | 被清掉的那些记忆提到的编号里，哪里都不再被提到的，跟着真删，不需要等待（`drop_unreferenced`） |
| `forget(mode="purge")` 当场真删 | 同上 |
| `wipe_free_space()`（每次真删之后） | `gc()` 删掉来源已不存在、且已保存 2 分钟以上的条目（刚收进来、日志还没写到的那几毫秒不算孤儿），然后 VACUUM `vault.db` |
| `vault delete v3` | 直接真删。来源记忆里的占位符保留，编号显示为「已删除」 |
| `scrub --apply --to-vault` | 处理 v7.14.0 之前存下、还没清理过的明文：日志里 `sender == user` 的先收进保险箱，占位符带上编号，其余照旧只隐去。先单独收一遍，之后每一步都只查不收，保证日志和中枢拿到同一个编号（否则两边正文对不上）。不加 `--to-vault` 时行为不变 |
| `doctor` / `nyx_health` | 新增 `vault` 一项。没开算正常；开了但用不了算失败；来源已真删、却还留在保险箱里的条目也算失败，并提示 `vault gc`。直达方式下还会报告配套插件有没有启用（读 `$HERMES_HOME/config.yaml`） |

## 9. 威胁模型

**防得住**
- 数据目录被拷走、同步、进了备份：里面只有密文，密钥不在数据目录里。
- 记忆被召回、被塞进提示词、被发给嵌入服务：那里只有占位符（同 v7.14.0）。
- 被召回内容里的提示注入、子 agent、定时任务想让 agent 交出密钥：§7 的闸门 1–3 挡住。
- 走 6.2 时，模型服务商在取回这一轮看到原文：工具结果里只有句柄。已在真实 Hermes 里验证（§6.2）。
- 主人点了一次「永远允许」，之后取回再也不问：每次都用新的 `rule_key`。
- 主人关掉了命令审批：nyx 能识别出来，改走确认码。

**防不住**
- **原文第一次贴进聊天的那一刻**。那时原文已经发给了模型服务商，也留在了 Hermes 的会话记录（`~/.hermes/state.db` 等）和聊天平台里，nyx 管不到这些。凡是在聊天里贴过的密钥，都应该换掉。
- 取回之后聊天平台上的那条消息（Telegram 等会一直保留）。要么取回后手动删掉那条消息，要么用命令行取。
- 走 6.3（经模型）时：原文会进模型服务商和 Hermes 的会话记录。
- 走 6.2 时其它启用了的 Hermes 插件：它们的 `post_llm_call` 钩子拿到的是替换后的回复（`turn_finalizer.py:485`）。
- 群聊。保险箱不区分发言人（§2），机器人在群里时，群里任何人都会被当成主人，取回的原文也会发在群里。开了保险箱就别把机器人放进群。
- 主人的聊天账号被盗。
- 这台机器已经被攻破，或者同一个系统用户下有恶意程序：能读到密钥文件，就能解密。
- 旁边有人看屏幕。

## 10. 配置

| 键（`nyx.json` / 环境变量） | 默认 | 取值 |
|---|---|---|
| `vault` / `NYX_VAULT` | `off` | `off` / `on` |
| `vault_reveal` / `NYX_VAULT_REVEAL` | `direct` | `direct`（6.2；没装插件时提示走命令行）/ `cli`（工具只能列出、不能取）/ `llm`（6.3） |
| `vault_kinds` / `NYX_VAULT_KINDS` | `all` | `all` / `tokens`（不收密码） |
| `NYX_VAULT_BACKEND` | `auto` | `auto` / `file` / `keyring` |
| `NYX_VAULT_KEY` | 无 | base64 编码的主密钥，优先于密钥文件 |

`vault`、`vault_reveal`、`vault_kinds` 三项出现在 `hermes memory setup` 的配置项里。同一项如果同时设了环境变量，环境变量优先。

## 11. 改动的文件

| 文件 | 改动 |
|---|---|
| `core/secret_vault.py`（新） | 两种后端、加密、去重、引用状态、`put / lookup / entries / get / delete / gc / wipe / rekey / status / health`、审计与频率、直达句柄、确认码、按字面隐去取回过的值 |
| `core/secret_filter.py` | `scrub(..., vault=None)`：每处完整匹配调用回调拿编号，占位符带 ` #vN`。现有的 `_PLACEHOLDER` 正则已经能匹配带编号的形式 |
| `core/memory_provider.py` | `sync_turn` 给用户一侧的消息传 `vault=` 回调；两个工具与闸门；`on_turn_start` 核对确认短语；系统提示加一行；配置项；其它写入钩子也按字面隐去取回过的值 |
| `nexsandglass_hermes_reveal/`（新） | 配套插件：注册 `transform_llm_output` |
| `nexsandglass/vault.py`（新） | §6.1 的命令行 |
| `core/erasure.py`、`core/quarantine.py` | 真删联动；`wipe_free_space` 覆盖 `vault.db` |
| `core/secret_scrub.py`、`scrub.py` | `--to-vault` |
| `core/memid.py` | `vault` 自检项 |
| `pyproject.toml` | 可选依赖 `vault`、`vault-keyring`；入口点 `hermes_agent.plugins` |
| `.github/workflows/*.yml` | CI 安装 `.[vault]`；发布前的冒烟测试检查 `nyx-reveal` 入口点 |
| `tests/test_secret_vault.py`（新）、`tests/conftest.py` | 22 项测试；清掉 `NYX_VAULT*`，`XDG_CONFIG_HOME` 指向临时目录（不碰开发者真实的密钥文件） |
| `benchmarks/vault_hermes_e2e.py`（新） | 真实 Hermes 下的端到端验收（发布前手动跑；CI 不装 hermes-agent） |

## 12. 测试与验收

测试里的 token 一律在运行时拼出来，源码里不出现完整 token，以免触发 GitHub 推送保护（同 v7.14.0）。

`tests/test_secret_vault.py` 覆盖：
- 加密：往返；去重；编号不复用；密钥文件 0600、目录 0700，且不在数据目录里；把密文挪到别的行解不开；换错密钥报「密钥不对」；权限太宽时拒绝；没有后端、或 cryptography 装坏了（抛 `BaseException`）时只隐去；钥匙串后端只信任真正的系统钥匙串。
- 收录：用户一侧的收，助手回复只认已有编号；`<private>` 和被截断的不收；`tokens` 档不收密码；带编号的占位符再过一遍不变；整个数据目录按原始字节找不到原文。
- 闸门：没有明确请求、子 agent、定时任务、不存在的编号、来源在隔离区、频率限制、确认短语连错 3 次锁定、模型在参数里自称已确认无效、Hermes 审批批准 / 拒绝 / 被关掉（改走确认码）、`rule_key` 每次不同。
- 直达：工具结果里没有原文；确认码不经过模型；钩子把句柄换成原文；别的会话换不了；用一次就失效；之后的 `sync_turn` 里原文按字面隐去。
- 取回方式：`cli` 档和没装插件时都返回 `use_cli`；`llm` 档返回原文，但原文不进 nyx 记忆。
- 插件：注册的是 `transform_llm_output`，源码里没有会让 Hermes 误判的词。
- 生命周期：遗忘后不能取、还原后能取、`purge` 和当场真删都会跟着删；同一个值在两条记忆里时，忘掉一条不影响另一条。
- 命令行：`list` 不出原文、`show`、`status`、`rekey` 之后还能解开且去重照常、`delete`；doctor 里有 `vault` 这一项。
- `scrub --to-vault`：预演不收；`--apply` 之后数据目录里找不到原文，日志与中枢一致，隔离区那条不收；保险箱没开时报错。

验收结果：
1. `NYX_VAULT=off` 时，原有测试只改了两处期望值（多了配置项和自检项），其余全部照过。
2. 真实 Hermes 下的端到端验收：见 §6.2。

## 13. 已定的选择（v7.15.0）

1. 默认取回方式是 `direct`。装了配套插件就直达；没装就让 agent 把命令行告诉主人，不退到经模型。
2. 密码也收（`all`）。不想收就设 `vault_kinds: tokens`。
3. `scrub --to-vault` 已经做了。已经 `scrub --apply` 过的原文找不回来。

## 14. 相关问题

- `owner_ids` 在 Hermes 0.19.0 下不起作用（见 §2），这次没改。
- ~~README 推荐的「pip 装完自动发现」在 Hermes 0.19.0 下不成立~~（v7.15.0 已修）。Hermes 0.19.0 只按目录查找记忆插件：先找内置的 `plugins/memory/<名字>/`，再找 `$HERMES_HOME/plugins/<名字>/`（`plugins/memory/__init__.py:124` 的 `find_provider_dir`），不扫描 `hermes_agent.memory_providers` 入口点。现在改用 `python3 -m nexsandglass_hermes install`：
  - 找到 Hermes 用的 Python，没装 nyx-memory 就装进去；
  - 在 `$HERMES_HOME/plugins/nyx/` 放入口文件；
  - 设 `memory.provider: nyx`；
  - 最后用 Hermes 自己的加载器确认一次。

  普通插件的入口点（`hermes_agent.plugins`，nyx-reveal 用的就是这种）Hermes 会扫描，不受影响。
