#!/usr/bin/env python3
"""No-model language regression: extraction, profile, final recall separately.

Every case uses a temporary StoreContext. No user's data or credential sources
are used. It measures original evidence visibility, not model answer quality.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def evaluate(corpus, split, root):
    from nexsandglass.core import clock, memid, profile, understand
    from nexsandglass.runtime import StoreContext, RecallRequest, observe, recall_request
    results = []
    for group in corpus["groups"]:
        if split != "all" and group["split"] != split:
            continue
        for case in group["cases"]:
            with StoreContext(str(root / case["id"])).bind():
                mids = []
                extracted = []
                classification_correct = 0
                for i, record in enumerate(case["records"]):
                    when = f"2026-10-{i + 1:02d} 09:00:00"
                    with clock.frozen(when):
                        report = observe(record["text"], source=record["source"], force_promote=True)
                        if not report.ok:
                            raise RuntimeError("fixture write failed: " + case["id"])
                        mids.append(next(r["mem_id"] for r in memid.iter_all() if r["text"] == record["text"]))
                        extracted.append(len(understand.extract_rules(record["text"], now=clock.now())))
                        hit = profile.classify(record["text"])
                        classification_correct += (hit[0] if hit else None) == record["expected_kind"]
                profile.refresh_ready(deadline=time.monotonic() + 5)
                view = profile.items()
                profile_ids = {row[2] for kind in profile.KINDS for row in view[kind]}
                packet = recall_request(RecallRequest(case["query"], token_budget=700,
                                                      session_id="language-eval", deadline=time.monotonic() + 5))
                evidence_ids = {e.source_mem_id for e in packet.evidence}
                expected = case["expected_recall"]
                visible = sum(mids[i] in evidence_ids and case["records"][i]["text"] in packet.text for i in expected)
                expected_profile = case["expected_profile"]
                current = sum(mids[i] in profile_ids for i in expected_profile)
                stale = sum(mids[i] in profile_ids for i in case["forbidden_profile"])
                results.append({"id": case["id"], "group_id": group["group_id"], "split": group["split"],
                                "scenario": group["scenario"], "direction": case["source_language"] + "->" + case["query_language"],
                                "classification_correct": classification_correct, "classification_total": len(case["records"]),
                                "recall_visible": visible, "recall_expected": len(expected),
                                "profile_visible": current, "profile_expected": len(expected_profile),
                                "stale_profile": stale, "extracted_facts": extracted,
                                "injected_characters": len(packet.text), "packet_status": packet.status})
            memid.close_store_connections()
    summary = {}
    for direction in sorted({r["direction"] for r in results}):
        rows = [r for r in results if r["direction"] == direction]
        totals = {key: sum(r[key] for r in rows) for key in (
            "classification_correct", "classification_total", "recall_visible", "recall_expected", "profile_visible", "profile_expected", "stale_profile")}
        summary[direction] = {"cases": len(rows), **totals}
    pairs = []
    for group in corpus["groups"]:
        if split != "all" and group["split"] != split:
            continue
        for pair in group.get("slot_pairs", []):
            a, b = profile.slots(pair["kind"], pair["a"]), profile.slots(pair["kind"], pair["b"])
            equivalent = bool(a and b and a == b)
            pairs.append({"group_id": group["group_id"], "correct": equivalent == pair["equivalent"],
                          "a_slots": sorted(a), "b_slots": sorted(b)})
    return {"summary": summary, "results": results, "slot_pairs": pairs,
            "limits": ["synthetic grouped regression; not blind or independent generalization evidence",
                       "lexical/rule configuration only; no embedding or model answer calls",
                       "profile visibility and historical recall are different metrics",
                       "extraction counts are diagnostics, not extraction precision"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--split", choices=("dev", "heldout", "all"), default="dev")
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()
    # This runner is deliberately standalone; never inherit external providers.
    with tempfile.TemporaryDirectory(prefix="nyx-language-eval-") as root:
        os.environ.update(NEXSANDBASE_HOME=root + "/legacy", HERMES_HOME=root + "/hermes",
                          XDG_CONFIG_HOME=root + "/config", NYX_EMBED="off", NYX_AUTO_PURGE="off",
                          NYX_UNDERSTAND_LLM="off", NYX_L3_LLM="off", NYX_RERANK="off")
        for name in ("WTHREAD_LLM_EXTRACTION", "NYX_UNDERSTAND_LLM", "EMBEDDING_API_URL", "NYX_EMBED_PROVIDER"):
            os.environ.pop(name, None)
        raw = args.corpus.read_bytes()
        report = evaluate(json.loads(raw), args.split, Path(root))
    report.update(schema_version=1, corpus_sha256=sha256(raw).hexdigest(),
                  source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
                  code_dirty=bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO, text=True).strip()),
                  python=sys.version.split()[0], backend="lexical-rules-no-model", split=args.split)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
