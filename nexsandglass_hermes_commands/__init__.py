"""
nyx-commands —— 会话里的 /nyx 命令（v7.20.0，配套的 Hermes 普通插件）

    /nyx                    帮助
    /nyx status             版本、记忆条数、有没有暂停、画像里各类有几条
    /nyx pause              暂停记录：之后的对话不写进 Nyx（召回照常），直到 /nyx resume
    /nyx resume             恢复记录
    /nyx forget-last [N]    把这个会话最近 N 轮（默认 1）送进隔离区，回显忘掉的是哪几句；保留期内可以恢复
                            （hermes nyx restore <mem_id>）
    /nyx follow-ups [on|off]  看现在的待跟进（新对话开始时给模型看的那几件）；on / off 打开或关掉（v7.23.0）

命令在 Hermes 里直接执行，不经过模型；结果只显示在这个聊天里。
谁能用：本机（CLI / TUI / 桌面）和私聊里都能用；群聊、频道里只能看 status —— 群里任何人都能打命令。
配了 owner_ids（nyx.json / hermes memory setup）时，只有主人能暂停、恢复和遗忘。

为什么单独一个插件：Hermes 把记忆提供器当成独占插件单独加载，斜杠命令只能由普通插件注册。随 nyx-memory 一起装
（入口点 hermes_agent.plugins），启用一次即可：

    hermes plugins enable nyx-commands
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_HELP_EN = """Nyx commands (run here, never sent to the model):
  /nyx status             version, number of memories, paused or not, what the profile holds
  /nyx pause              stop recording: what you say next is not remembered (recall keeps working)
  /nyx resume             start recording again
  /nyx forget-last [N]    move the last N turns of this chat (default 1) to the quarantine; restorable
  /nyx follow-ups [on|off]  what the model is shown as coming up or just happened; turn it on or off"""
_HELP_ZH = """Nyx 命令（在这里直接执行，不经过模型）：
  /nyx status             版本、记忆条数、有没有暂停、画像里有什么
  /nyx pause              暂停记录：接下来说的不会被记住（召回照常）
  /nyx resume             恢复记录
  /nyx forget-last [N]    把这个聊天最近 N 轮（默认 1）送进隔离区，可以恢复
  /nyx follow-ups [on|off]  看模型现在看到的待跟进（快到了的、刚过去还不知道结果的）；打开或关掉"""


def _t(en: str, zh: str) -> str:
    try:
        from nexsandglass.core.lang import t
        return t(en, zh)
    except Exception:
        return en


def _platform(source: Any) -> str:
    p = getattr(source, "platform", None)
    return str(getattr(p, "value", None) or p or "").lower()


def _local(source: Any) -> bool:
    """本机：经典 CLI 直接调（source=None），或者本机会话经共享网关转过来（Hermes 的 Platform.LOCAL）。"""
    return source is None or _platform(source) == "local"


def _who(source: Any) -> tuple:
    """(在群里吗, 是机器人吗, 可能的身份)。本机既不在群里也不是机器人。"""
    if _local(source):
        return False, False, set()
    chat = str(getattr(source, "chat_type", "") or "dm").lower()
    platform = _platform(source)
    uid = getattr(source, "user_id", None)
    ident = {str(x) for x in (uid, getattr(source, "user_name", None), getattr(source, "user_id_alt", None)) if x}
    if uid and platform:
        ident.add(f"{platform}:{uid}")
    return chat not in ("dm", ""), bool(getattr(source, "is_bot", False)), ident


def _owners() -> set:
    try:
        import nexsandglass_hermes as nh
        return {str(o) for o in (nh.load_config().get("owner_ids") or [])}
    except Exception:
        return set()


def allowed(action: str, source: Any) -> Optional[str]:
    """能不能执行；不能的话返回原因。status / help 谁都能看（不含记忆内容）。"""
    if action in ("status", "help") or _local(source):
        return None
    in_group, is_bot, ident = _who(source)
    if is_bot:
        return _t("Bots cannot change Nyx.", "机器人不能改 Nyx 的状态。")
    owners = _owners()
    if owners and not (ident & owners):
        return _t("Only the owner can do that.", "只有主人能这么做。")
    if in_group and not owners:
        return _t("Not in group chats: anyone here could type it. Use a private chat, or set owner_ids.",
                  "群聊里不行：群里任何人都能打这个命令。去私聊里用，或者配 owner_ids。")
    return None


def _follow_state() -> str:
    from nexsandglass.core import followups
    if followups.setting_off():
        return _t("off (follow_ups: off in nyx.json, or NYX_FOLLOW_UPS)", "关着（nyx.json 的 follow_ups: off，或 NYX_FOLLOW_UPS）")
    if followups.switched_off() is not None:
        return _t("off (/nyx follow-ups on)", "关着（/nyx follow-ups on 打开）")
    n = len(followups.current())
    return _t(f"on, {n} due", f"开着，现在 {n} 件")


def _status() -> str:
    from nexsandglass.core import memid, profile, recording
    from nexsandglass.core.sandglass_paths import __version__, _NB
    n = memid.count()
    pz = recording.paused()
    got = profile.items()
    kinds = ", ".join(f"{k} {len(v)}" for k, v in got.items() if v) or _t("empty", "空")
    rec = (_t(f"PAUSED since {str(pz.get('since') or '')[:16]} (/nyx resume)", f"已暂停，从 {str(pz.get('since') or '')[:16]} 起（/nyx resume 恢复）")
           if pz else _t("on", "开着"))
    try:
        fu = _follow_state()
    except Exception as e:
        fu = f"? ({e})"
    return _t(f"Nyx {__version__} · {n} memories · recording {rec}\nprofile: {kinds}\nfollow-ups: {fu}\ndata: {_NB}",
              f"Nyx {__version__} · {n} 条记忆 · 记录{rec}\n画像：{kinds}\n待跟进：{fu}\n数据：{_NB}")


def _follow_ups(arg: str, source: Any) -> str:
    from nexsandglass.core import followups
    if arg in ("on", "off"):
        _, _, ident = _who(source)
        changed = followups.switch(arg == "on", by=sorted(ident)[0] if ident else "local")
        if arg == "off":
            return _t("Follow-ups are off: new conversations no longer show the model what is coming up or just "
                      "happened." if changed else "Follow-ups were already off.",
                      "待跟进已关：新对话不再给模型看快到了的、刚过去的事。" if changed else "待跟进本来就是关着的。")
        msg = _t("Follow-ups are on." if changed else "Follow-ups were already on.",
                 "待跟进已打开。" if changed else "待跟进本来就开着。")
        if followups.setting_off():
            msg += _t(" But follow_ups: off in nyx.json (or NYX_FOLLOW_UPS=off) still keeps them off.",
                      " 不过 nyx.json 里 follow_ups: off（或 NYX_FOLLOW_UPS=off）仍然让它关着。")
        return msg
    if arg:
        return _t(f"Unknown: /nyx follow-ups {arg} (use on or off)", f"没有这个：/nyx follow-ups {arg}（用 on 或 off）")
    got = followups.current()
    state = _follow_state()
    if not got:
        return _t(f"Follow-ups: {state}. Nothing coming up in the next two days or waiting for news.",
                  f"待跟进：{state}。两天内没有要发生的事，也没有等着听结果的。")
    shown = "\n".join(followups.lines(got))
    return _t(f"Follow-ups: {state}. A new conversation shows the model:\n{shown}",
              f"待跟进：{state}。新对话开始时模型会看到：\n{shown}")


def _forget_last(n: int, session_id: str) -> str:
    from nexsandglass.core import memid
    from nexsandglass.core.memory_provider import recent_turns
    from nexsandglass.runtime import facade
    turns = recent_turns(session_id, n)
    ids = [mid for turn in turns for mid in turn]
    note = ""
    if not ids:
        # 这个进程里没记到（刚重启，或者 Hermes 换了会话 id）：按中枢里最新的 n 轮（用户一句 + 回复）算
        rows = memid.get_conn().execute(
            "SELECT mem_id, sender FROM memories WHERE deleted_at IS NULL ORDER BY seq DESC LIMIT ?", (n * 2,)).fetchall()
        ids = [r[0] for r in rows]
        note = _t(" (the newest memories overall: this chat's turns were not tracked since Nyx started)",
                  "（按全部记忆里最新的算：Nyx 启动以来没有记到这个聊天的轮次）")
    if not ids:
        return _t("Nothing to forget.", "没有可以遗忘的。")
    # 先取原文：回显给用户核对忘的是不是那一句（送进隔离区之后中枢里的正文就清空了，原文只在隔离区表里）
    marks = ",".join("?" * len(ids))
    said = [(r[0], r[1]) for r in memid.get_conn().execute(
        f"SELECT sender, text FROM memories WHERE mem_id IN ({marks}) ORDER BY seq", ids).fetchall()]
    r = facade.forget({"mem_ids": ids})
    if not r.get("ok") or not int(r.get("removed") or 0):
        return _t(f"Nothing was forgotten ({r.get('error') or 'already gone?'}).",
                  f"什么都没忘（{r.get('error') or '可能已经不在了'}）。")
    until = str(r.get("purge_after") or "")[:10]
    shown = [t for s, t in said if s not in ("agent", "assistant")] or [t for _, t in said]
    preview = "\n".join("  · " + " ".join(str(t).split())[:60] + ("…" if len(str(t)) > 60 else "") for t in shown[:5])
    from nexsandglass.core import hints
    again = hints.cmd("restore", " ".join(ids))
    return _t(f"Moved {r['removed']} memories to the quarantine{note}"
              + (f"; restorable until {until}" if until else "") + f":\n{preview}\nTo bring them back: `{again}`",
              f"已把 {r['removed']} 条送进隔离区{note}" + (f"，{until} 之前都能恢复" if until else "")
              + f"：\n{preview}\n要恢复：`{again}`")


def handle(raw_args: str = "", source: Any = None, session_id: str = "", **_kw) -> str:
    """/nyx 的处理函数（Hermes 按签名传 source / session_id）。"""
    parts = (raw_args or "").split()
    action = (parts[0].lower() if parts else "help").replace("_", "-")
    if action in ("followups", "follow-up", "followup"):
        action = "follow-ups"
    if action in ("help", "-h", "--help", "?"):
        return _t(_HELP_EN, _HELP_ZH)
    if action not in ("status", "pause", "resume", "forget-last", "follow-ups"):
        return _t(f"Unknown: /nyx {action}\n", f"没有这个命令：/nyx {action}\n") + _t(_HELP_EN, _HELP_ZH)
    why = allowed(action, source)
    if why:
        return why
    try:
        if action == "status":
            return _status()
        if action == "follow-ups":
            return _follow_ups(parts[1].lower() if len(parts) > 1 else "", source)
        from nexsandglass.core import recording
        if action == "pause":
            _, _, ident = _who(source)
            recording.pause(by=sorted(ident)[0] if ident else "local")
            return _t("Nyx recording paused: what you say next is not remembered. /nyx resume to turn it back on.",
                      "Nyx 已暂停记录：接下来说的不会被记住。/nyx resume 恢复。")
        if action == "resume":
            was = recording.resume()
            return _t("Nyx recording is on again." if was else "Nyx was already recording.",
                      "Nyx 恢复记录了。" if was else "Nyx 本来就在记录。")
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
        return _forget_last(max(1, min(n, 20)), session_id or "")
    except Exception as e:
        logger.warning("nyx-commands: /nyx %s failed: %s", action, e)
        return _t(f"/nyx {action} failed: {e}", f"/nyx {action} 失败：{e}")


def register(ctx) -> None:
    # import nexsandglass 会定下数据目录：先按当前 profile 的 nyx.json 定（同 nyx-reveal，v7.17.0）
    import nexsandglass_hermes
    nexsandglass_hermes.apply_data_dir()
    ctx.register_command("nyx", handle, description="Nyx memory: status / pause / resume / forget-last / follow-ups",
                         args_hint="status | pause | resume | forget-last [N] | follow-ups [on|off]")
