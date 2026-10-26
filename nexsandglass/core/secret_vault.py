"""
core/secret_vault.py — 密钥保险箱（v7.15.0，默认关）

v7.14.0 起，聊天里贴的 token / API key / 密码在写进记忆之前就换成占位符，原文丢掉。
NYX_VAULT=on 时原文不丢：加密存进 vault.db，占位符带编号 `[已隐去: HF token …a1b2 #v3]`；
主人明确要看时再按 docs/secret-vault.md 的三条路交还给主人。

  - 只收用户一侧的消息（nyx 是个人记忆，不区分发言人）；助手回复、工具输出里的只隐去
  - 加密：AES-256-GCM，主密钥在 ~/.config/nyx/vault.key（0600，**不在数据目录**：数据目录被备份 / 拷走时密钥不跟着走）
    或 NYX_VAULT_KEY；也可以放系统钥匙串（NYX_VAULT_BACKEND=keyring）。没有可用的后端就按 off 处理，**没有明文后备**
  - 去重：同一个值再贴一次还是同一个编号（带密钥的 HMAC，拿着 vault.db 离线猜不了短密码）
  - 编号自增、删掉不复用：旧记忆里的 #v3 永远不会指向另一把密钥
  - 状态按引用算：有没被遗忘的记忆提到 #v3 → 可取回；只在隔离区里 → 不能取回；哪里都没有 → 真删之后由 gc() 删掉

取回时的闸门（明确请求、人工确认）在 memory_provider；这里提供它们要用的状态：审计与频率、确认码、
直达取回的待发句柄（由配套的 Hermes 插件 nexsandglass_hermes_reveal 在回复发出前换成原文）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from nexsandglass.core.lang import t

logger = logging.getLogger(__name__)

DB_NAME = "vault.db"
_AAD = "nyx-vault-v1|{id}|{kind}"
_NO_VAULT_KINDS = {"private", "truncated"}       # <private> 是「别记」，被截断的不完整：都只隐去
RATE_HOUR, RATE_DAY = 3, 10
PENDING_TTL = 600                                # 直达取回的句柄 / 确认码 10 分钟有效（确认码 5 分钟）
CODE_TTL = 300
LOCK_AFTER, LOCK_SECONDS = 3, 900
GC_MIN_AGE = 120                                 # 刚收进来、日志还没写到的那几毫秒不算孤儿

_SCHEMA = """
CREATE TABLE IF NOT EXISTS secrets (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    kind             TEXT NOT NULL,
    label            TEXT NOT NULL,
    last4            TEXT NOT NULL DEFAULT '',
    context          TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    mac              BLOB NOT NULL UNIQUE,
    backend          TEXT NOT NULL,
    nonce            BLOB,
    ct               BLOB,
    revealed         INTEGER NOT NULL DEFAULT 0,
    last_revealed_at TEXT
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
    ts      TEXT NOT NULL,
    vid     INTEGER,
    action  TEXT NOT NULL,
    path    TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL,
    reason  TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);
