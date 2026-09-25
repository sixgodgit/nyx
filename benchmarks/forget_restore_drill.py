#!/usr/bin/env python3
"""benchmarks/forget_restore_drill.py — 沙盒演练：把 v7.4 那次"一次 forget 误抹 345 行真实对话"整个重放一遍。

不是单测 —— 单测证明的是每条性质成立；这里证明的是**事故场景**下能全量救回。
流程：
  1. 造一个 400 条（含多行消息）的真实形状日志，建齐索引
  2. 快照日志字节 + 体检
  3. 按"伪行号"式的错误批量选择，一次 forget 掉 150 条（事故）
  4. 验：被删的内容在所有检索路径上都摸不到，行号没位移，体检仍绿
  5. 走 CLI 看隔离区、还原 2 条；再用 API 还原其余 148 条
  6. 验：日志**逐字节**回到快照，FTS/倒排/中枢全部复原，体检绿，隔离区空
  7. 再真删 1 条 + purge → 彻底不可恢复（restore 拿不回来，fully_purged=True）
  8. 顺手量一下隔离区带来的开销
"""
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time

# 仓库根目录由本文件位置推出 —— 不硬编码任何绝对路径
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

HOME = tempfile.mkdtemp(prefix="nyx_drill_")
os.environ["NEXSANDBASE_HOME"] = HOME
os.environ.pop("NYX_FORGET_RETENTION_DAYS", None)

from nexsandglass.core import memid, quarantine, erasure, sandglass_log, sandglass_sqlite
from nexsandglass.features import sandglass_vault, shadow_sand

SG = os.path.join(HOME, "sandglass.txt")
memid._SANDGLASS = SG
memid._LOCK = SG + ".lock"
sandglass_log._SANDGLASS = SG
sandglass_vault._SANDGLASS = SG
sandglass_sqlite._DB = os.path.join(HOME, "sandglass.db")
erasure._NB = HOME
sandglass_vault.set_idx_path(os.path.join(HOME, "sandglass.idx"))
memid.set_db_path(os.path.join(HOME, "nyx.db"))
shadow_sand.set_shadow_path(os.path.join(HOME, "shadow_sand.db"))

ok = True


def check(label, cond, extra=""):
    global ok
    print(("  ✅ " if cond else "  ❌ ") + label + (("  " + str(extra)) if extra else ""))
    if not cond:
        ok = False


def reindex():
    sandglass_sqlite._last_sync_mtime = 0
    sandglass_sqlite.sync_all()
    sandglass_vault._idx_cache = None
    sandglass_vault._idx_mtime = 0
    sandglass_vault.rebuild_index()
    shadow_sand.rebuild_entity_index()


# ── 1. 造日志 ────────────────────────────────────────────
print("\n[1] 造 400 条真实形状的日志（含多行消息）")
random.seed(20260925)
TOPICS = ["荷兰 BV 公司注册", "海牙的房租", "特斯拉换吉利星越", "西安小吃店的评价",
          "服务器 CPU 飙高", "孩子的入学材料", "年报截止日", "川菜馆"]
t0 = time.time()
ids = []
for i in range(1, 401):
    topic = random.choice(TOPICS)
    if i % 7 == 0:
        text = f"第{i}条 关于{topic}的详细记录\n补充说明：{topic} 的第二行\n第三行 detail-{i}"
    else:
        text = f"第{i}条 关于{topic}的记录 detail-{i}"
    ids.append(sandglass_log.log_message(text, sender="user", return_id=True))
write_ms = (time.time() - t0) * 1000 / 400
reindex()
print(f"    写入 {write_ms:.2f} ms/条，FTS {sandglass_sqlite.count()} 条，"
      f"物理行 {len(open(SG, encoding='utf-8').read().splitlines())}")

