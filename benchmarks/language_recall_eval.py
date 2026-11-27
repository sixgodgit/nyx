#!/usr/bin/env python3
"""Frozen multilingual evidence recall with distractors and explicit backends."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import importlib.metadata
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def evaluate(data, root, backend):
    from nexsandglass.core import memid, semantic
    from nexsandglass.runtime import StoreContext, RecallRequest, observe, recall_request
    results = []
    for case in data["cases"]:
        with StoreContext(str(root / case["id"])).bind():
            started = time.perf_counter()
            mids = []
            for text in case["records"]:
                assert observe(text, source="user", force_promote=True).ok
                mids.append(next(r["mem_id"] for r in memid.iter_all() if r["text"] == text))
            written = time.perf_counter() - started
            if backend is not None:
                semantic.set_provider(backend)
                semantic.index_pending(limit=None)
                state = semantic.stats()
                assert state["ready"] and state["pending"] == 0, state
            started = time.perf_counter()
            packet = recall_request(RecallRequest(case["query"], token_budget=700,
                                                  deadline=time.monotonic() + 5))
            elapsed = time.perf_counter() - started
            evidence = {e.source_mem_id for e in packet.evidence}
            visible = [i for i, mid in enumerate(mids) if mid in evidence and case["records"][i] in packet.text]
            results.append({"id": case["id"], "group": case["group"], "split": case["split"],
                            "kind": case["kind"], "direction": case["direction"],
                            "expected": case["expected"], "visible": visible,
                            "hits": sum(i in visible for i in case["expected"]),
                            "forbidden": sum(i in visible for i in case["excluded"]),
                            "extra": sum(i not in case["expected"] for i in visible),
                            "write_seconds": written, "recall_seconds": elapsed,
                            "injected_characters": len(packet.text), "status": packet.status})
        memid.close_store_connections()
    summary = {}
    for kind in sorted({r["kind"] for r in results}):
        rows = [r for r in results if r["kind"] == kind]
        summary[kind] = {"cases": len(rows), "hits": sum(r["hits"] for r in rows),
                         "expected": sum(len(r["expected"]) for r in rows),
                         "forbidden": sum(r["forbidden"] for r in rows),
                         "extra": sum(r["extra"] for r in rows)}
    return {"summary": summary, "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=REPO / "benchmarks/fixtures/language_recall_v1.json")
    parser.add_argument("--backend", choices=("off", "local"), default="off")
    parser.add_argument("--model", help="Pinned local sentence-transformers model path; required for local")
    parser.add_argument("--model-revision", default=None)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()
    if args.backend == "local" and (not args.model or not Path(args.model).is_dir()):
        parser.error("local mode requires an existing pinned --model directory")
    with tempfile.TemporaryDirectory(prefix="nyx-language-recall-") as root:
        os.environ.update(NEXSANDBASE_HOME=root + "/legacy", HERMES_HOME=root + "/hermes",
                          XDG_CONFIG_HOME=root + "/config", NYX_EMBED="off", NYX_AUTO_PURGE="off",
                          NYX_L3_LLM="off", NYX_RERANK="off")
        for name in ("NYX_TIMEZONE", "NYX_LOCALE", "NYX_UNDERSTAND_LLM", "WTHREAD_LLM_EXTRACTION",
                     "NYX_EMBED_PROVIDER", "NYX_EMBED_MODEL", "EMBEDDING_API_URL", "NYX_NOW",
                     "NYX_EMBED_Z", "NYX_EMBED_MIN_SCORE", "NYX_EMBED_WEIGHT"):
            os.environ.pop(name, None)
        backend, loaded = None, None
        model_details = {"ready": False, "device": None, "dimension": None, "packages": {}}
        if args.backend == "local":
            import torch
            torch.set_num_threads(2)
            from nexsandglass.core.semantic import _Local
            backend = _Local(args.model)
            before = time.perf_counter()
            if not backend.load():
                raise RuntimeError("real local model failed to load")
            loaded = time.perf_counter() - before
            model_details = {"ready": backend.ready, "device": str(backend._model.device),
                             "dimension": backend._model.get_sentence_embedding_dimension(),
                             "packages": {name: importlib.metadata.version(name) for name in
                                          ("torch", "sentence-transformers", "transformers")}}
        raw = args.corpus.read_bytes()
        report = evaluate(json.loads(raw), Path(root), backend)
    report.update(schema_version=1, corpus_sha256=sha256(raw).hexdigest(), backend=args.backend,
                  model_revision=args.model_revision, model_load_seconds=loaded,
                  model_details=model_details, external_inference_requests=0,
                  source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
                  code_dirty=bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO, text=True).strip()),
                  python=sys.version.split()[0], limits=["synthetic regression, not blind or open-domain quality",
                  "original full evidence visibility, not answer-model utilization", "API mode not evaluated by this runner"])
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report["summary"]))


if __name__ == "__main__":
    main()
