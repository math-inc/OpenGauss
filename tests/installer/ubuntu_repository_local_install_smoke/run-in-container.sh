#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/src}"
GAUSS_HOME="${GAUSS_HOME:-/root/.gauss}"
WORKSPACE_DIR="${WORKSPACE_DIR:-/root/GaussWorkspaceSmoke}"
OPENAI_API_KEY="${OPENAI_API_KEY:-dummy-installer-key}"
INITIAL_OPENAI_API_KEY="$OPENAI_API_KEY"

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

if [[ ! -f "$REPO_ROOT/scripts/install-internal.sh" ]]; then
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
./scripts/install-internal.sh \
    --gauss-home "$GAUSS_HOME" \
    --workspace-dir "$WORKSPACE_DIR" \
    --with-workspace \
    2>&1 | tee "$INSTALL_LOG"

echo "==> Verifying first-run shell guidance"
assert_exists "$HOME/.local/bin/gauss"
grep -F "Start immediately:" "$INSTALL_LOG" >/dev/null || die "expected installer summary to show the direct gauss path"
grep -F "$HOME/.local/bin/gauss" "$INSTALL_LOG" >/dev/null || die "expected installer summary to print the linked gauss path"
grep -F "Start Options:" "$INSTALL_LOG" >/dev/null || die "expected installer summary to list post-install start options"
grep -F "/chat" "$INSTALL_LOG" >/dev/null || die "expected installer summary to mention /chat"
grep -F "/managed-chat" "$INSTALL_LOG" >/dev/null || die "expected installer summary to mention /managed-chat"
grep -F "gauss-open-session" "$INSTALL_LOG" >/dev/null || die "expected installer summary to mention gauss-open-session"
grep -F "gauss-open-guide" "$INSTALL_LOG" >/dev/null || die "expected installer summary to mention gauss-open-guide"
grep -F "cannot change PATH in the shell that launched the installer." "$INSTALL_LOG" >/dev/null || die "expected installer summary to explain current-shell PATH behavior"
if grep -F "Helper Commands:" "$INSTALL_LOG" >/dev/null; then
    die "expected installer summary to avoid helper-command clutter"
fi
if grep -F "gauss-use-openrouter-key" "$INSTALL_LOG" >/dev/null; then
    die "expected installer summary to avoid provider-key helper clutter"
fi
grep -F "Managed Lean workflow assets ready:" "$INSTALL_LOG" >/dev/null || die "expected installer to prewarm managed Lean workflow assets"
grep -F "Managed /prove staging verified:" "$INSTALL_LOG" >/dev/null || die "expected installer to verify managed /prove staging in the Lean workspace"
if grep -F "Skipping managed /prove staging verification" "$INSTALL_LOG" >/dev/null; then
    die "expected installer managed /prove verification to run in the Lean workspace"
fi
if grep -F "Would you like to run the setup wizard now?" "$INSTALL_LOG" >/dev/null; then
    die "expected installer auto mode to stay non-interactive"
fi
if [ "$PATH_HAS_LOCAL_BIN" -ne 1 ] && command -v gauss >/dev/null 2>&1; then
    die "expected gauss to stay off PATH until the shell is reloaded"
fi

export PATH="$HOME/.local/bin:$REPO_ROOT/venv/bin:$HOME/.elan/bin:$PATH"
export GAUSS_HOME
grep -F 'export GAUSS_HOME="${GAUSS_HOME:-' "$HOME/.bashrc" >/dev/null || die "expected shell block to preserve an explicitly set GAUSS_HOME"

echo "==> Verifying core commands"
for cmd in gauss uv node npm claude codex elan lake rg tmux ffmpeg; do
    assert_command "$cmd"
done

