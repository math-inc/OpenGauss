---
sidebar_position: 2
title: "Installation"
description: "Install Gauss from a Linux repository checkout."
---

# Installation

## Recommended

```bash
# From the root of a checked-out math-inc/opengauss repository
./scripts/install.sh
```

This repository-local installer targets Linux checkouts, with Ubuntu, Debian, and WSL as the primary supported environments. It keeps code in your existing checkout, writes runtime state to `~/.gauss/`, exposes `gauss` via `~/.local/bin/gauss`, and prewarms `~/GaussWorkspace`.

## Compatibility Behavior

- `gauss` is the primary command
- `gauss update` expects the active CLI to come from a checked-out `math-inc/opengauss` git repository
- if you already have `~/.gauss/`, the installer reuses it

## After Install

```bash
gauss
```

Inside the CLI, start by selecting a project:

```text
/project init
```

## Windows

Native Windows is not a primary target. Use WSL2 when possible.
