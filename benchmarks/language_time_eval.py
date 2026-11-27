#!/usr/bin/env python3
"""Frozen LANG-03 conformance, profile and event-target regression; no models."""
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

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def evaluate(data, root):
    from nexsandglass.runtime import StoreContext, LanguageContext, observe
    from nexsandglass.core import clock, memid, profile, followups
    from nexsandglass.language.time import resolve_time
    results = []
    for case in data["time"]:
        result = resolve_time(case["text"], reference=datetime.fromisoformat(case["reference"]),
                              language=LanguageContext(locale=case.get("locale"), timezone=case.get("timezone")))
        passed = result.status == case["status"]
        if "day" in case:
            passed &= bool(result.start and result.start.isoformat() == case["day"])
        results.append({"id": case["id"], "section": "time", "passed": passed, "resolution": result.to_dict()})
    for section in ("profile", "events"):
        for case in data[section]:
            with StoreContext(str(root / case["id"])).bind():
                ids = []
                for i, record in enumerate(case["records"]):
                    when, text = record if section == "events" else (f"2026-10-0{i+1} 09:00:00", record)
                    with clock.frozen(when):
                        assert observe(text, source="user", force_promote=True).ok
                        ids.append(next(r["mem_id"] for r in memid.iter_all() if r["text"] == text))
                if section == "events":
                    followups.refresh()
                    with clock.frozen(case["now"]):
                        rows = followups.due()
                    selected = {r["mem_id"] for r in rows}
                    days = [r["start"].isoformat() for r in rows]
                else:
                    profile.refresh()
                    view = profile.items()
                    selected = {r[2] for k in profile.KINDS for r in view[k]}
                    days = []
                visible = [i for i, mid in enumerate(ids) if mid in selected]
                passed = visible == case["keep"] and (not case.get("expected_day") or case["expected_day"] in days)
                results.append({"id": case["id"], "section": section, "visible": visible, "days": days,
                                "expected": case["keep"], "passed": bool(passed)})
            memid.close_store_connections()
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=REPO / "benchmarks/fixtures/language_time_v1.json")
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="nyx-language-time-eval-") as root:
        os.environ.update(NEXSANDBASE_HOME=root + "/legacy", HERMES_HOME=root + "/hermes",
                          XDG_CONFIG_HOME=root + "/config", NYX_EMBED="off", NYX_RERANK="off",
                          NYX_AUTO_PURGE="off", NYX_L3_LLM="off")
        for key in ("NYX_TIMEZONE", "NYX_LOCALE", "NYX_UNDERSTAND_LLM", "WTHREAD_LLM_EXTRACTION",
                    "NYX_EMBED_PROVIDER", "EMBEDDING_API_URL"):
            os.environ.pop(key, None)
        raw = args.corpus.read_bytes()
        results = evaluate(json.loads(raw), Path(root))
    summary = {section: {"passed": sum(bool(r["passed"]) for r in results if r["section"] == section),
                         "total": sum(r["section"] == section for r in results)}
               for section in ("time", "profile", "events")}
    report = {"schema_version": 1, "corpus_sha256": sha256(raw).hexdigest(), "summary": summary, "results": results,
              "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
              "code_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO, text=True).strip()),
              "python": sys.version.split()[0], "backend": "rules-no-model",
              "limits": ["synthetic conformance fixtures; not blind or open-domain language quality",
                         "new time API had no pre-change counterpart; baseline covers only profile/events",
                         "no answer-model or live-audio measurement"]}
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary))
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
