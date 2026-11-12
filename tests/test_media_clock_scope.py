"""Historical media writes must not change another request's clock."""
from datetime import datetime
from threading import Thread

import pytest

from nexsandglass import media
from nexsandglass.core import clock, sandglass_log


@pytest.mark.parametrize("fail", [False, True])
def test_asset_time_is_local_and_prior_clock_is_preserved(monkeypatch, fail):
    prior = datetime(2025, 5, 5)
    seen = []
    def log(*args, **kwargs):
        assert clock.now() == datetime(2020, 1, 1)
        thread = Thread(target=lambda: seen.append(clock.now()))
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()
        if fail:
            raise OSError("injected")
        return "m_asset"
    monkeypatch.setattr(sandglass_log, "log_message", log)
    original = clock._global
    clock.set_global(prior)
    try:
        if fail:
            with pytest.raises(OSError):
                media._write("caption", "media", "2020-01-01 00:00:00")
        else:
            assert media._write("caption", "media", "2020-01-01 00:00:00") == "m_asset"
        assert seen == [prior]
        assert clock.now() == prior
    finally:
        clock.set_global(original)


def test_nested_thread_clock_restored(monkeypatch):
    monkeypatch.setattr(sandglass_log, "log_message", lambda *a, **k: "m_asset")
    with clock.frozen("2024-02-03"):
        media._write("caption", "media", "2020-01-01 00:00:00")
        assert clock.now() == datetime(2024, 2, 3)
