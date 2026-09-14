"""DejaVu —— 把「检索失败」当成一类信号来建模。

问题
----
常规检索（BM25 / 向量）只回答「找到什么」：
  - 没发生过 → 返回空
  - 发生过但没找到 → **也返回空**

两者输出一样，调用方无从区分。于是 agent 只能说「我没有相关记忆」，
而用户确信聊过——这是 recall 系统的一类系统性失败。

做法
----
把「是否接触过」单独建模成一层廉价信号：

    Veil (Bloom, 128 KiB)   —— 这个词我见过吗？（无假阴性，有假阳性）
    Mist (SQLite)           —— 见过的话，在哪、几次、什么时候？（可寻回）

``sense()`` 不返回记忆，只返回**熟悉度**。调用方据此决定：
是回「没聊过」，还是回「好像聊过，让我再找找」。

它不替代检索
------------
Déjà Vu 不降低 miss 率、不提高召回质量。它只把 miss 分成两类：
「确实没有」和「有过但我找不到」。后者才值得再花力气。

零外部依赖：stdlib + sqlite3。
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Iterable, List, Optional, Sequence, Union

from .mist import Mist
from .types import FamiliarityResult, GazeReport, HuntResult, Phantom
from .veil import DEFAULT_BITS, DEFAULT_HASHES, Veil

logger = logging.getLogger(__name__)

VEIL_FILE = "veil.bin"
MIST_FILE = "mist.db"

# ── 分词 / 实体嗅探 ────────────────────────────────────────
# 中英混排场景下的保守策略：取「明显的实体」而非做词性分析。
# 过度分词（把每个字都当 token）会让 Veil 迅速饱和、假阳性飙升。
_ENTITY_PATTERN = re.compile(
    r"([A-Z][a-z]+(?:[-\s][A-Z][a-z]+)+)|"   # 英文专名 John Smith / New-York
    r"\"([^\"]+)\"|"                          # 双引号内的词
    r"'([^']+)'"                              # 单引号内的词
)
_TOKEN_PATTERN = re.compile(r"[a-zA-Z][\w.-]{2,}")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")

# ⚠️ 已知边界：**纯数字不被索引**。
# "_TOKEN_PATTERN" 要求首字符为英文字母，中文 n-gram 也不含数字。
# 因此形如 42 / 19289 / 20260913 的 token 对 Veil 完全不可见 ——
# 若你的关键信息是订单号、金额、日期，需要自行扩展本模式
# （例如 r"[a-zA-Z0-9][\w.-]{2,}"，代价是 Veil 饱和更快）。
# 详见 docs/dejavu.md「它不是什么」第 4 条。

MIN_TOKEN_LEN = 3
MAX_TOKEN_LEN = 40

# 中文 n-gram 长度。用固定边界切分，保证同一个词在不同上下文里
# 产生**一致的** token —— 否则 "川菜馆" 会随上下文切成 "了川菜馆"
# 或 "菜馆"，imprint 与 sense 永远对不上（这正是旧实现的缺陷）。
CJK_NGRAM = 2


def _cjk_ngrams(run: str) -> set:
    """把一个连续中文字串切成固定长度的 n-gram 集合。

    固定边界（而非滑动窗口）是关键：同一个词只在落入 n-gram 边界时
    才会被切出来，因而在不同句子里产生相同的 token。
    为保证「所有中文片段都能被切到」，用两种对齐方式各切一遍：
    从第 0 字起、从第 1 字起（覆盖奇偶偏移）。

    整串（≤4 字）额外保留，让「川菜馆」这种短词能整体命中，
    而不必依赖它恰好在某个 n-gram 边界上。
    """
    out = set()
    n = CJK_NGRAM
    if len(run) < n:
        return out
    for offset in (0, 1):
        for i in range(offset, len(run) - n + 1, n):
            out.add(run[i:i + n])
    if n <= len(run) <= 4:
        out.add(run)
    return out


def scent(text: str) -> List[str]:
    """从文本中嗅出实体与 token（小写归一化、去重）。

    返回的是一组「值得记住是否见过」的词。调用方可以替换这套规则，
    只要保证 imprint 与 sense 用同一套。
    """
    if not text:
        return []
    tokens = set()
    # 英文专名 / 引号内容（原样保留）
    for m in _ENTITY_PATTERN.finditer(text):
        t = (m.group(0) or "").strip()
        if len(t) >= 2:
            tokens.add(t.lower())
    # 英文 token
    for m in _TOKEN_PATTERN.finditer(text):
        t = m.group(0).lower()
        if MIN_TOKEN_LEN <= len(t) <= MAX_TOKEN_LEN:
            tokens.add(t)
    # 中文：按固定 n-gram 切，保证跨上下文一致
    for m in _CJK_RUN.finditer(text):
        tokens.update(_cjk_ngrams(m.group(0)))
    return [t for t in tokens if t]


class DejaVu:
    """「感觉聊过但搜不到」的感知层。

    参数
    ----
    storage_dir : 数据目录（显式传入，不读环境变量、不假设任何路径）
    bits        : Veil 位图大小，默认 1<<20（128 KiB）
    hashes      : Veil 哈希个数，默认 7
    autoload    : 构造时自动从磁盘恢复（默认 True）
    autosave    : 每 N 次 imprint 自动持久化 Veil（默认 100；0 = 关闭）

    示例
    ----
    >>> dv = DejaVu("./mem")
    >>> dv.imprint("msg-1", "上周和张老板聊了川菜馆")
    >>> r = dv.sense("那家川菜馆")
    >>> r.familiar
    True
    """

    def __init__(
        self,
        storage_dir: Union[str, os.PathLike],
        *,
        bits: int = DEFAULT_BITS,
        hashes: int = DEFAULT_HASHES,
        autoload: bool = True,
        autosave: int = 100,
    ):
        if not storage_dir:
            raise ValueError("storage_dir is required")
        self.storage_dir = os.path.abspath(os.fspath(storage_dir))
        os.makedirs(self.storage_dir, exist_ok=True)

        self._veil_path = os.path.join(self.storage_dir, VEIL_FILE)
        self._mist_path = os.path.join(self.storage_dir, MIST_FILE)

        self._veil = Veil(bits=bits, hashes=hashes)
        self._mist = Mist(self._mist_path)
        self._autosave = max(0, int(autosave))
        self._since_save = 0

        if autoload:
            self._veil.load(self._veil_path)

    # ── 写入 ────────────────────────────────────────────────
    def imprint(self, key: str, text: str, ts: Optional[str] = None) -> int:
        """留下印记：Veil 置位 + Mist 记痕。

        参数
        ----
        key  : 调用方自定义的引用（消息 ID / 行号 / URL / 文件名……），
               Déjà Vu 只存不解释
        text : 要嗅探的文本
        ts   : 时间戳（默认取当前时间）

        返回被铭刻的 token 数量（0 表示没嗅出任何东西）。
        """
        tokens = scent(text)
        if not tokens:
            return 0
        moment = ts or datetime.now().isoformat(sep=" ", timespec="seconds")
        snippet = (text or "").strip()[:80]
        for t in tokens:
            self._veil.touch(t)
        try:
            # 一次事务写完全部 token，末尾统一提交。
            # 逐条 commit 会让每条记忆产生 ~16 次 fsync：
            # 实测 4.29 ms/条(逐条) vs 0.33 ms/条(合并) —— 13 倍差距。
            self._mist.haunt_many(tokens, moment, str(key) if key else "",
                                  snippet, commit=False)
            self._mist.commit()
        except Exception:
            logger.warning("dejavu: haunt failed", exc_info=True)

        self._since_save += 1
        if self._autosave and self._since_save >= self._autosave:
            self.persist()
        return len(tokens)

    # ── 感知 ────────────────────────────────────────────────
    def sense(self, text: str) -> FamiliarityResult:
        """感知——这段文本里有熟悉的气息吗？

        不返回记忆，只回答「见过没有」和「有多少比例见过」。
        """
        tokens = scent(text)
        if not tokens:
            return FamiliarityResult(
                familiar=False, score=0.0, phantoms=[], unknown=[],
                total=0, reason="没有可嗅探的 token",
            )
        known = [t for t in tokens if self._veil.probe(t)]
        unknown = [t for t in tokens if t not in known]
        ratio = len(known) / len(tokens)
        if not known:
            reason = f"{len(tokens)} 个 token 全部未见"
        elif not unknown:
            reason = f"{len(tokens)} 个 token 全部见过"
        else:
            reason = f"{len(known)}/{len(tokens)} 个 token 见过"
        return FamiliarityResult(
            familiar=bool(known),
            score=round(ratio, 4),
            phantoms=known[:10],
            unknown=unknown[:10],
            total=len(tokens),
            reason=reason,
        )

    # ── 寻回 ────────────────────────────────────────────────
    def hunt(self, query: str, limit: int = 5) -> List[Phantom]:
        """寻回——深入迷雾，把幽灵钓出来。

        **能力边界（实测）**

        命中的前提是：查询中的 token 与已记录的 phantom token 存在
        **字面重合**。因此：

        ================  ========  ====================================
        场景              能否寻回  说明
        ================  ========  ====================================
        用词一致          能        「川菜馆」→「川菜馆」
        部分字面重合      能        「赵顾问」→「顾问」
        实体改写          通常不能  「张老板」→「张总」（仅首字相同）
        纯同义表述        不能      「川菜馆」→「麻辣的店」（零共同字）
        跨语言            不能      「budget」→「预算」
        ================  ========  ====================================

        后三种需要语义嵌入模型，而本模块的设计契约是**零外部依赖**，
        两者不可兼得。实测 4 个语义改写场景中 hunt 寻回 0 个，
        但 ``sense()`` 正确判定 3 个为「熟悉」——**分类有效，寻回有限**。

        若需要语义寻回，请在 ``imprint`` 时把实体别名一并写入文本，
        或在上层接入向量检索。

        返回 ``Phantom`` 列表（可能为空：熟悉但确实找不回细节，
        这正是「感觉聊过但想不起来」的状态）。
        """
        s = self.sense(query)
        if not s.familiar:
            return []
        tokens = scent(query)
        if not tokens:
            return []

        # 优先用「确实见过」的 token 去查，减少 Bloom 假阳性带来的空转
        candidates = [t for t in tokens if t in s.phantoms] or tokens[:3]

        # ── 性能关键：只做精确匹配，逐 token 的子串降级代价极高 ──
        # 实测（23 万 phantom）：精确匹配 0.02-0.1 ms，子串扫描 30-105 ms。
        # 逐 token 降级时，只要有 1 个未命中就吃掉整个 hunt 的耗时
        # （hunt 从 53 ms 恶化到 617 ms 的实测根因）。
        # 因此这里全部走精确匹配；整体无结果时，用**整句**降级一次。
        gathered = {}
        for token in candidates:
            p = self._mist.stalk_exact(token)
            if p is not None:
                gathered[token] = p
        phantoms = sorted(
            gathered.values(),
            key=lambda p: (p.sightings, p.last_spotted),
            reverse=True,
        )[:limit]
        if phantoms:
            return phantoms

        # 熟悉但精确匹配不到：按整句子串降级一次（只此一次）
        return self._mist.stalk(query, limit)

    def hunt_detailed(self, query: str, limit: int = 5) -> HuntResult:
        """``hunt()`` 的带置信度版本（MCP / 日志用）。"""
        s = self.sense(query)
        if not s.familiar:
            return HuntResult(hunted=False, conviction=0.0, phantoms=[],
                              reason="不熟悉，未发起寻回")
        phantoms = self.hunt(query, limit=limit)
        if not phantoms:
            return HuntResult(hunted=True,
                              conviction=round(s.score * 0.3, 4),
                              phantoms=[],
                              reason="熟悉但未寻回具体痕迹")
        top = max(p.sightings for p in phantoms)
        conviction = round(s.score * 0.6 + min(1.0, top / 20) * 0.4, 4)
        return HuntResult(hunted=True, conviction=conviction, phantoms=phantoms,
                          reason=f"寻回 {len(phantoms)} 道痕迹")

    # ── 统计 ────────────────────────────────────────────────
    def gaze(self) -> GazeReport:
        vs = self._veil.stats()
        census = self._mist.census()
        return GazeReport(
            veil_items=vs["items"],
            veil_bits=vs["bits"],
            veil_hashes=vs["hashes"],
            veil_fill_ratio=vs["fill_ratio"],
            mist_total=census["total"],
            restless=census["restless"],
            storage_dir=self.storage_dir,
        )

    # ── 持久化与维护 ────────────────────────────────────────
    def persist(self) -> None:
        """持久化 Veil（Mist 每次写入即提交，无需额外动作）。"""
        try:
            self._veil.persist(self._veil_path)
            self._since_save = 0
        except Exception:
            logger.warning("dejavu: persist failed", exc_info=True)

    def reindex(self) -> int:
        """从 Mist 重建 Veil。返回重建的 token 数。

        用途：位图损坏、扩容后重灌、或疑心两者不一致时。
        """
        self._veil.reset()
        rows = self._mist.all_entries()
        for token, _moment, _key, _snippet in rows:
            if token:
                self._veil.touch(token)
        self.persist()
        return len(rows)

    def forget(self, key_or_token: str) -> int:
        """删除 token。返回删除的 phantom 行数。

        注意：Veil 是位图，无法单独清除一位（清位会影响其他 token）。
        所以 forget 只清 Mist——该 token 仍可能被 sense() 判为熟悉，
        但 hunt() 已经找不回内容。要彻底清空请用 ``reindex()``。
        """
        return self._mist.forget(key_or_token)

    def cleanup(self, days: int = 90) -> int:
        """清理 ``days`` 天未出现的鬼影。返回删除行数。"""
        n = self._mist.cleanup(days)
        if n:
            logger.info("dejavu: cleanup removed %s stale phantoms", n)
        return n

    def close(self) -> None:
        self.persist()
        self._mist.close()

    # ── 上下文管理器 ────────────────────────────────────────
    def __enter__(self) -> "DejaVu":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── 属性 ────────────────────────────────────────────────
    @property
    def veil(self) -> Veil:
        return self._veil

    @property
    def mist(self) -> Mist:
        return self._mist

    def __repr__(self) -> str:
        return (f"DejaVu(storage_dir={self.storage_dir!r}, "
                f"veil_items={self._veil.density}, "
                f"mist_total={self._mist.census()['total']})")
