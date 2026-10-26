"""
NexSandglass TTY Wrapper — 任何终端 Agent 自动落沙
==================================================
用法：python nexsandglass.py wrap [agent-command]
      python nexsandglass.py wrap claude
      python nexsandglass.py wrap codex
      python nexsandglass.py wrap opencode

自动监听 stdin/stdout，Agent 的每句话都会落到沙漏。
不依赖 Hermes，不依赖 MCP，不改 Agent 代码。
"""

import os
import platform
import re
import sys

# 确保能 import sandglass_log（包安装后可用；直接运行脚本时降级为 no-op）
try:
    from nexsandglass.core.sandglass_log import log_message
except ImportError:
    def log_message(text, sender="agent"):
        return False


def _strip_ansi(text):
    """去除 ANSI 转义码。"""
    return re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text).strip()


def wrap_command():
    """包装启动任意 Agent 命令，自动落沙。"""
    if len(sys.argv) < 3:
        print("usage: nexsandglass wrap [command] [args...]")
        print("example: nexsandglass wrap claude")
        sys.exit(1)

    if platform.system() == "Windows":
        print("⚠️  The NexSandglass TTY wrapper does not support Windows.")
        print("   On Windows, use:")
        print("   - the Hermes plugin (records automatically)")
        print("   - sandglass_log.py (record by hand)")
        print("   - the MCP tools (record automatically)")
        sys.exit(1)

    import pty
    import select
    import tty
    import termios
    cmd = sys.argv[2:]
    print(f"🧵 NexSandglass: started {cmd[0]}, recording...")
    print("   (Ctrl+D or exit to quit)")
    print()

    pid, fd = pty.fork()

    if pid == 0:
        os.execvp(cmd[0], cmd)
    else:
        old_settings = termios.tcgetattr(sys.stdin)
        tty.setraw(sys.stdin)
        try:
            _relay_io(fd, old_settings, pid)
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
            try:
                os.waitpid(pid, 0)
            except (ChildProcessError, OSError):
                pass
            print()
            print("🧵 NexSandglass: stopped")


def _relay_io(fd, old_settings, pid):
    """转发 I/O 并落沙。按行处理，不依赖换行计数。"""
    import select
    agent_buffer = ""
    while True:
        r, _, _ = select.select([sys.stdin, fd], [], [])
        if sys.stdin in r:
            data = os.read(sys.stdin.fileno(), 4096)
            if not data: break
            os.write(fd, data)
            text = data.decode("utf-8", errors="replace").strip()
            if text and len(text) > 1:
                log_message(text, sender="user")

        if fd in r:
            data = os.read(fd, 4096)
            if not data: break
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
            agent_buffer += data.decode("utf-8", errors="replace")
            # 按行落沙
            while "\n" in agent_buffer:
                line, agent_buffer = agent_buffer.split("\n", 1)
                line = _strip_ansi(line).strip()
                if line and not _is_prompt(line):
                    log_message(line[:500], sender="agent")
    # 落剩余
    if agent_buffer.strip():
        log_message(_strip_ansi(agent_buffer).strip()[:500], sender="agent")


def _is_prompt(text):
    """检测是否是命令提示符。规避误杀 Markdown 引用和文档命令。"""
    if not text: return True
    # 真正的 prompt 特征：短 + 以特殊符号结尾
    if len(text) > 30: return False
    # Markdown 引用 "> text" → 不是 prompt
    if text.startswith("> ") or text.startswith("$ ") or text.startswith(">>> "):
        return False
    # prompt 常见模式
    return any(text.endswith(c) for c in ("$", "#", ">", "❯", ":")) and len(text) < 15


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "wrap":
        wrap_command()
    else:
        print("NexSandglass V2.3.17 — TTY Wrapper")
        print("usage: python nexsandglass.py wrap [command] [args...]")
        sys.exit(1)
