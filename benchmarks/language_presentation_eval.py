#!/usr/bin/env python3
"""Frozen presentation conformance; no translation or model-answer grading."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()
    raw = (REPO / "benchmarks/fixtures/language_presentation_v1.json").read_bytes()
    results = []
    with tempfile.TemporaryDirectory(prefix="nyx-presentation-eval-") as folder:
        root = Path(folder)
        os.environ.update(NEXSANDBASE_HOME=str(root/"legacy"), HERMES_HOME=str(root/"hermes"),
                          XDG_CONFIG_HOME=str(root/"config"), NYX_EMBED="off", NYX_RERANK="off",
                          NYX_AUTO_PURGE="off", NYX_L3_LLM="off", NYX_LANG="en")
        for name in ("NYX_RESPONSE_LANGUAGE", "NYX_LOCALE", "NYX_TIMEZONE", "NYX_NOW", "NYX_EMBED_PROVIDER",
                     "EMBEDDING_API_URL", "NYX_UNDERSTAND_LLM", "WTHREAD_LLM_EXTRACTION"):
            os.environ.pop(name, None)
        from nexsandglass.runtime import ContextItem, ContextSection, EvidenceRef, LanguageContext, RecallRequest, StoreContext
        from nexsandglass.runtime.host_context import render_host_context
        from nexsandglass.runtime.presentation import presentation_for
        from nexsandglass.core import memid, provenance
        for case in json.loads(raw)["cases"]:
            with StoreContext(str(root/case["id"])).bind():
                mid = memid.allocate(case["quote"], sender=case["source"])
                original = memid.get(mid)
                prov = provenance.get(mid)
                ref = EvidenceRef(mid, mid, origin=prov.origin, trust_signal=prov.signal)
                language = LanguageContext(input_language=case["input_language"], response_language=case["response_language"],
                                           response_basis="user" if case["response_language"] else "unspecified")
                request = RecallRequest("presentation conformance", language=language)
                item = ContextItem("a", prov.label()+case["quote"], evidence=(ref,))
                packet, trace = render_host_context(request, (ContextSection("retrieval", items=(item,)),),
                                                    presentation=presentation_for(language))
                actual_frame = trace.presentation.frame_language if trace.presentation else None
                passed = (actual_frame == case["frame_language"] and packet.evidence == (ref,)
                          and case["quote"] in packet.text and memid.get(mid) == original
                          and packet.matches_request(request) and trace.text_sha256 == sha256(packet.text.encode()).hexdigest())
                results.append({"id": case["id"], "passed": passed, "frame_language": actual_frame,
                                "status": packet.status, "reason_codes": list(packet.reason_codes),
                                "canonical_unchanged": memid.get(mid) == original,
                                "evidence_unchanged": packet.evidence == (ref,), "estimated_tokens": packet.est_tokens})
            memid.close_store_connections()
    report = {"schema_version": 1, "passed": sum(r["passed"] for r in results), "total": len(results),
              "results": results, "corpus_sha256": sha256(raw).hexdigest(),
              "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
              "code_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO, text=True).strip()),
              "python": sys.version.split()[0], "backend": "no-model-no-translator",
              "limits": ["synthetic new-API conformance, no comparable legacy presentation API",
                         "does not grade translation correctness, answer language adherence or model utilization"]}
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps({"passed": report["passed"], "total": report["total"]}))
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
