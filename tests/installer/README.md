# Installer Tests

This directory holds installer verification scenarios that run in clean,
containerized environments.

Each scenario usually includes:

- `Dockerfile`: the base image for the scenario
- `run.sh`: host-side entrypoint that builds the image and runs the container
- `run-in-container.sh`: in-container entrypoint that executes the actual test
- `README.md`: scenario-specific notes and requirements

Current scenarios:

- `ubuntu_repository_local_install_smoke`
  Verifies the repository-local `./scripts/install-internal.sh` flow on a stock
  `ubuntu:24.04` container. This scenario:
  - starts from the stock Ubuntu base image
  - exercises `./scripts/install-internal.sh` from a mounted git checkout
  - verifies the installer bootstraps its own Debian/Ubuntu prerequisites
  - stages a dummy `OPENAI_API_KEY` to test non-interactive provider setup
  - verifies the workflow-derived workspace, config, guide, and helper scripts

- `ubuntu_managed_prove_sorry_smoke`
  Verifies that a stock `ubuntu:24.04` container can install from the current
  checkout and stage a managed `/prove` run against a tiny Lean project
  containing one `sorry`. This scenario:
  - installs via `./scripts/install-internal.sh` from a mounted git checkout
  - accepts either `ANTHROPIC_API_KEY` (Claude backend) or `OPENAI_API_KEY`
    (Codex backend)
  - initializes a local `.gauss` manifest in the fixture project
  - verifies the staged startup context, managed skill, and backend instructions
  - verifies the managed MCP config points `LEAN_PROJECT_PATH` at the fixture
  - leaves an opt-in `LIVE_MANAGED_PROVE_SMOKE=1` path for manual backend runs