echo "==> Verifying workflow outputs"
assert_exists "$GAUSS_HOME/.env"
assert_exists "$GAUSS_HOME/config.yaml"
assert_exists "$GAUSS_HOME/install-root"
assert_exists "$GAUSS_HOME/guide/index.html"
grep -F "Start Here" "$GAUSS_HOME/guide/index.html" >/dev/null || die "expected generated guide to include Start Here"
grep -F "/chat" "$GAUSS_HOME/guide/index.html" >/dev/null || die "expected generated guide to mention /chat"
grep -F "/managed-chat" "$GAUSS_HOME/guide/index.html" >/dev/null || die "expected generated guide to mention /managed-chat"
grep -F "If You Opened This In Morph" "$GAUSS_HOME/guide/index.html" >/dev/null || die "expected generated guide to include Morph guidance"
if grep -F "gauss-use-claude-login" "$GAUSS_HOME/guide/index.html" >/dev/null; then
    die "expected generated guide to avoid login-helper clutter"
fi
if grep -F "$GAUSS_HOME/.env" "$GAUSS_HOME/guide/index.html" >/dev/null; then
    die "expected generated guide to avoid exposing the staged .env path"
fi
assert_exists "$GAUSS_HOME/autoformalize/assets/lean4-skills/.gauss-managed-revision"
assert_exists "$GAUSS_HOME/skins/mathinc.yaml"
assert_exists "$WORKSPACE_DIR/PAPER.md"
assert_exists "$WORKSPACE_DIR/.gauss/project.yaml"
assert_exists "$WORKSPACE_DIR/lean-toolchain"
assert_exists "$HOME/.local/bin/gauss-configure-main-provider"
assert_exists "$HOME/.local/bin/gauss-open-session"
assert_exists "$HOME/.local/bin/gauss-open-guide"
assert_exists "$HOME/.local/bin/gauss-launch-session"
assert_exists "$HOME/.claude/settings.json"
assert_exists "$HOME/.claude/plugins/known_marketplaces.json"
assert_exists "$HOME/.claude/plugins/installed_plugins.json"

echo "==> Verifying recorded install root"
INSTALL_ROOT_VALUE="$(cat "$GAUSS_HOME/install-root")"
[[ "$INSTALL_ROOT_VALUE" == "$REPO_ROOT" ]] || die "install-root mismatch: $INSTALL_ROOT_VALUE"

echo "==> Verifying config defaults and staged provider state"
python3 - "$GAUSS_HOME" "$WORKSPACE_DIR" "$INITIAL_OPENAI_API_KEY" "$HOME" <<'PY'
from pathlib import Path
import json
import sys
import yaml

gauss_home = Path(sys.argv[1])
workspace_dir = Path(sys.argv[2])
expected_key = sys.argv[3]
home_dir = Path(sys.argv[4])

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
assert (workspace_dir / "lean-toolchain").read_text(encoding="utf-8").strip() == "leanprover/lean4:v4.28.0"

env_text = (gauss_home / ".env").read_text(encoding="utf-8")
assert f'OPENAI_API_KEY="{expected_key}"' in env_text
assert 'OPENAI_BASE_URL="https://api.openai.com/v1"' in env_text

claude_settings = json.loads((home_dir / ".claude" / "settings.json").read_text(encoding="utf-8"))
marketplace = claude_settings["extraKnownMarketplaces"]["lean4-skills"]
assert marketplace["source"] == {"source": "github", "repo": "cameronfreer/lean4-skills"}
assert marketplace["autoUpdate"] is True
assert claude_settings["enabledPlugins"]["lean4@lean4-skills"] is True

known_marketplaces = json.loads((home_dir / ".claude" / "plugins" / "known_marketplaces.json").read_text(encoding="utf-8"))
known_marketplace = known_marketplaces["lean4-skills"]
assert known_marketplace["source"] == {"source": "github", "repo": "cameronfreer/lean4-skills"}
assert known_marketplace["autoUpdate"] is True
assert Path(known_marketplace["installLocation"]).exists()

installed_plugins = json.loads((home_dir / ".claude" / "plugins" / "installed_plugins.json").read_text(encoding="utf-8"))
plugin_entry = installed_plugins["plugins"]["lean4@lean4-skills"][0]
assert plugin_entry["scope"] == "user"
assert Path(plugin_entry["installPath"]).exists()
PY

echo "==> Verifying gauss works from the repository-local venv"
GAUSS_VERSION_OUTPUT="$(gauss --version)"
printf '%s\n' "$GAUSS_VERSION_OUTPUT"
[[ "$GAUSS_VERSION_OUTPUT" == *"Gauss v"* ]] || die "unexpected gauss --version output"

