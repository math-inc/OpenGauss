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

If you want the fastest path, `https://morph.new/opengauss` launches the hosted setup in under 10 seconds. The local installers below are the batteries-included path for your own machine and can take up to 10 minutes.

### macOS and Linux

```bash
git clone https://github.com/math-inc/OpenGauss.git
cd OpenGauss
./scripts/install.sh
```

This is the canonical local install path. It bootstraps the local installer runtime, runs the shared `opengauss` installer flow on your machine, and then auto-attaches to the final `gauss` tmux session when possible.

It will:

1. Install `uv` if needed
2. Create a repo-local installer environment
3. Install or upgrade the local runner
4. Run the shared `opengauss` installer flow on your machine
5. Auto-attach you to the local `gauss` tmux session when possible, or print the exact `tmux attach -t gauss` command if not

You can pass normal template-runner flags through to the alternate script, for example:

```bash
./scripts/install.sh --plain
./scripts/install.sh --secret OPENAI_API_KEY=...
./scripts/install.sh --secret ANTHROPIC_API_KEY=...
```

### Windows (via WSL2)

Open Gauss on Windows runs through WSL2 using the same shared installer flow.

From PowerShell:

```powershell
.\scripts\install.ps1 -WithWorkspace
```

This bootstrap:

1. Starts your WSL distro
2. Clones or updates `OpenGauss` inside your WSL home directory
3. Runs `./scripts/install.sh` there, which executes the shared `opengauss` installer flow inside WSL

If no WSL distro is initialized yet, the bootstrap will install Ubuntu for you with `wsl --install -d Ubuntu`. If that process drops you into the new Linux shell, type `exit` to return to PowerShell and rerun `.\scripts\install.ps1 -WithWorkspace`. Windows may also ask to enable WSL features or restart before you rerun the installer.

If WSL is not installed yet:

```powershell
wsl --install -d Ubuntu
```

You can also install manually inside WSL:

```bash
wsl
git clone https://github.com/math-inc/OpenGauss.git ~/OpenGauss
cd ~/OpenGauss
./scripts/install.sh
```

Use a Linux path such as `~/OpenGauss`, not `/mnt/c/...`, for the best performance and terminal behavior.
## Configuration

### 🖥️ Using Local Models (vLLM)
If you prefer to run models locally (e.g., using a local GPU) to save on API costs:

1. **Start your vLLM server** (OpenAI-compatible):
   ```bash
   python -m vllm.entrypoints.openai.api_server --model <model_name>
   ```

2. **Point Gauss at that server** with `gauss setup`, or update `OPENAI_BASE_URL` in `~/.gauss/.env`.

### Updating

```bash
cd OpenGauss
git pull
gauss update
```

## Quick start

```
gauss                         # Launch the CLI
/project create ~/my-project --template-source <template-or-git-url>
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

If you use `/project create` often, set a default template once with
`gauss.project.template_source` in `~/.gauss/config.yaml` or the
`GAUSS_BLUEPRINT_TEMPLATE_SOURCE` environment variable.

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
`gauss doctor` also reports the managed-workflow backend, auth mode, `uv` / `lake`
availability, and whether the current working directory resolves to an active
Gauss project.

---

This repository was forked from `nousresearch/hermes-agent`.
