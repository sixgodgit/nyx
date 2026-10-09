"""tests/test_structural_guards.py — 让同一类缺陷在代码层面无法再进来（v7.13.3）

2026-10-09 诊断报告：语义层与旧 vector_store 用了同一个文件名、同一个表名、不同的列。
CREATE TABLE IF NOT EXISTS 静默跳过，此后每次写入都失败，失败又被吞成 warning —— 一个月一条向量都没写进去。
同期查出来的同类问题：8 处固定名字的临时文件（并发写互相截断 / 被对方挪走）。

这些是**结构**问题，靠代码评审记不住，所以写成扫源码的测试：
  1. 同一个表名在整个包里只能有一种列定义
  2. 一个 .db 文件名被多个模块引用，必须在下面的白名单里写明理由
  3. 不允许 `path + ".tmp"` 这种固定名字的临时文件（用 core/fsutil.atomic_write）
  4. 开库 + 设 WAL + 建表只能走 core/sqlite_open.connect（v7.13.4：v7.13.3 发布流水线上
     4 个线程同时第一次开库，一个在 PRAGMA journal_mode=WAL 上撞出 database is locked）
"""
import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1] / "nexsandglass"

# 有意共享的数据库文件 —— 新增共享必须在这里写清楚为什么不会撞表
SHARED_DB_FILES = {
    "sandglass.db": "sandglass_paths 只定义路径常量；表只有 sandglass_sqlite 建",
    "shadow_sand.db": "sandglass_paths 定义路径；shadow_sand 建 trust/entities/fact_tags，"
                      "weavethread 在同一个库里建 wthread_triples（表名不重）",
    "mist.db": "dejavu.core 建库；interfaces.nyx 只是把路径传给 dejavu",
    "vectors.db": "只有 semantic 在迁移时**只读**打开 v7.13 的旧文件；不在里面建表",
}


def _code_strings(tree):
    """代码里真正使用的字符串常量（不含模块 / 类 / 函数的 docstring）。"""
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docs.add(id(first.value))
    return [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


def _sources():
    for p in sorted(ROOT.rglob("*.py")):
        yield p, p.read_text(encoding="utf-8")


def _join_literals(src: str) -> str:
    """把相邻的字符串字面量拼起来（"CREATE TABLE x " "(a, b)" 这种跨行写法）。"""
    return re.sub(r"""(["'])\s*\n?\s*\1(?=\S)""", "", re.sub(r"""(["'])\s*\n\s*(["'])""", "", src))


def _tables():
    out = {}
    for p, src in _sources():
        s = _join_literals(src)
        for m in re.finditer(r"CREATE\s+(?:VIRTUAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
                             r"([A-Za-z_]\w*)\s*(?:USING\s+\w+\s*)?\(", s, re.I):
            i, depth = m.end(), 1
            j = i
            while j < len(s) and depth:
                depth += {"(": 1, ")": -1}.get(s[j], 0)
                j += 1
            cols = []
            for part in re.split(r",(?![^()]*\))", s[i:j - 1]):
                w = re.match(r"\s*[\"']?([A-Za-z_]\w*)", part)
                if w and w.group(1).upper() not in ("PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "CONSTRAINT"):
                    cols.append(w.group(1).lower())
            out.setdefault(m.group(1).lower(), []).append((p.relative_to(ROOT).as_posix(), tuple(cols)))
    return out


def test_scanner_sees_the_tables_it_must_see():
    """扫描器自己也要验收：漏扫等于没有这道防线。"""
    t = _tables()
    for name in ("memories", "tombstones", "provenance", "quarantine", "embeddings", "semantic_meta",
                 "sandglass", "sandglass_fts", "trust", "entities", "wthread_triples", "legacy_vectors"):
        assert name in t, f"扫描器没看到表 {name}"
    assert t["embeddings"][0][1] == ("mem_id", "line_start", "model", "dim", "vec", "indexed_at")


def test_each_table_name_has_exactly_one_schema():
    """同名表可以在多处出现（建表 + 迁移兜底），但列必须完全一样；一旦漂移这里就红。"""
    bad = {}
    for name, defs in _tables().items():
        if len({cols for _, cols in defs}) > 1:
            bad[name] = defs
    assert not bad, ("同一个表名出现了多种定义 —— 落到同一个库文件里时，CREATE TABLE IF NOT EXISTS 会静默跳过，"
                     f"后写的那一方每次写入都失败：{bad}")


def test_shared_db_filenames_are_reviewed():
    users = {}
    for p, src in _sources():
        for c in _code_strings(ast.parse(src)):
            for m in re.finditer(r"(?:^|[/\\])([\w.-]+\.(?:db|sqlite3?))$", c.value):
                users.setdefault(m.group(1), set()).add(p.relative_to(ROOT).as_posix())
    shared = {f: sorted(m) for f, m in users.items() if len(m) > 1 and f not in SHARED_DB_FILES}
    assert not shared, f"这些数据库文件名被多个模块引用，却不在 SHARED_DB_FILES 白名单里：{shared}"
    # 旧 vector_store 的代码里不许再出现 vectors.db / ~/.hermes 默认路径（它就是撞车的那一方）
    vs = [c.value for c in _code_strings(ast.parse((ROOT / "core" / "vector_store.py").read_text(encoding="utf-8")))]
    assert not [v for v in vs if "vectors.db" in v or ".hermes" in v], vs


def test_no_fixed_name_temp_files():
    hits = []
    for p, src in _sources():
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)
                    and isinstance(node.right, ast.Constant) and isinstance(node.right.value, str)
                    and node.right.value.endswith(".tmp")):
                hits.append(f"{p.relative_to(ROOT)}:{node.lineno}: ... + {node.right.value!r}")
    assert not hits, "固定名字的临时文件（并发写会互相截断）—— 改用 core.fsutil.atomic_write：\n" + "\n".join(hits)


