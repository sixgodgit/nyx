# Nyx 协议文档 — Thalamus ↔ Hypnos ↔ Nyx 数据流转

> 三位一体协议：**Thalamus**（丘脑路由）→ **Hypnos**（睡神认知循环）→ **Nyx**（夜神感知）

---

## 1. 架构总览

```
                        ┌──────────────────────────────────┐
                        │          用户 / LLM 调用            │
                        └──────────┬───────────────────────┘
                                   │ 请求
                                   v
 ┌─────────────────────────────────────────────────────────────────┐
 │  [L0] Thalamus — 丘脑路由中枢                                    │
 │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐   │
 │  │ 瀑布分发  │→ │ 模型编排  │→ │ 结果聚合  │→ │  上下文管理   │   │
 │  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────┬───────┘   │
 │       │              │             │               │            │
 │       └──────────────┴─────────────┴───────────────┘            │
 │                          │                                      │
 └──────────────────────────┼──────────────────────────────────────┘
                            │ sandglass_log.log_message()
                            │ shadow_sand.shadow_index()
                            v
 ┌─────────────────────────────────────────────────────────────────┐
 │  [L1-L2] Hypnos — 睡神认知循环                                    │
 │  ┌─────────────────────────────────────────────────────────┐    │
 │  │  sandglass_log ───→ sandglass.txt (明文日志，无上限)       │    │
 │  │       │                                                   │    │
 │  │       v                                                   │    │
 │  │  shadow_sand ───→ shadow_sand.db (实体索引 + 信任分)       │    │
 │  │       │                                                   │    │
 │  │       v                                                   │    │
 │  │  sandglass_vault ───→ sandglass.db (FTS5 / 倒排 / mmap)   │    │
 │  └─────────────────────────────────────────────────────────┘    │
 │                          │                                      │
 └──────────────────────────┼──────────────────────────────────────┘
                            │ nyx_kindle(line_num, moment, text)
                            v
 ┌─────────────────────────────────────────────────────────────────┐
 │  [L3] Nyx — 夜之感知层                                           │
 │  ┌─────────────────────────────────────────────────────────┐    │
 │  │  ┌──────────┐    ┌──────────────────────────────────┐   │    │
 │  │  │  _Veil   │    │          _Mist                   │   │    │
 │  │  │ (Bloom   │    │  ┌────────────────────────────┐  │   │    │
 │  │  │  Filter) │    │  │  phantoms (SQLite)         │  │   │    │
 │  │  │          │    │  │  token     TEXT PK          │  │   │    │
 │  │  │ touch()  │    │  │  born      TEXT (首次出现)   │  │   │    │
 │  │  │ probe()  │    │  │  last_spotted TEXT (最近)   │  │   │    │
 │  │  │ rest()   │    │  │  sightings  INT (次数)      │  │   │    │
 │  │  │ stir()   │    │  │  traces     TEXT (行号)     │  │   │    │
 │  └──┴──────────┘    │  │  whisper    TEXT (片段)     │  │   │    │
 │  nyx_veil.bin        │  │  updated_at TEXT           │  │   │    │
 │  (128KB 位图)        │  └────────────────────────────┘  │   │    │
 │                     └──────────────────────────────────┘   │    │
 │  nyx_mist.db                                              │    │
 └─────────────────────────────────────────────────────────────┘    │
                            │                                      │
 └──────────────────────────┼──────────────────────────────────────┘
                            │ 搜索 / 追猎
                            v
 ┌─────────────────────────────────────────────────────────────────┐
 │  [用户请求流]                                                      │
 │                                                                  │
 │  写入路径:                                                        │
 │    Thalamus ──→ Hypnos (sandglass_log) ──→ Nyx (nyx_kindle)      │
 │                                                                  │
 │  读取路径:                                                        │
 │    Thalamus ──→ Hypnos (sandglass_vault.search)                  │
 │                    │ 结果 < 2 条?                                 │
 │                    v                                              │
 │                  Nyx (nyx_hunt) ──→ 注入 Phantom 低语 ──→ 用户    │
 │                                                                  │
 │  维护路径:                                                        │
 │    nyx_forget(token)   手动遗忘                                   │
 │    nyx_cleanup(days)   定时清理                                   │
 │    nyx_reindex()       重建 Veil ←→ Mist 一致性                   │
 └─────────────────────────────────────────────────────────────────┘
```

---

## 2. 数据流转时序

### 2.1 写入路径（落沙时）

```
Thalamus (用户消息/LLM响应)
  │
  ├─→ sandglass_log.log_message(line_num, timestamp, text)
  │     └─→ sandglass.txt  ← L1: 明文日志
  │
  ├─→ shadow_sand.shadow_index(text)
  │     └─→ shadow_sand.db  ← L2: 实体索引
  │
  └─→ nyx.nyx_kindle(line_num, timestamp, text)  ← L3: 夜感
        ├─→ _scent(text)          → 嗅出实体 token 列表
        ├─→ _veil.touch(token)    → Bloom Filter 留印
        └─→ _mist.haunt(token, ...) → SQLite 铭刻 Phantom
              ├─ 首次出现: INSERT
              └─ 重复出现: UPDATE sightings+1, last_spotted
```

