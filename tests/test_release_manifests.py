"""The pip wheel and both Hermes repository installs identify one release."""
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.search(
    r"^__version__\s*=\s*['\"]([^'\"]+)['\"]",
    (ROOT / "nexsandglass/core/sandglass_paths.py").read_text(encoding="utf-8"),
    re.M,
).group(1)


@pytest.mark.parametrize("directory", [
    "nexsandglass_hermes", "hermes-plugin", "hermes-plugin-vector",
])
def test_packaged_and_repository_manifests_share_the_release_version(directory):
    manifest = (ROOT / directory / "plugin.yaml").read_text(encoding="utf-8")
    assert re.search(r"^name:\s*nyx\s*$", manifest, re.M)
    assert re.search(r"^version:\s*(\S+)\s*$", manifest, re.M).group(1) == VERSION


@pytest.mark.parametrize("directory,extras", [
    ("hermes-plugin", ""), ("hermes-plugin-vector", "[vector]"),
])
def test_repository_install_requires_the_same_release_and_bounds_the_next_major(directory, extras):
    manifest = (ROOT / directory / "plugin.yaml").read_text(encoding="utf-8")
    dependencies = manifest.split("python_dependencies:", 1)[1]
    requirement = re.search(r"^\s*-\s*(\S+)\s*$", dependencies, re.M).group(1)
    upper = int(VERSION.split(".", 1)[0]) + 1
    assert requirement == f"nyx-memory{extras}>={VERSION},<{upper}"
