#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/src}"
GAUSS_HOME="${GAUSS_HOME:-/root/.gauss}"
WORKSPACE_DIR="${WORKSPACE_DIR:-/root/GaussWorkspaceSmoke}"
OPENAI_API_KEY="${OPENAI_API_KEY:-dummy-installer-key}"

die() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 1
}

assert_exists() {
    local path="$1"
    [ -e "$path" ] || die "expected path to exist: $path"
}

assert_command() {
    local cmd="$1"
    command -v "$cmd" >/dev/null 2>&1 || die "expected command on PATH: $cmd"
}

if [[ ! -f "$REPO_ROOT/scripts/install.sh" ]]; then
    die "installer script not found under $REPO_ROOT"
fi

if [[ ! -e "$REPO_ROOT/.git" ]]; then
    die "$REPO_ROOT must be a git checkout"
fi

echo "==> Installer scenario: ubuntu_repository_local_install_smoke"
echo "==> Using repository checkout: $REPO_ROOT"

cd "$REPO_ROOT"
INSTALL_LOG="$(mktemp)"
PATH_HAS_LOCAL_BIN=0
case ":$PATH:" in
    *":$HOME/.local/bin:"*)
        PATH_HAS_LOCAL_BIN=1
        ;;
esac
export OPENAI_API_KEY
./scripts/install.sh \
    --gauss-home "$GAUSS_HOME" \
    --workspace-dir "$WORKSPACE_DIR" \
    --with-workspace \
    2>&1 | tee "$INSTALL_LOG"

echo "==> Verifying first-run shell guidance"
assert_exists "$HOME/.local/bin/gauss"
grep -F "Start immediately:" "$INSTALL_LOG" >/dev/null || die "expected installer summary to show the direct gauss path"
grep -F "$HOME/.local/bin/gauss" "$INSTALL_LOG" >/dev/null || die "expected installer summary to print the linked gauss path"
grep -F "Start Options:" "$INSTALL_LOG" >/dev/null || die "expected installer summary to list post-install start options"
grep -F "gauss-open-session" "$INSTALL_LOG" >/dev/null || die "expected installer summary to mention gauss-open-session"
grep -F "gauss-open-guide" "$INSTALL_LOG" >/dev/null || die "expected installer summary to mention gauss-open-guide"
grep -F "cannot change PATH in the shell that launched ./scripts/install.sh" "$INSTALL_LOG" >/dev/null || die "expected installer summary to explain current-shell PATH behavior"
grep -F "Managed /prove staging verified:" "$INSTALL_LOG" >/dev/null || die "expected installer to verify managed /prove staging in the Lean workspace"
if grep -F "Skipping managed /prove staging verification" "$INSTALL_LOG" >/dev/null; then
    die "expected installer managed /prove verification to run in the Lean workspace"
fi
if grep -F "Would you like to run the setup wizard now?" "$INSTALL_LOG" >/dev/null; then
    die "expected installer to skip the setup wizard prompt when a main provider was auto-configured"
fi
if [ "$PATH_HAS_LOCAL_BIN" -ne 1 ] && command -v gauss >/dev/null 2>&1; then
    die "expected gauss to stay off PATH until the shell is reloaded"
fi

export PATH="$HOME/.local/bin:$REPO_ROOT/venv/bin:$HOME/.elan/bin:$PATH"
export GAUSS_HOME

echo "==> Verifying core commands"
for cmd in gauss uv node npm claude codex elan lake rg tmux ffmpeg; do
    assert_command "$cmd"
done

echo "==> Verifying workflow outputs"
assert_exists "$GAUSS_HOME/.env"
assert_exists "$GAUSS_HOME/config.yaml"
assert_exists "$GAUSS_HOME/install-root"
assert_exists "$GAUSS_HOME/guide/index.html"
assert_exists "$GAUSS_HOME/skins/mathinc.yaml"
assert_exists "$WORKSPACE_DIR/PAPER.md"
assert_exists "$WORKSPACE_DIR/.gauss/project.yaml"
assert_exists "$HOME/.local/bin/gauss-configure-main-provider"
assert_exists "$HOME/.local/bin/gauss-open-session"
assert_exists "$HOME/.local/bin/gauss-open-guide"
assert_exists "$HOME/.local/bin/gauss-launch-session"