### 2.2 读取路径（搜索时）

```
Thalamus (用户搜索)
  │
  └─→ sandglass_vault.search(query)  ← L2: 文本搜索
        │
        ├─ 结果 ≥ 2 条 → 直接返回
        │
        └─ 结果 < 2 条 → Hypnos 自动触发：
              └─→ nyx.nyx_hunt(query)
                    ├─→ nyx_sense(query)     → 感知熟悉度
                    ├─→ _veil.probe(token)   → 逐个检查
                    └─→ _mist.stalk(token)   → 模糊匹配鬼影
                          └─→ 返回 Phantom list
                                    │
                                    v
                              注入到用户结果
```

### 2.3 维护路径

```
定时维护 (cron / MCP 工具):
  └─→ nyx_cleanup(days=90)
        └─→ DELETE phantoms WHERE julianday('now')
                 - julianday(last_spotted) > days

手动遗忘:
  └─→ nyx_forget("entity_token")
        └─→ DELETE FROM phantoms WHERE token=?

灾难恢复:
  └─→ nyx_reindex()
        └─→ Mist → 遍历所有 token → 重建 Veil bits
            当 nyx_veil.bin 损坏或迁移后调用
```

---

## 3. Thalamus 与 Nyx 的接口契约

| 方向 | Thalamus 调用 | Nyx 响应 | 说明 |
|------|--------------|---------|------|
| 写入 | `nyx_kindle(ln, ts, text)` | 无返回值 | 纯副作用，失败不阻塞 |
| 感知 | `nyx_sense(text)` | `{familiar_ratio, known_tokens, ...}` | 同步返回，~2μs |
| 追猎 | `nyx_hunt(query, limit=5)` | `{hunted, conviction, phantoms}` | 降级容错 |
| 凝视 | `nyx_gaze()` | `{veil_density, total_phantoms, ...}` | 运维监控 |
| 安息 | `nyx_rest()` | 无返回值 | 强制持久化 |
| 遗忘 | `nyx_forget(token)` | `{forgotten, token, removed}` | 精确删除 |
| 清理 | `nyx_cleanup(days=90)` | `{purged, threshold_days, detail}` | 批量过期 |
| 重建 | `nyx_reindex()` | `{reindexed, veil_before, veil_after, ...}` | 数据修复 |

---

## 4. 错误处理与降级策略

```
nyx_kindle ───→ 失败 → 日志警告，不抛出异常
nyx_sense  ───→ 失败 → 返回空感知（familiar_ratio=0）
nyx_hunt    ───→ 失败 → 返回空结果，保留熟悉度提示
                      （conviction 折半，标记可能聊过）
nyx_gaze    ───→ 失败 → 返回 veil_density，total=0
nyx_forget  ───→ 失败 → forgotten=False + error reason
nyx_cleanup ───→ 失败 → purged=0 + error reason
nyx_reindex ───→ 失败 → reindexed=False + error reason
```

所有 Nyx 接口均包装在 `try/except` 内，**不影响上游** Thalamus 或 Hypnos 的正常流转。

---

## 5. 文件依赖图

```
nyx.py
 ├── sandglass_paths.py  ─→ _NB (neurobase 根路径)
 ├── nyx_veil.bin        ─→ 128KB Bloom Filter 位图
 ├── nyx_mist.db         ─→ SQLite Phantom 存储 (WAL 模式)
 └── hashlib / sqlite3   ─→ 标准库，无外部依赖
```

---

## 6. 约束条件

| 约束 | 值 | 原因 |
|------|---|------|
| Veil 假阳性率 | ~0.5% (1M bits, 7 hashes) | Bloom Filter 数学保证 |
| Veil 最大容量 | ~150K 实体 (1% dentsity) | 超过后假阳性率上升 |
| Mist 回溯窗口 | 最近 50 条行号 | 防止 traces 字段膨胀 |
| Mist 最大行宽 | whisper 截断 80 字符 | 控制 DB 大小 |
| 清理默认阈值 | 90 天 | 超过 3 月未提及≈遗忘 |
| LLM 调用数 | **0** | 纯算法，无 LLM 依赖 |

---

## 7. 与 NexSandglass 容器协议

```
NexSandglass/
 ├── sandglass_log.py      → L1: 写入 sandglass.txt
 ├── shadow_sand.py        → L2: 实体索引 shadow_sand.db
 ├── sandglass_vault.py    → L2: 搜索 sandglass.db
 ├── sandglass_mcp.py      → MCP 工具注册
 ├── nyx.py                → L3: 夜之感知   ← 本文档描述
 ├── sandglass_paths.py    → 路径配置
 └── scripts/
      └── backfill_nyx.py  → 回填已有数据到 Nyx
```

---

> *"Nyx 不知晓回忆的内容，只知晓黑暗中有那个影子。"*
