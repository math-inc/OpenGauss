#!/usr/bin/env bash
# ============================================================================
# Open Gauss Internal Workflow Installer
# ============================================================================
# Repository-local installer implementation used by the shared installer flow.
#
# Supported contract:
# - Run from the root of a checked-out math-inc/OpenGauss repository.
# - Linux and macOS. Ubuntu/Debian/WSL are the primary Linux environments.
# - macOS support uses Homebrew for system dependencies.
#
# Usage:
#   ./scripts/install-internal.sh
#   ./scripts/install-internal.sh --workspace-dir "$HOME/GaussWorkspace"
#   ./scripts/install-internal.sh --gauss-home "$HOME/.gauss"
#
# ============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
NC='\033[0m'

PYTHON_VERSION="3.11"
NODE_MAJOR="22"
LEAN_TOOLCHAIN="${GAUSS_LEAN_TOOLCHAIN:-leanprover/lean4:v4.28.0}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

GAUSS_HOME="${GAUSS_HOME:-$HOME/.gauss}"
WORKSPACE_DIR="${GAUSS_WORKSPACE_DIR:-$HOME/GaussWorkspace}"
RECREATE_VENV=false
SKIP_SYSTEM_PACKAGES=false
CREATE_WORKSPACE=false
AUTO_CONFIGURED_MAIN_PROVIDER=false
SETUP_MODE="auto"
CLI_SELF_CHECK_STATUS=""
MANAGED_SELF_CHECK_STATUS=""

OS=""
DISTRO=""
DEBIAN_LIKE=false
MACOS=false
IS_WSL=false
UV_CMD=""
FILE_PYTHON="python3"
VENV_DIR=""
VENV_BIN=""
VENV_PYTHON=""
GAUSS_BIN=""
GUIDE_DIR=""
INSTALL_ROOT_FILE=""
SHELL_RELOAD_HINT="open a new shell"
ACTIVE_SHELL_NAME="shell"
SECONDARY_SHELL_RELOAD_HINT=""
SECONDARY_SHELL_NAME=""

usage() {
    cat <<'TXT'
Open Gauss Internal Workflow Installer

Run this script from the root of a checked-out math-inc/OpenGauss repository.
End users should use ./scripts/install.sh instead.

Options:
  --gauss-home PATH       Override the Gauss home directory (default: ~/.gauss)
  --workspace-dir PATH    Override the prewarmed Lean workspace path
                          (default: ~/GaussWorkspace)
  --skip-system-packages  Do not run apt-get even on Debian/Ubuntu
  --with-workspace        Also create a prewarmed Lean+Mathlib workspace
                          (downloads ~2 GB, adds 5-15 min)
  --skip-setup            Do not prompt for or run `gauss setup` at the end
  --run-setup             Run `gauss setup` immediately at the end (requires a TTY)
  --noninteractive        Alias for --skip-setup (for templates/CI)
  --skip-setup-wizard     Alias for --skip-setup
  --recreate-venv         Remove and recreate the repository virtualenv
  -h, --help              Show this help

Environment:
  GAUSS_HOME              Same as --gauss-home
  GAUSS_WORKSPACE_DIR     Same as --workspace-dir
  GAUSS_SKIP_SYSTEM_PACKAGES
                          true/false equivalent of --skip-system-packages
  GAUSS_CREATE_WORKSPACE  true/false equivalent of --with-workspace
  GAUSS_RECREATE_VENV     true/false equivalent of --recreate-venv
  GAUSS_SETUP_MODE        auto/skip/run equivalent of setup flags
  GAUSS_LEAN_TOOLCHAIN    Lean toolchain used for workspace bootstrap
                          (default: leanprover/lean4:v4.28.0)

Optional staged provider keys:
  OPENROUTER_API_KEY
  OPENAI_API_KEY
  ANTHROPIC_API_KEY

Behavior:
  Exported non-empty provider keys are written to ~/.gauss/.env.
  Unset provider keys leave existing ~/.gauss/.env values untouched.
  Export a provider key as an empty string to clear the staged value.
TXT
}

print_banner() {
    echo
    echo -e "${MAGENTA}${BOLD}"
    echo "┌─────────────────────────────────────────────────────────┐"
    echo "│        ∑ Open Gauss Workflow Installer                 │"
    echo "├─────────────────────────────────────────────────────────┤"
    echo "│  Repository-local, Lean-ready batteries included       │"
    echo "└─────────────────────────────────────────────────────────┘"
    echo -e "${NC}"
    echo -e "${YELLOW}⚠ This install can take up to 10 minutes.${NC}"
    echo -e "${YELLOW}⚠ For a setup in under 10 seconds, try: https://morph.new/opengauss${NC}"
    echo
}

log_info() {
    echo -e "${CYAN}→${NC} $1"
}

log_success() {
    echo -e "${GREEN}✓${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}⚠${NC} $1"
}

log_error() {
    echo -e "${RED}✗${NC} $1"
}

refresh_paths() {
    VENV_DIR="$REPO_ROOT/venv"
    VENV_BIN="$VENV_DIR/bin"
    VENV_PYTHON="$VENV_BIN/python"
    GUIDE_DIR="$GAUSS_HOME/guide"
    INSTALL_ROOT_FILE="$GAUSS_HOME/install-root"
}

set_boolean_from_env() {
    local env_name="$1"
    local target_name="$2"
    if [ "${!env_name+x}" != "x" ]; then
        return
    fi
    case "${!env_name}" in
        1|true|TRUE|yes|YES|on|ON)
            printf -v "$target_name" true
            ;;
        0|false|FALSE|no|NO|off|OFF|"")
            printf -v "$target_name" false
            ;;
        *)
            log_error "$env_name must be one of: 1, 0, true, false, yes, no, on, off."
            exit 1
            ;;
    esac
}

apply_env_overrides() {
    set_boolean_from_env "GAUSS_RECREATE_VENV" "RECREATE_VENV"
    set_boolean_from_env "GAUSS_SKIP_SYSTEM_PACKAGES" "SKIP_SYSTEM_PACKAGES"
    set_boolean_from_env "GAUSS_CREATE_WORKSPACE" "CREATE_WORKSPACE"

    if [ "${GAUSS_SETUP_MODE+x}" = "x" ]; then
        case "$GAUSS_SETUP_MODE" in
            auto|skip|run)
                SETUP_MODE="$GAUSS_SETUP_MODE"
                ;;
            *)
                log_error "GAUSS_SETUP_MODE must be one of: auto, skip, run."
                exit 1
                ;;
        esac
    fi

    refresh_paths
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --gauss-home)
                GAUSS_HOME="$2"
                shift 2
                ;;
            --workspace-dir)
                WORKSPACE_DIR="$2"
                shift 2
                ;;
            --skip-system-packages)
                SKIP_SYSTEM_PACKAGES=true
                shift
                ;;
            --with-workspace)
                CREATE_WORKSPACE=true
                shift
                ;;
            --skip-setup|--noninteractive|--skip-setup-wizard)
                if [ "$SETUP_MODE" = "run" ]; then
                    log_error "Use only one of --skip-setup/--noninteractive or --run-setup."
                    exit 1
                fi
                SETUP_MODE="skip"
                shift
                ;;
            --run-setup)
                if [ "$SETUP_MODE" = "skip" ]; then
                    log_error "Use only one of --skip-setup/--noninteractive or --run-setup."
                    exit 1
                fi
                SETUP_MODE="run"
                shift
                ;;
            --recreate-venv)
                RECREATE_VENV=true
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                usage
                exit 1
                ;;
        esac
    done
    refresh_paths
}

