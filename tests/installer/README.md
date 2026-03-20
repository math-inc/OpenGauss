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
  Verifies the repository-local `./scripts/install.sh` flow on a stock
  `ubuntu:24.04` container. This scenario:
  - starts from the stock Ubuntu base image
  - exercises `./scripts/install.sh` from a mounted git checkout
  - verifies the installer bootstraps its own Debian/Ubuntu prerequisites
  - stages a dummy `OPENAI_API_KEY` to test non-interactive provider setup
  - verifies the workflow-derived workspace, config, guide, and helper scripts