snapshot = open(SG, "rb").read()
h0 = memid.health(SG)
check("事故前体检全绿", h0["ok"], [n for n, c in h0["checks"].items() if not c["ok"]])
lines_before = len(open(SG, encoding="utf-8").read().splitlines())
fts_before = sandglass_sqlite.count()

# ── 2. 事故：一次批量 forget 误抹 150 条 ──────────────────
print("\n[2] 事故重放：一次 forget 误抹 150 条（v7.4 当时是 345 行）")
victims = sorted(random.sample(range(1, 401), 150))
victim_ids = [memid.resolve(s) for s in victims]
sample = [memid.get(m)["text"].splitlines()[0][:30] for m in victim_ids[:5]]
t0 = time.time()
rep = erasure.forget({"seqs": victims}, reason="wrong_bulk_selection", apply=True,
                     journal_path=SG)
forget_ms = (time.time() - t0) * 1000
print(f"    forget: found={rep['found']} 隔离={rep['quarantined']} "
      f"到期={rep['purge_after']} 耗时 {forget_ms:.0f} ms（{forget_ms/150:.2f} ms/条）")
check("150 条全部进了隔离区", rep["quarantined"] == 150, rep["quarantined"])
check("报告明确说可反悔", rep["recoverable"] is True)

# ── 3. 事故后：用户读不到，但别人没被弄坏 ─────────────────
print("\n[3] 事故后状态（该没的没了，不该动的没动）")
v = erasure.verify_erasure(sample, SG)
check("被删内容在所有检索路径上摸不到", v["clean"] is True, list(v["found_in"]))
check("隔离区如实报告内容还在磁盘上", bool(v["in_quarantine"]))
check("不敢声称已彻底擦除", v["fully_purged"] is False)
check("物理行数不变（别人的行号没位移）",
      len(open(SG, encoding="utf-8").read().splitlines()) == lines_before)
check("FTS 只少了被删的那些", sandglass_sqlite.count() == fts_before - 150,
      sandglass_sqlite.count())
alive_seq = next(s for s in range(400, 0, -1) if s not in victims)
survivor = memid.get(memid.resolve(alive_seq))
check("幸存记忆完好（seq=%d）" % alive_seq,
      survivor is not None and f"第{alive_seq}条" in survivor["text"])
h1 = memid.health(SG)
check("事故后体检仍绿（脱敏是正常状态，不是劣化）", h1["ok"],
      [n for n, c in h1["checks"].items() if not c["ok"]])
print(f"    pending_purge: {h1['checks']['pending_purge']}")

# ── 4. CLI ───────────────────────────────────────────────
print("\n[4] 走 CLI（人在事故现场真正会敲的东西）")
env = dict(os.environ, PYTHONPATH=REPO, NEXSANDBASE_HOME=HOME)
r = subprocess.run([sys.executable, os.path.join(REPO, "scripts/nyx_quarantine.py"),
                    "list", "--limit", "3"], capture_output=True, text=True, env=env)
print("    " + "\n    ".join(r.stdout.strip().splitlines()[:5]))
check("list 退出码 0（没有过期未清）", r.returncode == 0, r.stderr.strip()[:120])

r = subprocess.run([sys.executable, os.path.join(REPO, "scripts/nyx_quarantine.py"),
                    "restore", victim_ids[0]], capture_output=True, text=True, env=env)
print("    " + "\n    ".join(r.stdout.strip().splitlines()))
check("CLI 还原退出码 0", r.returncode == 0, r.stderr.strip()[:200])

r = subprocess.run([sys.executable, os.path.join(REPO, "scripts/nyx_quarantine.py"),
                    "purge"], capture_output=True, text=True, env=env)
check("purge 预演不动任何东西（没到期）", "命中 0 条" in r.stdout, r.stdout.strip()[:120])

# ── 5. 全量还原 ──────────────────────────────────────────
print("\n[5] 还原其余 149 条")
t0 = time.time()
failed = []
for m in victim_ids[1:]:
    rr = erasure.restore(m)
    if not rr["ok"]:
        failed.append((m, rr["problems"]))
