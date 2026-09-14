"""
NexSandglass SearchRouter V2.8.6 — 四路并发搜索架构（统一入口）
==================================================================
影子沙 + FTS5 + IDX + TF-IDF 四路并发 → 沙子密度融合(trust+simhash) → mmap兜底

V2.8.6: 统一搜索入口 — search_semantic 委托 SearchRouter
       density×trust+simhash_bonus 统一公式
       SimHash 统一为 l3_search_core 128-bit
       密度计算与IDX/TF-IDF同源(_query_tokens)
       删除重复 _simhash / _simhash_density_decay
"""
import concurrent.futures
import math
import mmap
import os

from nexsandglass.features.sandglass_vault import _SANDGLASS, _parse_line, _query_tokens
from nexsandglass.l3.l3_search_core import simhash as _l3_simhash


def simhash_rerank(candidates, query) -> list:
    """⚠ 已从检索管线中移除，仅为兼容保留。理由见 SearchRouter.search 内注释。

    不要重新接回管线：它会用汉明距离覆盖 density×trust 排序。
    """
    q_fp = _l3_simhash(query)
    if q_fp == -1:
        return candidates
    def hamming(item):
        text = item[2] if len(item) > 2 else ""
        fp = _l3_simhash(text[:500])
        if fp == -1: return 999
        return bin(fp ^ q_fp).count('1')
    return sorted(candidates, key=hamming)


def sand_density(candidates, query_tokens, query) -> list:
    q_fp = _l3_simhash(query)
    if q_fp == -1:
        q_fp = 0
    trust_scores = {}
    try:
        from nexsandglass.features.shadow_sand import shadow_boost
        line_nums = {c[0] for c in candidates if len(c) > 0}
        boosted = shadow_boost(line_nums, limit=len(candidates))
        trust_scores = {ln: score for score, ln in boosted}
    except Exception:
        pass
    scored = []
    nq = max(len(query_tokens), 1)
    for item in candidates:
        ln = item[0]
        text = item[2] if len(item) > 2 else ""
        # 直接做子串判断，不要对整条记忆重新分词。
        # _query_tokens(text) 产出的就是 text 的 n-gram 集合，求交集等价于
        # 「这个 query token 是否出现在 text 里」—— 但前者要把几 KB 正文
        # 全量切成 2/3/4 字窗口。Step 3 之后候选从"单行"变成"整条记忆"，
        # 这一步的成本放大了近一个数量级，实测合并排序占了单次查询 2.5s 中的 1.9s。
        low = text.lower()
        matched = sum(1 for t in query_tokens if t in low)
        density = matched / nq
        trust = trust_scores.get(ln, 0.5)
        fp = _l3_simhash(text[:500])
        if fp == -1:
            sim_bonus = 0
        else:
            dist = bin(q_fp ^ fp).count('1')
            sim_bonus = min(1.0 / (1 + dist / 128), 0.5)
        final = density * trust + sim_bonus
        scored.append((final, item))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored]


def dynamic_expand(candidates, tokens, limit: int) -> list:
    if len(candidates) >= limit:
        return candidates[:limit]
    expanded = candidates[:]
    seen = {c[0] if len(c) > 0 else 0 for c in expanded}
    low_tokens = [t.lower() for t in tokens]
    for item in candidates[limit:]:
        low = (item[2] if len(item) > 2 else "").lower()
        if any(t in low for t in low_tokens):
            if item[0] not in seen:
                expanded.append(item)
                seen.add(item[0])
                if len(expanded) >= limit * 2:
                    break
    return expanded[:limit * 2]


class ShadowSearch:
    def __init__(self, sandfile=None):
        self.sandfile = sandfile or _SANDGLASS
    def search(self, query: str, limit: int = 10) -> list:
        try:
            from nexsandglass.features.shadow_sand import shadow_search
            return shadow_search(query, limit)
        except Exception:
            return []


class Fts5Search:
    def search(self, query: str, limit: int = 10) -> list:
        try:
            from nexsandglass.core.sandglass_sqlite import search as fts5_search, sync_incremental
            sync_incremental()
            return fts5_search(query, limit)
        except Exception:
            return []


class IdxSearch:
    """IDX倒排索引搜索—中文子串+英文模糊。独立可测。

    倒排现在把 token 映射到**记录首行号**，正文经 sandglass_sqlite.get_records
    取整条记忆 —— 不再 open(file).readlines()[ln-1] 只拿首行。
    """
    def __init__(self, sandfile=None, idx_path=None):
        self.sandfile = sandfile
        self.idx_path = idx_path

    def search(self, query: str, limit: int = 30) -> list:
        try:
            from nexsandglass.features.sandglass_vault import _sync_index, _query_tokens, rebuild_index
            from nexsandglass.core.sandglass_sqlite import get_records, sync_incremental
            idx = _sync_index()
            if not idx:
                rebuild_index()
                idx = _sync_index()
            if not idx: return []
            tokens = _query_tokens(query)
            from nexsandglass.core.sandglass_sqlite import count as _cnt
            candidates = {}
            for token, postings in _usable_postings(idx, tokens, max(_cnt(), 1)):
                for ln in postings:
                    candidates[ln] = candidates.get(ln, 0) + 1
            if not candidates: return []
            sync_incremental()
            # 先按命中 token 数排序截断，再取记录正文。
            # 高频 n-gram 会命中几千条，之前是全部取出整条正文再截断 —— 纯浪费。
            top = sorted(candidates.items(), key=lambda kv: kv[1], reverse=True)[:limit]
            recs = get_records([ln for ln, _ in top])
            return [(ln, recs[ln][0], recs[ln][2]) for ln, _ in top if ln in recs]
        except Exception:
            return []


