<p align="center">
  <a href="https://morph.new/opengauss">
    <img src="https://img.shields.io/badge/Open%20in-Morph-f23f42?style=for-the-badge" alt="Open in Morph">
  </a>
</p>

# Open Gauss

Open Gauss is a project-scoped Lean workflow orchestrator from Math, Inc. It gives `gauss` a multi-agent frontend for the `lean4-skills` `prove`, `draft`, `autoprove`, `formalize`, and `autoformalize` workflows, while staging the Lean tooling, MCP/LSP wiring, and backend session state those workflows need.

Open Gauss handles project detection, managed backend setup, workflow spawning, swarm tracking, and recovery. The proving and formalization behavior still comes from `cameronfreer/lean4-skills`; Gauss exposes it through a Gauss-native CLI and project model.

Each lifted slash command spawns a managed backend child agent in the active project and forwards the same argument tail into the corresponding `lean4-skills` workflow command:

- `/prove ...` -> `/lean4:prove ...`
- `/draft ...` -> `/lean4:draft ...`
- `/autoprove ...` -> `/lean4:autoprove ...`
- `/formalize ...` -> `/lean4:formalize ...`
- `/autoformalize ...` -> `/lean4:autoformalize ...`

## Install

### macOS and Linux

```bash
git clone https://github.com/math-inc/OpenGauss.git
cd OpenGauss
./scripts/install.sh
```

The installer will:

1. Install system dependencies (via Homebrew on macOS, apt on Ubuntu/Debian)
2. Install `uv`, Node.js, Claude Code, and the Lean toolchain if missing
3. Create a Python virtualenv and install Gauss
4. Link the `gauss` command to `~/.local/bin/gauss`
5. Set up `~/.gauss/` for runtime config and secrets
6. Auto-configure the main provider/model from staged keys when possible

`~/.local/bin/gauss` is ready immediately after install. To use plain `gauss` in the same terminal, reload your shell first:

```bash
source ~/.bashrc   # bash
source ~/.zshrc    # zsh
gauss
```

Run `gauss setup` later if you want to review or change the selected provider, model, or other settings.

If you prefer Gauss to create a prewarmed Lean workspace during install:

```bash
./scripts/install.sh --with-workspace
```

Otherwise, after install just start Gauss:

```bash
gauss
```
## Configuration

### 🖥️ Using Local Models (vLLM)
If you prefer to run models locally (e.g., using a local GPU) to save on API costs:

1. **Start your vLLM server** (OpenAI-compatible):
   ```bash
   python -m vllm.entrypoints.openai.api_server --model <model_name>
   ```
2. **Point Gauss at that server** with `gauss setup`, or update `OPENAI_BASE_URL` in `~/.gauss/.env`.

### Options

```bash
./scripts/install.sh --with-workspace    # Also create a prewarmed Lean+Mathlib workspace (~2 GB download)
./scripts/install.sh --skip-system-packages  # Skip Homebrew/apt package installation
./scripts/install.sh --recreate-venv     # Force-recreate the Python virtualenv
```

### Updating

```bash
cd OpenGauss
git pull
gauss update
```

## Quick start

```
gauss                         # Launch the CLI
/project create ~/my-project  # Create a new Lean project
/prove 1+1=2                  # Spawn a proving agent
/swarm                        # See running agents
```

If you already have a Lean project:

```
cd ~/my-lean-project
gauss
/project init                 # Register it as a Gauss project
/prove                        # Start proving
```

## The core loop

1. Start the CLI with `gauss`
2. Create or select the active project with `/project`
3. Launch `/prove`, `/draft`, `/autoprove`, `/formalize`, or `/autoformalize`
4. Gauss spawns a managed backend child session that runs the corresponding `lean4-skills` workflow command in the active project
5. Use `/swarm` to track or reattach to running workflow agents

## Project model

Gauss treats Lean work as project-scoped by default. Before launching managed workflows, select the active project once and then let Gauss keep spawning backend child agents inside that project root.

- `/project init [path] [--name <name>]` registers an existing Lean repo as a Gauss project
- `/project convert [path] [--name <name>]` registers an existing Lean blueprint repo
- `/project create <path> [--template-source <source>] [--name <name>]` bootstraps a project from a template and registers it
- `/project use [path]` pins the current session to an existing Gauss project
- `/project clear` removes the session override and falls back to ambient project discovery

Gauss discovers `.gauss/project.yaml` upward from the current working directory, but managed workflow child agents launch from the active project root so the forwarded Lean workflow command always runs in the right project context.

## Workflow commands

- `/prove [scope or flags]` — spawn a guided proving agent
- `/draft [topic or flags]` — draft Lean declaration skeletons
- `/autoprove [scope or flags]` — spawn an autonomous proving agent
- `/formalize [topic or flags]` — spawn an interactive formalization agent
- `/autoformalize [topic or flags]` — spawn an autonomous formalization agent
- `/swarm` — list running workflow agents
- `/swarm attach <task-id>` — reattach to a running agent
- `/swarm cancel <task-id>` — cancel a running agent

## Managed workflow prerequisites

- `gauss.autoformalize.backend` defaults to `claude-code`
- Built-in backends: `claude-code`, `codex`
- `claude` or `codex` installed and authenticated for the backend you select
- Claude auth can come from either:
  - the normal `claude auth login` flow / Claude credential files
  - a saved `ANTHROPIC_API_KEY` in `~/.gauss/.env`
- If both are present, Gauss defaults to Claude's own local auth and only falls back to `ANTHROPIC_API_KEY` when no Claude credentials are available
- Override with `gauss.autoformalize.auth_mode` in `~/.gauss/config.yaml`:
  - `auto` (default): prefer local backend auth, then fall back to saved env/API-key auth
  - `login`: ignore staged API-key auth and let the backend use the normal interactive login flow
  - `api-key`: force the managed session onto saved env/API-key auth
- `uv` or `uvx` available
- `ripgrep` (`rg`) available for Lean local search
- An active Gauss project selected via `.gauss/project.yaml`

Gauss checks these before launch and tells you exactly what is missing.

---

This repository was forked from `nousresearch/hermes-agent`.
