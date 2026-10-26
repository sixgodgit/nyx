"""
python3 -m nexsandglass.vault —— 密钥保险箱的命令行（v7.15.0）

    python3 -m nexsandglass.vault status          # 开没开、后端、密钥文件、条目数、配套插件
    python3 -m nexsandglass.vault list [关键词]    # 编号、类别、末 4 位、保存日期、上下文；不出原文
    python3 -m nexsandglass.vault show v3         # 原文打到终端（不经过任何模型、聊天平台、会话记录）
    python3 -m nexsandglass.vault show v3 --copy  # 放进剪贴板，30 秒后清空
    python3 -m nexsandglass.vault delete v3       # 真删
    python3 -m nexsandglass.vault gc              # 删掉来源记忆已被真删的条目
    python3 -m nexsandglass.vault rekey           # 换主密钥，全部重新加密
    python3 -m nexsandglass.vault audit           # 最近的取回 / 删除记录（不含原文）

开关：NYX_VAULT=on（Hermes 用户：hermes memory setup 里的 vault）。设计与威胁模型见 docs/secret-vault.md。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time

from nexsandglass.core.lang import t


def _vault():
    from nexsandglass.core import secret_vault
    return secret_vault


def _id(s: str) -> int:
    vid = _vault().parse_id(s)
    if vid is None:
        raise SystemExit(t(f"write the id like v3: {s!r}", f"编号写成 v3 这样：{s!r}"))
    return vid


_CLIP = (("pbcopy",), ("wl-copy",), ("xclip", "-selection", "clipboard"), ("xsel", "--clipboard", "--input"),
         ("clip.exe",))


def _copy(text: str) -> bool:
    for cmd in _CLIP:
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text.encode("utf-8"), check=True, timeout=5)
                return True
            except Exception:
                continue
    return False


def cmd_status(args) -> int:
    v = _vault()
    st = v.status()
    st["hermes_plugin"] = v.health().get("hermes_plugin")
    if args.json:
        print(json.dumps(st, ensure_ascii=False, indent=1, default=str))
        return 0
    on = st['level'] == 'on'
    print(t(f"Vault: {'on' if on else 'off'} · backend {st['backend'] or 'none'} · reveal {st['reveal']}"
            f" · keeps {'everything' if st['kinds'] == 'all' else 'tokens only (no passwords)'}",
            f"保险箱：{'开' if on else '关'} · 后端 {st['backend'] or '无'} · 取回方式 {st['reveal']}"
            f" · 收 {'全部' if st['kinds'] == 'all' else '只收 token（不收密码）'}"))
    if st["reason"]:
        print(f"  ⚠ {st['reason']}")
    print(t(f"  database: {st['db']}", f"  库：{st['db']}"))
    if st.get("key"):
        mode = st.get("key_mode")
        print(t(f"  master key: {st['key']}" + (f" (mode {mode})" if mode else "")
                + " — take it along when you move machines; keep a copy in a password manager",
                f"  主密钥：{st['key']}" + (f"（权限 {mode}）" if mode else "")
                + " —— 换机器时要一起带走，最好另存一份进密码管理器"))
    if "entries" in st:
        print(t(f"  entries {st['entries']} · source quarantined {st.get('held', 0)} · source gone {st.get('orphans', 0)}",
                f"  条目 {st['entries']} · 来源在隔离区 {st.get('held', 0)} · 来源已不存在 {st.get('orphans', 0)}"))
    if st.get("hermes_plugin"):
        print(t(f"  companion plugin nyx-reveal: {st['hermes_plugin']}", f"  配套插件 nyx-reveal：{st['hermes_plugin']}"))
    return 0


def cmd_list(args) -> int:
    es = _vault().entries(args.query or "")
    if args.json:
        print(json.dumps([e.public() for e in es], ensure_ascii=False, indent=1))
        return 0
    if not es:
        print(t("Nothing in the vault" + (f" matching {args.query!r}" if args.query else ""),
                "保险箱里没有" + (f"和「{args.query}」相关的" if args.query else "东西")))
        return 0
    for e in es:
        p = e.public()
        tail = (' ' + p['tail']) if p['tail'] else ''
        print(t(f"{p['id']:>5}  {p['label']}{tail} · saved {p['saved']} · revealed {p['revealed']}× · {p['state']}",
                f"{p['id']:>5}  {p['label']}{tail} · {p['saved']} 存 · 取回 {p['revealed']} 次 · {p['state']}"))
        if p["context"]:
            print(f"       {p['context']}")
    return 0


def cmd_show(args) -> int:
    v = _vault()
    vid = _id(args.id)
    e = v.entry(vid)
    if e is None:
        print(t(f"No v{vid} in the vault", f"保险箱里没有 v{vid}"), file=sys.stderr)
        return 1
    try:
        value = v.get(vid)
    except v.VaultError as ex:
        v.audit("reveal", vid, "error", str(ex), "cli")
        print(t(f"Cannot read it: {ex}", f"取不出来：{ex}"), file=sys.stderr)
        return 1
    v.mark_revealed(vid)
    v.audit("reveal", vid, "ok", "", "cli")
    if e.state != "live":
        print(t(f"(note: {e.public()['state']})", f"（注意：{e.public()['state']}）"), file=sys.stderr)
    if args.copy:
        if not _copy(value):
            print(t("No clipboard tool found (pbcopy / wl-copy / xclip / xsel / clip.exe); printing it instead:",
                    "没找到剪贴板工具（pbcopy / wl-copy / xclip / xsel / clip.exe），改为打到终端："), file=sys.stderr)
            print(value)
            return 0
        print(t(f"v{vid} ({e.label}) copied to the clipboard; clearing in {args.clear} s (Ctrl+C clears now)",
                f"v{vid}（{e.label}）已复制到剪贴板，{args.clear} 秒后清空（Ctrl+C 立即清空）"))
        try:
            time.sleep(args.clear)
        except KeyboardInterrupt:
            pass
        _copy("")
        print(t("Clipboard cleared", "剪贴板已清空"))
        return 0
    print(value)
    return 0


def cmd_delete(args) -> int:
    v = _vault()
    vids = [_id(s) for s in args.ids]
    if not args.yes:
        for vid in vids:
            e = v.entry(vid)
            print(f"  v{vid}: {e.label + ' · ' + e.created_at[:10] if e else t('(does not exist)', '（不存在）')}")
        if input(t("Delete for good (cannot be undone)? Type yes to confirm: ",
                   "真删（不能恢复）？输入 yes 确认：")).strip().lower() != "yes":
            print(t("Nothing deleted", "没有删"))
            return 1
    n = v.delete(vids)
    print(t(f"Deleted {n}", f"删掉 {n} 条"))
    return 0


def cmd_gc(args) -> int:
    gone = _vault().gc()
    ids = ', '.join(f'v{i}' for i in gone)
    print(t(f"Deleted {len(gone)} entries whose source memory is gone" + (f": {ids}" if gone else ""),
            f"删掉 {len(gone)} 条来源记忆已不存在的条目" + (f"：{ids}" if gone else "")))
    return 0


def cmd_rekey(args) -> int:
    v = _vault()
    try:
        n = v.rekey()
    except v.VaultError as ex:
        print(t(f"Not rekeyed: {ex}", f"没换：{ex}"), file=sys.stderr)
        return 1
    print(t(f"New master key in place ({v.key_path()}); re-encrypted {n} entries. If you kept a copy of the old key "
            "somewhere, it is useless now: replace it with the new one.",
            f"已换新主密钥（{v.key_path()}），重新加密 {n} 条。旧密钥另存过的话，那一份已经没用了，换成新的。"))
    return 0


def cmd_audit(args) -> int:
    rows = _vault().audit_rows(args.n)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    for r in rows:
        vid = f"v{r['vid']}" if r["vid"] is not None else "-"
        extra = " · ".join(x for x in (r["path"], r["platform"], r["reason"]) if x)
        print(f"{r['ts']}  {r['action']:<7} {vid:>5}  {r['outcome']}" + (f"  ({extra})" if extra else ""))
    return 0


def main(argv=None) -> int:
    from nexsandglass.core import hints
    ap = argparse.ArgumentParser(prog=hints.cmd("vault"), description=t("Nyx secret vault", "Nyx 密钥保险箱"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status", help=t("on or off, backend, key file, entry count", "开没开、后端、密钥文件、条目数"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("list", help=t("list entries (never shows secrets)", "列出条目（不出原文）"))
    p.add_argument("query", nargs="?")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_list)
    p = sub.add_parser("show", help=t("print the secret in the terminal", "把原文打到终端"))
    p.add_argument("id")
    p.add_argument("--copy", action="store_true", help=t("copy to the clipboard instead of printing", "放进剪贴板而不是打出来"))
    p.add_argument("--clear", type=int, default=30, help=t("clear the clipboard after N seconds (default 30)", "几秒后清空剪贴板（默认 30）"))
    p.set_defaults(fn=cmd_show)
    p = sub.add_parser("delete", help=t("delete for good", "真删"))
    p.add_argument("ids", nargs="+")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_delete)
    sub.add_parser("gc", help=t("delete entries whose source memory is gone", "删掉来源记忆已不存在的条目")).set_defaults(fn=cmd_gc)
    sub.add_parser("rekey", help=t("replace the master key", "换主密钥")).set_defaults(fn=cmd_rekey)
    p = sub.add_parser("audit", help=t("recent reveals and deletions", "最近的取回 / 删除记录"))
    p.add_argument("-n", type=int, default=30)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_audit)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
