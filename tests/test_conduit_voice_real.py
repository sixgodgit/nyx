"""Optional cross-repository integration, without real audio/model calls."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(not (os.environ.get("CONDUIT_NOTIFIER_SRC") and os.environ.get("HERMES_AGENT_SRC")),
                    reason="Requires real Conduit notifier and Hermes source checkouts")
def test_real_conduit_startup_memory():
    script = Path(__file__).parent / "integration/conduit_voice_check.py"
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, (result.stdout + result.stderr)[-5000:]
    assert "CONDUIT NYX VOICE CHECK: PASS" in result.stdout
