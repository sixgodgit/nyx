"""Managed consolidation copies: source-aware cleanup and explicit verification.

Snapshots are rollback copies, not an independent source of truth. Forgetting a
record removes its snapshot rows too; recoverable raw data stays only in the
normal quarantine, where retention and restoration are already tracked.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from nexsandglass.core.fsutil import atomic_write


def directory() -> str:
    # Preserve an explicit legacy runtime override, without importing the
    # runtime module (which itself uses this helper). The unmodified import-time
    # default must not pin later configured-data-dir scopes to the old path.
    runtime = sys.modules.get("nexsandglass.runtime.consolidation")
    if runtime is not None:
        override = getattr(runtime, "_SNAPSHOT_DIR", None)
        initial = getattr(runtime, "_DEFAULT_SNAPSHOT_DIR", override)
        if override is not None and override != initial:
            return os.fspath(override)
    from nexsandglass.core.sandglass_paths import configured_data_dir
    base = configured_data_dir() or os.path.join(os.path.expanduser("~"), ".nyx")
    return os.path.join(base, "consolidation_snapshots")


def _files():
    root = Path(directory())
    if not root.exists():
        return []
    # Include abandoned temporary files: a crash can leave one containing text.
    return sorted(root.iterdir())


def _load(path: Path) -> list[dict]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("not a regular managed snapshot file")
    with path.open(encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("invalid snapshot rows")
    return rows


def refs(row: dict) -> set[str]:
    out = set()
    for key in ("memory_id", "source_id", "source_mem_id"):
        if isinstance(row.get(key), str):
            out.add(row[key])
    for key in ("source_ids", "source_mem_ids"):
        if isinstance(row.get(key), (list, tuple)):
            out.update(v for v in row[key] if isinstance(v, str))
    return out


def scrub(memories: list, *, derived_ids=(), legacy_matches=None, journal_path=None) -> dict:
    """Drop exactly linked snapshot rows, retaining unrelated rows and files.

    memories use erasure's (mem_id, line_start, line_end, ts, sender, seq, text)
    shape. Timestamp-only legacy IDs require a matching original content prefix;
    a same-second timestamp alone is deliberately insufficient.
    """
    from nexsandglass.core import memid
    selected = {r[0] for r in memories}
    linked = selected | set(derived_ids)
    legacy = {}
    for r in memories:
        legacy.setdefault("engram:" + str(r[3]), set()).add(str(r[6])[:300])
    for alias, contents in (legacy_matches or {}).items():
        legacy.setdefault(alias, set()).update(contents)
    report = {"removed": 0, "files": 0, "errors": []}
    with memid.journal_lock(journal_path):
        ambiguous = set()
        for source in memories:
            for mid, text in memid.get_conn().execute(
                "SELECT mem_id,text FROM memories WHERE ts=? AND deleted_at IS NULL", (source[3],)):
                if mid not in selected and str(text)[:300] == str(source[6])[:300]:
                    ambiguous.add(("engram:" + str(source[3]), str(source[6])[:300]))
        try:
            files = _files()
        except OSError as e:
            report["errors"].append(str(e))
            return report
        for path in files:
            try:
                rows = _load(path)
                kept = []
                for row in rows:
                    dependencies = refs(row)
                    remove = bool(dependencies & linked)
                    # New rows carry canonical source metadata. Never override it
                    # using a coincidentally equal timestamp from another record.
                    explicit = dependencies - {row.get("memory_id", "")}
                    if not remove and not explicit:
                        aliases = dependencies & set(legacy)
                        content = str(row.get("content", ""))
                        for alias in aliases:
                            if content not in legacy[alias]:
                                continue
                            if (alias, content) in ambiguous:
                                report["errors"].append(f"{path.name}: ambiguous legacy snapshot source {alias}")
                            else:
                                remove = True
                    if not remove:
                        kept.append(row)
                removed = len(rows) - len(kept)
                if removed:
                    with atomic_write(str(path)) as f:
                        json.dump(kept, f, ensure_ascii=False, indent=1)
                    report["removed"] += removed
                    report["files"] += 1
            except (OSError, ValueError, TypeError) as e:
                report["errors"].append(f"{path.name}: {e}")
    return report


def find_text(needles, *, journal_path=None) -> tuple[dict, list]:
    """Report retained text and scan errors separately; errors are not clean."""
    from nexsandglass.core import memid
    found, errors = {}, []
    with memid.journal_lock(journal_path):
        try:
            files = _files()
        except OSError as e:
            return {}, [str(e)]
        for path in files:
            try:
                rows = _load(path)
                # Search decoded strings: serializing again would escape quotes,
                # backslashes and newlines and could falsely report them absent.
                texts = tuple(_strings(rows))
                for needle in needles:
                    if any(needle in text for text in texts):
                        found.setdefault(path.name, []).append(needle[:60])
            except (OSError, ValueError, TypeError) as e:
                errors.append(f"{path.name}: {e}")
    return found, errors


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
