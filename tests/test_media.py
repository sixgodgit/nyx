"""图片、声音、视频的记忆（v7.21.0 框架）"""
import json
import os
import sys
import wave
from argparse import Namespace

import pytest

from tests.test_hermes_provider import env, _reindex, _provider  # noqa: F401  复用隔离目录

from nexsandglass import media as M
from nexsandglass.core import lookup
from nexsandglass.media import understanders as U

_PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82")


@pytest.fixture()
def media(env, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(M, "media_dir", lambda: str(tmp_path / "media"))
    U._discover()                       # 先载入内置后端，再存一份：测试里登记的假后端用完就撤
    saved = dict(U._REGISTRY)
    yield tmp_path
    U._REGISTRY.clear()
    U._REGISTRY.update(saved)


def _png(d, name="cat.png", extra=b""):
    p = d / name
    p.write_bytes(_PNG + extra)
    return str(p)


def _wav(d, name="voice.wav", seconds=2):
    p = str(d / name)
    with wave.open(p, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 8000 * seconds)
    return p


class Fake:
    def __init__(self, name, kinds, moments=None, fail=None, why=None):
        self.name, self.kinds, self._m, self._fail, self._why = name, kinds, moments or [], fail, why
        self.calls = 0

    def available(self):
        return self._why

    def understand(self, asset, path):
        self.calls += 1
        assert os.path.exists(path)
        if self._fail:
            raise RuntimeError(self._fail)
        return self._m


def test_register_without_backends(media):
    r = M.add(_png(media), note="这是我家的猫，叫朵朵，第一次见雪。", when="2026-01-10 09:30")
    assert r["status"] == "unavailable" and r["new"] and r["kind"] == "image"
    assert os.path.exists(r["blob"]) and r["blob"].startswith(str(media / "media"))   # 复制进数据目录
    kinds = {m["kind"] for m in r["moments"]}
    assert kinds == {"shared", "note"}
    note = lookup.get(next(m["mem_id"] for m in r["moments"] if m["kind"] == "note"))
    assert note["who"] == "user" and note["ts"].startswith("2026-01-10 09:30")      # 附言算你说的，日期是拍摄时间
    shared = lookup.get(next(m["mem_id"] for m in r["moments"] if m["kind"] == "shared"))
    assert shared["who"] == "media" and "Shared a photo: cat.png" in shared["text"]
    assert any(i.get("mem_id") == note["mem_id"] for i in lookup.search("朵朵"))


def test_same_file_is_not_registered_twice(media):
    a = M.add(_png(media))
    b = M.add(_png(media, name="copy.png"), note="又发了一次")
    assert a["asset_id"] == b["asset_id"] and not b["new"]
    assert [m["kind"] for m in b["moments"]].count("note") == 1
    assert len(M.list_assets()) == 1


def test_refused(media):
    assert M.add(str(media / "nope.png"))["status"] == "refused"
    txt = media / "a.txt"
    txt.write_text("hi")
    assert M.add(str(txt))["status"] == "refused"
    from nexsandglass.core import settings
    with settings.scope({"NYX_MEDIA_MAX_MB": "0.00001"}):
        assert "NYX_MEDIA_MAX_MB" in M.add(_png(media))["reason"]
    with pytest.raises(M.MediaError):
        M.add(_png(media), when="last spring")


def test_backends_write_unverified_moments_with_time_spans(media):
    U.register(Fake("fake-asr", ("audio",), [U.Moment("transcript", "明天下午三点去接孩子", 0.0, 1.5),
                                            {"kind": "transcript", "text": "顺便买牛奶", "start_s": 1.5}]))
    r = M.add(_wav(media), when="2026-02-01")
    assert r["status"] == "ok" and r["duration_s"] == 2.0 and r["understood"] == {"fake-asr": "ok"}
    tr = [lookup.get(m["mem_id"]) for m in r["moments"] if m["kind"] == "transcript"]
    assert len(tr) == 2 and all(i["who"] == "media" and i["trust"] == "unverified" for i in tr)
    assert "[audio voice.wav · transcript 0:00-0:01] 明天下午三点去接孩子" in tr[0]["text"]
    read = json.loads(_provider().handle_tool_call("nyx_read", {"id": tr[0]["mem_id"]}))["memory"]
    assert read["text"].startswith("(unverified · source: media)")                 # 模型推断，不是你说的


def test_failure_partial_and_later_understanding(media):
    good = Fake("cap", ("image",), [U.Moment("caption", "一只白猫站在雪地里")])
    U.register(Fake("ocr", ("image",), fail="model crashed"))
    U.register(good)
    r = M.add(_png(media))
    assert r["status"] == "partial" and r["understood"]["ocr"].startswith("error")
    U.register(Fake("ocr", ("image",), [U.Moment("ocr", "SNOW 2026")]))
    out = M.understand(pending=True)
    assert out[0]["status"] == "ok" and good.calls == 1                             # 成功过的不再跑
    assert {m["kind"] for m in M.get(r["asset_id"])["moments"]} >= {"caption", "ocr"}


def test_unavailable_now_understood_later(media):
    U.register(Fake("vision", ("image",), [U.Moment("caption", "海边的日落")], why="no model configured"))
    r = M.add(_png(media))
    assert r["status"] == "unavailable"
    U.register(Fake("vision", ("image",), [U.Moment("caption", "海边的日落")]))
    assert M.understand(r["asset_id"])[0]["status"] == "ok"
    assert any("海边的日落" in (i.get("text") or "") for i in lookup.search("日落"))


def test_command_backend(media):
    from nexsandglass.core import settings
    cmd = f'{sys.executable} -c "import sys; print(\'transcribed:\', sys.argv[1].rsplit(\'.\', 1)[-1])" {{path}}'
    with settings.scope({"NYX_MEDIA_AUDIO_CMD": cmd}):
        assert U.CommandUnderstander("audio").available() is None
        r = M.add(_wav(media))
    assert r["understood"] == {"command:audio": "ok"}
    t = [lookup.get(m["mem_id"])["text"] for m in r["moments"] if m["kind"] == "transcript"]
    assert t and t[0].endswith("transcribed: wav")
    assert U.CommandUnderstander("image").available()                                # 没设：不可用


def test_forget_restore_purge_follow_the_file(media):
    r = M.add(_png(media), note="朵朵的照片")
    blob, ids = r["blob"], [m["mem_id"] for m in r["moments"]]
    f = M.forget(r["asset_id"])
    assert f["ok"] and f["removed"] == len(ids) and os.path.exists(blob)          # 文件先留着，能救回
    assert all(lookup.get(i)["status"] == "quarantined" for i in ids)
    assert M.list_assets() == [] and M.add(_png(media))["status"] == "refused"
    assert M.restore(r["asset_id"])["ok"] and M.get(r["asset_id"])["status"] == "unavailable"
    assert all(lookup.get(i)["status"] == "live" for i in ids)

    M.forget(r["asset_id"])
    from nexsandglass.runtime import facade
    rep = facade.purge_forgotten(apply=True, mem_ids=ids)                           # 隔离区清理
    assert rep["media_erased"] == 1 and not os.path.exists(blob)
    assert M.get(r["asset_id"])["status"] == "erased"
    again = M.add(_png(media))                                                       # 擦掉之后可以重新登记
    assert again["new"] and again["status"] == "unavailable"


def test_forget_now_erases_records_and_file(media):
    r = M.add(_png(media))
    assert M.forget(r["asset_id"], now=True)["erased"]
    assert not os.path.exists(r["blob"]) and lookup.get(r["moments"][0]["mem_id"])["status"] == "erased"


def test_broken_plugin_does_not_break_registration(media, monkeypatch):
    class EP:
        name = "broken"

        def load(self):
            raise ImportError("missing dependency")
    import importlib.metadata as md
    monkeypatch.setattr(md, "entry_points", lambda group=None: [EP()])
    monkeypatch.setattr(U, "_DISCOVERED", False)
    assert any(r["name"] == "command:image" for r in U.describe())
    assert M.add(_png(media))["status"] == "unavailable"


def test_cli(media, capsys, monkeypatch):
    from nexsandglass.media.cli import main
    p = _png(media)
    assert main(["add", p, "--note", "朵朵第一次见雪", "--when", "2026-01-10"]) == 0
    out = capsys.readouterr().out
    assert "unavailable" in out and "understand --pending" in out
    aid = M.list_assets()[0]["asset_id"]
    assert main(["list"]) == 0 and aid in capsys.readouterr().out
    assert main(["show", aid[:8]]) == 0 and "朵朵第一次见雪" in capsys.readouterr().out
    assert main(["backends"]) == 0 and "command:audio" in capsys.readouterr().out
    assert main(["forget", aid]) == 1                                                 # 不是终端、没加 --yes
    assert main(["forget", aid, "--yes"]) == 0 and main(["restore", aid]) == 0
    import nexsandglass_hermes as nh
    from nexsandglass_hermes import cli as hcli
    monkeypatch.setattr(nh, "apply_data_dir", lambda: "")
    monkeypatch.setattr(nh, "load_config", lambda: {})
    capsys.readouterr()
    assert hcli.nyx_command(Namespace(nyx_command="media", nyx_args=["list"])) == 0
    assert aid in capsys.readouterr().out
