"""
NexSandglass 织线——织布机的线材 — V2.9.3-dev
三元组提取 + SQLite 存储 + 图谱查询
零 LLM，纯正则，存 shadow_sand.db 的 wthread_triples 表
"""
import re
import sqlite3
import os
import logging
from datetime import datetime, timezone

from nexsandglass.core.sandglass_paths import _NB

_DB = os.path.join(_NB, "shadow_sand.db")

# ── 关系提取模式（正则）──
_EXTRACT_PATTERNS = [
    # 决策类
    (r'(?:决定用|最终用|换用|改用|选了|选择用|采用了|用了|用上了)\s*([\u4e00-\u9fff\w]+)', '使用'),
    (r'(?:放弃|不用|砍掉|去掉|移除|弃用|拒了|不用了)\s*了?\s*([\u4e00-\u9fff\w]+)', '放弃'),
    (r'([\u4e00-\u9fff\w]{2,8})\s*(?:比|优于|好过|胜过|强于|不如)\s*([\u4e00-\u9fff\w]+)', '对比'),
    (r'(?:把|将)\s*([\u4e00-\u9fff\w]+)\s*(?:换成|替换为|改成|迁移到|切到|替代为)\s*([\u4e00-\u9fff\w]+)', '替换为'),
    (r'用了?\s*([\u4e00-\u9fff\w]+)\s*替代\s*([\u4e00-\u9fff\w]+)', '替代'),
    (r'从\s*([\u4e00-\u9fff\w]+)\s*迁到\s*([\u4e00-\u9fff\w]+)', '迁移'),
    # 关系类
    (r'(?:装了|安装了|部署了|搭建了)\s*([\u4e00-\u9fff\w]+)', '安装'),
    (r'(?:依赖|基于|构建在)\s*([\u4e00-\u9fff\w]+)上?', '依赖'),
    # 偏好类
    (r'(?:喜欢|偏好|倾向|偏爱)\s*([\u4e00-\u9fff\w]+)', '偏好'),
    (r'(?:讨厌|不喜欢|反感|烦)\s*([\u4e00-\u9fff\w]+)', '反感'),
]


logger = logging.getLogger(__name__)

