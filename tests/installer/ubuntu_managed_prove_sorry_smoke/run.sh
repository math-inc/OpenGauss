#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
IMAGE_TAG="${IMAGE_TAG:-opengauss-installer-ubuntu-managed-prove-sorry-smoke}"

if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker is not installed or not on PATH" >&2
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    echo "ERROR: docker daemon is not running" >&2
    exit 1
fi

if [[ -z "${ANTHROPIC_API_KEY:-}" && -z "${OPENAI_API_KEY:-}" ]]; then
    echo "ERROR: set ANTHROPIC_API_KEY or OPENAI_API_KEY for the managed prove smoke" >&2
    exit 1
fi

echo "==> Building $IMAGE_TAG"
docker build -t "$IMAGE_TAG" -f "$SCRIPT_DIR/Dockerfile" "$SCRIPT_DIR"

echo "==> Running ubuntu_managed_prove_sorry_smoke"
docker run --rm \
    -e ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
    -e OPENAI_API_KEY="${OPENAI_API_KEY:-}" \
    -e PROVE_TIMEOUT_SECONDS="${PROVE_TIMEOUT_SECONDS:-900}" \
    -e REPO_ROOT=/src \
    -v "$REPO_ROOT:/src" \
    "$IMAGE_TAG" \
    bash -lc "/src/tests/installer/ubuntu_managed_prove_sorry_smoke/run-in-container.sh"
