"""``_locate_real_git`` heals a broken Git-for-Windows shim from the install that owns it."""

from pathlib import Path

from hermes_cli import update_cmd_git


def _install(root: Path, *real: str) -> Path:
    """A Git-for-Windows tree under *root*: the ``cmd`` shim plus the given real-binary paths."""
    shim = root / "cmd" / "git.exe"
    for rel in ("cmd/git.exe", *real):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"MZ")
    return shim


def _healthy_probe(monkeypatch):
    # Host-independent: a Windows box's own Program Files Git must not answer first.
    monkeypatch.setattr(update_cmd_git, "_PROGRAM_FILES_GIT_ROOTS", ())
    monkeypatch.setattr(update_cmd_git, "_probe_fork_bomb", lambda argv: False)
    monkeypatch.setattr(update_cmd_git, "_portable_git_candidates", list)


def test_path_git_outside_program_files_heals_to_its_own_git_core(tmp_path, monkeypatch):
    root = tmp_path / "D" / "tools" / "PortableGit"
    shim = _install(root, "mingw64/libexec/git-core/git.exe")
    monkeypatch.setattr("hermes_cli.update_pause_record._git_executable", lambda: str(shim))
    _healthy_probe(monkeypatch)

    assert update_cmd_git._locate_real_git() == root / "mingw64" / "libexec" / "git-core" / "git.exe"


def test_layout_without_a_libexec_copy_uses_the_arch_bin_binary(tmp_path, monkeypatch):
    root = tmp_path / "Git"
    shim = _install(root, "mingw64/bin/git.exe")
    monkeypatch.setattr("hermes_cli.update_pause_record._git_executable", lambda: str(shim))
    _healthy_probe(monkeypatch)

    assert update_cmd_git._locate_real_git() == root / "mingw64" / "bin" / "git.exe"


def test_a_real_binary_on_path_adds_no_candidates(tmp_path, monkeypatch):
    real = tmp_path / "Git" / "mingw64" / "bin" / "git.exe"
    real.parent.mkdir(parents=True)
    real.write_bytes(b"MZ")
    monkeypatch.setattr("hermes_cli.update_pause_record._git_executable", lambda: str(real))
    _healthy_probe(monkeypatch)

    assert update_cmd_git._locate_real_git() is None