run_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
        return
    fi
    if command -v sudo >/dev/null 2>&1; then
        sudo "$@"
        return
    fi
    log_error "System package installation requires root or sudo."
    exit 1
}

detect_os() {
    case "$(uname -s)" in
        Linux*)
            OS="linux"
            if [ -f /etc/os-release ]; then
                # shellcheck disable=SC1091
                . /etc/os-release
                DISTRO="${ID:-unknown}"
            else
                DISTRO="unknown"
            fi
            if grep -qiE '(microsoft|wsl)' /proc/sys/kernel/osrelease 2>/dev/null || \
               grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null; then
                IS_WSL=true
            fi
            ;;
        Darwin*)
            OS="darwin"
            DISTRO="macos"
            MACOS=true
            ;;
        *)
            log_error "Unsupported operating system: $(uname -s)"
            log_info "Use a Linux checkout (Ubuntu/Debian/WSL recommended) and rerun ./scripts/install-internal.sh."
            exit 1
            ;;
    esac

    case "$DISTRO" in
        ubuntu|debian)
            DEBIAN_LIKE=true
            ;;
    esac

    if [ "$MACOS" = true ]; then
        log_success "Detected macOS environment"
    elif [ "$IS_WSL" = true ]; then
        log_success "Detected Linux environment ($DISTRO under WSL)"
    else
        log_success "Detected Linux environment ($DISTRO)"
    fi
}

require_repo_checkout() {
    local required_paths=(
        "$REPO_ROOT/pyproject.toml"
        "$REPO_ROOT/gauss_cli/main.py"
        "$REPO_ROOT/docs/skins/mathinc.yaml"
        "$REPO_ROOT/package.json"
        "$REPO_ROOT/package-lock.json"
    )

    if [ ! -e "$REPO_ROOT/.git" ]; then
        log_error "Expected a git checkout at $REPO_ROOT."
        log_info "Clone math-inc/OpenGauss and run ./scripts/install-internal.sh from that repository."
        exit 1
    fi

    for path in "${required_paths[@]}"; do
        if [ ! -e "$path" ]; then
            log_error "Repository validation failed. Missing: $path"
            exit 1
        fi
    done

    if ! git -C "$REPO_ROOT" rev-parse --show-toplevel >/dev/null 2>&1; then
        log_error "$REPO_ROOT is not a valid git checkout."
        exit 1
    fi

    log_success "Repository checkout validated: $REPO_ROOT"
}

