import os
import shutil
import subprocess
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_executable(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    path.chmod(0o755)


def test_install_wrapper_translates_installer_flags_for_local_template_run(tmp_path):
    repo = tmp_path / "repo"
    scripts_dir = repo / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts" / "install.sh", scripts_dir / "install.sh")

    runner_bin = repo / ".opengauss-installer-venv" / "bin"
    runner_bin.mkdir(parents=True)
    _write_executable(
        runner_bin / "python",
        """#!/usr/bin/env bash
        exit 0
        """,
    )

    args_log = tmp_path / "morph-args.txt"
    env_log = tmp_path / "morph-env.txt"
    _write_executable(
        runner_bin / "morphcloud",
        f"""#!/usr/bin/env bash
        set -euo pipefail
        printf '%s\n' "$@" > "{args_log}"
        python3 - <<'PY'
import os
from pathlib import Path

keys = [
    "GAUSS_HOME",
    "GAUSS_WORKSPACE_DIR",
    "GAUSS_SKIP_SYSTEM_PACKAGES",
    "GAUSS_CREATE_WORKSPACE",
    "GAUSS_SETUP_MODE",
    "GAUSS_RECREATE_VENV",
]
Path("{env_log}").write_text(
    "".join(f"{{key}}={{os.environ.get(key, '')}}\\n" for key in keys),
    encoding="utf-8",
)
PY
        """,
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "uv",
        """#!/usr/bin/env bash
        set -euo pipefail
        exit 0
        """,
    )
    _write_executable(
        fake_bin / "tmux",
        """#!/usr/bin/env bash
        set -euo pipefail
        exit 1
        """,
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["OPEN_GAUSS_AUTO_ATTACH"] = "0"

    result = subprocess.run(
        [
            "bash",
            "scripts/install.sh",
            "--gauss-home",
            "/tmp/custom-gauss-home",
            "--workspace-dir",
            "/tmp/custom-workspace",
            "--skip-system-packages",
            "--with-workspace",
            "--skip-setup",
            "--recreate-venv",
            "--plain",
            "--json",
            "--param",
            "foo=bar",
            "--secret",
            "baz=qux",
        ],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout

    assert args_log.read_text(encoding="utf-8").splitlines() == [
        "devbox",
        "template",
        "run",
        "opengauss",
        "--experimental-run-locally",
        "--plain",
        "--json",
        "--param",
        "foo=bar",
        "--secret",
        "baz=qux",
    ]
    env_values = dict(
        line.split("=", 1)
        for line in env_log.read_text(encoding="utf-8").splitlines()
        if line
    )
    assert env_values == {
        "GAUSS_HOME": "/tmp/custom-gauss-home",
        "GAUSS_WORKSPACE_DIR": "/tmp/custom-workspace",
        "GAUSS_SKIP_SYSTEM_PACKAGES": "1",
        "GAUSS_CREATE_WORKSPACE": "1",
        "GAUSS_SETUP_MODE": "skip",
        "GAUSS_RECREATE_VENV": "1",
    }
