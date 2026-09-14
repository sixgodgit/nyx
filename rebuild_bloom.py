#!/usr/bin/env python3
"""Rebuild Nyx veil + phantom index from full sandglass history via nyx_kindle."""
import os
import re
import sys
import time

sys.path.insert(0, "/root/.hermes/NexSandglass")
NB = "/root/.hermes/nexsandglass"
SG = os.path.join(NB, "sandglass.txt")

start = time.time()
total = 0
with open(SG, encoding="utf-8", errors="replace") as f:
    for _ in f:
        total += 1
print(f"TOTAL_SANDS: {total}")

import nexsandglass.interfaces.nyx as nyx
nyx._awaken()
print(f"BLOOM_BEFORE: {nyx._veil._count}")

pat = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| [^|]+ \| (.*)$")
processed = 0
with open(SG, encoding="utf-8", errors="replace") as f:
    for line_num, line in enumerate(f, 1):
        m = pat.match(line.rstrip("\n"))
        if not m:
            continue
        moment, text = m.group(1), m.group(2).strip()
        if not text:
            continue
        try:
            nyx.nyx_kindle(line_num, moment, text)
        except Exception:
            pass
        processed += 1
        if processed % 3000 == 0:
            print(f"  ...{processed}/{total} bloom={nyx._veil._count}", flush=True)

try:
    nyx._veil.rest(nyx._VEIL_PATH)
    print("VEIL_SAVED")
except Exception as e:
    print("VEIL_SAVE_ERR:", str(e)[:80])

print(f"BLOOM_AFTER: {nyx._veil._count}")
print(f"PROCESSED: {processed}/{total}")
print(f"ELAPSED: {time.time()-start:.1f}s")
