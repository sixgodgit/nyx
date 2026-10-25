## Nyx 装好了

1. **重启 Hermes**（网关：`hermes gateway restart`）。之后每轮对话自动记忆、自动召回。
2. 没加 `--enable` 的话，先执行一次 `hermes config set memory.provider nyx`。
3. 选项（记忆目录、每轮召回预算、语义检索、密钥保险箱……）：`hermes memory setup`。
4. 自检：`hermes memory status`；或者在对话里让 agent 调用 `nyx_health`。

可选：**密钥保险箱**（聊天里贴过的 key 之后还能让 agent 交还给你，原文不经过模型）。

```bash
hermes plugins enable nyx-reveal      # 再在 hermes memory setup 里把 vault 设成 on，然后重启
```

说明：https://github.com/sixgodgit/nyx/blob/main/nexsandglass_hermes/README.md
