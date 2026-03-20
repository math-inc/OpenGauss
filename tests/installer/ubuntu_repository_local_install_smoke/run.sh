#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
IMAGE_TAG="${IMAGE_TAG:-opengauss-installer-ubuntu-repository-local-smoke}"

if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker is not installed or not on PATH" >&2
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    echo "ERROR: docker daemon is not running" >&2
    exit 1
fi

echo "==> Building $IMAGE_TAG"
docker build -t "$IMAGE_TAG" -f "$SCRIPT_DIR/Dockerfile" "$SCRIPT_DIR"

echo "==> Running ubuntu_repository_local_install_smoke"
docker run --rm \
    -e REPO_ROOT=/src \
    -v "$REPO_ROOT:/src" \
    "$IMAGE_TAG" \
    bash -lc "/src/tests/installer/ubuntu_repository_local_install_smoke/run-in-container.sh"
