from pathlib import Path

import pytest


DOC = Path("website/docs/user-guide/windows-native.md")
pytestmark = pytest.mark.skipif(
    not DOC.exists(), reason="website/ is intentionally pruned in this fork"
)


def test_windows_native_install_path_docs_match_installer() -> None:
    doc = DOC.read_text()
    install = Path("scripts/install.ps1").read_text()

    assert "%LOCALAPPDATA%\\hermes\\hermes-agent\\venv\\Scripts" in doc
    assert "Get-Command hermes        # should print C:\\Users\\<you>\\AppData\\Local\\hermes\\hermes-agent\\venv\\Scripts\\hermes.exe" in doc
    assert '$hermesBin = "$InstallDir\\venv\\Scripts"' in install