echo "==> Verifying rerun idempotence and staged-key preservation"
printf '\nSMOKE_RERUN_MARKER\n' >> "$WORKSPACE_DIR/PAPER.md"
touch "$WORKSPACE_DIR/KEEP_ME.txt"
unset OPENAI_API_KEY OPENROUTER_API_KEY ANTHROPIC_API_KEY
./scripts/install-internal.sh \
    --gauss-home "$GAUSS_HOME" \
    --workspace-dir "$WORKSPACE_DIR" \
    --with-workspace \
    --skip-system-packages
grep -F 'SMOKE_RERUN_MARKER' "$WORKSPACE_DIR/PAPER.md" >/dev/null || die "expected PAPER.md marker to survive rerun"
assert_exists "$WORKSPACE_DIR/KEEP_ME.txt"
grep -F "OPENAI_API_KEY=\"$INITIAL_OPENAI_API_KEY\"" "$GAUSS_HOME/.env" >/dev/null || die "expected staged OPENAI_API_KEY to be preserved on rerun"
grep -F 'OPENAI_BASE_URL="https://api.openai.com/v1"' "$GAUSS_HOME/.env" >/dev/null || die "expected OPENAI_BASE_URL to be preserved on rerun"

echo "==> Verifying launcher summary"
SUMMARY_OUTPUT="$(gauss-launch-session --print-summary)"
printf '%s\n' "$SUMMARY_OUTPUT"
[[ "$SUMMARY_OUTPUT" == *"Managed backend: claude-code"* ]] || die "expected managed backend summary"
[[ "$SUMMARY_OUTPUT" == *"Main chat: ready."* ]] || die "expected ready main-chat summary"
[[ "$SUMMARY_OUTPUT" == *"$WORKSPACE_DIR"* ]] || die "expected workspace path in launcher summary"
[[ "$SUMMARY_OUTPUT" == *"/chat"* ]] || die "expected launcher summary to mention /chat"
[[ "$SUMMARY_OUTPUT" == *"/managed-chat"* ]] || die "expected launcher summary to mention /managed-chat"
[[ "$SUMMARY_OUTPUT" == *"gauss-open-guide"* ]] || die "expected launcher summary to mention gauss-open-guide"
[[ "$SUMMARY_OUTPUT" == *"opens Gauss directly at the top level"* ]] || die "expected launcher summary to mention direct top-level launch"
if [[ "$SUMMARY_OUTPUT" == *"Staged keys:"* ]]; then
    die "expected launcher summary to avoid staged-key details"
fi
if [[ "$SUMMARY_OUTPUT" == *"Gauss Setup — Non-interactive mode"* ]]; then
    die "expected launcher summary to avoid inlined setup output"
fi
if [[ "$SUMMARY_OUTPUT" == *"gauss-use-openrouter-key"* ]]; then
    die "expected launcher summary to avoid provider-key helper clutter"
fi

echo "==> Verifying no-provider launcher fallback state"
cp "$GAUSS_HOME/.env" "$GAUSS_HOME/.env.backup"
python3 - "$GAUSS_HOME" <<'PY'
from pathlib import Path
import sys

env_path = Path(sys.argv[1]) / ".env"
drop_keys = {
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_TOKEN",
    "OPENAI_BASE_URL",
}
kept = []
for line in env_path.read_text(encoding="utf-8").splitlines():
    key = line.split("=", 1)[0].strip()
    if key not in drop_keys:
        kept.append(line)
env_path.write_text(("\n".join(kept).rstrip() + "\n") if kept else "", encoding="utf-8")
PY

