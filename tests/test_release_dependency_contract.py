import json
from pathlib import Path

from packaging.requirements import Requirement


ROOT = Path(__file__).resolve().parents[1]


def test_build_copies_constraints_before_installation():
    config = json.loads((ROOT / "railpack.json").read_text())
    inputs = config["steps"]["install"]["inputs"]
    assert inputs[0] == "..."
    assert {"local": True, "include": ["requirements-python312.lock.txt"]} in inputs


def requirements(path):
    return [Requirement(line.strip()) for line in path.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith(("#", "-"))]


def test_production_installs_use_verified_constraints():
    lines = (ROOT / "requirements.txt").read_text().splitlines()
    assert "-c requirements-python312.lock.txt" in lines


def test_direct_runtime_versions_match_lock():
    locked = {item.name.lower(): str(item.specifier)
              for item in requirements(ROOT / "requirements-python312.lock.txt")}
    for item in requirements(ROOT / "requirements.txt"):
        assert str(item.specifier) == locked[item.name.lower()]


def test_lock_has_exact_versions_without_unbounded_runtime_dependencies():
    for item in requirements(ROOT / "requirements-python312.lock.txt"):
        versions = list(item.specifier)
        assert len(versions) == 1
        assert versions[0].operator == "=="
        assert "*" not in versions[0].version
