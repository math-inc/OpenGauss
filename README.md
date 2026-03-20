<p align="center">
  <a href="https://morph.new/gauss-batteries-opengauss-dev-wipv2-latest-20260319-024019">
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

## What the harness adds

- Managed multi-agent workflow spawning without leaving the current terminal session
- A local `.gauss` project model with upward discovery and explicit `/project` lifecycle commands
- Pinned Lean 4 context, including the `lean4-skills` plugin and `lean-lsp-mcp`
- Backend startup prompts that forward Gauss workflow commands to the staged `lean4-skills` workflow surface
- Preflight checks for active Gauss projects, backend auth, `uv` or `uvx`, and `rg`
- Isolated Gauss-managed state for agent configuration and Lean MCP wiring
- A swarm status surface for parallel workflow runs, attach/detach, and recovery

## Install

```bash
# From the root of a checked-out math-inc/opengauss repository
./scripts/install.sh
```

This workflow-derived installer targets Linux checkouts, with Ubuntu, Debian, and WSL as the primary supported environments. It keeps the code in your existing repository checkout, defaults runtime state to `~/.gauss/`, exposes `gauss` through `~/.local/bin/gauss`, prewarms `~/GaussWorkspace`, and writes helper commands such as `gauss-open-session` and `gauss-open-guide`.

## First run

```text
gauss
/project init
/prove
```

If you want Gauss to manage multiple workflows in parallel against the same project:

```text
gauss
/project use .
/prove Main.lean
/draft "The next missing theorem statement"
/autoprove --max-cycles=4
/swarm
/swarm attach af-001
```

Or launch an interactive source-backed formalization workflow directly:

```text
/formalize --source ./paper.pdf "Theorem 3.2"
```

Or kick off the autonomous end-to-end source-backed workflow:

```text
/autoformalize --source ./paper.pdf --claim-select=first --out=Paper.lean
```

You can switch the managed backend from inside the running CLI:

```text
/autoformalize-backend codex
```

## Workflow surface

The managed workflow surface in the interactive CLI is:

- `/prove [scope or flags]`
- `/draft [topic or flags]`
- `/autoprove [scope or flags]`
- `/formalize [topic or flags]`
- `/autoformalize [topic or flags]`
- `/swarm`
- `/swarm attach <task-id>`
- `/swarm cancel <task-id>`

These commands keep the underlying Lean workflow semantics. Gauss adds project selection, backend staging, session isolation, and swarm supervision around them.

## Compatibility notes

- `gauss` is the primary command
- `gauss update` expects the active CLI to come from a checked-out `math-inc/opengauss` git repository
- Fresh installs default to `~/.gauss/`
- If you already have `~/.gauss/`, the installer reuses it
- `/handoff` remains available as a compatibility alias for `/autoformalize`

## Prerequisites for managed workflows

- `gauss.autoformalize.backend` defaults to `claude-code`
- Built-in managed-workflow backends: `claude-code`, `codex`
- `claude` or `codex` installed and authenticated for the backend you select
- Claude auth can come from either:
  - the normal `claude auth login` flow / Claude credential files
  - a saved `ANTHROPIC_API_KEY` in `~/.gauss/.env`
- If both are present, Gauss defaults to Claude's own local auth and only falls back to `ANTHROPIC_API_KEY` when no Claude credentials are available
- Codex auth can come from either:
  - the normal `codex login` flow / `~/.codex/auth.json`
  - a saved `OPENAI_API_KEY` in `~/.gauss/.env`
- Override that with `gauss.autoformalize.auth_mode` in `~/.gauss/config.yaml`:
  - `auto` (default): prefer local backend auth, then fall back to saved env/API-key auth when supported
  - `login`: ignore staged API-key auth and let the backend use the normal interactive login flow
  - `api-key`: force the managed session onto saved env/API-key auth
- For one-off overrides, set `GAUSS_AUTOFORMALIZE_BACKEND=claude-code` or `GAUSS_AUTOFORMALIZE_BACKEND=codex`,
  plus `GAUSS_AUTOFORMALIZE_AUTH_MODE=login` or `GAUSS_AUTOFORMALIZE_AUTH_MODE=api-key`
  before launching `gauss`
- `uv` or `uvx` available
- `ripgrep` (`rg`) available for Lean local search
- An active Gauss project selected via `.gauss/project.yaml`

Gauss checks these before launch and tells you exactly what is missing.

This repository was forked from `nousresearch/hermes-agent`
