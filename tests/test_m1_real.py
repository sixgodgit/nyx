"""Public typed recall against the real write/search/erasure pipeline."""
import os
import subprocess
import sys


def test_typed_recall_write_forget_restore(tmp_path):
    env = dict(os.environ, NEXSANDBASE_HOME=str(tmp_path / "memory"),
               HERMES_HOME=str(tmp_path / "hermes"), XDG_CONFIG_HOME=str(tmp_path / "config"),
               NYX_EMBED="off", NYX_AUTO_PURGE="off")
    script = r'''
from time import monotonic
from nexsandglass.runtime import RecallRequest, observe, recall, recall_request, forget, restore
from nexsandglass.core import memid

text = "The cobalt mug is kept on shelf delta."
report = observe(text, source="user", session_id="m1-real", force_promote=True)
assert report.ok, report
mid = next(row["mem_id"] for row in memid.iter_all() if row["text"] == text)
def read():
    req = RecallRequest("cobalt mug shelf delta", session_id="m1-real", turn_id="1",
                        token_budget=600, deadline=monotonic() + 5)
    packet = recall_request(req)
    assert packet.can_apply_to(req), packet
    return packet
packet = read()
assert "shelf delta" in packet.text, packet.to_dict()
assert any(e.source_mem_id == mid and e.trust_signal == "trusted" for e in packet.evidence)
assert "shelf delta" in recall("cobalt mug shelf delta", token_budget=600).text
assert forget({"mem_id": mid})["ok"]
assert "shelf delta" not in read().text
assert restore(mid)["ok"]
assert "shelf delta" in read().text
print("M1 REAL PIPELINE: PASS")
'''
    result = subprocess.run([sys.executable, "-c", script], env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, (result.stdout + result.stderr)[-5000:]
    assert "M1 REAL PIPELINE: PASS" in result.stdout