echo "==> Verifying recorded install root"
INSTALL_ROOT_VALUE="$(cat "$GAUSS_HOME/install-root")"
[[ "$INSTALL_ROOT_VALUE" == "$REPO_ROOT" ]] || die "install-root mismatch: $INSTALL_ROOT_VALUE"

echo "==> Verifying config defaults and staged provider state"
python3 - "$GAUSS_HOME" "$WORKSPACE_DIR" "$OPENAI_API_KEY" <<'PY'
from pathlib import Path
import sys
import yaml

gauss_home = Path(sys.argv[1])
workspace_dir = Path(sys.argv[2])
expected_key = sys.argv[3]

config = yaml.safe_load((gauss_home / "config.yaml").read_text(encoding="utf-8"))
assert config["display"]["skin"] == "mathinc"
assert config["terminal"]["backend"] == "local"
assert config["terminal"]["cwd"] == str(workspace_dir)
assert config["gauss"]["autoformalize"]["backend"] == "claude-code"
assert config["gauss"]["autoformalize"]["auth_mode"] == "auto"
assert config["agent"]["max_turns"] == 90
assert config["model"]["provider"] == "custom"
assert config["model"]["default"] == "gpt-5.4"
assert config["model"]["base_url"] == "https://api.openai.com/v1"

env_text = (gauss_home / ".env").read_text(encoding="utf-8")
assert f'OPENAI_API_KEY="{expected_key}"' in env_text
assert 'OPENAI_BASE_URL="https://api.openai.com/v1"' in env_text
PY

echo "==> Verifying gauss works from the repository-local venv"
GAUSS_VERSION_OUTPUT="$(gauss --version)"
printf '%s\n' "$GAUSS_VERSION_OUTPUT"
[[ "$GAUSS_VERSION_OUTPUT" == *"Gauss v"* ]] || die "unexpected gauss --version output"

echo "==> Verifying rerun idempotence and staged-key preservation"
printf '\nSMOKE_RERUN_MARKER\n' >> "$WORKSPACE_DIR/PAPER.md"
touch "$WORKSPACE_DIR/KEEP_ME.txt"
unset OPENAI_API_KEY OPENROUTER_API_KEY ANTHROPIC_API_KEY
./scripts/install.sh \
    --gauss-home "$GAUSS_HOME" \
    --workspace-dir "$WORKSPACE_DIR" \
    --with-workspace \
    --skip-system-packages
grep -F 'SMOKE_RERUN_MARKER' "$WORKSPACE_DIR/PAPER.md" >/dev/null || die "expected PAPER.md marker to survive rerun"
assert_exists "$WORKSPACE_DIR/KEEP_ME.txt"
grep -F 'OPENAI_API_KEY="dummy-installer-key"' "$GAUSS_HOME/.env" >/dev/null || die "expected staged OPENAI_API_KEY to be preserved on rerun"
grep -F 'OPENAI_BASE_URL="https://api.openai.com/v1"' "$GAUSS_HOME/.env" >/dev/null || die "expected OPENAI_BASE_URL to be preserved on rerun"

echo "==> Verifying launcher summary"
SUMMARY_OUTPUT="$(gauss-launch-session --print-summary)"
printf '%s\n' "$SUMMARY_OUTPUT"
[[ "$SUMMARY_OUTPUT" == *"OpenAI-compatible main provider configured"* ]] || die "expected OpenAI provider summary"
[[ "$SUMMARY_OUTPUT" == *"$WORKSPACE_DIR"* ]] || die "expected workspace path in launcher summary"

echo "==> ubuntu_repository_local_install_smoke passed"
