"""
core/embedding_provider.py — 可插拔 Embedding 后端

设计：
- 抽象 EmbeddingProvider 接口，支持本地模型（sentence-transformers）和外部 API
- 默认走本地免费方案（不强制联网/付费）
- 懒加载：首次调用时才初始化模型，避免 import 时开销
- Fail-safe：模型加载失败时返回 None（调用方退回词法检索）

本地默认模型：paraphrase-multilingual-MiniLM-L12-v2
  - 支持中英混合（50+ 语言）
  - 向量维度 384（轻量）
  - ~50MB 磁盘，首次下载后离线可用

安装依赖（可选）：
  pip install sentence-transformers numpy
  未安装时自动降级为 None（不影响现有词法检索）
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# 向量维度常量（与默认模型一致）
EMBEDDING_DIM: int = 384


class EmbeddingProvider:
    """Embedding 后端抽象接口。"""

    def encode(self, texts: list[str]) -> list[list[float]]:
        """将文本列表编码为向量列表。失败返回空列表。"""
        raise NotImplementedError

    def encode_one(self, text: str) -> Optional[list[float]]:
        """单条编码。"""
        results = self.encode([text])
        return results[0] if results else None

    @property
    def dim(self) -> int:
        return EMBEDDING_DIM

    @property
    def available(self) -> bool:
        return True


class SentenceTransformerProvider(EmbeddingProvider):
    """基于 sentence-transformers 的本地 Embedding（中英混合支持）。"""

    def __init__(self, model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"):
        self._model_name = model_name
        self._model = None

    def _ensure_model(self):
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
            # 本地缓存目录（离线可用）
            cache_dir = os.path.expanduser("~/.cache/nexsandglass/models")
            os.makedirs(cache_dir, exist_ok=True)
            self._model = SentenceTransformer(self._model_name, cache_folder=cache_dir)
            logger.info("[EmbeddingProvider] 本地模型加载成功: %s", self._model_name)
        except Exception as e:
            logger.warning("[EmbeddingProvider] 本地模型加载失败（将退回词法检索）: %s", e)
            self._model = None

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self._ensure_model()
        if self._model is None:
            return []
        try:
            import numpy as np
            embeddings = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
            return embeddings.tolist()
        except Exception as e:
            logger.warning("[EmbeddingProvider] 编码失败: %s", e)
            return []

    @property
    def available(self) -> bool:
        self._ensure_model()
        return self._model is not None


class ExternalAPIProvider(EmbeddingProvider):
    """外部 Embedding API（OpenAI 兼容接口，可选）。"""

    def __init__(self, api_url: str = "", api_key: str = "", model: str = "text-embedding-3-small"):
        self._api_url = api_url or os.environ.get("EMBEDDING_API_URL", "")
        self._api_key = api_key or os.environ.get("EMBEDDING_API_KEY", "")
        self._model = model
        self._dim = EMBEDDING_DIM if "3-small" in model else 1536

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts or not self._api_url:
            return []
        try:
            import httpx
            resp = httpx.post(
                self._api_url,
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                json={"input": texts, "model": self._model},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            return [item["embedding"] for item in data.get("data", [])]
        except Exception as e:
            logger.warning("[ExternalAPIProvider] 调用失败: %s", e)
            return []

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def available(self) -> bool:
        return bool(self._api_url)


class NullProvider(EmbeddingProvider):
    """不可用时的空实现（fail-safe）。"""

    def encode(self, texts: list[str]) -> list[list[float]]:
        return []

    def encode_one(self, text: str) -> Optional[list[float]]:
        return None

    @property
    def dim(self) -> int:
        return EMBEDDING_DIM

    @property
    def available(self) -> bool:
        return False


# ── 全局单例（懒加载）──────────────────────────────────────────

_provider: Optional[EmbeddingProvider] = None


def get_embedding_provider() -> EmbeddingProvider:
    """获取全局 EmbeddingProvider 单例（优先本地，可选外部 API）。"""
    global _provider
    if _provider is not None:
        return _provider
    # 检查是否配置外部 API
    api_url = os.environ.get("EMBEDDING_API_URL", "")
    if api_url:
        _provider = ExternalAPIProvider(api_url=api_url)
        if _provider.available:
            return _provider
    # 默认本地
    local = SentenceTransformerProvider()
    if local.available:
        _provider = local
    else:
        _provider = NullProvider()
    return _provider


def reset_provider():
    """测试用：重置单例。"""
    global _provider
    _provider = None