def _ensure_table():
    """确保 wthread_triples 表存在"""
    conn = sqlite3.connect(_DB, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wthread_triples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            relation TEXT NOT NULL,
            object TEXT NOT NULL,
            source_line INTEGER,
            confidence REAL DEFAULT 0.5,
            source TEXT DEFAULT 'regex',
            valid_from TEXT,
            valid_until TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wthread_subject ON wthread_triples(subject)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wthread_relation ON wthread_triples(relation)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wthread_object ON wthread_triples(object)")
    # 新增 source 列（兼容旧表）
    try:
        conn.execute("ALTER TABLE wthread_triples ADD COLUMN source TEXT DEFAULT 'regex'")
    except Exception:
        pass  # 列已存在
    # 时序事实列（Task 5，兼容旧表）
    try:
        conn.execute("ALTER TABLE wthread_triples ADD COLUMN valid_from TEXT")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE wthread_triples ADD COLUMN valid_until TEXT")
    except Exception:
        pass
    conn.commit()
    conn.close()



def wthread_extract_with_source(text: str, line_num: int = 0) -> list:
    """
    带来源标注的抽取。返回 [(subject, relation, object, source), ...]
    source = 'regex' 或 'llm'
    """
    regex_triples = []
    for pattern, relation in _EXTRACT_PATTERNS:
        for match in re.finditer(pattern, text):
            groups = match.groups()
            if len(groups) == 1:
                obj = _clean_entity(groups[0])
                if obj:
                    regex_triples.append(("subject", relation, obj))
            elif len(groups) == 2:
                subj = _clean_entity(groups[0])
                obj = _clean_entity(groups[1])
                if subj and obj:
                    regex_triples.append((subj, relation, obj))
    result = [(s, r, o, "regex") for s, r, o in regex_triples]
    # LLM 补充
    llm_triples = wthread_extract_llm(text, existing_regex=regex_triples)
    for subj, rel, obj, conf in llm_triples:
        if not any(s == subj and r == rel and o == obj for s, r, o in regex_triples):
            result.append((subj, rel, obj, "llm"))
    return result

def wthread_extract(text: str, line_num: int = 0) -> list:
    """从文本提取三元组。返回 [(subject, relation, object), ...]"""
    triples = []
    for pattern, relation in _EXTRACT_PATTERNS:
        for match in re.finditer(pattern, text):
            groups = match.groups()
            if len(groups) == 1:
                obj = _clean_entity(groups[0])
                if obj:
                    triples.append(("subject", relation, obj))
            elif len(groups) == 2:
                subj = _clean_entity(groups[0])
                obj = _clean_entity(groups[1])
                if subj and obj:
                    triples.append((subj, relation, obj))
    return triples


def wthread_extract_llm(text: str, existing_regex: list = None) -> list:
    """
    可选 LLM 抽取层：抓正则漏掉的三元组，做实体归一化。
    
    返回 [(subject, relation, object, confidence), ...]
    source 标记为 'llm'。
    
    需要配置 WTHREAD_LLM_EXTRACTION=1 且可用的 LLM 后端。
    失败时返回空列表（fail-safe，退回纯正则）。
    """
    if not os.environ.get("WTHREAD_LLM_EXTRACTION"):
        return []
    try:
        # 延迟导入，避免强依赖
        from nexsandglass.core.llm_extract import llm_extract_triples
        result = llm_extract_triples(text, existing_regex=existing_regex)
        if result:
            logger.info("[wthread_extract_llm] LLM 补充抽取 %d 条三元组", len(result))
        return result
    except Exception as e:
        logger.debug("[wthread_extract_llm] LLM 抽取失败（退回正则）: %s", e)
        return []



def _clean_entity(text: str) -> str:
    """清洗提取的实体——去掉语气词/连词前缀"""
    text = text.strip()
    # 去掉句首的 了/着/过/的/是/在/和/与/或
    text = re.sub(r'^[了着过的和在或与已已]', '', text)
    # 至少2字或英文2字符
    if len(text) >= 2:
        return text
    return ""


def wthread_store(text: str, line_num: int = 0, subject: str = "user") -> int:
    """提取并存储三元组。返回存储数量"""
    _ensure_table()
    if os.environ.get("WTHREAD_LLM_EXTRACTION"):
        triples_with_source = wthread_extract_with_source(text, line_num)
    else:
        triples_with_source = [(s, r, o, "regex") for s, r, o in wthread_extract(text, line_num)]
    if not triples_with_source:
        return 0
    
    conn = sqlite3.connect(_DB, timeout=10)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    count = 0
    for subj, rel, obj, src_tag in triples_with_source:
        if subj == "subject":
            subj = subject
        # 去重检查
        exists = conn.execute(
            "SELECT id FROM wthread_triples WHERE subject=? AND relation=? AND object=?",
            (subj, rel, obj)
        ).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO wthread_triples (subject, relation, object, source_line, source, created_at) VALUES (?,?,?,?,?,?)",
            (subj, rel, obj, line_num, src_tag, now)
        )
        count += 1
    conn.commit()
    conn.close()
    return count


