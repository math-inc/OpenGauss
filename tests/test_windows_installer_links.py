from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_windows_installers_reference_current_repository():
    install_ps1 = (REPO_ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")
    install_cmd = (REPO_ROOT / "scripts" / "install.cmd").read_text(encoding="utf-8")

    for source in (install_ps1, install_cmd):
        assert "math-inc/OpenGauss" in source
        assert "morph-labs/gauss-agent" not in source
        assert "NousResearch/gauss-agent" not in source
