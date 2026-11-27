**跨厂商实时语音的 Nyx 记忆接入方案**

调查日期：2026-11-26。状态：源码调查与现有基础测试完成，以下新增组件尚未实现。
本次暂停直接连接用户实例，未访问其服务或读取私人记忆。没有发布、部署或修改产品代码。
M1、LANG 与 M2 的进度仍以 [PLAN.md](../../PLAN.md) 为准。

结论：已有一条可复用的厂商无关路径：Conduit → Hermes memory provider → Nyx。
要让实时语音自然检索记忆，应补上跨通道的转写调度与结果注入。可以统一自动发起检索，
但“检索已发生”“记忆已送出”“本次回答开始前已可用”“模型实际采用”是不同的验收项。

**调查基线与当前能力**

| 仓库 | 本次读取的完整 SHA |
| --- | --- |
| kaishi00/hermes-conduit | `8f79a0a97d0462b65d4e1ecb8a888a069be49f5e` |
| kaishi00/hermes-conduit-notifier | `8c157894c95df03b2f4a4306d9eb8e1a27c786c1`，插件 0.202.2 |
| kaishi00/hermes-conduit-desktop | `bb330900a3a50803408a8343613d3bcffecbb634` |

这三个当前源码基线不代表用户手机、桌面或服务器的安装版本。

**已完成并进入上游：此前提交的 Nyx 语音上下文**