"""


class VaultError(Exception):
    """给主人看的原因（不含原文）。"""


# ══════════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════════

def level() -> str:
    v = (os.environ.get("NYX_VAULT") or "off").strip().lower()
    return "on" if v in ("on", "1", "true", "yes") else "off"


def reveal_mode() -> str:
    v = (os.environ.get("NYX_VAULT_REVEAL") or "direct").strip().lower()
    return v if v in ("direct", "cli", "llm") else "direct"


def kinds() -> str:
    v = (os.environ.get("NYX_VAULT_KINDS") or "all").strip().lower()
    return v if v in ("all", "tokens") else "all"


def db_path() -> str:
    from nexsandglass.core import memid
    return os.path.join(os.path.dirname(memid._db_path()), DB_NAME)     # 与 nyx.db 同目录


def key_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "nyx", "vault.key")


_crypto_ok: Dict[str, bool] = {}


def _have_crypto() -> bool:
    """装了能用的 cryptography。装坏了也算没装：系统包里的 cryptography 缺 _cffi_backend 时，
    import 抛的是 pyo3 的 PanicException —— 它继承 BaseException，except Exception 接不住（实测）。"""
    if "v" not in _crypto_ok:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
            from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # noqa: F401
            _crypto_ok["v"] = True
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as e:
            logger.warning("[vault] cryptography 用不了（%s: %s），保险箱的加密文件后端不可用", type(e).__name__, e)
            _crypto_ok["v"] = False
    return _crypto_ok["v"]


# 只认真正的系统钥匙串。keyrings.alt 的 PlaintextKeyring 之类会把「密钥」明文写进家目录，比不存还糟
_KEYRING_OK = ("keyring.backends.macOS", "keyring.backends.Windows", "keyring.backends.SecretService",
               "keyring.backends.libsecret", "keyring.backends.kwallet")


def _keyring():
    try:
        import keyring
        kr = keyring.get_keyring()
    except Exception:
        return None
    chain = list(getattr(kr, "backends", None) or [kr])
    if chain and all(type(b).__module__.startswith(_KEYRING_OK) for b in chain):
        return keyring
    return None


def backend() -> Optional[str]:
    """auto：装了 cryptography 用加密文件，否则用系统钥匙串；都没有返回 None。

    auto 不先选钥匙串：网关多在无桌面的机器上跑，而桌面上后台线程碰钥匙串可能弹授权框、卡住写入。
    """
    want = (os.environ.get("NYX_VAULT_BACKEND") or "auto").strip().lower()
    if want == "keyring":
        return "keyring" if _keyring() is not None else None
    if want == "file":
        return "file" if _have_crypto() else None
    if _have_crypto():
        return "file"
    return "keyring" if _keyring() is not None else None


def unavailable_reason() -> str:
    if level() != "on":
        return t("off (turn it on with NYX_VAULT=on, or vault: on in hermes memory setup)",
                 "没开（NYX_VAULT=on 或 hermes memory setup 里的 vault 才开）")
    if backend() is None:
        want = (os.environ.get("NYX_VAULT_BACKEND") or "auto").strip().lower()
        if want == "keyring":
            return t("no usable system keychain (pip install 'nyx-memory[vault-keyring]'; needs a desktop keychain service)",
                     "没有可用的系统钥匙串（pip install 'nyx-memory[vault-keyring]'，且需要桌面环境的钥匙串服务）")
        return t("no encryption backend: pip install 'nyx-memory[vault]' (installs cryptography)",
                 "没有加密后端：pip install 'nyx-memory[vault]'（装 cryptography）")
    if backend() == "file":
        try:
            _master_key(create=False, missing_ok=True)
        except VaultError as e:
            return str(e)
    return ""


def enabled() -> bool:
    return not unavailable_reason()


# ══════════════════════════════════════════════════════════
# 密钥
# ══════════════════════════════════════════════════════════

def _decode_key(text: str) -> bytes:
    try:
        k = base64.b64decode(text.strip(), validate=True)
    except Exception:
        k = b""
    if len(k) != 32:
        raise VaultError(t("malformed vault key (expected 32 bytes, base64)", "保险箱密钥格式不对（应为 32 字节的 base64）"))
    return k


def _env_key() -> str:
    from nexsandglass.core import credentials
    return credentials.get("NYX_VAULT_KEY")


def _master_key(create: bool, missing_ok: bool = False) -> Optional[bytes]:
    env = _env_key()
    if env:
        return _decode_key(env)
    p = key_path()
    if os.path.exists(p):
        if os.name != "nt" and os.stat(p).st_mode & 0o077:
            raise VaultError(t(f"vault key file {p} is readable by group or others; chmod 600 it first",
                                  f"保险箱密钥文件 {p} 的权限太宽（组或其他人可读）；chmod 600 之后再用"))
        with open(p, "r", encoding="ascii") as f:
            return _decode_key(f.read())
    if not create:
        if missing_ok:
            return None
        raise VaultError(t(f"no vault key on this machine ({p}); bring the key along when you move machines",
                              f"这台机器上没有保险箱密钥（{p}），解不开；换机器时要把密钥一起带过来"))
    os.makedirs(os.path.dirname(p), mode=0o700, exist_ok=True)
    key = secrets.token_bytes(32)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(base64.b64encode(key).decode() + "\n")
    logger.info("[vault] 新建保险箱密钥 %s", p)
    return key


def _subkeys(master: bytes) -> Tuple[bytes, bytes]:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    def derive(info: bytes) -> bytes:
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(master)
    return derive(b"nyx-vault-enc-v1"), derive(b"nyx-vault-mac-v1")


def _service() -> str:
    return "nyx-vault:" + hashlib.sha256(os.path.realpath(db_path()).encode()).hexdigest()[:12]


def _keys(create: bool) -> Tuple[Optional[bytes], bytes, str]:
    """(加密子密钥 | None, 去重子密钥, 后端)。"""
    be = backend()
    if be is None:
        raise VaultError(unavailable_reason())
    if be == "file":
        enc, mac = _subkeys(_master_key(create=create))
        return enc, mac, be
    kr = _keyring()
    m = kr.get_password(_service(), "_mac")
    if not m:
        if not create:
            raise VaultError(t("the system keychain has no entry for this vault", "系统钥匙串里没有这个保险箱的条目"))
        m = secrets.token_hex(32)
        kr.set_password(_service(), "_mac", m)
    return None, bytes.fromhex(m), be


def _mac(mac_key: bytes, value: str) -> bytes:
    return hmac.new(mac_key, value.encode("utf-8"), hashlib.sha256).digest()


def _key_check(mac_key: bytes) -> str:
    return hmac.new(mac_key, b"nyx-vault-key-check", hashlib.sha256).hexdigest()[:24]


# ══════════════════════════════════════════════════════════
# 库
# ══════════════════════════════════════════════════════════

_lock = threading.RLock()


def _setup(c: sqlite3.Connection) -> None:
    c.executescript(_SCHEMA)
    c.commit()


def _open() -> sqlite3.Connection:
    from nexsandglass.core import sqlite_open
    c = sqlite_open.connect(db_path(), setup=_setup)
    try:
        os.chmod(db_path(), 0o600)
    except OSError:
        pass
    return c


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _check_key(c: sqlite3.Connection, mac_key: bytes, write: bool) -> None:
    row = c.execute("SELECT v FROM meta WHERE k='key_check'").fetchone()
    kc = _key_check(mac_key)
    if row is None:
        if write:
            c.execute("INSERT INTO meta(k, v) VALUES('key_check', ?)", (kc,))
        return
    if not hmac.compare_digest(row[0], kc):
        raise VaultError(t("wrong key: this vault was encrypted with a different key", "密钥不对：这个保险箱是用另一把密钥加密的"))


# 按类别认密码，不看标签：标签 v7.16.0 起是英文（password），以前是「密码」（v7.16.0 修：只比标签的话换语言就失效）
_PASSWORD_KINDS = frozenset({"password", "url_password"})


def _last4(kind: str, label: str, value: str) -> str:
    if kind in _PASSWORD_KINDS:
        return ""
    v = re.sub(r"[^A-Za-z0-9]", "", value)
    return v[-4:] if len(v) >= 12 else ""


def wants(kind: str, label: str) -> bool:
    """这一类收不收（不看开没开）。"""
    if kind in _NO_VAULT_KINDS:
        return False
    return not (kinds() == "tokens" and kind in _PASSWORD_KINDS)


def put(kind: str, label: str, value: str, context: str = "") -> Optional[int]:
    """收进保险箱，返回编号；同一个值返回已有编号。没开 / 不收 / 出错返回 None（出错只记日志，不挡写入）。"""
    if not value or not wants(kind, label) or not enabled():
        return None
    try:
        with _lock:
            enc, mac_key, be = _keys(create=True)
            c = _open()
            try:
                _check_key(c, mac_key, write=True)
                m = _mac(mac_key, value)
                row = c.execute("SELECT id FROM secrets WHERE mac=?", (m,)).fetchone()
                if row:
                    c.commit()
                    return int(row[0])
                cur = c.execute(
                    "INSERT INTO secrets(kind, label, last4, context, created_at, mac, backend) VALUES(?,?,?,?,?,?,?)",
                    (kind, label, _last4(kind, label, value), context[:200], _now(), m, be))
                vid = int(cur.lastrowid)
                if be == "file":
                    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
                    nonce = secrets.token_bytes(12)
                    ct = AESGCM(enc).encrypt(nonce, value.encode("utf-8"), _AAD.format(id=vid, kind=kind).encode())
                    c.execute("UPDATE secrets SET nonce=?, ct=? WHERE id=?", (nonce, ct, vid))
                else:
                    _keyring().set_password(_service(), f"v{vid}", value)
                c.commit()
                return vid
            except BaseException:
                c.rollback()
                raise
            finally:
                c.close()
    except Exception as e:
        logger.warning("[vault] 没收进保险箱（只隐去）：%s", e)
        return None


def lookup(value: str) -> Optional[int]:
    """已有的编号（不新增）。给助手回复用：刚取回过的值出现在回复里，占位符也带上编号。"""
    if not value or level() != "on" or not os.path.exists(db_path()):
        return None
    try:
        with _lock:
            _, mac_key, _ = _keys(create=False)
            c = _open()
            try:
                row = c.execute("SELECT id FROM secrets WHERE mac=?", (_mac(mac_key, value),)).fetchone()
                return int(row[0]) if row else None
            finally:
                c.close()
    except Exception:
        return None


class Collector:
    """传给 secret_filter.scrub(vault=...) 的回调：收进保险箱，记下这次新出现的编号，好补上下文。"""

    def __init__(self, only_lookup: bool = False):
        self.only_lookup = only_lookup
        self.ids: List[int] = []

    def __call__(self, kind: str, label: str, value: str) -> Optional[int]:
        vid = lookup(value) if self.only_lookup else put(kind, label, value)
        if vid is not None:
            self.ids.append(vid)
        return vid

    def finish(self, text: str) -> None:
        """每条补一段上下文：隐去之后的那条消息里，占位符前后各 40 个字（列表里认出是哪一把用）。"""
        if self.only_lookup or not self.ids:
            return
        try:
            with _lock:
                c = _open()
                try:
                    for vid in set(self.ids):
                        tag = f"#v{vid}]"
                        i = text.find(tag)
                        if i < 0:
                            continue
                        ctx = text[max(0, i - 60): i + len(tag) + 40].replace("\n", " ").strip()
                        c.execute("UPDATE secrets SET context=? WHERE id=? AND context=''", (ctx, vid))
                    c.commit()
                finally:
                    c.close()
        except Exception as e:
            logger.debug("[vault] 上下文没补上: %s", e)


# ══════════════════════════════════════════════════════════
# 引用 → 状态
# ══════════════════════════════════════════════════════════

def _refs(vid: int) -> str:
    """live：有没被遗忘的记忆提到它；held：只在隔离区里；none：哪里都没有。"""
    from nexsandglass.core import memid
    conn = memid.get_conn()
    needle = f"%#v{vid}]%"
    if conn.execute("SELECT 1 FROM memories WHERE deleted_at IS NULL AND text LIKE ? LIMIT 1",
                    (needle,)).fetchone():
        return "live"
    has_q = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quarantine'").fetchone()
    if has_q and conn.execute("SELECT 1 FROM quarantine WHERE text LIKE ? OR payload LIKE ? LIMIT 1",
                              (needle, needle)).fetchone():
        return "held"
    return "none"


def _state_label(state: str) -> str:
    return {"live": t("available", "可取回"),
            "held": t("source memory is in quarantine (restore it first)", "来源记忆在隔离区（还原后才能取回）"),
            "none": t("source memory no longer exists", "来源记忆已不存在")}[state]


@dataclass
class Entry:
    id: int
    kind: str
    label: str
    last4: str
    context: str
    created_at: str
    revealed: int
    last_revealed_at: Optional[str]
    state: str = "live"

    def public(self) -> dict:
        return {"id": f"v{self.id}", "label": self.label, "tail": (f"…{self.last4}" if self.last4 else ""),
                "saved": self.created_at[:10], "context": self.context, "state": _state_label(self.state),
                "revealed": self.revealed}


def parse_id(v) -> Optional[int]:
    m = re.fullmatch(r"\s*#?[vV]?(\d{1,9})\s*", str(v or ""))
    return int(m.group(1)) if m else None


def entries(query: str = "", with_state: bool = True, vid: Optional[int] = None) -> List[Entry]:
    if not os.path.exists(db_path()):
        return []
    sql = "SELECT id, kind, label, last4, context, created_at, revealed, last_revealed_at FROM secrets"
    with _lock:
        c = _open()
        try:
            rows = (c.execute(sql + " WHERE id=?", (vid,)) if vid is not None else
                    c.execute(sql + " ORDER BY id")).fetchall()
        finally:
            c.close()
    out = [Entry(*r) for r in rows]
    q = (query or "").strip().lower()
    if q:
        out = [e for e in out if any(q in (s or "").lower() for s in (e.label, e.kind, e.context, e.last4))
               or parse_id(q) == e.id]
    if with_state:
        for e in out:
            try:
                e.state = _refs(e.id)
            except Exception:
                e.state = "none"
    return out


def entry(vid: int) -> Optional[Entry]:
    es = entries(vid=vid)
    return es[0] if es else None


def get(vid: int) -> str:
    """原文。调用方负责闸门与审计；这里只管解密。"""
    if not os.path.exists(db_path()):
        raise VaultError(f"保险箱里没有 v{vid}")
    with _lock:
        c = _open()
        try:
            row = c.execute("SELECT kind, backend, nonce, ct FROM secrets WHERE id=?", (vid,)).fetchone()
            if row is None:
                raise VaultError(f"保险箱里没有 v{vid}")
            kind, be, nonce, ct = row
            if be == "file":
                if not _have_crypto():
                    raise VaultError(t("cryptography is not installed: pip install 'nyx-memory[vault]'",
                                       "没有 cryptography，解不开：pip install 'nyx-memory[vault]'"))
                enc, mac_key = _subkeys(_master_key(create=False))
                _check_key(c, mac_key, write=False)
                from cryptography.exceptions import InvalidTag
                from cryptography.hazmat.primitives.ciphers.aead import AESGCM
                try:
                    return AESGCM(enc).decrypt(nonce, ct, _AAD.format(id=vid, kind=kind).encode()).decode("utf-8")
                except InvalidTag:
                    raise VaultError(t(f"v{vid} cannot be decrypted: the ciphertext was changed, or a different key encrypted it",
                                       f"v{vid} 解不开：密文被改过，或者不是这把密钥加密的"))
            kr = _keyring()
            if kr is None:
                raise VaultError(t("this entry lives in the system keychain, which is not available now",
                                   "这一条存在系统钥匙串里，但现在没有可用的钥匙串"))
            v = kr.get_password(_service(), f"v{vid}")
            if v is None:
                raise VaultError(t(f"v{vid} is not in the system keychain", f"系统钥匙串里找不到 v{vid}"))
            return v
        finally:
            c.close()


def mark_revealed(vid: int) -> None:
    with _lock:
        c = _open()
        try:
            c.execute("UPDATE secrets SET revealed=revealed+1, last_revealed_at=? WHERE id=?", (_now(), vid))
            c.commit()
        finally:
            c.close()


def delete(vids: List[int]) -> int:
    if not vids or not os.path.exists(db_path()):
        return 0
    n = 0
    with _lock:
        c = _open()
        try:
            for vid in vids:
                row = c.execute("SELECT backend FROM secrets WHERE id=?", (vid,)).fetchone()
                if row is None:
                    continue
                if row[0] == "keyring" and _keyring() is not None:
                    try:
                        _keyring().delete_password(_service(), f"v{vid}")
                    except Exception as e:
                        logger.warning("[vault] 钥匙串里的 v%s 没删掉: %s", vid, e)
                c.execute("DELETE FROM secrets WHERE id=?", (vid,))
                c.execute("INSERT INTO audit(ts, vid, action, outcome) VALUES(?,?,?,?)", (_now(), vid, "delete", "ok"))
                n += 1
            c.commit()
        finally:
            c.close()
    if n:
        wipe()
    return n


_REF_RX = re.compile(r"#v(\d{1,9})\]")


def refs_in(texts) -> List[int]:
    """这些正文里提到的保险箱编号。"""
    return sorted({int(m.group(1)) for t in texts if t for m in _REF_RX.finditer(str(t))})


def drop_unreferenced(vids: List[int]) -> List[int]:
    """真删一批记忆之后：它们提到的编号里，哪里都不再被提到的，跟着删（不等 GC_MIN_AGE）。"""
    if not vids or not os.path.exists(db_path()):
        return []
    gone = []
    for vid in vids:
        try:
            if _refs(vid) == "none":
                gone.append(vid)
        except Exception as e:
            logger.warning("[vault] v%s 的引用没查成，先不删: %s", vid, e)
    delete(gone)
    return gone


def gc(min_age: int = GC_MIN_AGE) -> List[int]:
    """删掉没有任何记忆（含隔离区）再提到的条目 —— 来源记忆被真删之后跟着真删。"""
    if not os.path.exists(db_path()):
        return []
    cutoff = (datetime.now() - timedelta(seconds=min_age)).isoformat(timespec="seconds")
    gone = [e.id for e in entries() if e.state == "none" and e.created_at <= cutoff]
    delete(gone)
    return gone


def wipe() -> Optional[str]:
    """删掉的密文不留在空闲页里。"""
    if not os.path.exists(db_path()):
        return None
    from nexsandglass.core import sqlite_open
    with _lock:
        return sqlite_open.vacuum(db_path())


def rekey() -> int:
    """换主密钥（file 后端）：全部解开、用新密钥重新加密，再换掉密钥文件。返回条数。"""
    if backend() != "file" or _env_key():
        raise VaultError(t("rekey works only with the encrypted-file backend and a key file",
                             "只有加密文件后端、且密钥在密钥文件里时才能 rekey"))
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    with _lock:
        old_enc, old_mac = _subkeys(_master_key(create=False))
        new = secrets.token_bytes(32)
        new_enc, new_mac = _subkeys(new)
        c = _open()
        try:
            _check_key(c, old_mac, write=False)
            rows = c.execute("SELECT id, kind, nonce, ct FROM secrets WHERE backend='file'").fetchall()
            for vid, kind, nonce, ct in rows:
                aad = _AAD.format(id=vid, kind=kind).encode()
                value = AESGCM(old_enc).decrypt(nonce, ct, aad)
                n2 = secrets.token_bytes(12)
                c.execute("UPDATE secrets SET nonce=?, ct=?, mac=? WHERE id=?",
                          (n2, AESGCM(new_enc).encrypt(n2, value, aad), _mac(new_mac, value.decode("utf-8")), vid))
            c.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('key_check', ?)", (_key_check(new_mac),))
            p = key_path()
            tmp = p + ".new"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as f:
                f.write(base64.b64encode(new).decode() + "\n")
            c.commit()
            os.replace(tmp, p)
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()
    wipe()
    return len(rows)


def _hermes_plugin_enabled() -> Optional[bool]:
    """$HERMES_HOME/config.yaml 的 plugins.enabled 里有没有 nyx-reveal（读不到返回 None）。只看文本，不依赖 PyYAML。"""
    home = os.environ.get("HERMES_HOME") or os.path.join(os.path.expanduser("~"), ".hermes")
    p = os.path.join(home, "config.yaml")
    try:
        with open(p, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    return bool(re.search(r"^\s*-\s*['\"]?nyx-reveal['\"]?\s*$|[\[,]\s*['\"]?nyx-reveal['\"]?\s*[\],]", text, re.M))


def health() -> dict:
    """doctor / nyx_health 的一项。没开 = ok；开了但用不了、或有该删没删的孤儿 = 不 ok。"""
    if level() != "on":
        return {"ok": True, "state": "off"}
    why = unavailable_reason()
    if why:
        return {"ok": False, "state": "unavailable", "reason": why}
    out = {"ok": True, "state": "on", "backend": backend(), "reveal": reveal_mode()}
    try:
        es = entries()
    except Exception as e:
        return {**out, "ok": False, "error": str(e)}
    cutoff = (datetime.now() - timedelta(seconds=GC_MIN_AGE)).isoformat(timespec="seconds")
    stale = [e.id for e in es if e.state == "none" and e.created_at <= cutoff]
    out.update(entries=len(es), held=sum(1 for e in es if e.state == "held"), orphans=len(stale))
    if stale:
        from nexsandglass.core import hints
        out.update(ok=False, problems=[t(f"{len(stale)} entries outlived their erased source memories: {hints.cmd('vault', 'gc')}",
                                              f"{len(stale)} 条的来源记忆已被真删，保险箱里还留着：{hints.cmd('vault', 'gc')}")])
    if out["reveal"] == "direct":
        out["hermes_plugin"] = {True: t("enabled", "已启用"),
                                False: t("not enabled (hermes plugins enable nyx-reveal)", "没启用（hermes plugins enable nyx-reveal）"),
                                None: t("unknown (no Hermes config found)", "不知道（没找到 Hermes 配置）")}[_hermes_plugin_enabled()]
    return out


def status() -> dict:
    be = backend()
    out = {"level": level(), "backend": be, "reveal": reveal_mode(), "kinds": kinds(), "db": db_path(),
           "enabled": enabled(), "reason": unavailable_reason(), "hook": hook_registered()}
    if be == "file":
        p = key_path()
        env = _env_key()
        out["key"] = "NYX_VAULT_KEY" if env else p
        if not env and os.path.exists(p) and os.name != "nt":
            out["key_mode"] = oct(os.stat(p).st_mode & 0o777)
    try:
        es = entries()
        out["entries"] = len(es)
        out["orphans"] = sum(1 for e in es if e.state == "none")
        out["held"] = sum(1 for e in es if e.state == "held")
    except Exception as e:
        out["entries_error"] = str(e)
    return out


# ══════════════════════════════════════════════════════════
# 审计 / 频率
# ══════════════════════════════════════════════════════════

def audit(action: str, vid: Optional[int], outcome: str, reason: str = "", path: str = "", platform: str = "") -> None:
    """只记发生了什么；永远不记原文。"""
    try:
        with _lock:
            c = _open()
            try:
                c.execute("INSERT INTO audit(ts, vid, action, path, outcome, reason, platform) VALUES(?,?,?,?,?,?,?)",
                          (_now(), vid, action, path, outcome, reason[:200], platform))
                c.commit()
            finally:
                c.close()
    except Exception as e:
        logger.warning("[vault] 审计没写上: %s", e)


def audit_rows(limit: int = 50) -> List[dict]:
    if not os.path.exists(db_path()):
        return []
    with _lock:
        c = _open()
        try:
            rows = c.execute("SELECT ts, vid, action, path, outcome, reason, platform FROM audit "
                             "ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        finally:
            c.close()
    keys = ("ts", "vid", "action", "path", "outcome", "reason", "platform")
    return [dict(zip(keys, r)) for r in rows]


def rate_ok() -> Tuple[bool, str]:
    """经工具取回：每小时 RATE_HOUR 次、每天 RATE_DAY 次（命令行不算）。"""
    with _lock:
        c = _open()
        try:
            def n(sec):
                since = (datetime.now() - timedelta(seconds=sec)).isoformat(timespec="seconds")
                return c.execute("SELECT COUNT(*) FROM audit WHERE action='reveal' AND outcome='ok' AND path!='cli' "
                                 "AND ts>=?", (since,)).fetchone()[0]
            if n(3600) >= RATE_HOUR:
                return False, t(f"already revealed {RATE_HOUR} times in the last hour; try later, or use the terminal command",
                                 f"一小时内已经取回 {RATE_HOUR} 次；稍后再试，或在终端用命令行取")
            if n(86400) >= RATE_DAY:
                return False, t(f"already revealed {RATE_DAY} times today; try tomorrow, or use the terminal command",
                                 f"今天已经取回 {RATE_DAY} 次；明天再试，或在终端用命令行取")
            return True, ""
        finally:
            c.close()


# ══════════════════════════════════════════════════════════
# 直达取回：句柄 / 待发 / 确认码（只在本进程内存里）
# ══════════════════════════════════════════════════════════

@dataclass
class _Pending:
    vid: int
    label: str
    saved: str
    value: str
    expires: float


@dataclass
class _Session:
    pending: List[_Pending] = field(default_factory=list)
    notices: List[Tuple[str, float]] = field(default_factory=list)
    code: Optional[Tuple[int, str, float]] = None           # (vid, 6 位码, 过期时间)
    confirmed: Dict[int, int] = field(default_factory=dict)  # vid → 第几轮确认的
    failures: int = 0
    locked_until: float = 0.0


_sessions: Dict[str, _Session] = {}
_revealed: Dict[str, Tuple[int, str, float]] = {}            # 原文 → (编号, 类别, 过期)：取回过的值再出现时也隐去
_hook = {"registered": False}


def _sess(session_id: str) -> _Session:
    s = _sessions.get(session_id or "")
    if s is None:
        s = _sessions[session_id or ""] = _Session()
    return s


def mark_hook_registered() -> None:
    """配套 Hermes 插件加载时调用：之后直达取回才可用。"""
    _hook["registered"] = True


def hook_registered() -> bool:
    return _hook["registered"]


def handle(vid: int) -> str:
    return f"⟦nyx:v{vid}⟧"


def remember_revealed(vid: int, label: str, value: str) -> None:
    with _lock:
        _revealed[value] = (vid, label, time.time() + 3600)


def redact_revealed(text: Optional[str]) -> str:
    """取回过的原文再出现（模型复述、主人贴回来）时，不管认不认得出格式，都换成带编号的占位符。

    直达取回时钩子把原文追加在回复末尾，Hermes 再把改写后的回复交给 sync_turn ——
    「密码）：hunter2」这种写法过滤器的规则认不出来，只能按字面找。
    """
    if not text or not _revealed:
        return text or ""
    from nexsandglass.core import secret_filter as _secret_filter
    now = time.time()
    with _lock:
        for v, (vid, label, exp) in list(_revealed.items()):
            if exp < now:
                _revealed.pop(v, None)
            elif v in text:
                text = text.replace(v, f"{_secret_filter.MARK}{label} #v{vid}]")
    return text


def queue_reveal(session_id: str, e: Entry, value: str) -> str:
    with _lock:
        s = _sess(session_id)
        s.pending = [p for p in s.pending if p.vid != e.id and p.expires > time.time()]
        s.pending.append(_Pending(e.id, e.label, e.created_at[:10], value, time.time() + PENDING_TTL))
    return handle(e.id)


def queue_notice(session_id: str, text: str) -> None:
    with _lock:
        _sess(session_id).notices.append((text, time.time() + PENDING_TTL))


def has_pending(session_id: str) -> bool:
    s = _sessions.get(session_id or "")
    return bool(s and (s.pending or s.notices))


def _fmt(value: str) -> str:
    if "\n" in value or "`" in value:
        return "\n```\n" + value + "\n```\n"
    return f"`{value}`"


def render(session_id: str, text: str) -> Optional[str]:
    """回复发给主人之前：把句柄换成原文；模型没写句柄就追加在末尾；再追加 nyx 自己的提示（确认码）。

    只处理这个会话的；用一次就清掉。没有要换的返回 None（Hermes：返回 None = 不改写）。
    """
    with _lock:
        s = _sessions.get(session_id or "")
        if not s:
            return None
        now = time.time()
        pend = [p for p in s.pending if p.expires > now]
        notes = [t for t, exp in s.notices if exp > now]
        s.pending, s.notices = [], []
    if not pend and not notes:
        return None
    out = text or ""
    tail = []
    for p in pend:
        h = handle(p.vid)
        if h in out:
            out = out.replace(h, _fmt(p.value))
        else:
            tail.append(t(f"🔑 v{p.vid} ({p.label}, saved {p.saved}): {_fmt(p.value)}",
                          f"🔑 v{p.vid}（{p.label}，{p.saved} 存）：{_fmt(p.value)}"))
    out = re.sub(r"⟦nyx:v\d+⟧", t("(handle expired)", "（句柄已过期）"), out)
    tail.extend(notes)
    if tail:
        out = out.rstrip() + "\n\n" + "\n".join(tail)
    return out


# 英文 confirm v3 123456（v7.16.0）；中文「确认 v3 123456」照样认
_CONFIRM_RX = re.compile(r"^\s*(?:confirm|确认)\s*#?[vV](\d{1,9})\s+(\d{6})\s*[。.!！]?\s*$", re.I)


def new_code(session_id: str, vid: int) -> str:
    code = f"{secrets.randbelow(10 ** 6):06d}"
    with _lock:
        _sess(session_id).code = (vid, code, time.time() + CODE_TTL)
    return code


def confirm_phrase(vid: int, code: str) -> str:
    return t(f"confirm v{vid} {code}", f"确认 v{vid} {code}")


def check_confirm(session_id: str, message: str, turn: int) -> Optional[str]:
    """主人这一轮的原话是不是确认短语。对了 → 这一轮放行一次；错了记一次，连错 LOCK_AFTER 次锁 LOCK_SECONDS 秒。

    返回给 agent 的说明（None = 这条消息与确认无关）。
    """
    m = _CONFIRM_RX.match(message or "")
    if not m:
        return None
    vid, code = int(m.group(1)), m.group(2)
    with _lock:
        s = _sess(session_id)
        now = time.time()
        if s.locked_until > now:
            return "locked"
        if s.code and s.code[0] == vid and s.code[2] > now and hmac.compare_digest(s.code[1], code):
            s.code = None
            s.confirmed[vid] = turn
            s.failures = 0
            return "ok"
        s.failures += 1
        if s.failures >= LOCK_AFTER:
            s.locked_until = now + LOCK_SECONDS
            s.failures = 0
            s.code = None
    audit("confirm", vid, "wrong_code")
    return "wrong"


def confirmed(session_id: str, vid: int, turn: int) -> bool:
    with _lock:
        s = _sessions.get(session_id or "")
        return bool(s and s.confirmed.get(vid) == turn)


def consume_confirmation(session_id: str, vid: int) -> None:
    with _lock:
        s = _sessions.get(session_id or "")
        if s:
            s.confirmed.pop(vid, None)


def locked(session_id: str) -> bool:
    s = _sessions.get(session_id or "")
    return bool(s and s.locked_until > time.time())


def _reset_for_tests() -> None:
    with _lock:
        _sessions.clear()
        _revealed.clear()
        _hook["registered"] = False
