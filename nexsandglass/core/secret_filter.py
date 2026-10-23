"""
core/secret_filter.py — 密钥、密码不进记忆（v7.14.0）

为什么要有它：nyx 把对话原样写进日志。用户在聊天里贴一个 token（「帮我配一下，key 是 hf_…」），
它就以明文躺在 sandglass.txt、中枢、全文 / 倒排索引、影子副本、备份里，之后还会被召回、塞回提示词 ——
多人会话里塞给别人看；语义检索走 API 时，还会被发给嵌入服务。用户几乎从不希望记忆替他保管这些。

规则：**进入任何存储之前**隐去，换成占位符 `[已隐去: HF token …a1b2]`。
  - token / API key 保留末 4 位，能分清「是哪一把」，拿不回原文（4 个字符对 30+ 位随机串没有意义）
  - 密码、私钥、<private>…</private> 包住的内容一个字符都不留
  - 占位符不以 [REDACTED 开头 —— 那是遗忘留下的标记，整条记录会被所有索引跳过
  - 只认格式明确的东西：已知前缀的 token、PEM 私钥块、JWT、带凭据的连接串、`XXX_API_KEY=…` 这类赋值、
    「密码是 …」。宁可漏掉一个不认识格式的，也不把普通句子抹掉（tests/test_secret_filter.py 有正反例）

档位 NYX_SECRET_FILTER：on（默认：token + 密码 + 私密标记）/ tokens（不动密码）/ off。

入口：scrub(text) 用在所有写入入口（宿主钩子、FormationEngine.observe、log_message、memid.allocate、
l0 缓冲）和发给嵌入服务之前；升级之前已经存下来的见 core/secret_scrub.py。
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

MARK = "[已隐去: "
_PLACEHOLDER = re.compile(r"\[已隐去: [^\]\n]*\]")


def level() -> str:
    v = (os.environ.get("NYX_SECRET_FILTER") or "on").strip().lower()
    return v if v in ("on", "tokens", "off") else "on"


@dataclass
class Rule:
    kind: str
    label: str
    rx: "re.Pattern"
    tail: bool = False                      # 占位符里留末 4 位
    ok: Optional[Callable] = None           # 额外校验（match → bool）；不过就不当成密钥
    passwords: bool = False                 # 只在 on 档生效


def _has(s, *classes) -> bool:
    return all(re.search(c, s) for c in classes)


def _r(kind, label, pattern, tail=False, ok=None, passwords=False, flags=0) -> Rule:
    return Rule(kind, label, re.compile(pattern, flags), tail, ok, passwords)


# 有 (?P<v>…) 的规则只替换 v 那一段（前缀留着：「HF_TOKEN=」「postgres://user:」），否则替换整个匹配。
# 顺序有讲究：更具体的排在前面（sk-ant- 先于 sk-），已经换成占位符的不会再被认出来
RULES: List[Rule] = [
    _r("private", "私密内容", r"<private>.*?</private>", flags=re.I | re.S),
    _r("private_key", "私钥",
       r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----[\s\S]*?-----END (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"),
    _r("anthropic", "Anthropic API key", r"\bsk-ant-[A-Za-z0-9_\-]{20,}", True),
    _r("openrouter", "OpenRouter API key", r"\bsk-or-v1-[A-Za-z0-9]{32,}", True),
    _r("openai", "OpenAI API key", r"\bsk-proj-[A-Za-z0-9_\-]{20,}", True),
    _r("sk", "API key", r"\bsk-[A-Za-z0-9]{20,}", True),          # OpenAI 旧格式、DeepSeek、Moonshot……
    _r("huggingface", "HF token", r"\bhf_[A-Za-z0-9]{30,}", True),
    _r("github", "GitHub token", r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})", True),
    _r("gitlab", "GitLab token", r"\bglpat-[A-Za-z0-9_\-]{20,}", True),
    _r("pypi", "PyPI token", r"\bpypi-[A-Za-z0-9_\-]{40,}", True),
    _r("npm", "npm token", r"\bnpm_[A-Za-z0-9]{36,}", True),
    _r("aws", "AWS access key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", True),
    _r("google", "Google API key", r"\bAIza[0-9A-Za-z_\-]{35}", True),
    _r("slack", "Slack token", r"\bxox[abprs]-[A-Za-z0-9\-]{10,}", True),
    _r("slack_webhook", "Slack webhook", r"https://hooks\.slack\.com/services/[A-Za-z0-9/]{20,}"),
    _r("stripe", "Stripe key", r"\b(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{16,}", True),
    _r("sendgrid", "SendGrid key", r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}", True),
    _r("telegram", "Telegram bot token", r"\b\d{6,12}:[A-Za-z0-9_\-]{35}\b", True,
       ok=lambda m: _has(m.group(0).split(":", 1)[1], r"[A-Za-z]", r"\d|_|-")),
    _r("discord", "Discord token", r"\b[MN][A-Za-z\d]{23,27}\.[\w\-]{6}\.[\w\-]{27,40}\b", True),
    _r("jwt", "JWT", r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}", True),
    # 带凭据的连接串：postgres://user:pass@host —— 只隐去密码那一段
    _r("url_password", "密码", r"\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s/:@\"']+:(?P<v>[^\s/@\"']+)@"),
    # 环境变量式赋值：OPENAI_API_KEY=…、HF_TOKEN: …、DB_PASSWORD="…"（全大写、以 KEY / TOKEN / SECRET / PASSWORD 结尾）
    _r("assignment", "密钥",
       r"\b[A-Z][A-Z0-9_]*(?:_KEY|TOKEN|SECRET|PASSWORD|PASSWD)\b\s*[:=]\s*[\"']?(?P<v>[^\s\"'`,;]{8,})", True,
       ok=lambda m: not m.group("v").startswith(("$", "<", "{", "%"))),        # ${VAR} / <your-key> 是占位，不是值
    # 小写键值：api_key = …、access_token: …、Authorization: Bearer …（值要像凭据：16 位以上、字母数字都有）
    _r("assignment", "密钥",
       r"\b(?:api[_\-]?key|secret[_\-]?key|access[_\-]?token|auth[_\-]?token|refresh[_\-]?token|client[_\-]?secret"
       r"|bearer)\s*[:=]?\s*[\"']?(?P<v>[A-Za-z0-9_\-./+=]{16,})", True,
       ok=lambda m: _has(m.group("v"), r"[A-Za-z]", r"\d"), flags=re.I),
    # 「密码是 …」：值必须是 ASCII（「密码是什么来着」不算）。有分隔符（是 / 为 / : / =）时 4 位以上都算；
    # 没有分隔符（「wifi密码12345678」）要带数字
    _r("password", "密码",
       r"(?:密码|口令|PIN码|pin码)\s*(?P<sep>是|为|:|：|=)?\s*[\"'“]?"
       r"(?P<v>[!-~]{3,63}?[A-Za-z0-9@#$%^&*_\-+=~])(?=[,.;!?)\]}\"'”]*(?:[^!-~]|$))",
       ok=lambda m: bool(m.group("sep")) or bool(re.search(r"\d", m.group("v"))), passwords=True),
    # 英文：password: … / password is …，要带数字或符号（「the password is required」不算）
    _r("password", "密码",
       r"\b(?:password|passwd)\s*(?::|=|\s+is\s+)\s*[\"']?"
       r"(?P<v>[!-~]{3,63}?[A-Za-z0-9@#$%^&*_\-+=~])(?=[,.;!?)\]}\"']*(?:[^!-~]|$))",
       ok=lambda m: bool(re.search(r"[\d@#$%^&*_\-+=~!]", m.group("v"))), passwords=True, flags=re.I),
]

# 兜底：调用方先截断再写（「[:500]」）时，token 可能只剩前半截、长度不够上面的规则。
# 文本末尾的「已知前缀 + 8 位以上」一律当成截断的密钥
_TRUNCATED = re.compile(r"\b(?:sk-(?:ant-|or-v1-|proj-)?|hf_|gh[pousr]_|github_pat_|glpat-|pypi-|npm_|xox[abprs]-|AIza|eyJ)"
                        r"(?P<rest>[A-Za-z0-9_\-.]{8,})$")

# 快速预筛：每条写入都要过一遍，大多数句子一个都不沾，没必要跑全部规则
_HINT = re.compile(
    r"sk-|hf_|gh[pousr]_|github_pat_|glpat-|pypi-|npm_|AKIA|ASIA|AIza|xox|hooks\.slack|_live_|_test_|SG\.|eyJ"
    r"|PRIVATE KEY|<private>|://[^\s/:@]+:[^\s/@]+@|\d{6}:|\.[\w\-]{6}\.|密码|口令|PIN码|pin码"
    r"|(?i:passw|bearer|api[_\-]?key|secret|token|_key\b)")


@dataclass
class Finding:
    kind: str
    label: str


@dataclass
class Result:
    text: str
    findings: List[Finding] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.findings)


def _mark(label: str, value: str, tail: bool) -> str:
    v = re.sub(r"[^A-Za-z0-9]", "", value)          # 末 4 位只取字母数字：不把引号、逗号带进占位符
    return f"{MARK}{label}" + (f" …{v[-4:]}" if tail and len(v) >= 12 else "") + "]"


def scrub(text: Optional[str], mode: str = None, keep_lines: bool = False, capture: Optional[list] = None) -> Result:
    """隐去 text 里的密钥 / 密码。返回 Result(text, findings)；findings 只有类别，没有原文。

    keep_lines：替换后行数不变（改写已经存下的日志时用 —— 行号是别处的主键）。
    capture：传一个 list 进来，被隐去的原文追加进去（只给存量清理在内存里用，去别的存储里找同一个字符串）。
    幂等：占位符本身不会再被认成密钥。
    """
    mode = mode or level()
    if not text or mode == "off" or not _HINT.search(text):
        return Result(text or "")
    found: List[Finding] = []
    s = text
    for rule in RULES:
        if rule.passwords and mode != "on":
            continue
        # 已经换成的占位符里也有「密码」两个字：从那里开始的匹配不算（否则会把占位符后面的正文一起抹掉）
        done = [(p.start(), p.end()) for p in _PLACEHOLDER.finditer(s)]

        def rep(m, rule=rule, done=done):
            whole = m.group(0)
            if any(m.start() < e and m.end() > b for b, e in done) or (rule.ok is not None and not rule.ok(m)):
                return whole
            found.append(Finding(rule.kind, rule.label))
            if "v" in rule.rx.groupindex:
                a, b = m.start("v") - m.start(), m.end("v") - m.start()
                secret = whole[a:b]
                out = whole[:a] + _mark(rule.label, secret, rule.tail) + whole[b:]
            else:
                secret = whole
                out = _mark(rule.label, whole, rule.tail)
            if capture is not None:
                capture.append(secret)
            return out + ("\n" * secret.count("\n") if keep_lines else "")
        s = rule.rx.sub(rep, s)
    tm = _TRUNCATED.search(s)
    if tm and _has(tm.group("rest"), r"[A-Za-z]", r"\d") and not any(tm.start() < e and tm.end() > b for b, e in
                      ((p.start(), p.end()) for p in _PLACEHOLDER.finditer(s))):
        found.append(Finding("truncated", "密钥（被截断）"))
        if capture is not None:
            capture.append(s[tm.start():])
        s = s[:tm.start()] + f"{MARK}密钥（被截断）]"
    if found:
        logger.info("[secret_filter] 隐去 %d 处：%s", len(found), "、".join(sorted({f.label for f in found})))
    return Result(s, found)


def clean(text: Optional[str], mode: str = None) -> str:
    """scrub(text).text 的简写，给写入入口用。"""
    return scrub(text, mode).text
