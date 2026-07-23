# NexSandglass

> 夜神 Nyx — 跨会话记忆感知系统

NexSandglass 是 Hermes Agent 的记忆基础设施，提供：
- **沙漏** — 长期记忆存储、全文搜索、语义搜索
- **织线** — 知识图谱（实体关系三元组）
- **Déjà Vu** — 模糊感知（"感觉聊过但检索不到"的 ghost 检测）
- **影子沙** — 结构化事实存储（带信任评分）
- **情绪/画像** — 用户状态追踪、偏移率计算

## 包结构

```
nexsandglass/
├── core/              # 基础设施
│   ├── memory_provider.py     # 记忆后端抽象
│   ├── search_router.py       # 搜索路由（倒排 + 语义）
│   ├── sandglass_sqlite.py    # SQLite 存储
│   ├── sandglass_log.py       # 日志
│   ├── sandglass_archive.py   # 归档
│   ├── sandglass_paths.py     # 路径配置
│   ├── emotion_vocab.py       # 情绪词库
│   ├── sandglass.py           # 沙漏主类
│   └── l0_buffer.py           # L0 缓冲
│
├── l3/                # L3 智能层
│   ├── l3_search_core.py      # 搜索核心
│   ├── persona_l3.py          # 用户画像
│   ├── offset_l3.py           # 偏移率
│   ├── weave_l3.py            # 织线引擎
│   ├── scene_l3.py            # 场景管理
│   ├── emotion_l3.py          # 情绪处理
│   ├── l3_tasks.py            # 任务管理
│   ├── l3_persona_verify.py   # 画像验证
│   ├── l3_persona.py          # 画像（旧）
│   └── offset_signals.py      # 偏移信号
│
├── features/          # 功能模块
│   ├── sandglass_vault.py     # 安全存储
│   ├── sandglass_think.py     # 思考引擎
│   ├── shadow_sand.py         # 结构化事实
│   ├── weavethread.py         # 织线线程
│   ├── pulse.py               # 心跳脉冲
│   ├── nightwatch.py          # 夜间守护
│   ├── decision_particles.py  # 决策粒子
│   ├── multi_analysis.py      # 多分析
│   └── soul_diff.py           # 灵魂差分
│
├── interfaces/        # 对外接口
│   ├── sandglass_mcp.py       # MCP 服务器
│   ├── plugin.py              # Hermes 插件
│   └── nexsandglass.py        # CLI / TTY 包装
│
└── utils/            # 工具
    ├── heartbeat.py           # 心跳
    └── discipline.py          # 纪律规则
```

## 安装

```bash
pip install -e .
```

## 使用

```python
from nexsandglass import core, l3, features

# 搜索记忆
results = core.search_router.search("关键词")

# 模糊感知（Déjà Vu）
ghosts = features.sandglass_vault.check_dejavu("上次聊过这个")

# 知识图谱
relations = core.sandglass.get_thread("entity_name")
```

## 配置

NexSandglass 作为 Hermes Agent 的 MCP 服务器运行，配置在 `~/.hermes/config.yaml` 的 `mcp` 部分。

## 版本

| 版本 | 日期 | 说明 |
|------|------|------|
| 3.0.0 | 2026-07 | 重构为模块化包结构 |
| 2.9.9 | 2026-06 | 最后单体脚本版本 |
