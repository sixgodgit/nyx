## Nyx is installed

1. **Make Nyx the memory provider:** `hermes config set memory.provider nyx`. Some Hermes versions already do this with `--enable`; running it again is harmless.
2. **Restart Hermes** (gateway: `hermes gateway restart`). From then on every turn is remembered and recalled automatically.
3. Options (data directory, recall budget, semantic search, secret vault, language…): `hermes memory setup`.
4. Check: `hermes memory status` (should show `Provider: nyx`) and `hermes nyx doctor`.

Optional: **secret vault**. A key you pasted in chat can later be handed back to you, after you approve it, without the model seeing the value:

```bash
hermes plugins enable nyx-reveal      # then set vault to on in hermes memory setup, and restart
```

Terminal commands: `hermes nyx vault | doctor | scrub | repair | status`.
Docs: https://github.com/sixgodgit/nyx/blob/main/hermes-plugin/README.md

This variant also installs a local embedding model (sentence-transformers, about 470 MB downloaded from Hugging Face on first run).

---

## Nyx 装好了

1. **设为记忆插件**：`hermes config set memory.provider nyx`。有的 Hermes 版本加了 `--enable` 已经设好，再执行一次也没关系。
2. **重启 Hermes**（网关：`hermes gateway restart`）。之后每轮对话自动记忆、自动召回。
3. 选项（记忆目录、每轮召回预算、语义检索、密钥保险箱……）：`hermes memory setup`。
4. 自检：`hermes memory status`（应显示 `Provider: nyx`）；`hermes nyx doctor`。

可选：**密钥保险箱**（聊天里贴过的 key 之后还能让 agent 交还给你，要你确认，原文不经过模型）：`hermes plugins enable nyx-reveal`，再在 `hermes memory setup` 里把 vault 设成 on，然后重启。

终端命令：`hermes nyx vault | doctor | scrub | repair | status`。中文界面：`hermes memory setup` 里把 `lang` 设成 `zh`。中文说明：https://github.com/sixgodgit/nyx/blob/main/nexsandglass_hermes/README.zh-CN.md
