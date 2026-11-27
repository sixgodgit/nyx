"""Real Nyx + Conduit memory API contract, with disposable host configuration.

CONDUIT_NOTIFIER_SRC points to the notifier checkout; HERMES_AGENT_SRC to Hermes.
No audio, model credentials, deployed server, or phone is required by this check.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


def main():
    root = Path(tempfile.mkdtemp(prefix="nyx-conduit-contract-"))
    home, data = root / "hermes", root / "memory"
    home.mkdir()
    os.environ.update(HERMES_HOME=str(home), NEXSANDBASE_HOME=str(data),
                      XDG_CONFIG_HOME=str(root / "config"),
                      NYX_EMBED="off", NYX_AUTO_PURGE="off", NYX_LANG="en")
    for name in ("NYX_UNDERSTAND_LLM", "NYX_OWNER_IDS", "NYX_NOW", "NYX_FOLLOW_UPS",
                 "NYX_ALLOW_PROD_DB", "NYX_CONTEXT_TOKENS", "NYX_BUNDLE_MAX_TOKENS",
                 "NYX_EMBED_PROVIDER", "NYX_EMBED_MODEL",
                 "EMBEDDING_API_URL", "EMBEDDING_API_KEY", "NYX_RERANK", "NYX_SECRET_FILTER",
                 "WTHREAD_LLM_EXTRACTION", "LLM_EXTRACT_API_URL", "DEEPSEEK_API_KEY",
                 "OPENROUTER_API_KEY", "NYX_EVAL_API_URL", "NYX_EVAL_API_KEY",
                 "NYX_JUDGE_API_URL", "NYX_JUDGE_API_KEY", "NYX_VAULT", "NYX_VAULT_BACKEND",
                 "NYX_VAULT_REVEAL", "NYX_VAULT_KINDS", "NYX_VAULT_KEY", "HERMES_CRON_SESSION"):
        os.environ.pop(name, None)
    repo = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(repo), os.environ["HERMES_AGENT_SRC"]]
    notifier = Path(os.environ["CONDUIT_NOTIFIER_SRC"])
    (home / "nyx.json").write_text(json.dumps({"data_dir": str(data)}))
    (home / "config.yaml").write_text("memory:\n  provider: nyx\n")

    from nexsandglass.core.memory_provider import NexSandglassProvider
    from nexsandglass.core import clock, memid, erasure, profile
    # API/worker threads intentionally share this child's simulated date.
    # A thread-local freeze on the caller does not cross the TestClient portal.
    clock.set_global("2026-11-14 12:00:00")
    writer = NexSandglassProvider()
    writer.initialize("fixture", hermes_home=str(home), agent_context="primary")
    warm = getattr(writer, "_warm_thread", None)
    if warm is not None:
        warm.join(5)
        assert not warm.is_alive(), "fixture initialization did not finish"
    with clock.frozen("2026-11-14 09:00:00"):
        writer.sync_turn("我对花生过敏。", "知道了。", session_id="fixture")
        writer.sync_turn("以后用中文回复，简短一点。", "好的。", session_id="fixture")
        writer.sync_turn("明天我有个面试。", "祝你顺利。", session_id="fixture")
        writer.sync_turn("我的备用蓝色钥匙放在书房的第二个抽屉。", "知道了。", session_id="fixture")

    spec = importlib.util.spec_from_file_location("nyx_real_conduit_api", notifier / "dashboard/plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/conduit_push")
    # Only host config/builtin file reads are replaced. Provider discovery,
    # initialization, per-profile execution, HTTP route and Nyx are real.
    api.memory._hermes_memory_config = lambda: {"memory": {"provider": "nyx"}}
    api.memory._hermes_builtin_memory = lambda config: ""
    api.memory._memory_provider_init_kwargs = lambda: {
        "platform": "conduit_voice", "hermes_home": str(home), "agent_context": "primary"}
    api.memory._memory_providers = api.memory._MemoryProviders()
    failures = []
    unexpected = api.core._unexpected
    def capture_failure(what, exc, **kwargs):
        failures.append((type(exc).__name__, getattr(exc, "name", None)))
        return unexpected(what, exc, **kwargs)
    api.core._unexpected = capture_failure
    journal = data / "sandglass.txt"
    before = journal.read_bytes()
    results = {}
    client = TestClient(app)
    try:
        with clock.frozen("2026-11-14 12:00:00"), patch.object(
                NexSandglassProvider, "prefetch", side_effect=AssertionError("startup called recall")), patch.object(
                NexSandglassProvider, "sync_turn", side_effect=AssertionError("startup wrote a turn")), patch.object(
                NexSandglassProvider, "system_prompt_block", side_effect=AssertionError("startup called tool prompt")):
            # A cold index held by background warm-up must report unreadiness,
            # rather than silently accepting an empty startup snapshot.
            profile._REFRESH_LOCK.acquire()
            try:
                busy = client.get("/api/plugins/conduit_push/memory/context")
                if os.environ.get("CONDUIT_EXPECT_CONTEXT_FALLBACK") == "1":
                    assert busy.status_code == 200, busy.text
                    assert busy.json()["context"] == "" and busy.json()["provider"] == "nyx"
                    results["provider_label_alone_is_not_evidence_of_nyx_facts"] = True
                else:
                    assert busy.status_code == 502, busy.text
                assert "花生" not in busy.text
            finally:
                profile._REFRESH_LOCK.release()
            results["cold_index_readiness_error"] = True
            response = client.get("/api/plugins/conduit_push/memory/context")
            assert response.status_code == 200, (response.text, failures)
            payload = response.json()
            assert payload["provider"] == "nyx" and payload["recall"] is True
            assert "花生" in payload["context"] and "面试" in payload["context"], payload
            assert "中文" in payload["context"] and "sandglass_search" not in payload["context"]
            assert response.headers["cache-control"] == "no-store"
            assert journal.read_bytes() == before
            entry = next(iter(api.memory._memory_providers._entries.values()))
            trace = entry.provider.context_trace("conduit_voice")
            from hashlib import sha256
            assert trace["evidence_ids"] and trace["stage"] == "generated"
            assert trace["text_sha256"] == sha256(payload["context"].encode()).hexdigest()
            assert trace["sent"] is None and trace["applied"] is None
            results["source_bound_nyx_output_reached_http"] = True
            results["automatic_startup_facts"] = True
            results["no_recall_tool_or_turn_write"] = True
            allergy = next(r for r in memid.iter_all() if "花生" in r["text"] and r["sender"] == "user")
            forgotten = erasure.forget({"mem_id": allergy["mem_id"]}, apply=True)
            assert forgotten["ok"], forgotten
            assert "花生" not in client.get("/api/plugins/conduit_push/memory/context").json()["context"]
            assert erasure.restore(allergy["mem_id"])["ok"]
            assert "花生" in client.get("/api/plugins/conduit_push/memory/context").json()["context"]
            results["forget_restore_on_next_context_read"] = True
            # The cached provider owns its profile settings. Change the real
            # profile config rather than an outer caller's thread-local scope.
            entry = next(iter(api.memory._memory_providers._entries.values()))
            entry.provider.save_config({"follow_ups": "off"}, str(home))
            without = client.get("/api/plugins/conduit_push/memory/context").json()["context"]
            assert "面试" not in without
            results["followups_off_respected"] = True
        recalled = client.post("/api/plugins/conduit_push/memory/recall", json={"query": "备用蓝色钥匙"})
        assert recalled.status_code == 200, recalled.text
        assert recalled.json()["available"] is True
        assert "书房" in recalled.json()["results"] and "第二个抽屉" in recalled.json()["results"]
        query_trace = entry.provider.context_trace("hermes_prefetch")
        assert query_trace["evidence_ids"] and query_trace["applied"] is None
        results["explicit_historical_memory_http_lookup"] = True
        cross_language = client.post("/api/plugins/conduit_push/memory/recall", json={"query": "Am I allergic to peanuts?"})
        assert cross_language.status_code == 200, cross_language.text
        assert "花生过敏" in cross_language.json()["results"], cross_language.json()
        results["cross_language_alias_reached_real_http"] = True
        entry.provider.save_config({"response_language": "zh"}, str(home))
        localized = client.get("/api/plugins/conduit_push/memory/context")
        assert localized.status_code == 200, localized.text
        assert "首选回答语言：zh" in localized.json()["context"]
        assert "花生过敏" in localized.json()["context"]
        localized_recall = client.post("/api/plugins/conduit_push/memory/recall", json={"query": "Am I allergic to peanuts?"})
        assert localized_recall.status_code == 200, localized_recall.text
        assert "首选回答语言：zh" in localized_recall.json()["results"]
        assert "花生过敏" in localized_recall.json()["results"]
        assert journal.read_bytes() == before
        results["response_language_frame_reached_http_without_rewriting_memory"] = True
    finally:
        api.memory._memory_providers.clear()
        writer.shutdown()
        client.close()
    print(json.dumps(results, ensure_ascii=False))
    print("CONDUIT NYX VOICE CHECK: PASS")


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        # Hermes may redirect normal stderr into its own log. Keep a failing
        # isolated check actionable for the parent pytest process as well.
        import traceback
        traceback.print_exc(file=sys.__stderr__)
        raise
