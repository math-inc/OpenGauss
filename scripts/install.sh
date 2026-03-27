#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNNER_VENV="$REPO_ROOT/.opengauss-installer-venv"
DEFAULT_INSTALL_TARGET="opengauss"
INSTALL_TARGET="${OPEN_GAUSS_INSTALL_TARGET:-${OPEN_GAUSS_TEMPLATE_TARGET:-$DEFAULT_INSTALL_TARGET}}"
DEFAULT_SESSION_NAME="gauss"
SESSION_NAME="${OPEN_GAUSS_SESSION_NAME:-$DEFAULT_SESSION_NAME}"
MORPH_ARGS=()

usage() {
  cat <<'TXT'
Open Gauss installer wrapper

Usage:
  ./scripts/install.sh [installer options] [morphcloud passthrough options]

Installer options:
  --gauss-home PATH
  --workspace-dir PATH
  --skip-system-packages
  --with-workspace
  --skip-setup
  --run-setup
  --noninteractive
  --skip-setup-wizard
  --recreate-venv
  -h, --help

Morph passthrough options:
  --attach
  --force
  --json
  --plain
  --param KEY=VALUE
  --secret KEY=VALUE

Behavior:
  Installer options are translated into environment variables for the local
  Morph template run, so they work with `--experimental-run-locally`.
TXT
}

die() {
  printf '%s\n' "$1" >&2
  exit 1
}

set_setup_mode() {
  local mode="$1"
  local current="${GAUSS_SETUP_MODE:-auto}"
  if [ "$current" != "auto" ] && [ "$current" != "$mode" ]; then
    die "Use only one of --skip-setup/--noninteractive or --run-setup."
  fi
  export GAUSS_SETUP_MODE="$mode"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --gauss-home)
        if [ $# -lt 2 ]; then
          die "--gauss-home requires a PATH value."
        fi
        export GAUSS_HOME="$2"
        shift 2
        ;;
      --workspace-dir)
        if [ $# -lt 2 ]; then
          die "--workspace-dir requires a PATH value."
        fi
        export GAUSS_WORKSPACE_DIR="$2"
        shift 2
        ;;
      --skip-system-packages)
        export GAUSS_SKIP_SYSTEM_PACKAGES=1
        shift
        ;;
      --with-workspace)
        export GAUSS_CREATE_WORKSPACE=1
        shift
        ;;
      --skip-setup|--noninteractive|--skip-setup-wizard)
        set_setup_mode "skip"
        shift
        ;;
      --run-setup)
        set_setup_mode "run"
        shift
        ;;
      --recreate-venv)
        export GAUSS_RECREATE_VENV=1
        shift
        ;;
      --attach|--force|--json|--plain)
        MORPH_ARGS+=("$1")
        shift
        ;;
      --param|--secret)
        if [ $# -lt 2 ]; then
          die "$1 requires a KEY=VALUE argument."
        fi
        MORPH_ARGS+=("$1" "$2")
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      --)
        shift
        while [[ $# -gt 0 ]]; do
          MORPH_ARGS+=("$1")
          shift
        done
        ;;
      *)
        MORPH_ARGS+=("$1")
        shift
        ;;
    esac
  done
}

print_direct_start_hint() {
  printf 'Open Gauss is ready. Start with: gauss setup\n'
}

parse_args "$@"

if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'python3 is required to bootstrap the Open Gauss installer.' >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  printf '%s\n' 'Installing uv (required to bootstrap the Open Gauss installer)...'
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if [ ! -x "$RUNNER_VENV/bin/python" ]; then
  uv venv "$RUNNER_VENV"
fi

uv pip install --python "$RUNNER_VENV/bin/python" morphcloud --upgrade

printf 'Running Open Gauss installer flow locally from target: %s\n' "$INSTALL_TARGET"
"$RUNNER_VENV/bin/morphcloud" devbox template run "$INSTALL_TARGET" --experimental-run-locally "${MORPH_ARGS[@]}"
run_exit=$?
if [ "$run_exit" -ne 0 ]; then
  exit "$run_exit"
fi

if ! command -v tmux >/dev/null 2>&1; then
  print_direct_start_hint
  exit 0
fi

if ! tmux has-session -t "$SESSION_NAME" >/dev/null 2>&1; then
  printf 'Open Gauss is ready, but tmux session %s was not found.\n' "$SESSION_NAME" >&2
  print_direct_start_hint
  exit 0
fi

if [ "${OPEN_GAUSS_AUTO_ATTACH:-1}" != "0" ] && [ -t 0 ] && [ -t 1 ]; then
  printf 'Attaching to Open Gauss session: %s\n' "$SESSION_NAME"
  if [ -n "${TMUX:-}" ]; then
    if tmux switch-client -t "$SESSION_NAME"; then
      exit 0
    fi
    printf 'Open Gauss is ready. Attach with: tmux attach -t %s\n' "$SESSION_NAME"
    exit 0
  fi
  exec tmux attach -t "$SESSION_NAME"
fi

printf 'Open Gauss is ready. Attach with: tmux attach -t %s\n' "$SESSION_NAME"
