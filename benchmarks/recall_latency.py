#!/usr/bin/env python3
"""召回延迟基准（v7.13.2）：按真实写入路径造 N 轮对话，测 Hermes 插件的启动、首轮与每轮耗时。

    python3 benchmarks/recall_latency.py --turns 20000            # 约 3 万条记忆，~40 秒造数据
    python3 benchmarks/recall_latency.py --turns 100000           # 约 15 万条记忆，~3 分钟
    python3 benchmarks/recall_latency.py --home 已有数据目录        # 测已有的记忆库（只读查询）

两种首轮：wait = 预热完成后才说第一句（通常情况）；immediate = 启动后立刻说第一句。
conversation = 真实对话节奏：每轮先 prefetch 再 sync_turn（写入），测的是「上一轮刚写过之后」的查询。
（v7.13.2 的基准只连续查询、中间不写入，漏掉了「每次有新行就整份重读索引」—— 15 万条时每轮 1.3–2.0 秒。）
--home 指向已有数据目录时，conversation 模式会写入：脚本先把目录复制一份，在副本上跑。
每种在独立子进程里测，互不共享内存缓存。语义检索默认关（NYX_EMBED=off），测的是词法 + 图 + 时间三路。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUERIES = ["我的护照放哪了", "老周上次说的预算表", "房租交了吗", "李医生的电话是多少", "我现在住哪",
           "上个月去成都的事", "Kevin 发来的合同", "体检报告结果", "机器人项目进展", "咖啡馆见了谁"]


def generate(turns: int) -> None:
    from datetime import datetime, timedelta
    from nexsandglass.core import clock, sandglass_log
    random.seed(7)
    people = ["小满", "老周", "王总", "李医生", "阿文", "Emma", "张老师", "妈妈", "房东", "Kevin"]
    places = ["杭州", "上海", "海牙", "鹿特丹", "成都", "西安", "公司", "健身房", "医院", "咖啡馆"]
    things = ["预算表", "季度报告", "护照", "车险", "房租", "体检报告", "机器人项目", "川菜馆", "签证材料", "合同"]
    verbs = ["讨论了", "提醒我", "确认了", "搞定了", "推迟了", "发来了", "约了", "修改了", "担心", "提到"]
    tails = ["下周三之前要处理", "金额大概 3200 欧", "感觉压力很大", "这次比上次顺利", "记得带上原件",
             "电话是 0612345678", "还要再确认一次", "已经付款了", "等对方回复", "顺便买了咖啡"]
    start = datetime(2025, 10, 1, 9, 0)
    t0 = time.time()
    for i in range(turns):
        clock.set_global(start + timedelta(minutes=37 * i))
        sandglass_log.log_message(f"{random.choice(people)}{random.choice(verbs)}{random.choice(things)}，"
                                  f"在{random.choice(places)}，{random.choice(tails)}", "user")
        if i % 2 == 0:
            sandglass_log.log_message(f"好的，我记下了：{random.choice(things)}的事{random.choice(tails)}", "agent")
    clock.set_global(None)
    from nexsandglass.core import sandglass_sqlite
    from nexsandglass.features import sandglass_vault
    sandglass_sqlite.sync_all()
    sandglass_vault.rebuild_index()
    print(json.dumps({"generated_turns": turns, "write_ms_per_turn": round((time.time() - t0) / turns * 1000, 2)}))


def measure(mode: str) -> None:
    t = time.perf_counter()
    from nexsandglass.core.memory_provider import NexSandglassProvider
    p = NexSandglassProvider()
    p.initialize("bench", agent_context="primary", platform="bench")
    out = {"mode": mode, "initialize_ms": round((time.perf_counter() - t) * 1000)}
    if mode == "conversation":
        p._warm_thread.join()
        ts, ws = [], []
        for i, q in enumerate(QUERIES * 3):
            a = time.perf_counter()
            p.prefetch(q, session_id="bench")
            ts.append((time.perf_counter() - a) * 1000)
            a = time.perf_counter()
            p.sync_turn(f"{q}——第{i}轮我说的话，老周提醒我体检报告", "好的，我记下了体检报告的事", session_id="bench")
            ws.append((time.perf_counter() - a) * 1000)
        ts.sort()
        ws.sort()
        out.update(turn_p50_ms=round(statistics.median(ts[1:])), turn_p95_ms=round(ts[int(len(ts) * 0.95) - 1]),
                   turn_max_ms=round(ts[-1]), sync_turn_p50_ms=round(statistics.median(ws)))
        from nexsandglass.core import memid
        out["memories"] = memid.count()
        print(json.dumps(out, ensure_ascii=False))
        return
    if mode == "wait":
        a = time.perf_counter()
        p._warm_thread.join()
        out["background_warm_ms"] = round((time.perf_counter() - a) * 1000)
    a = time.perf_counter()
    p.prefetch(QUERIES[0], session_id="bench")
    out["first_turn_ms"] = round((time.perf_counter() - a) * 1000)
    ts = []
    for _ in range(3):
        for q in QUERIES:
            a = time.perf_counter()
            p.prefetch(q, session_id="bench")
            ts.append((time.perf_counter() - a) * 1000)
    ts.sort()
    out.update(turn_p50_ms=round(statistics.median(ts)), turn_p95_ms=round(ts[int(len(ts) * 0.95) - 1]),
               turn_max_ms=round(ts[-1]))
    from nexsandglass.core import memid
    out["memories"] = memid.count()
    print(json.dumps(out, ensure_ascii=False))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--turns", type=int, default=20000)
    ap.add_argument("--home", help="已有数据目录（不造数据）")
    ap.add_argument("--_child", choices=["gen", "wait", "immediate", "conversation"], help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args._child == "gen":
        generate(args.turns)
        return 0
    if args._child:
        measure(args._child)
        return 0

    home = args.home or tempfile.mkdtemp(prefix="nyx-latency-")
    conv_home = home
    if args.home:                      # conversation 会写入：在副本上跑，不动原目录
        import shutil
        conv_home = tempfile.mkdtemp(prefix="nyx-latency-conv-")
        shutil.rmtree(conv_home)
        shutil.copytree(args.home, conv_home)
    env = dict(os.environ, NEXSANDBASE_HOME=home, NYX_EMBED=os.environ.get("NYX_EMBED", "off"),
               PYTHONPATH=REPO + os.pathsep + os.environ.get("PYTHONPATH", ""))
    run = lambda mode: subprocess.run([sys.executable, __file__, "--_child", mode, "--turns", str(args.turns)],
                                      env=env, capture_output=True, text=True, check=True).stdout.strip().splitlines()[-1]
    if not args.home:
        print(run("gen"))
    for mode in ("wait", "immediate"):
        print(run(mode))
    env["NEXSANDBASE_HOME"] = conv_home
    print(run("conversation"))
    if conv_home != home:
        import shutil
        shutil.rmtree(conv_home, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
