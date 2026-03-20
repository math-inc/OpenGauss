# Ubuntu Repository-Local Installer Smoke

This scenario verifies the workflow-derived `./scripts/install.sh` path on a
bare `ubuntu:24.04` container using a mounted local checkout.

It exercises:

- `./scripts/install.sh --workspace-dir /root/GaussWorkspaceSmoke`
- the installer's own Debian/Ubuntu bootstrap step for required prerequisites
- repository-local dependency installation (`.[full]`, `mini-swe-agent`, `npm ci`)
- helper script generation and provider auto-configuration with a dummy
  `OPENAI_API_KEY`
- the guide file, workspace manifest, and key config defaults

## Requirements

- Docker installed and running on the host

## Run

```bash
tests/installer/ubuntu_repository_local_install_smoke/run.sh
```
