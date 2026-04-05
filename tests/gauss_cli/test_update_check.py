"""Tests for the update check mechanism in gauss_cli.banner."""

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_version_string_no_v_prefix():
    """__version__ should be bare semver without a 'v' prefix."""
    from gauss_cli import __version__
    assert not __version__.startswith("v"), f"__version__ should not start with 'v', got {__version__!r}"


def test_check_for_updates_uses_cache(tmp_path):
    """When cache is fresh, check_for_updates should return cached value without calling git."""
    from gauss_cli.banner import check_for_updates

    # Create a fake git repo recorded by the installer and fresh cache
    repo_dir = tmp_path / "checkout"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()
    (tmp_path / "install-root").write_text(str(repo_dir))

    cache_file = tmp_path / ".update_check"
    cache_file.write_text(json.dumps({"ts": time.time(), "behind": 3}))

    with patch.dict("os.environ", {"GAUSS_HOME": str(tmp_path)}, clear=False):
        with patch("gauss_cli.banner.subprocess.run") as mock_run:
            result = check_for_updates()

    assert result == 3
    mock_run.assert_not_called()


def test_check_for_updates_expired_cache(tmp_path):
    """When cache is expired, check_for_updates should call git fetch."""
    from gauss_cli.banner import check_for_updates

    repo_dir = tmp_path / "checkout"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()
    (tmp_path / "install-root").write_text(str(repo_dir))

    # Write an expired cache (timestamp far in the past)
    cache_file = tmp_path / ".update_check"
    cache_file.write_text(json.dumps({"ts": 0, "behind": 1}))

    mock_result = MagicMock(returncode=0, stdout="5\n")

    with patch.dict("os.environ", {"GAUSS_HOME": str(tmp_path)}, clear=False):
        with patch("gauss_cli.banner.subprocess.run", return_value=mock_result) as mock_run:
            result = check_for_updates()

    assert result == 5
    assert mock_run.call_count == 3  # git fetch + upstream lookup + git rev-list


def test_check_for_updates_no_git_dir(tmp_path):
    """Returns None when .git directory doesn't exist anywhere."""
    import gauss_cli.banner as banner

    # Create a fake banner.py so the fallback path also has no .git
    fake_banner = tmp_path / "gauss_cli" / "banner.py"
    fake_banner.parent.mkdir(parents=True, exist_ok=True)
    fake_banner.touch()

    original = banner.__file__
    try:
        banner.__file__ = str(fake_banner)
        with patch.dict("os.environ", {"GAUSS_HOME": str(tmp_path)}, clear=False):
            with patch("gauss_cli.banner.subprocess.run") as mock_run:
                result = banner.check_for_updates()
        assert result is None
        mock_run.assert_not_called()
    finally:
        banner.__file__ = original


def test_check_for_updates_can_be_disabled_via_env(tmp_path):
    """Installer and CI callers can skip the update probe entirely."""
    from gauss_cli.banner import check_for_updates

    repo_dir = tmp_path / "checkout"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()
    (tmp_path / "install-root").write_text(str(repo_dir))

    with patch.dict(
        "os.environ",
        {"GAUSS_HOME": str(tmp_path), "GAUSS_SKIP_UPDATE_CHECK": "1"},
        clear=False,
    ):
        with patch("gauss_cli.banner.subprocess.run") as mock_run:
            result = check_for_updates()

    assert result is None
    mock_run.assert_not_called()


def test_check_for_updates_fallback_to_project_root():
    """Dev install: falls back to Path(__file__).parent.parent when GAUSS_HOME has no git repo."""
    import gauss_cli.banner as banner

    project_root = Path(banner.__file__).parent.parent.resolve()
    if not (project_root / ".git").exists():
        pytest.skip("Not running from a git checkout")

    # Point GAUSS_HOME at a temp dir with no recorded install root.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        with patch.dict("os.environ", {"GAUSS_HOME": td}, clear=False):
            with patch("gauss_cli.banner.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="0\n")
                result = banner.check_for_updates()
        # Should have fallen back to project root and run git commands
        assert mock_run.call_count >= 1


def test_check_for_updates_legacy_home_checkout_still_works(tmp_path):
    """Older ~/.gauss/opengauss-dev installs still resolve for update checks."""
    from gauss_cli.banner import check_for_updates

    legacy_repo = tmp_path / "opengauss-dev"
    legacy_repo.mkdir()
    (legacy_repo / ".git").mkdir()

    mock_result = MagicMock(returncode=0, stdout="2\n")

    with patch.dict("os.environ", {"GAUSS_HOME": str(tmp_path)}, clear=False):
        with patch("gauss_cli.banner.subprocess.run", return_value=mock_result) as mock_run:
            result = check_for_updates()

    assert result == 2
    assert mock_run.call_count == 3


def test_prefetch_non_blocking():
    """prefetch_update_check() should return immediately without blocking."""
    import gauss_cli.banner as banner

    # Reset module state
    banner._update_result = None
    banner._update_check_done = threading.Event()

    with patch.object(banner, "check_for_updates", return_value=5):
        start = time.monotonic()
        banner.prefetch_update_check()
        elapsed = time.monotonic() - start

        # Should return almost immediately (well under 1 second)
        assert elapsed < 1.0

        # Wait for the background thread to finish
        banner._update_check_done.wait(timeout=5)
        assert banner._update_result == 5


def test_get_update_result_timeout():
    """get_update_result() returns None when check hasn't completed within timeout."""
    import gauss_cli.banner as banner

    # Reset module state — don't set the event
    banner._update_result = None
    banner._update_check_done = threading.Event()

    start = time.monotonic()
    result = banner.get_update_result(timeout=0.1)
    elapsed = time.monotonic() - start

    # Should have waited ~0.1s and returned None
    assert result is None
    assert elapsed < 0.5