restore_ms = (time.time() - t0) * 1000
print(f"    耗时 {restore_ms:.0f} ms（{restore_ms/149:.2f} ms/条）")
check("149 条全部还原成功", not failed, failed[:3])

# ── 6. 验收：逐字节回到事故前 ────────────────────────────
print("\n[6] 验收：是否真的回到事故前")
check("日志文件与事故前**逐字节**一致", open(SG, "rb").read() == snapshot)
check("中枢里没有残留墓碑", len(memid.tombstoned_ids()) == 0, len(memid.tombstoned_ids()))
check("中枢/日志一致性 ok", memid.verify(SG)["ok"] is True)
back = erasure.verify_erasure(sample, SG)
check("被误删的内容重新可检索", back["clean"] is False, list(back["found_in"]))
check("检索路径（search）真的回来了", "search" in back["found_in"], list(back["found_in"]))
check("FTS 条数回到事故前", sandglass_sqlite.count() == fts_before,
      f"{sandglass_sqlite.count()} vs {fts_before}")
check("隔离区已清空（不留第二个真相来源）", quarantine.stats()["pending"] == 0)
h2 = memid.health(SG)
check("还原后体检全绿", h2["ok"], [n for n, c in h2["checks"].items() if not c["ok"]])

# 倒排索引：抽查被还原的记忆能被词命中
v0 = memid.get(victim_ids[0])
idx = sandglass_vault._sync_index()
toks = [t for t, lines in idx.items() if v0["line_start"] in lines]
check("倒排索引里也回来了（该行重新挂到 %d 个词条上）" % len(toks), len(toks) > 0)

# ── 7. 真删 + purge：不可逆必须仍然可达 ──────────────────
print("\n[7] 真删一条并到期擦除（不可逆路径仍然可达）")
sandglass_log.log_message("误粘贴的凭据 sk-LIVE-DEADBEEF-0001", sender="user")
reindex()
gone = memid.resolve(401)
rep2 = erasure.forget({"contains": "sk-LIVE-DEADBEEF"}, apply=True, journal_path=SG,
                      retention_days=1)
check("先进隔离区", rep2["quarantined"] == 1)
pr = quarantine.purge(apply=True, now=__import__("datetime").datetime.now()
                      + __import__("datetime").timedelta(days=2))
check("到期擦除成功", pr["purged"] == 1 and pr["ok"])
check("VACUUM 执行了（否则不能说磁盘上没了）", pr["vacuumed"] is True)
check("purge 之后还原拿不回来", erasure.restore(gone)["found"] is False)
v3 = erasure.verify_erasure(["sk-LIVE-DEADBEEF"], SG)
check("fully_purged 为真", v3["fully_purged"] is True, v3["found_in"])

rep3 = erasure.forget({"contains": f"第{alive_seq}条"}, apply=True, journal_path=SG, mode="purge")
check("mode=purge 当场不可逆（老行为仍可达）",
      rep3["quarantined"] == 0 and rep3["recoverable"] is False)
check("purge 模式下也删干净了",
      erasure.verify_erasure([f"第{alive_seq}条 关于"], SG)["fully_purged"] is True)

# ── 8. 开销 ──────────────────────────────────────────────
print("\n[8] 隔离区的开销")
db = os.path.getsize(os.path.join(HOME, "nyx.db"))
print(f"    forget {forget_ms/150:.2f} ms/条（含抓取现场）| restore {restore_ms/149:.2f} ms/条")
print(f"    nyx.db 现为 {db/1024:.0f} KiB（隔离区已清空）")

print("\n" + ("=" * 60))
print("演练结论：" + ("全部通过 —— 150 条误删可逐字节救回，真删仍然不可逆" if ok else "有项目未通过"))
print("=" * 60)
shutil.rmtree(HOME, ignore_errors=True)
sys.exit(0 if ok else 1)