用户提交的 [notifier #937](https://github.com/kaishi00/hermes-conduit-notifier/pull/937)
是已有成果。维护者通过 [#940](https://github.com/kaishi00/hermes-conduit-notifier/pull/940)
接续合入，GitHub 记录的合并日期为 2026-11-20，纳入插件 **0.185.0**。
#940 明确写明承接 #937；本次核对的 0.202.2 源码仍调用该接口。

该更新在所选提供方实现 `voice_prompt_block()` 时，把 Nyx 的语音事实放进 `/memory/context`，
再由已有的各语音模式开场 briefing 使用。因此，Nyx 的开场画像、约束和待跟进接入已经完成，
后续开发应直接复用。原 PR 的范围明确不包含逐轮语义召回和实时写入；本方案新增部分聚焦于
用户说话后的自动检索、轮次管理与动态结果注入，不重复实现既有开场能力。

| 通道 | 当前开场记忆 | 当前通话中检索 | 不由模型决定的逐轮调度 |
| --- | --- | --- | --- |
| Gemini Live | 开启该模式的记忆共享后读取 context | `recall_memory` 工具，经共享工具桥调用 provider | 尚未接入 |
| Qwen Live | 同上，独立开关与共享授权 | 同一工具桥，经 Qwen 协议适配 | 尚未接入 |
| Grok Live | 同上 | 同一工具桥，经 Grok 协议适配 | 尚未接入 |
| GPT-Live，ChatGPT 订阅或现有 API-key 模式 | 开场 briefing 包含记忆 | 无独立 recall 工具；可把工作委派给 Hermes | 尚未接入 |
| Local Live 的前置快模型／语音服务器自身模型 | 当前 agent setup 没有接入 memory 参数和 recall 声明 | 可委派 Hermes；直接记忆能力存在缺口 | 尚未接入 |
| Classic／Local Live 的完整 Hermes 逐轮模式 | 使用 Hermes 自己的常驻上下文 | 经过正常 Hermes turn，可运行 Nyx provider 的 prefetch | 复用现有 Hermes 生命周期，仍受提供方过滤、预算和可用性约束 |

上游 [Hermes memory in a live call 功能清单](https://github.com/kaishi00/hermes-conduit/blob/8f79a0a97d0462b65d4e1ecb8a888a069be49f5e/docs/FEATURE_MAP.md#L656-L689)
明确记载了 GPT-Live 无 recall 工具及 Local Live agent 无记忆开关这两个缺口。
同一清单将桌面列为对应功能的 sibling；桌面也有开场 context 和 Gemini/Qwen/Grok 工具检索。

现有入口可继续保留：

- `GET /api/plugins/conduit_push/memory/context?profile=...`：专用语音事实与内置快照。
- `POST /api/plugins/conduit_push/memory/recall?profile=...`：调用所选 provider 的 `prefetch()`。
- Nyx 实现 `voice_prompt_block()`；其他提供方未实现时仍走既有兼容路径。

不要在开场新增空查询或全库通用检索。上游现有 ruling 采用专用 voice facts，明确不在开场
做额外 recall，以避免启动延迟。新的自动检索从用户的稳定发言开始。

**方案一：复用现有完整 Hermes 语音回合**

已存在的 `voice.speech_server.drive_turns: true`，即语音服务设置中的 **Hermes answers**，
可以让兼容的 OpenAI Realtime 协议服务承担听和说，由 Hermes 处理回答。
应选择完整 Hermes 逐轮路径；Local Live 的 agent 快模型仍有上表中的记忆缺口。
直接试用时可优先选择 Classic 的自定义语音服务免提（Hands-free on your speech server），
按完整 Hermes 回合运行；Local Live 前置快模型的记忆能力仍需单独补齐。

流程为：语音转写 → Hermes turn → Nyx prefetch／常驻约束 → Hermes 模型流式回答 → 语音播放。
这样可以把记忆查询放进生成前流程。是否找到了正确事实、是否成功渲染以及模型是否采纳仍需验收。
回答的推理由 Hermes 所选模型负责，原生语音模型的自主对话能力和首句延迟可能改变。

上游已提供 OpenAI Realtime、Qwen Audio Realtime 等 speech server preset；
其他服务需兼容所用 WebSocket 事件、转写、读回和打断约定，不能仅凭厂商名称保证可用。
这条路径不等于把 ChatGPT 订阅的 GPT-Live 改造成公开 Realtime API，也不会自动改变计费来源。

参考：[现有设置与模式说明](https://github.com/kaishi00/hermes-conduit/blob/8f79a0a97d0462b65d4e1ecb8a888a069be49f5e/docs/OWN_VOICE_SERVER.md#L31-L96)，
[插件的逐轮接管行为](https://github.com/kaishi00/hermes-conduit-notifier/blob/8c157894c95df03b2f4a4306d9eb8e1a27c786c1/README.md#L1245-L1278)。

**方案二：保留原生实时语音，增加统一自动召回**

建议作为 M2 的主要实现方向。在插件中新增通用 `VoiceMemoryCoordinator`；手机、Watch
和桌面只实现事件采集及厂商协议适配，使共同策略保持在同一处。组件名和下列新接口均为设计，
当前源码没有这些新增能力。

```mermaid
flowchart LR
    A[各通道的稳定转写] --> B[Conduit 插件统一调度]
    B --> C[Hermes 绑定的 Nyx provider]
    C --> D[有预算与来源的记忆结果]
    D --> E[当前通话与轮次校验]
    E --> F[对应语音协议追加上下文]
```

调度层需要：

1. 将 ASR 片段归并为带修订号的稳定输入。可对稳定部分短暂去抖、提前预取，再由最终转写确认；
   不对每个 delta 发请求，不依赖用户说“调用 Nyx”，也不只靠提示词要求模型选择工具。
   使用有界近期对话帮助“她”“那件事”等指代形成可检索的问题，保留原话与派生查询的区别；
   这与 M2-01 的工作记忆共同实现，不能把未经证实的查询摘要变成长期事实。
2. 按已认证档案绑定通话；携带 `call_id / turn_id / revision / request_id` 和连接代次。
   档案访问仍由接入层授权，正文中的这些 ID 不提供权限。结果发出前必须与当前状态核对。
3. 合并并发，缓存同一稳定查询；显式工具调用与自动调用共享额度。取消等待不等于终止提供方线程，
   迟到结果必须丢弃或明确转为下一轮背景，不能冒充已参与上一轮回答。
4. 召回结果携带来源、适用状态、预算及遗漏原因，以资料形式注入。保留整条否定、更正和条件，
   不能用任意字符前缀替代完整证据。已发送到远端的旧内容不能宣称已被完全撤回。
5. 记录 requested/generated/sent/partial/failed 等可观测事件；没有宿主应用确认时，applied 为未知。
   `send()` 成功与回答采用记忆必须分别评估。

短期可在客户端稳定转写事件上调用既有 `/memory/recall`，先验证收益。
正式跨设备实现可新增兼容的 prepare/event 路由，将共同节流、关联和失效策略放入插件。
旧 context/recall 签名继续支持。当前接口主要返回文本；不能假装它已经返回完整 ContextPacket。
本地 M1 契约集成发布后，再协调新增结构化输出。HTTP 时间预算应在服务端转成本机单调截止时间，
不能把客户端的 monotonic 值当成服务器时间戳。

各通道已有的静默注入出口：

| 通道 | 当前出口 | 接入时的注意事项 |
| --- | --- | --- |
| Gemini | `.contextNote` → `clientContent`，`turnComplete=false` | 保留为上下文，不额外启动回复；不保证已抢在当前生成之前 |
| Qwen | 同一抽象，映射为 conversation item，未发 `response.create` | 根据 Qwen 的转写修订与连接代次做失效 |
| Grok | `.contextNote` → conversation item，`wantsResponse=false` | 与已有工具结果和自动 VAD 生成区分 |
| GPT-Live | GA 的 `session.thinking.append`；旧协议的 `session.context.append` commentary | 每片最多 500 UTF-8 字节；当前 Bool 只代表至少部分片段已发送 |
| Local Live agent | `.contextNote` → `conduit.agent.note` | 同时补开场 memory 与 recall 工具声明；服务自身模型仍经该桥适配 |

GPT-Live 的长结果不能直接按字符数假定能放入一片：中文与英文的 UTF-8 成本不同。
需要记录整包完整发送与部分发送的差异，控制记忆大小；不能让先发出的残缺句子暂时丢失后半段否定。
较长的不可分事实应省略或使用经过验证的结构化短表达，不能把分片成功称为原子应用。

**可保证的边界**

统一调度能让支持转写与上下文追加的语音通道主动调用 Nyx。它不能给任意闭源语音接口凭空增加能力。

ChatGPT 订阅 GPT-Live 的现有控制器明确没有可靠的暂停生成控制。其 GA 用户 turnDone 可能在
助手已开始输出时才合成，因此必须从较早的转写事件预取，并把迟到结果如实记录。
同样，当前 Conduit 的 API-key GPT-Live 使用 `/v1/live/sessions` 和 client delegation，
不能只因它使用 API key，就当作已接入普通 Realtime API 的 `create_response=false` 闸门。

对于这类通道，可以承诺程序自动触发召回及尽早追加，但不能承诺每次首句之前必定完成检索。
需要生成前强制记忆检查的任务，应进入完整 Hermes 回合，或另行实现、测试真正具备生成闸门的协议路径。
仅静音扬声器无法证明模型暂停生成或旧音频已被废弃。

**接入范围、约束与验收**

- 每种模型的记忆共享权限继续独立，默认关闭；切换厂商不能复用另一厂商的授权。
  Local Live agent 新增记忆开关时也遵守这条规则。
- 手机/iPad 共用控制器；CarPlay 复用手机通话。Watch 使用自身转写及授权 relay/grant；
  新事件不能绕过已有档案授权。桌面提供对应的薄适配，并同步 feature map、设置说明和测试。
- 目前 context/recall 各每档案 30 次/分钟，query 最多 500 字符，context 最多 8000、recall 最多 4000；
  单请求约 10 秒，提供方卡住时可返回 503。自动检索需给显式查询留出额度，不能承诺无限逐句请求。
- 内核的估算 token 预算、外部字符限额和 GPT-Live UTF-8 分片限额分别校验。
- 读取与写入分开处理：先沿用保存通话后的既有同步；若以后逐轮写回，须和 call-end 归档共享幂等身份，
  避免重复保存。临时转写不直接晋升长期事实。

建议实施顺序：

1. 补齐 Local Live agent 的记忆授权、开场数据及 recall 声明；列出所有模式的真实 capability。
2. 实现统一稳定转写调度与薄协议适配，优先验证“无需说 Nyx 也会发起召回”。
3. 验证 GPT-Live 的追加结果、分片和晚到路径；有严格要求时使用 Hermes 回合策略。
4. 完成手机、Watch、桌面/CarPlay、重连、打断、跨档案、中英转写修订和记忆关闭的回放与真机测试。

固定同一份测试记忆和问题，分别记录：是否发起召回、相关事实是否返回、是否完整送出、是否在回答前到达、
回答是否正确使用事实、首句延迟、无关注入、过时事实与重复写入。报告分位延迟，不能仅用平均 HTTP 时间代替通话体验。

**本次验证与限制**

最新版插件的现有 `test_memory.py / test_local_live.py / test_speech_server.py`：
**297 通过**，1 项已有 Starlette/httpx 弃用提醒，39.08 秒。
使用仓库 CI 的 `pytest --rootdir=tests` 方式；普通根目录调用会提前导入插件包根并缺少 Hermes 上下文，
因此未把该调用错误当作产品故障。未修改测试或跳过检查来获得通过。

这轮验证涵盖现有 provider 路由、隔离、限额、回退、语音接管及读回基础；它使用测试替身，
没有验证新调度器，也没有连接用户的 Nyx、真实厂商语音服务或启动计费通话。
本环境没有运行 iOS/watchOS XCTest 或真实设备音频测试。核心阶段进度没有因此提前完成。

本地测试证据：`/workspace/analysis/nyx-multivendor-voice-upstream-verified-2026-11-26.log`。

其他关键源码：

- [共享静默上下文协议](https://github.com/kaishi00/hermes-conduit/blob/8f79a0a97d0462b65d4e1ecb8a888a069be49f5e/Shared/GeminiLive/GeminiLiveProtocol.swift#L158-L184)
- [Gemini/Qwen 的 memory 与工具装配](https://github.com/kaishi00/hermes-conduit/blob/8f79a0a97d0462b65d4e1ecb8a888a069be49f5e/Conduit/Services/AppState%2BGeminiQwenLive.swift#L229-L310)
- [Grok 复用同一工具桥](https://github.com/kaishi00/hermes-conduit/blob/8f79a0a97d0462b65d4e1ecb8a888a069be49f5e/Conduit/Services/AppState.swift#L2182-L2265)
- [Local Live agent 装配缺口](https://github.com/kaishi00/hermes-conduit/blob/8f79a0a97d0462b65d4e1ecb8a888a069be49f5e/Conduit/Services/AppState%2BLocalLive.swift#L67-L99)
- [GPT-Live 追加协议与转写完成机制](https://github.com/kaishi00/hermes-conduit/blob/8f79a0a97d0462b65d4e1ecb8a888a069be49f5e/Conduit/Voice/GPTLive/GPTLiveProtocol.swift#L144-L280)
- [插件当前 memory 路由](https://github.com/kaishi00/hermes-conduit-notifier/blob/8c157894c95df03b2f4a4306d9eb8e1a27c786c1/dashboard/api/memory.py#L415-L524)