def wthread_query(entity: str = None, relation: str = None, limit: int = 20) -> list:
    """查询织线——织布机的线材。可按实体或关系过滤。
    返回 [{subject, relation, object, source_line, confidence}, ...]
    """
    _ensure_table()
    conn = sqlite3.connect(_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    
    if entity and relation:
        rows = conn.execute(
            "SELECT * FROM wthread_triples WHERE (subject=? OR object=?) AND relation=? ORDER BY id DESC LIMIT ?",
            (entity, entity, relation, limit)
        ).fetchall()
    elif entity:
        rows = conn.execute(
            "SELECT * FROM wthread_triples WHERE subject=? OR object=? ORDER BY id DESC LIMIT ?",
            (entity, entity, limit)
        ).fetchall()
    elif relation:
        rows = conn.execute(
            "SELECT * FROM wthread_triples WHERE relation=? ORDER BY id DESC LIMIT ?",
            (relation, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM wthread_triples ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    
    conn.close()
    return [dict(r) for r in rows]


def wthread_graph(entity: str, depth: int = 1) -> dict:
    """以实体为中心展开子图。depth=1 返回直接关系，depth=2 返回二跳。
    返回 {entity, relations: [{relation, target}], subgraph: {...}}
    """
    direct = wthread_query(entity=entity)
    
    result = {
        "entity": entity,
        "relations": [],
        "subgraph": {}
    }
    
    seen = set()
    for r in direct:
        target = r["object"] if r["subject"] == entity else r["subject"]
        result["relations"].append({
            "relation": r["relation"],
            "target": target,
            "direction": "→" if r["subject"] == entity else "←"
        })
    
    if depth >= 2:
        for rel in result["relations"]:
            target = rel["target"]
            if target not in seen:
                seen.add(target)
                sub = wthread_query(entity=target)
                result["subgraph"][target] = [
                    {"relation": s["relation"], "target": s["object"] if s["subject"] == target else s["subject"]}
                    for s in sub[:5]
                ]
    
    return result


def wthread_stats() -> dict:
    """图谱统计"""
    _ensure_table()
    conn = sqlite3.connect(_DB, timeout=10)
    total = conn.execute("SELECT COUNT(*) FROM wthread_triples").fetchone()[0]
    entities = conn.execute("SELECT COUNT(DISTINCT subject) + COUNT(DISTINCT object) FROM wthread_triples").fetchone()
    relations = conn.execute("SELECT relation, COUNT(*) as c FROM wthread_triples GROUP BY relation ORDER BY c DESC").fetchall()
    conn.close()
    return {
        "total_triples": total,
        "relations": [(r, c) for r, c in relations]
    }

def wthread_to_weave(entity: str = "user") -> list:
    """织线→织布机桥接：将结构化三元组转为因果链线索。
    织布机可用此替代原始沙子扫描，获得更精准的因果关系。
    
    返回 [{from, relation, to, direction}, ...]
    """
    triples = wthread_query(entity=entity)
    chains = []
    for t in triples:
        chains.append({
            "from": t["subject"],
            "relation": t["relation"],
            "to": t["object"],
            "direction": "→" if t["subject"] == entity else "←",
            "source_line": t["source_line"]
        })
    
    # 按关系类型分组
    grouped = {}
    for c in chains:
        rel = c["relation"]
        if rel not in grouped:
            grouped[rel] = []
        grouped[rel].append(c["to"])
    
    return {
        "chains": chains,
        "grouped": grouped,
        "summary": " | ".join(f'{rel}: {", ".join(targets[:3])}' for rel, targets in grouped.items())
    }


def wthread_weave(limit: int = 3) -> str:
    """快捷桥接：返回织布机可注入的因果摘要。
    用于 session_context 或 system_prompt_block 注入。
    """
    result = wthread_to_weave("user")
    lines = ["织线因果:"]
    for rel, targets in result["grouped"].items():
        lines.append(f"  {rel}: " + ", ".join(targets[:limit]))
    return "\n".join(lines)
def wthread_add(subject: str, relation: str, object: str, source_line: int = 0, source: str = "regex") -> bool:
    """LLM 手动补漏——Agent 发现正则漏抓的关系时，通过 MCP 工具补入。
    返回 True 表示写入成功或已存在。
    """
    _ensure_table()
    conn = sqlite3.connect(_DB, timeout=10)
    exists = conn.execute(
        "SELECT id FROM wthread_triples WHERE subject=? AND relation=? AND object=?",
        (subject, relation, object)
    ).fetchone()
    if exists:
        conn.close()
        return True
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO wthread_triples (subject, relation, object, source_line, source, created_at) VALUES (?,?,?,?,?,?)",
        (subject, relation, object, source_line, source or 'regex', now)
    )
    conn.commit()
    conn.close()
    return True
