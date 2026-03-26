#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNNER_VENV="$REPO_ROOT/.opengauss-installer-venv"
DEFAULT_INSTALL_TARGET="opengauss"
INSTALL_TARGET="${OPEN_GAUSS_INSTALL_TARGET:-${OPEN_GAUSS_TEMPLATE_TARGET:-$DEFAULT_INSTALL_TARGET}}"
DEFAULT_SESSION_NAME="gauss"
SESSION_NAME="${OPEN_GAUSS_SESSION_NAME:-$DEFAULT_SESSION_NAME}"

print_direct_start_hint() {
  printf 'Open Gauss is ready. Start with: gauss setup\n'
}

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
"$RUNNER_VENV/bin/morphcloud" devbox template run "$INSTALL_TARGET" --experimental-run-locally "$@"
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