# 高 DF 剪枝阈值：出现在超过这个比例的文档里的 token，IDF≈0，
# 对排序没有贡献，却会把候选集撑到几千条。小语料不剪（下限 50 篇）。
_HIGH_DF_RATIO = 0.30
_HIGH_DF_FLOOR = 50


def _usable_postings(idx: dict, tokens, n_docs: int):
    """(token, postings) 列表，已剔除高 DF 的噪音 n-gram。"""
    cap = max(_HIGH_DF_FLOOR, int(n_docs * _HIGH_DF_RATIO))
    out = [(t, idx[t]) for t in tokens if t in idx and len(idx[t]) <= cap]
    if not out:      # 全被剪掉（query 全是高频片段）→ 退回不剪
        out = [(t, idx[t]) for t in tokens if t in idx]
    return out


class TfidfSearch:
    """TF-IDF 检索。文档单位是**一条记忆**。

    不做全表扫描：候选和 DF 都从倒排索引取，只对入围的少数记录读正文算 TF。
    旧实现把全部 8852 条记忆读进内存逐条打分，其中绝大多数 score=0 会被丢弃 ——
    纯浪费，而且 Step 3 之后正文变成整条记忆，成本又放大了一个数量级。
    """
    def __init__(self, sandfile=None):
        self.sandfile = sandfile or _SANDGLASS

    def search(self, query: str, limit: int = 30) -> list:
        try:
            from nexsandglass.features.sandglass_vault import _query_tokens, _sync_index, rebuild_index
            from nexsandglass.core.sandglass_sqlite import get_records, count, sync_incremental
            tokens = _query_tokens(query)
            if not tokens: return []
            sync_incremental()
            idx = _sync_index()
            if not idx:
                rebuild_index(); idx = _sync_index()
            if not idx: return []
            N = max(count(), 1)

            # 一阶段：用 IDF 之和粗选候选
            rough = {}
            for t, postings in _usable_postings(idx, tokens, N):
                idf = math.log((N + 1) / (len(postings) + 1))
                for ln in postings:
                    rough[ln] = rough.get(ln, 0.0) + idf
            if not rough: return []
            short = sorted(rough.items(), key=lambda kv: kv[1], reverse=True)[:max(limit * 3, 30)]

            # 二阶段：只对入围的读正文，算真正的 tf×idf
            recs = get_records([ln for ln, _ in short])
            df = {t: len(p) for t, p in _usable_postings(idx, tokens, N)}
            scored = []
            for ln, _ in short:
                if ln not in recs: continue
                ts, _sender, text = recs[ln]
                low = text.lower()
                score = 0.0
                for t in tokens:
                    c = low.count(t)
                    if c:
                        score += (c / max(len(text), 1)) * math.log((N + 1) / (df.get(t, 0) + 1))
                if score > 0:
                    scored.append((score, ln, ts, text))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [(ln, ts, text) for _, ln, ts, text in scored[:limit]]
        except Exception:
            return []


class MmapFallback:
    def __init__(self, sandfile=None):
        self.sandfile = sandfile or _SANDGLASS
    def search(self, query: str, limit: int = 10) -> list:
        results = []
        results_token = []
        try:
            from nexsandglass.features.sandglass_vault import _query_tokens
            tokens = _query_tokens(query)
            has_tokens = bool(tokens)
            with open(self.sandfile, "rb") as f:
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    ln = 0
                    for line in iter(mm.readline, b""):
                        ln += 1
                        try:
                            decoded = line.decode("utf-8", errors="ignore").strip()
                            if " | " not in decoded: continue
                            parts = decoded.split(" | ", 2)
                            if len(parts) < 3: continue
                            ts, sender, text = parts
                            # mmap 直接扫原始日志，绕过所有索引层的过滤 ——
                            # 已抹除的记录必须在这里也挡住，否则它是最后一个漏点。
                            if text.lstrip().startswith("[REDACTED"):
                                continue
                            if query.lower() in text.lower():
                                results.append((ln, ts, text[:300]))
                                if len(results) >= limit: break
                            elif has_tokens and any(tk in text.lower() for tk in tokens):
                                if len(results_token) < limit:
                                    results_token.append((ln, ts, text[:300]))
                        except: pass
            if not results and results_token:
                results = results_token[:limit]
            if results:
                from nexsandglass.core.sandglass_sqlite import search_in, sync_incremental
                lns = [r[0] for r in results[:500]]
                sync_incremental()
                ranked = search_in(lns, query)
                if ranked:
                    return [(r[0], r[1], r[2]) for r in ranked[:limit]]
            return results[:limit]
        except Exception:
            return []


