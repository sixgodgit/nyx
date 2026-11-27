"""Explicit host-bound store scope, independent of request/session metadata.

Creating a StoreContext is a trusted in-process host operation. It does not
authenticate a request or make a data directory named in user text accessible.
No process environment or imported module globals are changed by binding it.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from hashlib import sha256
import os
import threading
from types import MappingProxyType
from typing import Callable, Mapping

from . import settings as _settings


class StoreScopeError(PermissionError):
    """An explicit store operation attempted to use a different store's path."""


@dataclass(frozen=True)
class StoreContext:
    data_dir: str
    profile_id: str = ""
    settings: Mapping[str, str] | None = field(default=None, repr=False, compare=False)
    credential_getter: Callable[[str], str] | None = field(default=None, repr=False, compare=False)
    _credential_identity: object = field(default_factory=object, repr=False, compare=False)

    def __post_init__(self):
        raw = os.fspath(self.data_dir)
        if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
            raise ValueError("data_dir must be a nonempty filesystem path")
        if not isinstance(self.profile_id, str):
            raise TypeError("profile_id must be text")
        if self.credential_getter is not None and not callable(self.credential_getter):
            raise TypeError("credential_getter must be callable or None")
        values = dict(self.settings or {})
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in values.items()):
            raise TypeError("store settings must map strings to strings")
        root = os.path.realpath(os.path.abspath(os.path.expanduser(raw)))
        object.__setattr__(self, "data_dir", root)
        object.__setattr__(self, "settings", MappingProxyType(values))

    @property
    def store_id(self) -> str:
        """Stable correlation key for this root, not an authorization token."""
        return sha256(self.data_dir.encode()).hexdigest()

    def path(self, relative: str) -> str:
        raw = os.fspath(relative)
        if not isinstance(raw, str) or not raw or "\x00" in raw or os.path.isabs(raw):
            raise StoreScopeError("store paths must be relative to the bound root")
        if ".." in raw.split(os.sep):
            raise StoreScopeError("parent traversal is not a store-relative path")
        return self.check_path(os.path.join(self.data_dir, raw))

    def check_path(self, value) -> str:
        result = os.path.realpath(os.path.abspath(os.fspath(value)))
        if os.path.commonpath((self.data_dir, result)) != self.data_dir:
            raise StoreScopeError("path is outside the bound store")
        return result

    @contextmanager
    def bind(self):
        with scope(self):
            yield self


_CURRENT: ContextVar[StoreContext | None] = ContextVar("nyx_store_context", default=None)


def current() -> StoreContext | None:
    return _CURRENT.get()


@contextmanager
def scope(context: StoreContext):
    if not isinstance(context, StoreContext):
        raise TypeError("context must be a StoreContext")
    # Preserve production-test protection for explicit roots as well as the
    # legacy boot path. No directory/file is created by binding alone.
    from . import prodguard
    prodguard.check(context.data_dir, "数据目录")
    token = _CURRENT.set(context)
    try:
        # An empty new store scope must not inherit another profile's API
        # credentials or feature flags. Operator environment precedence remains.
        with _settings.scope(context.settings):
            yield context
    finally:
        _CURRENT.reset(token)


def path(relative: str, legacy=None) -> str:
    context = current()
    if context is not None:
        return context.path(relative)
    if legacy is not None:
        return os.fspath(legacy)
    from .sandglass_paths import _NB
    return os.path.join(_NB, relative)


def canonical_path(relative: str, candidate=None) -> str:
    """Reject an explicit canonical-file override while a store is bound."""
    context = current()
    if context is None:
        return os.fspath(candidate) if candidate is not None else path(relative)
    wanted = context.path(relative)
    if candidate is not None and context.check_path(candidate) != wanted:
        raise StoreScopeError("canonical file does not match the bound store")
    return wanted


def spawn_thread(target, *, name=None, daemon=True, args=(), kwargs=None):
    """Return an unstarted Thread carrying this store/settings/clock context."""
    captured = copy_context()
    values = dict(kwargs or {})
    run = _with_clock(target, args, values)
    return threading.Thread(target=lambda: captured.run(run),
                            name=name, daemon=daemon)


def submit(executor, fn, *args, **kwargs):
    """Each executor job gets its own Context; no reused-worker scope leakage."""
    captured = copy_context()
    return executor.submit(captured.run, _with_clock(fn, args, kwargs))


def _with_clock(fn, args, kwargs):
    # clock.frozen is thread-local; copying ContextVars alone cannot carry it.
    from . import clock
    simulated = clock.now() if clock.is_simulated() else None
    def run():
        if simulated is None:
            return fn(*args, **kwargs)
        with clock.frozen(simulated):
            return fn(*args, **kwargs)
    return run
