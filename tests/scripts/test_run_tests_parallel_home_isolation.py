"""Isolation tests for per-file subprocess homes in run_tests_parallel."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER_PATH = REPO_ROOT / "scripts" / "run_tests_parallel.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_tests_parallel", _RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_attempt_home_is_nested_under_disk_backed_temp_root(monkeypatch, tmp_path: Path) -> None:
    """An inherited TMPDIR inside the live Hermes home must not capture the test home."""
    mod = _load_runner()
    inherited_tmp = tmp_path / ".hermes" / "cache" / "scratch"
    inherited_tmp.mkdir(parents=True)
    monkeypatch.setenv("TMPDIR", str(inherited_tmp))
    runner_root = tmp_path / "runner-root"
    runner_root.mkdir()
    monkeypatch.setattr(mod, "_runner_scratch_root", lambda: str(runner_root))

    hermes_home, temproot = mod._make_attempt_temp_roots()
    try:
        home_path = Path(hermes_home.name)
        attempt_path = Path(temproot)
        assert home_path.parent == attempt_path
        assert attempt_path.parent == tmp_path / "runner-root"
        assert inherited_tmp not in home_path.parents
    finally:
        hermes_home.cleanup()
        mod._rmtree_force(temproot)
