# NexSandglass / Nyx（夜神）

> **夜神 Nyx — Hermes Agent 的跨会话记忆感知系统**

NexSandglass 是 Hermes Agent 的记忆基础设施，在 Hermes 原生 memory 工具关闭时接管全部跨会话记忆、事实存储、联想检索和 déjà vu 检测。

![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python) ![License](https://img.shields.io/badge/License-MIT-green) ![Version](https://img.shields.io/badge/version-3.0.0-blue)

---

## 📋 核心能力

| 能力 | 模块 | 说明 |
|------|------|------|
| 🧠 **沙漏 Sandglass** | `core/sandglass_sqlite.py` | 长期记忆存储、全文搜索、语义搜索 |
| 🕸️ **织线 Thread** | `core/thread_*.py` | 知识图谱（实体关系三元组），支持时间窗口查询 |
| 👻 **Déjà Vu** | `core/dejavu.py` | 模糊感知 —"感觉聊过但检索不到"的 ghost 检测（Bloom Filter） |
| 🏜️ **影子沙 Fact Store** | `core/fact_store.py` | 结构化事实存储（带信任评分） |
| 📊 **情绪/画像** | `core/emotion_vocab.py`, `core/persona.py` | 用户状态追踪、偏移率计算、回音折 |
| 🌙 **梦境 Dream** | `dream/` | 夜间多阶段复盘：记忆整理、反思成长、创造联结 |
| 🔌 **MCP 接口** | `interfaces/sandglass_mcp.py`, `interfaces/nyx.py` | MCP 工具接入 Hermes / Claude |

---

## 📦 包结构

```
nexsandglass/
├── core/                    # 基础设施
│   ├── memory_provider.py        # 记忆后端抽象
│   ├── search_router.py          # 检索路由（TF-IDF / ChromaDB）
│   ├── sandglass_sqlite.py       # SQLite 沙漏存储
│   ├── sandglass_paths.py        # 数据路径管理
│   ├── sandglass_archive.py      # 记忆归档
│   ├── l0_buffer.py              # L0 缓冲层
│   ├── emotion_vocab.py          # 情绪词库
│   ├── fact_store.py             # 事实存储（信任评分）
│   ├── dejavu.py                 # Déjà Vu 感知
│   └── thread_*.py               # 织线知识图谱
├── interfaces/               # 对外接口
│   ├── nexsandglass.py           # 主接口
│   ├── nyx.py                    # Nyx 适配层
│   ├── sandglass_mcp.py          # MCP 工具
│   └── plugin.py                 # 插件接口
├── utils/                    # 工具
│   ├── heartbeat.py              # 心跳
│   └── discipline.py             # 纪律约束
├── l3/                       # L3 层（高级记忆）
├── features/                 # 特性模块
│   └── ...                       # 梦境、画像等
├── demo/                     # 演示脚本
├── docs/                     # 文档
│   ├── PROTOCOL.md               # 通信协议
│   ├── persona.md                # 画像说明
│   └── 偏移率说明书.md           # 偏移率算法说明
├── experiments/              # 实验代码
├── scripts/                  # 部署脚本
├── skills/nyx/               # Hermes 技能层
│   ├── SKILL.md                  # 技能定义
│   └── references/               # 参考文档（14 篇）
├── Dockerfile                # 容器化部署
├── docker-compose.yml        # 编排
├── install.sh / install.bat  # 安装脚本
└── pyproject.toml            # 包配置 (v3.0.0)
```

---

## 🚀 安装

### 方式一：直接安装

```bash
# Linux / macOS
./install.sh

# Windows
install.bat
```

### 方式二：Docker

```bash
docker compose up -d
```

### 方式三：作为 Hermes 技能

技能文件位于 `skills/nyx/`，复制到 Hermes 技能目录即可自动加载：

```bash
cp -r skills/nyx ~/.hermes/skills/memory/
```

---

## 🔧 配置

`memory_bus_config.yaml`（或 Hermes `config.yaml`）示例：

```yaml
memory:
  provider: nexsandglass
  memory_enabled: true
  user_profile_enabled: true
  nyx:
    sandglass:
      backend: tfidf        # 或 chromadb
      auto_consolidate: true
    dream:
      enabled: true
      cron: "0 3 * * *"
    dejavu:
      enabled: true
      sensitivity: 0.7
```

---

## 🛠️ MCP 工具

| 工具 | 功能 |
|------|------|
| `sandglass_search` | 倒排索引关键词搜索 |
| `sandglass_semantic` | 语义搜索（TF-IDF / ChromaDB 后端） |
| `sandglass_recent` | 最近 N 条记忆 |
| `sandglass_persona` | 当前主人画像 |
| `sandglass_offset` | 当前偏移率（决策趋势） |
| `sandglass_echo` | 回音折（情感风向） |
| `sandglass_chart` | 情绪熵 ASCII 可视化 |
| `sandglass_dejavu` | Déjà Vu 检测（check/stats/save_bf） |
| `sandglass_thread` | 织线知识图谱查询 |
| `sandglass_thread_graph` | 实体子图展开 |
| `sandglass_thread_weave` | 因果链摘要 |
| `sandglass_dream` | 幽灵决策（"如果选另一个选项会怎样"） |
| `sandglass_export/import` | 记忆迁移 |
| `soul_export/merge` | 灵魂差分导出/合并 |
| `fact_store` | 事实增删查（信任评分） |
| `fact_feedback` | 信任评分反馈 |

---

## 📖 使用示例

```python
# 搜索记忆
sandglass_search(query="荷兰 BV 公司", limit=10)

# 语义搜索
sandglass_semantic(query="如何优化网络延迟", backend="tfidf")

# 查看主人画像
sandglass_persona()

# 查询知识图谱
sandglass_thread(entity="用户", relation="偏好", limit=5)

# 展开知识子图
sandglass_thread_graph(entity="Xian Delicious Foods", depth=2)

# 幽灵决策
sandglass_dream(question="如果选择另一个方案会怎样")
```

---

## 📊 数据存储

| 数据 | 位置 | 格式 |
|------|------|------|
| 沙漏记忆 | `~/.hermes/sandglass/` | SQLite / JSONL |
| 织线图谱 | `~/.hermes/sandglass/thread/` | JSON（三元组） |
| 事实存储 | `~/.hermes/sandglass/facts/` | SQLite |
| Déjà Vu | `~/.hermes/sandglass/dejavu.bf` | Bloom Filter |
| 梦境日志 | `~/.hermes/dreams/` | Markdown |

---

## 📝 更新日志

### v3.0.0 (2026-07-31)

- 🔄 重构为模块化包结构（core / interfaces / utils / features / l3）
- 🧠 技能层同步：`skills/nyx/` 完整纳入仓库（SKILL.md + 14 篇参考文档）
- 📝 README 重写：新增架构说明、MCP 工具表、使用示例
- 🐛 修复 `_fail_open` 装饰器循环导入问题
- 🔀 合并远端文件（nyx.py、PROTOCOL.md、demo/）

### 早期版本

- v2.9.9：雷军 TOCTOU 修复（Lock + 元组原子赋值）
- v2.9.9：词库自生长修复（马云 + 托尼双审）
- 基础沙漏记忆存储与检索

---

## 📄 License

MIT License

---

**Made with 🧠 by Nyx — 夜神记忆感知系统**
