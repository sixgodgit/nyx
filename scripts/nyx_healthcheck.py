#!/usr/bin/env python3
"""nyx_healthcheck.py —— 巡检一次，非绿就以非零退出码退出。

给 cron 用：
    0 9 * * *  NEXSANDBASE_HOME=/root/.hermes/nexsandglass \\
               /usr/local/lib/hermes-agent/venv/bin/python3 \\
               /root/.hermes/NexSandglass/scripts/nyx_healthcheck.py --quiet

为什么需要它：写入放大那个 bug 从 3 倍爬到 56 倍花了七天没人发现 ——
不是查不出来，是没有任何东西在定时看。现在指标齐了却还是只在人想起来时才跑，
等于把同一个坑挖回去。

退出码：0 全绿 / 1 有指标不绿 / 2 跑不起来
"""
import argparse
import json
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="全绿时不输出（cron 友好）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--window-days", type=int, default=3,
                    help="写入放大的观察窗口（天）")
    args = ap.parse_args()

    try:
        from nexsandglass.core import memid
    except Exception as e:
        print("nyx healthcheck: 导入失败: %s" % e, file=sys.stderr)
        return 2

    try:
        h = memid.health(window_days=args.window_days)
    except Exception as e:
        print("nyx healthcheck: 巡检本身抛异常: %s" % e, file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(h, ensure_ascii=False, indent=2))
        return 0 if h["ok"] else 1

    bad = [n for n, c in h["checks"].items() if not c.get("ok")]
    if h["ok"] and args.quiet:
        return 0

    out = sys.stdout if h["ok"] else sys.stderr
    print("nyx health: %s" % ("ok" if h["ok"] else "BAD (%s)" % ", ".join(bad)), file=out)
    for n, c in h["checks"].items():
        print("  %-22s %s  %s" % (
            n, "OK " if c.get("ok") else "BAD",
            " ".join("%s=%s" % (k, v) for k, v in c.items() if k != "ok")), file=out)
    return 0 if h["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
