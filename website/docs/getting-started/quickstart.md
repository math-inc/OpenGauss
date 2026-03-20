---
sidebar_position: 1
title: "Quickstart"
description: "Install Gauss, select a project, and launch the managed Lean prove/draft/autoprove/formalize workflows."
---

# Quickstart

## 1. Install

```bash
# From the root of a checked-out math-inc/opengauss repository
./scripts/install.sh
```

The installer targets Linux checkouts, defaults runtime state to `~/.gauss/`, exposes `gauss` via `~/.local/bin/gauss`, and prewarms `~/GaussWorkspace`. Run `gauss-open-session` if you want the batteries-included launcher; run `gauss` directly if you prefer the raw CLI.

## 2. Open the CLI

```bash
gauss
```

## 3. Select the active project

```text
/project init
```

Or point Gauss at an existing project:

```text
/project use /path/to/project
```

## 4. Launch a Lean workflow

```text
/prove
/draft "The next theorem statement"
/autoprove
/formalize --source ./paper.pdf "Theorem 3.2"
/autoformalize --source ./paper.pdf --claim-select=first --out=Paper.lean
```

## 5. What Gauss checks before launch

- the active project resolves to a valid `.gauss/project.yaml`
- the selected backend CLI is installed
- backend auth is available
- `uv` or `uvx` is available for the managed Lean MCP server
- the active project points at a Lean repository

If any check fails, Gauss stops before handoff and explains what is missing.
