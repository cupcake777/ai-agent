from pathlib import Path

import pytest


DOC = Path("website/docs/user-guide/windows-native.md")
pytestmark = pytest.mark.skipif(
    not DOC.exists(), reason="website/ is intentionally pruned in this fork"
)


def test_windows_native_install_path_docs_match_installer() -> None:
    doc = DOC.read_text()
    install = Path("scripts/install.ps1").read_text()

    # The launchers live in a dedicated bin/ dir on PATH — NOT the whole
    # venv\Scripts (which would shadow the user's python, #83797).
    assert "%LOCALAPPDATA%\\hermes\\hermes-agent\\bin" in doc
    assert (
        "Get-Command hermes        # should print "
        "C:\\Users\\<you>\\AppData\\Local\\hermes\\hermes-agent\\bin\\hermes.exe"
    ) in doc
    # Installer exposes $InstallDir\bin, and must copy the launchers into it.
    assert '$hermesBin = "$InstallDir\\bin"' in install
    assert "hermes.exe" in install and "hermes-acp.exe" in install
    # Guard against a regression back to putting venv\Scripts on PATH.
    assert '$hermesBin = "$InstallDir\\venv\\Scripts"' not in install