class SearchRouter:
    """搜索路由器——四路并发 + 沙子密度融合(density×trust+simhash) + 动态扩窗 + mmap兜底。
    V2.8.6: 统一为唯一搜索入口。
    """
    def __init__(self, shadow=None, fts5=None, idx=None, tfidf=None, mmap=None, vector=None):
        self.shadow = shadow or ShadowSearch()
        self.fts5 = fts5 or Fts5Search()
        self.idx = idx or IdxSearch()
        self.tfidf = tfidf or TfidfSearch()
        self.mmapfallback = mmap or MmapFallback()
        self.vector = vector  # 可选：向量语义检索（第五路）

    def search(self, query: str, limit: int = 10) -> list:
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
            fut_shadow = ex.submit(self.shadow.search, query, limit)
            fut_fts5 = ex.submit(self.fts5.search, query, max(limit * 2, 30))
            fut_idx = ex.submit(self.idx.search, query, max(limit * 2, 30))
            fut_tfidf = ex.submit(self.tfidf.search, query, max(limit * 2, 30))
            fut_vector = ex.submit(self._vector_search_wrapper, query, limit) if self.vector else None
        shadow_hits = fut_shadow.result() or []
        fts5_hits = fut_fts5.result() or []
        idx_hits = fut_idx.result() or []
        tfidf_hits = fut_tfidf.result() or []
        vector_hits = fut_vector.result() if fut_vector else []
        if shadow_hits:
            try:
                from nexsandglass.features.shadow_sand import shadow_retrieval_bump
                shadow_retrieval_bump([ln for _, ln in shadow_hits[:limit]])
            except Exception:
                pass
        all_candidates = []
        seen = set()
        for hits in [fts5_hits, idx_hits, tfidf_hits]:
            for item in hits:
                ln = item[0]
                if ln not in seen:
                    seen.add(ln)
                    all_candidates.append(item)
        if shadow_hits:
            from nexsandglass.core.sandglass_sqlite import get_records
            want = [ln for _, ln in shadow_hits[:limit] if ln not in seen]
            recs = get_records(want)
            for score, ln in shadow_hits[:limit]:
                if ln not in seen and ln in recs:
                    ts, sender, text = recs[ln]
                    if ts and text:
                        seen.add(ln)
                        all_candidates.append((ln, ts, text))
        if all_candidates:
            tokens = _query_tokens(query)
            ranked = sand_density(all_candidates, tokens, query)
            # 这里曾经还有一句 `ranked = simhash_rerank(ranked, query)`。
            # 它把候选全部按「与 query 的汉明距离」重新排序，把上一行精心算出的
            # density×trust 分数整个洗掉 —— 而 simhash 本来就已经作为 sim_bonus
            # 计入 sand_density 的公式里了，再排一次纯属破坏。
            # 实测：正确记录在候选中排第 0，density 排序后仍是第 0，
            # 被 simhash_rerank 扔到第 30，top5 全是 token 碰撞的假阳性。
            # simhash 是近重复检测用的，拿短 query 和长文档比汉明距离没有意义，
            # 而且文档越长罚得越狠 —— 恰好把最有价值的长记忆排到最后。
            # 向量语义 boosting（第五路融合）
            if vector_hits:
                ranked = self._vector_boost(ranked, vector_hits, query)
            ranked = dynamic_expand(ranked, tokens, limit)
            return ranked[:limit]
        return self.mmapfallback.search(query, limit)

    def _vector_search_wrapper(self, query: str, limit: int) -> list:
        """向量检索包装（fail-safe）。"""
        try:
            return self.vector.search(query, limit)
        except Exception:
            return []

    def _vector_boost(self, ranked: list, vector_hits: list, query: str) -> list:
        """
        向量语义 boosting：与向量检索结果命中的行号提升到前列。

        向量存储的 id 与沙漏行号一致时（如 sandglass_chroma 按行号索引），
        将语义命中的候选提前；id 非数字时忽略该路结果。
        """
        if not vector_hits or not ranked:
            return ranked
        boost: dict = {}
        for mid, score in vector_hits[:10]:
            try:
                ln = int(mid)
            except (TypeError, ValueError):
                continue
            if score > 0:
                boost[ln] = max(boost.get(ln, 0.0), float(score))
        if not boost:
            return ranked
        boosted_items = [it for it in ranked if boost.get(it[0], 0.0) > 0]
        if not boosted_items:
            return ranked
        boosted_items.sort(key=lambda it: boost[it[0]], reverse=True)
        seen = {it[0] for it in boosted_items}
        rest = [it for it in ranked if it[0] not in seen]
        return boosted_items + rest
