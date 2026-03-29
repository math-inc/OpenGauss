#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/src}"
GAUSS_HOME="${GAUSS_HOME:-/root/.gauss}"
FIXTURE_DIR="$REPO_ROOT/tests/installer/fixtures/lean_hello_sorry"
SCENARIO_DIR="$REPO_ROOT/tests/installer/ubuntu_managed_prove_sorry_smoke"
PROJECT_TARGET="HelloSorry/Basic.lean"

log() {
    printf '==> %s\n' "$1"
}

die() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 1
}

if [[ ! -f "$REPO_ROOT/scripts/install-internal.sh" ]]; then
    die "installer script not found under $REPO_ROOT"
fi

if [[ ! -d "$FIXTURE_DIR" ]]; then
    die "fixture directory not found: $FIXTURE_DIR"
fi

if [[ -z "${ANTHROPIC_API_KEY:-}" && -z "${OPENAI_API_KEY:-}" ]]; then
    die "set ANTHROPIC_API_KEY or OPENAI_API_KEY inside the container"
fi

if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    BACKEND="claude-code"
    AUTH_KEY_LABEL="ANTHROPIC_API_KEY"
elif [[ -n "${OPENAI_API_KEY:-}" ]]; then
    BACKEND="codex"
    AUTH_KEY_LABEL="OPENAI_API_KEY"
fi

log "Installer scenario: ubuntu_managed_prove_sorry_smoke"
log "Using repository checkout: $REPO_ROOT"
log "Using backend: $BACKEND ($AUTH_KEY_LABEL)"

cd "$REPO_ROOT"
./scripts/install-internal.sh \
    --gauss-home "$GAUSS_HOME" \
    --skip-setup

export GAUSS_HOME
export PATH="$HOME/.local/bin:$REPO_ROOT/venv/bin:$HOME/.elan/bin:$PATH"

WORKDIR="$(mktemp -d)"
PROJECT_DIR="$WORKDIR/HelloSorry"
cp -R "$FIXTURE_DIR" "$PROJECT_DIR"

log "Priming Lean project"
( cd "$PROJECT_DIR" && lake build )

log "Running managed /prove staging smoke"
SMOKE_ARGS=(
    --project-dir "$PROJECT_DIR"
    --target "$PROJECT_TARGET"
    --backend "$BACKEND"
    --timeout-seconds "${PROVE_TIMEOUT_SECONDS:-900}"
)
if [[ "${LIVE_MANAGED_PROVE_SMOKE:-}" == "1" ]]; then
    log "Enabling live managed workflow execution (best-effort debug path)"
    SMOKE_ARGS+=(--live-run)
fi
"$REPO_ROOT/venv/bin/python" "$SCENARIO_DIR/managed_prove_smoke.py" "${SMOKE_ARGS[@]}"

log "Current $PROJECT_TARGET"
sed -n '1,160p' "$PROJECT_DIR/$PROJECT_TARGET"
