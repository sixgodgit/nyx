#!/usr/bin/env python3
"""个人场景评测（v7.19.0）—— agent 在新会话里能不能看到「关于你」该看到的，又塞进了多少不相关的

故事在 personal_cases.py：一个人和自己的 agent 相处 40 天，300 轮工作和闲聊里零散说过饮食禁忌、过敏、
对 agent 的长期要求、家人、住处和搬家。每个故事在独立的 nyx 目录、独立的子进程里：
按时间冻结时钟，一问一答调用一次 NexSandglassProvider.sync_turn —— 和 Hermes 写入的是同一条路径。
写完后开一个新会话（第 41 天），取 system_prompt_block（每个会话常驻）和每个问题的 prefetch（这一轮注入），
也就是 agent 实际看到的全部记忆。

指标
  fact_visible     该看到的事实里，看到了多少（常驻块或这一轮注入里出现了用户原话 / 由它得出的信念）。
                   助手的复述不算：用户说「我对花生过敏」，助手回「会避开那类坚果」，后者不算看到了过敏
  probe_complete   个人问题里，该看到的全看到了的比例
  junk_per_probe   每个问题注入了几条不相关的（工作闲聊、别的个人事实、助手的客套话以外的）
  echo_per_probe   每个问题注入了几条助手对个人信息的回应（「好的，记住了」）
  unrelated_clean  和记忆无关的问题（光速多少）里，一条都没注入的比例
  inject_chars     每个问题注入的字数（Hermes 会把注入留在那条消息里、之后每轮重发）
  block_*          常驻块：字数、助手的话有几行、写死在代码里的内容（不是来自这个用户的）有几处
  wrong_beliefs    记错到用户头上的信念（女儿喜欢恐龙 → 用户喜欢恐龙）
  stale            过时的信念被当成现在的（搬家后仍是 lives in 杭州）

用法
  python3 benchmarks/personal_eval.py [--split dev|test|all] [--lang zh|en|all] [--semantic] \\
      [--json out.json] [--show]
  默认不开语义检索（与不装 [vector] 的默认安装一致）；--show 打印每个问题看到的内容。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)

from personal_cases import DAYS, FILLER, FILLER_TURNS, STORIES  # noqa: E402

START = datetime(2026, 9, 1)
# 写死在代码里、不可能来自故事的内容（故事发生在 2026 年 9–10 月）
FOREIGN = (r"NeuroBase", r"2026-06")
_AGENT = "source: agent"
CHANGED_HEADER = "Changed since"     # 常驻块里「已经改了」那一段的标题开头（v7.22.0）
FOLLOWUP_HEADER = "[Follow-ups"      # 常驻块里「待跟进」那一段的标题开头（v7.23.0）


def followup_section(block: str) -> list:
    """常驻块里待跟进那一段的条目行（"- …"）。"""
    i = block.find(FOLLOWUP_HEADER)
    if i < 0:
        return []
    sec = block[i:].split("\n\n", 1)[0]
    return [ln for ln in sec.splitlines()[1:] if ln.startswith("- ")]


def score_followups(lines: list, spec: dict) -> dict:
    """show 要在、hide 不能在；三类都不沾的条目算多出来的（比如从工作闲聊里抽出来的）。"""
    text = "\n".join(lines)
    pats = list(spec.get("show", [])) + list(spec.get("hide", [])) + list(spec.get("allow", []))
    return {"show": [bool(re.search(p, text)) for p in spec.get("show", [])],
            "wrong": [p for p in spec.get("hide", []) if re.search(p, text)],
            "extra": [ln for ln in lines if not any(re.search(p, ln) for p in pats)]}


# ══════════════════════════════════════════════════════════
# 故事 → 时间线
# ══════════════════════════════════════════════════════════

def timeline(story: dict) -> list:
    """[(时间, 用户, 助手, 是否个人信息)]，按时间排好。填充对话均匀撒在 DAYS 天里，个人信息插在它们中间。"""
    rnd = random.Random(story["seed"])
    f = FILLER[story["lang"]]
    turns = []
    for i in range(FILLER_TURNS):
        day = i * DAYS // FILLER_TURNS
        when = START + timedelta(days=day, hours=9, minutes=(i * 7) % 600)
        t, n = rnd.choice(f["topics"]), rnd.randint(2, 400)
        turns.append((when, rnd.choice(f["user"]).format(t=t, n=n),
                      rnd.choice(f["assistant"]).format(t=t, n=rnd.randint(2, 400)), False))
    for day, user, assistant in story["personal"]:
        when = START + timedelta(days=day, hours=20, minutes=rnd.randint(0, 50))
        turns.append((when, user, assistant, True))
    turns.sort(key=lambda x: x[0])
    return turns


# ══════════════════════════════════════════════════════════
# 子进程：一个故事
# ══════════════════════════════════════════════════════════

def _memory_lines(text: str) -> list:
    """prefetch 里的记忆行（"- [日期] 正文"），去掉日期前缀。"""
    out = []
    for ln in (text or "").splitlines():
        if ln.startswith("- "):
            out.append(re.sub(r"^- (\[[^\]]*\] )?", "", ln))
    return out


def _classify(line: str, probe: dict, story: dict, echoes: set) -> str:
    """relevant / echo / junk。"""
    pats = [story["facts"][k] for k in probe.get("expect", [])] + list(probe.get("ok", []))
    if _AGENT not in line and any(re.search(p, line, re.I) for p in pats):
        return "relevant"
    body = line.split(") ", 1)[1] if line.startswith("(unverified") and ") " in line else line
    if body in echoes:
        return "echo"
    return "junk"


def _visible(facts: dict, keys: list, block: str, injected: str) -> dict:
    """每个该看到的事实：在哪里看到的（block / turn / None）。助手说的行不算。"""
    def lines(t):
        return [ln for ln in (t or "").splitlines() if _AGENT not in ln]
    out = {}
    for k in keys:
        pat = facts[k]
        if any(re.search(pat, ln, re.I) for ln in lines(injected)):
            out[k] = "turn"
        elif any(re.search(pat, ln, re.I) for ln in lines(block)):
            out[k] = "block"
        else:
            out[k] = None
    return out


def run_story(story: dict) -> dict:
    from nexsandglass.core import clock
    from nexsandglass.core.memory_provider import NexSandglassProvider

    turns = timeline(story)
    clock.set_global(turns[0][0])
    p = NexSandglassProvider()
    p.initialize("history", agent_context="primary", platform="eval")
    t0 = time.time()
    for when, user, assistant, _ in turns:
        clock.set_global(when)
        p.sync_turn(user, assistant, session_id="history")
    write_s = time.time() - t0

    from nexsandglass.core import sandglass_sqlite, semantic
    from nexsandglass.features import sandglass_vault
    sandglass_sqlite._last_sync_mtime = 0
    sandglass_sqlite.sync_all()
    sandglass_vault._idx_cache = None
    sandglass_vault.rebuild_index()
    semantic.wait_idle(600)
    semantic.index_all()

    # 新会话：第 DAYS+1 天
    clock.set_global(START + timedelta(days=DAYS + 1, hours=10))
    q = NexSandglassProvider()
    q.initialize("probe", agent_context="primary", platform="eval")
    warm = getattr(q, "_warm_thread", None)
    if warm is not None:
        warm.join(60)
    t1 = time.time()
    block = q.system_prompt_block()
    block_ms = (time.time() - t1) * 1000
    echoes = {a for _, _, a in story["personal"]}

    probes = []
    for pr in story["probes"]:
        t2 = time.time()
        injected = q.prefetch(pr["q"], session_id="probe")
        ms = (time.time() - t2) * 1000
        mem = _memory_lines(injected)
        cls = [_classify(m, pr, story, echoes) for m in mem]
        vis = _visible(story["facts"], pr.get("expect", []), block, injected)
        stale = [s for s in pr.get("stale", []) if re.search(s, block + "\n" + injected)]
        probes.append({"q": pr["q"], "kind": pr.get("kind", "personal"), "expect": pr.get("expect", []),
                       "visible": vis, "n_lines": len(mem), "junk": cls.count("junk"), "echo": cls.count("echo"),
                       "relevant": cls.count("relevant"), "chars": len(injected or ""), "stale": stale,
                       "ms": round(ms, 1), "injected": injected})

    wrong = []
    for rel, obj in story.get("not_believed", []):
        try:
            facts = json.loads(q.handle_tool_call("nyx_belief", {"relation": rel})).get("facts") or []
        except Exception:
            facts = []
        if any(obj.lower() in str(f.get("object", "")).lower() for f in facts):
            wrong.append(f"{rel}:{obj}")

    block_lines = block.splitlines()
    # 说法改了的（v7.22.0）：常驻块「当前」部分 = 「Changed since」那段之前
    current = block.split(CHANGED_HEADER, 1)[0]
    changes = [{"now": bool(re.search(c["now"], current)), "was": bool(re.search(c["was"], current))}
               for c in story.get("block_changes", [])]
    fu_lines = followup_section(block)
    fu = score_followups(fu_lines, story["followups"]) if "followups" in story else None
    clock.set_global(None)
    return {"id": story["id"], "lang": story["lang"], "split": story["split"],
            "n_turns": len(turns), "write_ms_per_turn": round(write_s * 1000 / len(turns), 1),
            "block": block, "block_chars": len(block), "block_ms": round(block_ms, 1),
            "block_agent_lines": sum(1 for ln in block_lines if _AGENT in ln),
            "block_foreign": sum(1 for ln in block_lines for f in FOREIGN if re.search(f, ln)),
            "wrong_beliefs": wrong, "probes": probes, "changes": changes,
            "followup_lines": fu_lines, "followups": fu}


def _worker(args) -> None:
    story = next(s for s in STORIES if s["id"] == args.story)
    res = run_story(story)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)


# ══════════════════════════════════════════════════════════
# 主进程
# ══════════════════════════════════════════════════════════

def _spawn(story: dict, args, tmp: str) -> dict:
    home = os.path.join(tmp, story["id"])
    os.makedirs(home, exist_ok=True)
    out = os.path.join(home, "result.json")
    env = dict(os.environ, NEXSANDBASE_HOME=os.path.join(home, "nb"),
               PYTHONPATH=_REPO + os.pathsep + os.environ.get("PYTHONPATH", ""))
    env.setdefault("PYTHONHASHSEED", "0")
    for k in ("NYX_NOW", "HERMES_HOME", "NYX_UNDERSTAND_LLM", "WTHREAD_LLM_EXTRACTION"):
        env.pop(k, None)
    if not args.semantic:
        env["NYX_EMBED"] = "off"
    cmd = [sys.executable, os.path.abspath(__file__), "--worker", "--story", story["id"], "--out", out]
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=args.timeout)
    if r.returncode != 0 or not os.path.exists(out):
        return {"id": story["id"], "lang": story["lang"], "split": story["split"],
                "error": (r.stderr or r.stdout)[-3000:]}
    return json.load(open(out, encoding="utf-8"))


def score(results: list) -> dict:
    ok = [r for r in results if "error" not in r]
    probes = [p for r in ok for p in r["probes"]]
    pers = [p for p in probes if p["kind"] != "unrelated"]
    unrel = [p for p in probes if p["kind"] == "unrelated"]
    exp = sum(len(p["expect"]) for p in pers)
    vis = sum(1 for p in pers for v in p["visible"].values() if v)
    via_block = sum(1 for p in pers for v in p["visible"].values() if v == "block")
    mean = lambda xs: round(sum(xs) / len(xs), 2) if xs else None
    return {
        "stories": len(ok), "errors": len(results) - len(ok), "probes": len(probes),
        "fact_visible": round(vis / exp, 3) if exp else None,
        "fact_visible_via_block": round(via_block / exp, 3) if exp else None,
        "probe_complete": mean([1.0 if all(p["visible"].values()) else 0.0 for p in pers]),
        "junk_per_probe": mean([p["junk"] for p in probes]),
        "echo_per_probe": mean([p["echo"] for p in probes]),
        "lines_per_probe": mean([p["n_lines"] for p in probes]),
        "unrelated_clean": mean([1.0 if p["n_lines"] == 0 else 0.0 for p in unrel]),
        "inject_chars": mean([p["chars"] for p in probes]),
        "block_chars": mean([r["block_chars"] for r in ok]),
        "block_agent_lines": sum(r["block_agent_lines"] for r in ok),
        "block_foreign": sum(r["block_foreign"] for r in ok),
        "wrong_beliefs": sum(len(r["wrong_beliefs"]) for r in ok),
        "stale": sum(len(p["stale"]) for p in probes),
        # 说法改了以后：常驻块当前部分里有新说法的比例；还留着旧说法（没标成已改）的条数
        "changes": sum(len(r.get("changes", [])) for r in ok),
        "changes_now": mean([1.0 if c["now"] else 0.0 for r in ok for c in r.get("changes", [])]),
        "changes_was": sum(1 for r in ok for c in r.get("changes", []) if c["was"]),
        # 待跟进（v7.23.0）：该列的列出来的比例；不该列的（过去太久、说过结果、取消了、还早、假设）列了几件；
        # 待跟进故事里多出来的条目；别的故事（没有标注）里一共列了几件
        "fu_show": mean([1.0 if s else 0.0 for r in ok if r.get("followups") for s in r["followups"]["show"]]),
        "fu_wrong": sum(len(r["followups"]["wrong"]) for r in ok if r.get("followups")),
        "fu_extra": sum(len(r["followups"]["extra"]) for r in ok if r.get("followups")),
        "fu_other": sum(len(r.get("followup_lines") or []) for r in ok if not r.get("followups")),
        "recall_ms_mean": mean([p["ms"] for p in probes]),
    }


def report(results: list) -> dict:
    groups = {"all": results}
    for key in ("split", "lang"):
        for v in sorted({r[key] for r in results}):
            groups[f"{key}={v}"] = [r for r in results if r[key] == v]
    return {name: score(rs) for name, rs in groups.items()}


def _print(rep: dict) -> None:
    cols = ("fact_visible", "fact_visible_via_block", "probe_complete", "junk_per_probe", "echo_per_probe",
            "unrelated_clean", "inject_chars", "block_chars", "block_agent_lines", "block_foreign",
            "wrong_beliefs", "stale", "changes_now", "changes_was", "fu_show", "fu_wrong", "fu_extra", "fu_other")
    print(f"{'group':<12}" + "".join(f"{c[:14]:>15}" for c in cols))
    for name, s in rep.items():
        print(f"{name:<12}" + "".join(f"{str(s.get(c)):>15}" for c in cols))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", default="all", choices=("dev", "test", "all"))
    ap.add_argument("--lang", default="all", choices=("zh", "en", "all"))
    ap.add_argument("--semantic", action="store_true", help="开语义检索（要装 nyx-memory[vector]）")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--json")
    ap.add_argument("--show", action="store_true", help="打印每个问题 agent 看到的内容")
    ap.add_argument("--keep", help="nyx 目录留在这里（默认用完即删）")
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--story", help=argparse.SUPPRESS)
    ap.add_argument("--out", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.worker:
        _worker(args)
        return 0

    stories = [s for s in STORIES if args.split in ("all", s["split"]) and args.lang in ("all", s["lang"])]
    tmp = args.keep or tempfile.mkdtemp(prefix="nyx-personal-")
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            results = list(ex.map(lambda s: _spawn(s, args, tmp), stories))
    finally:
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)
    for r in results:
        if "error" in r:
            print(f"[{r['id']}] ERROR\n{r['error']}", file=sys.stderr)
    if args.show:
        for r in results:
            if "error" in r:
                continue
            print(f"\n{'=' * 30} {r['id']} ({r['split']}) {'=' * 30}\n[system_prompt_block]\n{r['block']}")
            for p in r["probes"]:
                miss = [k for k, v in p["visible"].items() if not v]
                print(f"\n--- {p['q']}  [{p['kind']}] junk={p['junk']} echo={p['echo']} "
                      f"missing={miss or '-'} stale={p['stale'] or '-'}\n{p['injected'] or '(nothing injected)'}")
            if r["wrong_beliefs"]:
                print(f"\nwrong beliefs: {r['wrong_beliefs']}")
            if r.get("followups"):
                f = r["followups"]
                print(f"\nfollow-ups: show={f['show']} wrong={f['wrong'] or '-'} extra={f['extra'] or '-'}")
    rep = report(results)
    _print(rep)
    if args.json:
        slim = [{k: v for k, v in r.items() if k != "block"} for r in results]
        for r in slim:
            for p in r.get("probes", []):
                p.pop("injected", None)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"report": rep, "results": slim}, f, ensure_ascii=False, indent=1)
    return 0 if all("error" not in r for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