# 直接 sqlite3.connect 后自己建表的函数 —— 只允许下面这些，并写明理由
DIRECT_DDL_ALLOWED = {
    "core/vector_store.py:_init_db": "已废弃的 sqlite-vec 后端（get_vector_store 不再返回它），不在任何默认路径上",
}


def _ddl_functions():
    out = []
    for p, src in _sources():
        for fn in ast.walk(ast.parse(src)):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            nodes = list(ast.walk(fn))
            if not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "connect"
                       and isinstance(n.func.value, ast.Name) and n.func.value.id == "sqlite3" for n in nodes):
                continue
            if any((isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and re.search(r"\b(?:CREATE\s+(?:VIRTUAL\s+)?(?:TABLE|INDEX)|ALTER\s+TABLE)\b", n.value, re.I))
                   or (isinstance(n, ast.Attribute) and n.attr == "executescript") for n in nodes):
                out.append(f"{p.relative_to(ROOT).as_posix()}:{fn.name}")
    return out


def test_only_sqlite_open_switches_journal_mode():
    hits = []
    for p, src in _sources():
        if p == ROOT / "core" / "sqlite_open.py":
            continue
        for c in _code_strings(ast.parse(src)):
            if re.search(r"journal_mode", c.value, re.I):
                hits.append(f"{p.relative_to(ROOT)}:{c.lineno}: {c.value.strip()[:60]!r}")
    assert not hits, ("自己设 journal_mode 的开库方式在并发首次开库时会撞出 database is locked —— "
                      "改用 core.sqlite_open.connect(path, setup=建表函数)：\n" + "\n".join(hits))


def test_no_direct_connect_then_ddl():
    bad = [f for f in _ddl_functions() if f not in DIRECT_DDL_ALLOWED]
    assert not bad, ("这些函数 sqlite3.connect 之后自己建表 / 改表：并发首次开库时会撞锁，而且没有重试 —— "
                     "把建表挪进 core.sqlite_open.connect(path, setup=...)：\n" + "\n".join(bad))
    assert set(DIRECT_DDL_ALLOWED) <= set(_ddl_functions()), "白名单里有已经不存在的条目，删掉它"
