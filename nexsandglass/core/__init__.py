"""Core infrastructure: memory backend, search routing, storage."""

from .emotion_vocab import detect, learn, load_vocab, mood_message, save_vocab
from .l0_buffer import l0_context, l0_remember, l0_size
from .memory_provider import NexSandglassProvider, register as register_provider
from .sandglass import count, read
from .sandglass_archive import (
    archive_path,
    archive_stats,
    cold_migration,
    is_old,
    parse_ts,
    search_archive,
)
from .sandglass_log import log_conversation, log_message
from .sandglass_paths import validate
from .sandglass_sqlite import search, search_in, search_year, sync_all, sync_incremental
from .search_router import (
    Fts5Search,
    IdxSearch,
    MmapFallback,
    SearchRouter,
    ShadowSearch,
    TfidfSearch,
)

__all__ = [
    # memory provider (Hermes plugin)
    "NexSandglassProvider",
    "register_provider",
    # search routing
    "SearchRouter",
    "ShadowSearch",
    "Fts5Search",
    "IdxSearch",
    "TfidfSearch",
    "MmapFallback",
    # sqlite FTS layer
    "search",
    "search_in",
    "search_year",
    "sync_all",
    "sync_incremental",
    # sandglass log / archive
    "log_message",
    "log_conversation",
    "archive_path",
    "archive_stats",
    "cold_migration",
    "is_old",
    "parse_ts",
    "search_archive",
    # emotion vocab
    "load_vocab",
    "save_vocab",
    "learn",
    "detect",
    "mood_message",
    # paths / flat sandglass reader
    "validate",
    "read",
    "count",
    # L0 buffer
    "l0_remember",
    "l0_context",
    "l0_size",
]