warn_wsl_checkout_location() {
    if [ "$IS_WSL" != true ]; then
        return
    fi
    case "$REPO_ROOT" in
        /mnt/*)
            log_warn "This checkout is on the Windows filesystem: $REPO_ROOT"
            log_warn "For faster installs and fewer file/PTY issues, clone OpenGauss inside your WSL home directory."
            log_info "Recommended:"
            log_info "  cd ~ && git clone https://github.com/math-inc/OpenGauss.git && cd OpenGauss"
            ;;
    esac
}

detect_shell_reload_hint() {
    local shell_name=""
    shell_name="$(basename "${SHELL:-}")"
    ACTIVE_SHELL_NAME="${shell_name:-shell}"
    case "$shell_name" in
        zsh)
            SHELL_RELOAD_HINT="source ~/.zshrc"
            SECONDARY_SHELL_RELOAD_HINT="source ~/.bashrc"
            SECONDARY_SHELL_NAME="bash"
            ;;
        bash)
            SHELL_RELOAD_HINT="source ~/.bashrc"
            SECONDARY_SHELL_RELOAD_HINT="source ~/.zshrc"
            SECONDARY_SHELL_NAME="zsh"
            ;;
        *)
            SHELL_RELOAD_HINT="source ~/.profile"
            SECONDARY_SHELL_RELOAD_HINT="source ~/.bashrc"
            SECONDARY_SHELL_NAME="bash"
            ;;
    esac
}

ensure_local_bin_path() {
    mkdir -p "$HOME/.local/bin"
    export PATH="$HOME/.local/bin:$PATH"
}

ensure_required_commands() {
    local missing=()
    local commands=(bash curl git python3 rg)
    if [ "$MACOS" != true ]; then
        commands+=(gcc jq make pkg-config tmux unzip xz zip ffmpeg)
    fi
    local cmd
    for cmd in "${commands[@]}"; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            missing+=("$cmd")
        fi
    done

    if [ "${#missing[@]}" -eq 0 ]; then
        return
    fi

    log_error "Missing required commands: ${missing[*]}"
    if [ "$MACOS" = true ]; then
        log_info "Install the missing tools with Homebrew: brew install ${missing[*]}"
    elif [ "$DEBIAN_LIKE" = true ]; then
        log_info "Run without --skip-system-packages or install them manually with apt-get."
    else
        log_info "Install the missing tools manually and rerun the installer."
    fi
    exit 1
}

install_system_packages() {
    if [ "$SKIP_SYSTEM_PACKAGES" = true ]; then
        log_info "Skipping system package bootstrap (--skip-system-packages)"
        ensure_required_commands
        return
    fi

    if [ "$MACOS" = true ]; then
        if ! command -v brew >/dev/null 2>&1; then
            log_error "Homebrew is required on macOS. Install it from https://brew.sh"
            exit 1
        fi
        local mac_packages=(ripgrep)
        if ! command -v rg >/dev/null 2>&1; then
            log_info "Installing macOS prerequisites via Homebrew..."
            brew install "${mac_packages[@]}"
        fi
        ensure_required_commands
        log_success "System packages are ready"
        return
    fi

    if [ "$DEBIAN_LIKE" != true ]; then
        log_warn "Automatic system package installation is only supported on Debian/Ubuntu and macOS."
        ensure_required_commands
        return
    fi

    local packages=(
        build-essential
        ca-certificates
        curl
        ffmpeg
        git
        gnupg
        jq
        libffi-dev
        pkg-config
        python3
        python3-dev
        python3-pip
        python3-venv
        ripgrep
        tmux
        unzip
        xz-utils
        zip
    )

    log_info "Installing Debian/Ubuntu workflow prerequisites..."
    export DEBIAN_FRONTEND=noninteractive
    run_root apt-get update -y
    run_root apt-get install -y --no-install-recommends "${packages[@]}"
    ensure_required_commands
    log_success "System packages are ready"
}

ensure_uv() {
    log_info "Ensuring uv is available..."

    if command -v uv >/dev/null 2>&1; then
        UV_CMD="uv"
    elif [ -x "$HOME/.local/bin/uv" ]; then
        UV_CMD="$HOME/.local/bin/uv"
    elif [ -x "$HOME/.cargo/bin/uv" ]; then
        UV_CMD="$HOME/.cargo/bin/uv"
    else
        log_info "Installing uv..."
        if ! curl -LsSf https://astral.sh/uv/install.sh | sh; then
            log_error "Failed to install uv."
            exit 1
        fi
        if [ -x "$HOME/.local/bin/uv" ]; then
            UV_CMD="$HOME/.local/bin/uv"
        elif [ -x "$HOME/.cargo/bin/uv" ]; then
            UV_CMD="$HOME/.cargo/bin/uv"
        else
            log_error "uv installed but could not be located."
            exit 1
        fi
    fi

    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    log_success "uv ready: $($UV_CMD --version)"
}

node_major_version() {
    node -p 'process.versions.node.split(".")[0]'
}

ensure_nodejs() {
    log_info "Ensuring Node.js ${NODE_MAJOR}.x is available..."

    if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
        local current_major
        current_major="$(node_major_version)"
        if [ "$current_major" -ge "$NODE_MAJOR" ]; then
            log_success "Node.js ready: $(node -v)"
            return
        fi
    fi

    if [ "$MACOS" = true ]; then
        log_info "Installing Node.js via Homebrew..."
        brew install node
    elif [ "$DEBIAN_LIKE" = true ]; then
        log_info "Installing Node.js ${NODE_MAJOR}.x via NodeSource..."
        run_root bash -lc 'curl -fsSL https://deb.nodesource.com/setup_22.x | bash -'
        run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
    else
        log_error "Node.js ${NODE_MAJOR}.x is required."
        log_info "Install Node.js ${NODE_MAJOR}.x and npm manually, then rerun ./scripts/install-internal.sh."
        exit 1
    fi
    log_success "Node.js ready: $(node -v)"
}

ensure_global_cli_tools() {
    log_info "Ensuring Claude Code and OpenAI Codex are available..."
    mkdir -p "$HOME/.local/bin"
    export PATH="$HOME/.local/bin:$PATH"

    if ! command -v claude >/dev/null 2>&1; then
        log_info "Installing Claude Code..."
        npm install -g --force --prefix "$HOME/.local" @anthropic-ai/claude-code@latest >/dev/null 2>&1 \
            || npm install -g --force --prefix "$HOME/.local" @anthropic-ai/claude-code >/dev/null 2>&1 \
            || true
    fi

    if ! command -v codex >/dev/null 2>&1; then
        log_info "Installing OpenAI Codex..."
        npm install -g --force --prefix "$HOME/.local" @openai/codex@latest >/dev/null 2>&1 \
            || npm install -g --force --prefix "$HOME/.local" @openai/codex >/dev/null 2>&1 \
            || true
    fi

    if ! command -v claude >/dev/null 2>&1; then
        log_warn "Claude Code not found — install manually: npm install -g @anthropic-ai/claude-code"
    else
        log_success "Claude Code ready: $(claude --version 2>/dev/null || printf 'installed')"
    fi

    if ! command -v codex >/dev/null 2>&1; then
        log_warn "OpenAI Codex not found — install manually: npm install -g @openai/codex"
    else
        log_success "OpenAI Codex ready: $(codex --version 2>/dev/null || printf 'installed')"
    fi
}

ensure_lean_toolchain() {
    log_info "Ensuring elan + Lean $LEAN_TOOLCHAIN are available..."

    if ! command -v elan >/dev/null 2>&1 && [ ! -x "$HOME/.elan/bin/elan" ]; then
        local elan_script
        elan_script="$(mktemp /tmp/elan-init.XXXXXX.sh)"
        curl -L https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -o "$elan_script"
        bash "$elan_script" -y
    fi

    export PATH="$HOME/.elan/bin:$PATH"

    if ! command -v elan >/dev/null 2>&1; then
        log_error "elan is not available after installation."
        exit 1
    fi

    elan toolchain install "$LEAN_TOOLCHAIN" >/dev/null 2>&1 || true
    elan default "$LEAN_TOOLCHAIN" >/dev/null 2>&1

    if ! command -v lake >/dev/null 2>&1; then
        log_error "lake is not available after configuring Lean."
        exit 1
    fi

    log_success "Lean toolchain selected: $LEAN_TOOLCHAIN"
    log_success "Lean ready: $(elan --version | head -n 1)"
    log_success "Lake ready: $(lake --version | head -n 1)"
}

sync_repo_submodules() {
    log_info "Syncing submodules..."
    git -C "$REPO_ROOT" submodule sync --recursive
    git -C "$REPO_ROOT" submodule update --init --recursive || true

    if [ -f "$REPO_ROOT/mini-swe-agent/pyproject.toml" ]; then
        log_success "mini-swe-agent submodule ready"
    else
        log_warn "mini-swe-agent submodule not available (terminal tools may be limited)"
    fi
}

ensure_python_runtime() {
    log_info "Ensuring Python $PYTHON_VERSION and the repository virtualenv are ready..."

    "$UV_CMD" python install "$PYTHON_VERSION"

    if [ -d "$VENV_DIR" ] && [ "$RECREATE_VENV" = true ]; then
        log_warn "Recreating the repository virtualenv ($VENV_DIR)..."
        rm -rf "$VENV_DIR"
    fi

    if [ -d "$VENV_DIR" ] && [ -x "$VENV_PYTHON" ]; then
        local current_python
        current_python="$("$VENV_PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
        if [ "$current_python" != "$PYTHON_VERSION" ]; then
            log_error "Existing virtualenv uses Python $current_python, expected $PYTHON_VERSION."
            log_info "Rerun with --recreate-venv to replace the repository virtualenv."
            exit 1
        fi
    else
        "$UV_CMD" venv "$VENV_DIR" --python "$PYTHON_VERSION"
    fi

    export VIRTUAL_ENV="$VENV_DIR"
    export PATH="$VENV_BIN:$HOME/.local/bin:$HOME/.elan/bin:$PATH"
    FILE_PYTHON="$VENV_PYTHON"
    log_success "Virtualenv ready: $VENV_DIR"
}

install_repo_dependencies() {
    log_info "Installing Python and Node dependencies from the checked-out repository..."
    "$UV_CMD" pip install -e ".[full]" || "$UV_CMD" pip install -e ".[gauss]" || "$UV_CMD" pip install -e "."
    if [ -f "$REPO_ROOT/mini-swe-agent/pyproject.toml" ]; then
        "$UV_CMD" pip install -e "./mini-swe-agent" || log_warn "mini-swe-agent install failed"
    fi
    if [ -f "$REPO_ROOT/package-lock.json" ]; then
        npm ci --silent 2>/dev/null || npm install --silent 2>/dev/null || log_warn "npm install failed"
    fi
    log_success "Repository dependencies installed"
}

link_repo_binaries() {
    log_info "Linking repository-local executables into ~/.local/bin..."
    ln -sf "$VENV_BIN/gauss" "$HOME/.local/bin/gauss"
    if [ -x "$VENV_BIN/gauss-agent" ]; then
        ln -sf "$VENV_BIN/gauss-agent" "$HOME/.local/bin/gauss-agent"
    fi
    GAUSS_BIN="$HOME/.local/bin/gauss"
    export PATH="$HOME/.local/bin:$VENV_BIN:$HOME/.elan/bin:$PATH"
    log_success "gauss command is available at $GAUSS_BIN"
}

prepare_gauss_home() {
    log_info "Preparing $GAUSS_HOME..."
    mkdir -p \
        "$GAUSS_HOME" \
        "$GAUSS_HOME/cron" \
        "$GAUSS_HOME/sessions" \
        "$GAUSS_HOME/logs" \
        "$GAUSS_HOME/memories" \
        "$GAUSS_HOME/skills" \
        "$GAUSS_HOME/skins" \
        "$GAUSS_HOME/whatsapp/session" \
        "$GUIDE_DIR"
    chmod 700 "$GAUSS_HOME" || true

    touch "$GAUSS_HOME/.env"
    chmod 600 "$GAUSS_HOME/.env" || true

    printf '%s\n' "$REPO_ROOT" > "$INSTALL_ROOT_FILE"
    chmod 600 "$INSTALL_ROOT_FILE" || true

    log_success "Gauss home ready"
}

write_env_value() {
    local key="$1"
    local value="${2-}"
    "$FILE_PYTHON" - "$GAUSS_HOME/.env" "$key" "$value" <<'PY'
import json
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]

existing = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
kept = [line for line in existing if not line.startswith(f"{key}=")]
if value:
    kept.append(f"{key}={json.dumps(value)}")
env_path.write_text(("\n".join(kept).rstrip() + "\n") if kept else "", encoding="utf-8")
env_path.chmod(0o600)
PY
}

sync_optional_provider_keys() {
    log_info "Synchronizing staged provider keys into $GAUSS_HOME/.env..."

    local provider_keys=(
        OPENROUTER_API_KEY
        OPENAI_API_KEY
        ANTHROPIC_API_KEY
    )

    local key
    for key in "${provider_keys[@]}"; do
        if [ "${!key+x}" = "x" ] && [ -n "${!key}" ]; then
            write_env_value "$key" "${!key}"
            log_success "Staged $key"
        elif [ "${!key+x}" = "x" ]; then
            write_env_value "$key" ""
            log_info "Cleared staged $key (explicit empty value exported in the current shell)"
        else
            log_info "Keeping existing $key (no value exported in the current shell)"
        fi
    done
}

ensure_workspace() {
    if [ "$CREATE_WORKSPACE" != true ]; then
        log_info "Skipping Lean workspace (use --with-workspace to create one, or /project create inside Gauss)"
        return
    fi

    log_info "Preparing the Lean workspace at $WORKSPACE_DIR (this downloads Mathlib ~2 GB)..."

    if [ ! -f "$WORKSPACE_DIR/lakefile.toml" ] && [ ! -f "$WORKSPACE_DIR/lakefile.lean" ]; then
        if [ -e "$WORKSPACE_DIR" ]; then
            log_error "$WORKSPACE_DIR already exists but is not a Lean workspace."
            log_info "Choose a different --workspace-dir or move the existing directory aside."
            exit 1
        fi
        mkdir -p "$(dirname "$WORKSPACE_DIR")"
        (
            cd "$(dirname "$WORKSPACE_DIR")"
            lake new "$(basename "$WORKSPACE_DIR")" math
        )
        log_success "Lean workspace created at $WORKSPACE_DIR"
    else
        log_success "Lean workspace already exists at $WORKSPACE_DIR"
    fi

    if [ ! -f "$WORKSPACE_DIR/PAPER.md" ]; then
        cat > "$WORKSPACE_DIR/PAPER.md" <<'TXT'
# Gauss Workspace

This Lean workspace is prewarmed and already registered as the active Gauss project.

Quickstart:
1. Run `gauss-open-session` for the batteries-included launcher.
2. Or launch `gauss` directly inside this workspace.
3. Use `/prove`, `/draft`, `/autoprove`, `/formalize`, `/autoformalize`, or `/swarm`.
4. Keep paper notes, extracted statements, and scratch proofs in this project.
TXT
        log_success "Created $WORKSPACE_DIR/PAPER.md"
    else
        log_info "Keeping existing $WORKSPACE_DIR/PAPER.md"
    fi
}

initialize_gauss_workspace() {
    log_info "Initializing Gauss defaults..."

    cp "$REPO_ROOT/docs/skins/mathinc.yaml" "$GAUSS_HOME/skins/mathinc.yaml"

    if [ -d "$WORKSPACE_DIR" ] && [ "$CREATE_WORKSPACE" = true ]; then
        "$FILE_PYTHON" - "$WORKSPACE_DIR" <<'PY'
import sys
from gauss_cli.project import initialize_gauss_project

initialize_gauss_project(sys.argv[1], name="Gauss Workspace")
PY
    fi

    "$GAUSS_BIN" config set display.skin mathinc
    "$GAUSS_BIN" config set terminal.backend local
    if [ -d "$WORKSPACE_DIR" ]; then
        "$GAUSS_BIN" config set terminal.cwd "$WORKSPACE_DIR"
    fi
    "$GAUSS_BIN" config set gauss.autoformalize.backend claude-code
    "$GAUSS_BIN" config set gauss.autoformalize.auth_mode auto
    "$GAUSS_BIN" config set agent.max_turns 90

    log_success "Gauss defaults applied"
}

ensure_shell_runtime_block() {
    log_info "Writing an idempotent shell runtime block..."

    "$FILE_PYTHON" - "$GAUSS_HOME" "$REPO_ROOT" "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile" "$HOME/.zprofile" <<'PY'
from pathlib import Path
import re
import sys

gauss_home = sys.argv[1]
repo_root = sys.argv[2]
shell_configs = [Path(arg).expanduser() for arg in sys.argv[3:]]

block = f"""# >>> gauss workflow installer env >>>
export GAUSS_HOME="{gauss_home}"
export GAUSS_INSTALL_ROOT="{repo_root}"
if [ -f "{gauss_home}/.env" ]; then
  set -a
  . "{gauss_home}/.env"
  set +a
fi
export PATH="$HOME/.local/bin:{repo_root}/venv/bin:$HOME/.elan/bin:$PATH"
export PROMPT_TOOLKIT_NO_CPR=1
# <<< gauss workflow installer env <<<
"""

pattern = re.compile(
    r"(?ms)^# >>> gauss workflow installer env >>>\n.*?^# <<< gauss workflow installer env <<<\n?"
)

for config_path in shell_configs:
    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    if pattern.search(existing):
        updated = pattern.sub(block, existing)
    else:
        updated = existing.rstrip("\n")
        if updated:
            updated += "\n\n"
        updated += block
    config_path.write_text(updated, encoding="utf-8")
PY

    export GAUSS_HOME
    export GAUSS_INSTALL_ROOT="$REPO_ROOT"
    export PATH="$HOME/.local/bin:$VENV_BIN:$HOME/.elan/bin:$PATH"
    export PROMPT_TOOLKIT_NO_CPR=1

    log_success "Shell runtime block written"
}

write_helper_assets() {
    log_info "Writing workflow-derived helper scripts and the local guide..."

    "$FILE_PYTHON" - "$HOME/.local/bin" "$GAUSS_HOME" "$REPO_ROOT" "$WORKSPACE_DIR" "$GUIDE_DIR" <<'PY_HELPERS'
from html import escape
from pathlib import Path
import subprocess
import textwrap
import sys

helper_dir = Path(sys.argv[1]).expanduser()
gauss_home = Path(sys.argv[2]).expanduser()
repo_root = Path(sys.argv[3]).expanduser()
workspace_dir = Path(sys.argv[4]).expanduser()
guide_dir = Path(sys.argv[5]).expanduser()

helper_dir.mkdir(parents=True, exist_ok=True)
guide_dir.mkdir(parents=True, exist_ok=True)

readme = (repo_root / "README.md").read_text(encoding="utf-8")
repo_head = subprocess.check_output(
    ["git", "-C", str(repo_root), "rev-parse", "--short=12", "HEAD"],
    text=True,
).strip()

guide_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Open Gauss Local Guide</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #faf4ec;
      --panel: #f0e5d9;
      --surface: #fcf8f3;
      --ink: #241d18;
      --muted: #655a4f;
      --accent: #485d42;
      --border: #d8cbbf;
      --code-bg: #e8ddd1;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{
      margin: 0;
      min-height: 100%;
      background: linear-gradient(180deg, #fffaf5 0%, var(--bg) 100%);
      color: var(--ink);
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
    }}
    body {{ display: grid; grid-template-rows: auto auto auto 1fr; }}
    header {{
      padding: 16px 20px 8px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
    }}
    header strong {{ font-size: 16px; letter-spacing: 0.03em; }}
    header a {{ color: var(--accent); text-decoration: none; font-weight: 700; }}
    .hero, .grid {{
      margin: 0 20px 14px;
      padding: 16px 18px;
      border: 1px solid var(--border);
      border-radius: 18px;
      background: var(--panel);
    }}
    .hero p, .grid p {{ margin: 0; line-height: 1.6; }}
    .grid {{
      display: grid;
      gap: 12px;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    }}
    .card {{
      padding: 14px;
      border: 1px solid rgba(72, 93, 66, 0.12);
      border-radius: 14px;
      background: rgba(252, 248, 243, 0.9);
    }}
    .card strong {{
      display: block;
      margin-bottom: 8px;
    }}
    .card code {{
      background: var(--code-bg);
      color: var(--ink);
      padding: 2px 6px;
      border-radius: 6px;
    }}
    main {{
      margin: 0 20px 20px;
      padding: 18px;
      border: 1px solid var(--border);
      border-radius: 18px;
      background: var(--surface);
      overflow: auto;
    }}
    pre {{
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font: 13px/1.45 "IBM Plex Mono", "SFMono-Regular", monospace;
      color: var(--ink);
    }}
  </style>
</head>
<body>
  <header>
    <strong>Open Gauss Batteries-Included Guide</strong>
    <a href="https://github.com/math-inc/OpenGauss" target="_blank" rel="noreferrer">Open repo</a>
  </header>
  <section class="hero">
    <p>This local installer uses the checked-out <code>math-inc/OpenGauss</code> repository at <code>{escape(str(repo_root))}</code> and currently resolves to <code>{repo_head}</code>. It preloads Lean 4, Claude Code, OpenAI Codex, the Math Inc skin, and a ready project at <code>{escape(str(workspace_dir))}</code>. Run <code>gauss-open-session</code> for the batteries-included launcher or <code>gauss</code> directly if you prefer to skip the session wrapper.</p>
  </section>
  <section class="grid">
    <div class="card">
      <strong>Ready Paths</strong>
      <p><code>{escape(str(repo_root))}</code><br><code>{escape(str(workspace_dir))}</code><br><code>{escape(str(gauss_home / ".env"))}</code></p>
    </div>
    <div class="card">
      <strong>Backend Helpers</strong>
      <p><code>gauss-use-claude-backend</code><br><code>gauss-use-codex-backend</code><br><code>gauss-use-auto-auth</code><br><code>gauss-use-claude-login</code><br><code>gauss-use-codex-login</code></p>
    </div>
    <div class="card">
      <strong>Main Provider Helpers</strong>
      <p><code>gauss-use-openrouter-key</code><br><code>gauss-use-anthropic-key</code><br><code>gauss-use-openai-key</code><br><code>gauss-configure-main-provider</code><br><code>gauss-open-session</code></p>
    </div>
    <div class="card">
      <strong>Optional Staged Keys</strong>
      <p><code>OPENROUTER_API_KEY</code><br><code>OPENAI_API_KEY</code><br><code>ANTHROPIC_API_KEY</code></p>
    </div>
  </section>
  <main><pre>{escape(readme)}</pre></main>
</body>
</html>
"""
(guide_dir / "index.html").write_text(guide_html, encoding="utf-8")

template_replacements = {
    "__GAUSS_HOME__": str(gauss_home),
    "__REPO_ROOT__": str(repo_root),
    "__WORKSPACE_DIR__": str(workspace_dir),
    "__GUIDE_PATH__": str(guide_dir / "index.html"),
}

scripts = {
    "gauss-configure-main-provider": """#!/usr/bin/env bash
set -euo pipefail
GAUSS_HOME="${GAUSS_HOME:-__GAUSS_HOME__}"
REPO_ROOT="${GAUSS_REPO_ROOT:-__REPO_ROOT__}"
WORKSPACE_DIR="${GAUSS_WORKSPACE_DIR:-__WORKSPACE_DIR__}"
export GAUSS_HOME
export PATH="$HOME/.local/bin:$REPO_ROOT/venv/bin:$HOME/.elan/bin:$PATH"
if [ -f "$GAUSS_HOME/.env" ]; then
  set -a
  . "$GAUSS_HOME/.env"
  set +a
fi
provider="${1:-auto}"

write_env_value() {
  "$REPO_ROOT/venv/bin/python" - "$GAUSS_HOME/.env" "$1" "$2" <<'PY'
import json
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
existing = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
kept = [line for line in existing if not line.startswith(f"{key}=")]
if value:
    kept.append(f"{key}={json.dumps(value)}")
env_path.write_text(("\\n".join(kept).rstrip() + "\\n") if kept else "", encoding="utf-8")
env_path.chmod(0o600)
PY
}

write_provider_config() {
  "$REPO_ROOT/venv/bin/python" - "$GAUSS_HOME/config.yaml" "$1" "$2" "$3" <<'PY'
import sys
from pathlib import Path

import yaml

config_path = Path(sys.argv[1])
provider, model_name, base_url = sys.argv[2:5]
config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
if not isinstance(config, dict):
    config = {}
model = config.get("model", {})
if isinstance(model, str):
    model = {"default": model}
if not isinstance(model, dict):
    model = {}
model["provider"] = provider
model["default"] = model_name
if base_url:
    model["base_url"] = base_url.rstrip("/")
else:
    model.pop("base_url", None)
config["model"] = model
config_path.parent.mkdir(parents=True, exist_ok=True)
config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
PY
}

deactivate_oauth_provider() {
  "$REPO_ROOT/venv/bin/python" - <<'PY'
try:
    from gauss_cli.auth import deactivate_provider
    deactivate_provider()
except Exception:
    pass
PY
}

set_openrouter() {
  if [ -z "${OPENROUTER_API_KEY:-}" ]; then
    printf '%s\\n' 'No OPENROUTER_API_KEY found in ~/.gauss/.env.' >&2
    return 1
  fi
  unset OPENAI_BASE_URL || true
  write_env_value OPENAI_BASE_URL ""
  write_provider_config openrouter anthropic/claude-opus-4.6 ""
  deactivate_oauth_provider
  printf '%s\\n' 'OpenRouter main provider configured (anthropic/claude-opus-4.6).'
}

set_anthropic() {
  if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -z "${ANTHROPIC_TOKEN:-}" ]; then
    printf '%s\\n' 'No ANTHROPIC_API_KEY or ANTHROPIC_TOKEN found in ~/.gauss/.env.' >&2
    return 1
  fi
  unset OPENAI_BASE_URL || true
  write_env_value OPENAI_BASE_URL ""
  write_provider_config anthropic claude-opus-4-6 ""
  deactivate_oauth_provider
  printf '%s\\n' 'Anthropic main provider configured (claude-opus-4-6).'
}

set_openai() {
  if [ -z "${OPENAI_API_KEY:-}" ]; then
    printf '%s\\n' 'No OPENAI_API_KEY found in ~/.gauss/.env.' >&2
    return 1
  fi
  export OPENAI_BASE_URL="https://api.openai.com/v1"
  write_env_value OPENAI_BASE_URL "$OPENAI_BASE_URL"
  write_provider_config custom gpt-5.4 "$OPENAI_BASE_URL"
  deactivate_oauth_provider
  printf '%s\\n' 'OpenAI-compatible main provider configured (https://api.openai.com/v1, gpt-5.4).'
}

case "$provider" in
  auto)
    if [ -n "${OPENROUTER_API_KEY:-}" ]; then
      set_openrouter
    elif [ -n "${ANTHROPIC_API_KEY:-}" ] || [ -n "${ANTHROPIC_TOKEN:-}" ]; then
      set_anthropic
    elif [ -n "${OPENAI_API_KEY:-}" ]; then
      set_openai
    else
      printf '%s\\n' 'No staged OpenRouter, Anthropic, or OpenAI key found for the main interactive provider.' >&2
      exit 1
    fi
    ;;
  openrouter)
    set_openrouter
    ;;
  anthropic)
    set_anthropic
    ;;
  openai|custom)
    set_openai
    ;;
  *)
    printf '%s\\n' 'Usage: gauss-configure-main-provider [auto|openrouter|anthropic|openai]' >&2
    exit 2
    ;;
esac
""",
    "gauss-use-claude-backend": """#!/usr/bin/env bash
set -euo pipefail
GAUSS_HOME="${GAUSS_HOME:-__GAUSS_HOME__}"
REPO_ROOT="${GAUSS_REPO_ROOT:-__REPO_ROOT__}"
export GAUSS_HOME
export PATH="$HOME/.local/bin:$REPO_ROOT/venv/bin:$HOME/.elan/bin:$PATH"
gauss config set gauss.autoformalize.backend claude-code
printf '%s\\n' 'Gauss managed backend set to claude-code.'
""",
    "gauss-use-codex-backend": """#!/usr/bin/env bash
set -euo pipefail
GAUSS_HOME="${GAUSS_HOME:-__GAUSS_HOME__}"
REPO_ROOT="${GAUSS_REPO_ROOT:-__REPO_ROOT__}"
export GAUSS_HOME
export PATH="$HOME/.local/bin:$REPO_ROOT/venv/bin:$HOME/.elan/bin:$PATH"
gauss config set gauss.autoformalize.backend codex
printf '%s\\n' 'Gauss managed backend set to codex.'
""",
    "gauss-use-auto-auth": """#!/usr/bin/env bash
set -euo pipefail
GAUSS_HOME="${GAUSS_HOME:-__GAUSS_HOME__}"
REPO_ROOT="${GAUSS_REPO_ROOT:-__REPO_ROOT__}"
export GAUSS_HOME
export PATH="$HOME/.local/bin:$REPO_ROOT/venv/bin:$HOME/.elan/bin:$PATH"
gauss config set gauss.autoformalize.auth_mode auto
printf '%s\\n' 'Gauss auth mode set to auto.'
""",
    "gauss-use-claude-login": """#!/usr/bin/env bash
set -euo pipefail
GAUSS_HOME="${GAUSS_HOME:-__GAUSS_HOME__}"
REPO_ROOT="${GAUSS_REPO_ROOT:-__REPO_ROOT__}"
export GAUSS_HOME
export PATH="$HOME/.local/bin:$REPO_ROOT/venv/bin:$HOME/.elan/bin:$PATH"
gauss config set gauss.autoformalize.backend claude-code
gauss config set gauss.autoformalize.auth_mode login
exec claude auth login
""",
    "gauss-use-codex-login": """#!/usr/bin/env bash
set -euo pipefail
GAUSS_HOME="${GAUSS_HOME:-__GAUSS_HOME__}"
REPO_ROOT="${GAUSS_REPO_ROOT:-__REPO_ROOT__}"
export GAUSS_HOME
export PATH="$HOME/.local/bin:$REPO_ROOT/venv/bin:$HOME/.elan/bin:$PATH"
gauss config set gauss.autoformalize.backend codex
gauss config set gauss.autoformalize.auth_mode login
exec codex login
""",
    "gauss-use-anthropic-key": """#!/usr/bin/env bash
set -euo pipefail
GAUSS_HOME="${GAUSS_HOME:-__GAUSS_HOME__}"
REPO_ROOT="${GAUSS_REPO_ROOT:-__REPO_ROOT__}"
export GAUSS_HOME
export PATH="$HOME/.local/bin:$REPO_ROOT/venv/bin:$HOME/.elan/bin:$PATH"
if ! [ -f "$GAUSS_HOME/.env" ] || ! grep -Eq '^ANTHROPIC_API_KEY=|^ANTHROPIC_TOKEN=' "$GAUSS_HOME/.env"; then
  printf '%s\\n' 'No ANTHROPIC_API_KEY or ANTHROPIC_TOKEN found in ~/.gauss/.env.' >&2
  exit 1
fi
gauss config set gauss.autoformalize.backend claude-code
gauss config set gauss.autoformalize.auth_mode api-key
provider_status="$(gauss-configure-main-provider anthropic)"
printf '%s\\n' 'Gauss managed backend set to claude-code with api-key auth.'
printf '%s\\n' "$provider_status"
""",
    "gauss-use-openrouter-key": """#!/usr/bin/env bash
set -euo pipefail
GAUSS_HOME="${GAUSS_HOME:-__GAUSS_HOME__}"
REPO_ROOT="${GAUSS_REPO_ROOT:-__REPO_ROOT__}"
export GAUSS_HOME
export PATH="$HOME/.local/bin:$REPO_ROOT/venv/bin:$HOME/.elan/bin:$PATH"
if ! [ -f "$GAUSS_HOME/.env" ] || ! grep -q '^OPENROUTER_API_KEY=' "$GAUSS_HOME/.env"; then
  printf '%s\\n' 'No OPENROUTER_API_KEY found in ~/.gauss/.env.' >&2
  exit 1
fi
gauss-use-auto-auth >/dev/null
provider_status="$(gauss-configure-main-provider openrouter)"
printf '%s\\n' 'Gauss main interactive provider now uses OpenRouter. Managed backend auth stays on auto.'
printf '%s\\n' "$provider_status"
""",
    "gauss-use-openai-key": """#!/usr/bin/env bash
set -euo pipefail
GAUSS_HOME="${GAUSS_HOME:-__GAUSS_HOME__}"
REPO_ROOT="${GAUSS_REPO_ROOT:-__REPO_ROOT__}"
export GAUSS_HOME
export PATH="$HOME/.local/bin:$REPO_ROOT/venv/bin:$HOME/.elan/bin:$PATH"
if ! [ -f "$GAUSS_HOME/.env" ] || ! grep -q '^OPENAI_API_KEY=' "$GAUSS_HOME/.env"; then
  printf '%s\\n' 'No OPENAI_API_KEY found in ~/.gauss/.env.' >&2
  exit 1
fi
gauss config set gauss.autoformalize.backend codex
gauss config set gauss.autoformalize.auth_mode api-key
provider_status="$(gauss-configure-main-provider openai)"
printf '%s\\n' 'Gauss managed backend set to codex with api-key auth.'
printf '%s\\n' "$provider_status"
""",
    "gauss-launch-session": """#!/usr/bin/env bash
set -euo pipefail
GAUSS_HOME="${GAUSS_HOME:-__GAUSS_HOME__}"
REPO_ROOT="${GAUSS_REPO_ROOT:-__REPO_ROOT__}"
WORKSPACE_DIR="${GAUSS_WORKSPACE_DIR:-__WORKSPACE_DIR__}"
export GAUSS_HOME
export PATH="$HOME/.local/bin:$REPO_ROOT/venv/bin:$HOME/.elan/bin:$PATH"
export PROMPT_TOOLKIT_NO_CPR=1

if [ -f "$GAUSS_HOME/.env" ]; then
  set -a
  . "$GAUSS_HOME/.env"
  set +a
fi

print_summary=0
if [ "${1:-}" = "--print-summary" ]; then
  print_summary=1
  shift
fi

staged_keys="none"
if [ -n "${OPENROUTER_API_KEY:-}" ] || [ -n "${OPENAI_API_KEY:-}" ] || [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  staged_keys=""
  [ -n "${OPENROUTER_API_KEY:-}" ] && staged_keys="${staged_keys} OPENROUTER_API_KEY"
  [ -n "${OPENAI_API_KEY:-}" ] && staged_keys="${staged_keys} OPENAI_API_KEY"
  [ -n "${ANTHROPIC_API_KEY:-}" ] && staged_keys="${staged_keys} ANTHROPIC_API_KEY"
  staged_keys="${staged_keys# }"
fi

interactive_provider="none staged"
launch_gauss=0
if provider_status="$(gauss-configure-main-provider auto 2>&1)"; then
  interactive_provider="$provider_status"
  launch_gauss=1
else
  interactive_provider="$provider_status"
fi

clear >/dev/null 2>&1 || true
cat <<TXT
Open Gauss workflow installer is ready.

Repo: $REPO_ROOT
Branch: $(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || printf 'detached')
Commit: $(git -C "$REPO_ROOT" rev-parse --short=12 HEAD 2>/dev/null || printf 'unknown')
Lean project: $WORKSPACE_DIR
Guide: __GUIDE_PATH__
Gauss project manifest: initialized
Default managed backend: claude-code
Default auth mode: auto
Main interactive provider: ${interactive_provider}
Staged keys: ${staged_keys}

Quickstart:
  /prove
  /draft "Theorem 3.2"
  /autoprove
  /formalize --source ./paper.pdf "Theorem 3.2"
  /autoformalize --source ./paper.pdf --claim-select=first --out=Paper.lean
  /swarm

Backend helpers:
  gauss-use-claude-backend
  gauss-use-codex-backend
  gauss-use-auto-auth
  gauss-use-claude-login
  gauss-use-codex-login
  gauss-use-openrouter-key
  gauss-use-anthropic-key
  gauss-use-openai-key

Interactive provider notes:
  Auto-selection priority: OpenRouter, then Anthropic, then OpenAI-compatible.
  OpenRouter affects the main chat UI only; managed workflow backends stay separate.
  PROMPT_TOOLKIT_NO_CPR=1 is enabled to avoid CPR warnings inside tmux.

The local guide is written to __GUIDE_PATH__.
TXT

if [ "$print_summary" -eq 1 ]; then
  exit 0
fi

cd "$WORKSPACE_DIR"
if [ -t 0 ] && [ -t 1 ]; then
  GAUSS_FORCE_FIRST_TIME_SETUP=1 gauss setup || true
  exec bash -i
fi
if [ "$launch_gauss" -eq 1 ]; then
  gauss "$@" || true
  exit 0
fi
exit 1
""",
    "gauss-open-session": """#!/usr/bin/env bash
set -euo pipefail
launcher="$HOME/.local/bin/gauss-launch-session"
if [ "${1:-}" = "--print-summary" ]; then
  exec "$launcher" --print-summary
fi
if [ "$#" -eq 0 ] && command -v tmux >/dev/null 2>&1 && [ -t 0 ] && [ -t 1 ] && [ -z "${TMUX:-}" ] && [ "${GAUSS_NO_TMUX:-0}" != "1" ]; then
  exec tmux new-session -A -s gauss "$launcher"
fi
exec "$launcher" "$@"
""",
    "gauss-open-guide": """#!/usr/bin/env bash
set -euo pipefail
guide_path="__GUIDE_PATH__"
if [ ! -f "$guide_path" ]; then
  printf '%s\\n' "Guide not found: $guide_path" >&2
  exit 1
fi
if command -v xdg-open >/dev/null 2>&1; then
  exec xdg-open "$guide_path"
elif command -v open >/dev/null 2>&1; then
  exec open "$guide_path"
fi
printf '%s\\n' "$guide_path"
""",
}

for name, template in scripts.items():
    content = textwrap.dedent(template).lstrip("\n")
    for old, new in template_replacements.items():
        content = content.replace(old, new)
    path = helper_dir / name
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
PY_HELPERS

    log_success "Helper scripts and guide are ready"
}

auto_configure_main_provider() {
    log_info "Applying workflow-style main provider auto-selection..."
    if provider_status="$("$HOME/.local/bin/gauss-configure-main-provider" auto 2>&1)"; then
        AUTO_CONFIGURED_MAIN_PROVIDER=true
        log_success "$provider_status"
    else
        log_warn "$provider_status"
        log_info "No interactive provider was auto-configured. Use the staged-key helpers later if needed."
    fi
}

prepare_managed_runtime_assets() {
    log_info "Prewarming managed Lean workflow assets..."

    local managed_status
    if ! managed_status="$("$VENV_PYTHON" - <<'PY'
import os

from gauss_cli.autoformalize import prepare_managed_runtime_assets

prepared = prepare_managed_runtime_assets(env=os.environ)
revision = prepared.get("skill_revision", "").strip()
claude_plugin_root = prepared.get("claude_plugin_root", "").strip()

parts = []
if revision:
    parts.append(f"lean4-skills {revision[:12]}")
if prepared.get("lean_lsp_mcp_spec", "").strip():
    parts.append(f"lean-lsp-mcp {prepared['lean_lsp_mcp_spec']}")
if claude_plugin_root:
    parts.append(f"Claude plugin {claude_plugin_root}")
else:
    parts.append("Claude plugin skipped")
print("; ".join(parts))
PY
    )"; then
        log_error "Managed Lean workflow prewarm failed."
        exit 1
    fi

    log_success "Managed Lean workflow assets ready: $managed_status"
}

run_post_install_self_check() {
    log_info "Running post-install verification..."

    local version_output
    if ! version_output="$("$GAUSS_BIN" --version 2>&1)"; then
        log_error "Gauss CLI verification failed."
        printf '%s\n' "$version_output" >&2
        exit 1
    fi
    version_output="${version_output%%$'\n'*}"
    CLI_SELF_CHECK_STATUS="$version_output"
    log_success "Gauss CLI responds: $CLI_SELF_CHECK_STATUS"

    if [ -f "$WORKSPACE_DIR/.gauss/project.yaml" ] && { [ -f "$WORKSPACE_DIR/lakefile.lean" ] || [ -f "$WORKSPACE_DIR/lakefile.toml" ]; }; then
        if "$VENV_PYTHON" - "$WORKSPACE_DIR" "$REPO_ROOT" <<'PY'
import os
import sys
from pathlib import Path

workspace_dir = Path(sys.argv[1]).resolve()
repo_root = Path(sys.argv[2]).resolve()
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from gauss_cli.autoformalize import resolve_autoformalize_request
from gauss_cli.config import load_config

env = dict(os.environ)
env["GAUSS_AUTOFORMALIZE_AUTH_MODE"] = "login"
plan = resolve_autoformalize_request(
    "/prove Main.lean",
    load_config(),
    active_cwd=str(workspace_dir),
    base_env=env,
)
assert plan.backend_command.startswith("/lean4:prove")
assert plan.managed_context.plugin_root.exists()
assert plan.managed_context.mcp_config_path.exists()
PY
        then
            MANAGED_SELF_CHECK_STATUS="$WORKSPACE_DIR"
            log_success "Managed /prove staging verified: $MANAGED_SELF_CHECK_STATUS"
        else
            log_error "Managed /prove staging verification failed."
            exit 1
        fi
    else
        MANAGED_SELF_CHECK_STATUS=""
        log_info "Skipping managed /prove staging verification because no active Lean workspace was found."
    fi
}

print_summary() {
    echo
    echo -e "${GREEN}${BOLD}"
    echo "┌─────────────────────────────────────────────────────────┐"
    echo "│             ✓ Open Gauss Is Ready                       │"
    echo "└─────────────────────────────────────────────────────────┘"
    echo -e "${NC}"
    echo
    echo -e "${CYAN}${BOLD}Ready Paths:${NC}"
    echo "  Repo:       $REPO_ROOT"
    echo "  Venv:       $VENV_DIR"
    echo "  Gauss home: $GAUSS_HOME"
    if [ -d "$WORKSPACE_DIR" ] && [ "$CREATE_WORKSPACE" = true ]; then
        echo "  Workspace:  $WORKSPACE_DIR"
    fi
    echo "  Guide:      $GUIDE_DIR/index.html"
    echo
    echo -e "${CYAN}${BOLD}Next Steps:${NC}"
    echo "  1. Start immediately:   $GAUSS_BIN"
    echo "  2. Verify the CLI:      $GAUSS_BIN --version"
    echo "  3. Reload for \`gauss\`: $SHELL_RELOAD_HINT   # current shell ($ACTIVE_SHELL_NAME)"
    if [ -n "$SECONDARY_SHELL_RELOAD_HINT" ]; then
        echo "                          $SECONDARY_SHELL_RELOAD_HINT   # if you switch to $SECONDARY_SHELL_NAME"
    fi
    echo "  4. Review settings:     gauss setup"
    echo
    echo -e "${CYAN}${BOLD}Start Options:${NC}"
    echo "  gauss                # direct CLI launch in this terminal"
    echo "  gauss-open-session   # batteries-included launcher (tmux when interactive)"
    echo "  gauss-open-guide     # open the local guide in a browser, or print its path"
    echo
    echo "  Inside Gauss, use /project create <path> to create a Lean project."
    echo
    echo -e "${CYAN}${BOLD}Helper Commands:${NC}"
    echo "  gauss-configure-main-provider [auto|openrouter|anthropic|openai]"
    echo "  gauss-use-openrouter-key"
    echo "  gauss-use-anthropic-key"
    echo "  gauss-use-openai-key"
    echo "  gauss-use-claude-backend"
    echo "  gauss-use-codex-backend"
    echo "  gauss-use-auto-auth"
    echo
    echo -e "${CYAN}${BOLD}Notes:${NC}"
    echo "  - The installer keeps code in your existing repository checkout."
    echo "  - The installer updates future shells, but it cannot change PATH in the shell that launched the installer."
    echo "  - If \`gauss\` is not found in this terminal yet, use $GAUSS_BIN once or source one of the shell files above."
    if [ -n "$CLI_SELF_CHECK_STATUS" ]; then
        echo "  - Verified CLI startup: $CLI_SELF_CHECK_STATUS."
    fi
    if [ -n "$MANAGED_SELF_CHECK_STATUS" ]; then
        echo "  - Verified managed /prove staging in: $MANAGED_SELF_CHECK_STATUS."
    fi
    echo "  - The local guide is written to $GUIDE_DIR/index.html."
    echo "  - No Morph iframe is exposed automatically; use gauss-open-guide if you want the local guide in a browser."
    echo "  - No tmux session is opened during install; use gauss-open-session when you want the workflow launcher."
}

main() {
    apply_env_overrides
    parse_args "$@"
    print_banner
    detect_os
    require_repo_checkout
    warn_wsl_checkout_location
    export GAUSS_HOME
    export GAUSS_INSTALL_ROOT="$REPO_ROOT"
    ensure_local_bin_path
    detect_shell_reload_hint
    install_system_packages
    ensure_uv
    ensure_nodejs
    ensure_global_cli_tools
    ensure_lean_toolchain
    sync_repo_submodules
    ensure_python_runtime
    install_repo_dependencies
    link_repo_binaries
    prepare_gauss_home
    sync_optional_provider_keys
    ensure_workspace
    initialize_gauss_workspace || true
    ensure_shell_runtime_block
    write_helper_assets
    auto_configure_main_provider
    prepare_managed_runtime_assets
    run_post_install_self_check
    print_summary
    run_setup_wizard
}

run_setup_wizard() {
    if [ "$SETUP_MODE" = "skip" ]; then
        log_info "Skipping setup wizard (--skip-setup); run 'gauss setup' later if needed."
        return
    fi
    if ! [ -t 0 ] || ! [ -t 1 ]; then
        if [ "$SETUP_MODE" = "run" ]; then
            log_error "--run-setup requires an interactive terminal because \`gauss setup\` is interactive."
            exit 1
        fi
        log_info "Skipping setup wizard (no interactive terminal detected); run 'gauss setup' later if needed."
        return
    fi
    if [ "$SETUP_MODE" = "run" ]; then
        log_info "Running setup wizard (--run-setup)..."
        echo
        "$VENV_PYTHON" -m gauss_cli.main setup
        return
    fi
    if [ "$AUTO_CONFIGURED_MAIN_PROVIDER" = true ]; then
        log_info "Skipping setup wizard prompt because the main provider was auto-configured. Run \`gauss setup\` any time to review or change it."
        return
    fi

    echo
    read -p "Would you like to run the setup wizard now? (configure API keys + model) [Y/n] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
        echo
        "$VENV_PYTHON" -m gauss_cli.main setup
    fi
}

main "$@"