NO_PROVIDER_SUMMARY="$(gauss-launch-session --print-summary)"
printf '%s\n' "$NO_PROVIDER_SUMMARY"
[[ "$NO_PROVIDER_SUMMARY" == *"Main chat: needs setup."* ]] || die "expected missing-provider launcher summary"
[[ "$NO_PROVIDER_SUMMARY" == *"/chat keeps you in Gauss and enables inline onboarding chat"* ]] || die "expected provider notes to mention inline /chat"
[[ "$NO_PROVIDER_SUMMARY" == *"/managed-chat opens the configured managed backend child session"* ]] || die "expected provider notes to mention managed child session"
[[ "$NO_PROVIDER_SUMMARY" == *"opens Gauss directly at the top level."* ]] || die "expected missing-provider summary to mention direct gauss launch"
[[ "$NO_PROVIDER_SUMMARY" == *"direct Gauss chat/model commands stay disabled until you run gauss setup."* ]] || die "expected missing-provider summary to mention deferred provider setup"
if grep -F "gauss --startup-input /start" "$HOME/.local/bin/gauss-launch-session" >/dev/null; then
    die "expected launcher to stop auto-injecting /start"
fi
if grep -F "gauss setup" "$HOME/.local/bin/gauss-launch-session" >/dev/null; then
    die "expected launcher to avoid forcing gauss setup when no provider is staged"
fi
if grep -F "exec bash -i" "$HOME/.local/bin/gauss-launch-session" >/dev/null; then
    die "expected launcher to avoid shell fallback when no provider is staged"
fi

echo "==> Verifying no-provider launcher enters gauss directly"
FAKE_BIN="$(mktemp -d)"
LAUNCH_LOG="$(mktemp)"
cat >"$FAKE_BIN/gauss" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s|%s\n' "$#" "$*" >>"$GAUSS_LAUNCH_LOG"
if [ "$#" -eq 0 ]; then
    exit 0
fi
printf 'unexpected gauss args: %s\n' "$*" >&2
exit 99
EOF
chmod +x "$FAKE_BIN/gauss"
if ! GAUSS_LAUNCH_LOG="$LAUNCH_LOG" PATH="$FAKE_BIN:$PATH" timeout 15s python3 - <<'PY'
import os
import pty
import sys

status = pty.spawn(["gauss-launch-session"])
if hasattr(os, "waitstatus_to_exitcode"):
    status = os.waitstatus_to_exitcode(status)
sys.exit(status)
PY
then
    die "expected no-provider launcher to reach gauss directly"
fi
if grep -Fx "setup" "$LAUNCH_LOG" >/dev/null; then
    die "expected launcher to skip gauss setup before launching gauss"
fi
grep -Fx "0|" "$LAUNCH_LOG" >/dev/null || die "expected launcher to enter gauss without startup-input injection"
mv "$GAUSS_HOME/.env.backup" "$GAUSS_HOME/.env"

echo "==> Verifying Lean bootstrap failures surface useful diagnostics"
BAD_TOOLCHAIN_HOME="/tmp/gauss-home-bad-toolchain"
BAD_TOOLCHAIN_LOG="/tmp/opengauss-bad-toolchain.log"
BAD_TOOLCHAIN_VALUE="leanprover/lean4:v0.0.0-opengauss-smoke"
rm -rf "$BAD_TOOLCHAIN_HOME" "$BAD_TOOLCHAIN_LOG"
if GAUSS_LEAN_TOOLCHAIN="$BAD_TOOLCHAIN_VALUE" ./scripts/install-internal.sh \
    --gauss-home "$BAD_TOOLCHAIN_HOME" \
    --workspace-dir /tmp/gauss-workspace-bad-toolchain \
    --skip-system-packages \
    --skip-setup \
    >"$BAD_TOOLCHAIN_LOG" 2>&1; then
    cat "$BAD_TOOLCHAIN_LOG"
    die "expected invalid Lean toolchain bootstrap to fail"
fi
grep -F "Failed to install Lean toolchain $BAD_TOOLCHAIN_VALUE." "$BAD_TOOLCHAIN_LOG" >/dev/null || die "expected Lean toolchain failure message"
grep -F "Captured command output:" "$BAD_TOOLCHAIN_LOG" >/dev/null || die "expected captured elan output"
grep -F "Try: export PATH=\"\$HOME/.elan/bin:\$PATH\" && elan toolchain install \"$BAD_TOOLCHAIN_VALUE\"" "$BAD_TOOLCHAIN_LOG" >/dev/null || die "expected manual recovery hint"

echo "==> ubuntu_repository_local_install_smoke passed"
