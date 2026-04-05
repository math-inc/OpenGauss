---
sidebar_position: 1
title: "Start Here"
description: "A plain-language OpenGauss quick start for mathematicians using either Morph or a local install."
---

# Start Here

OpenGauss is for Lean work, but you do **not** need to understand MCP, plugin internals, or agent orchestration to get started.

If you only want a guided first step or plain-language help in the current session, use `/chat`.

If you want a managed Claude Code or Codex child session first, use `/managed-chat`.

If you want OpenGauss to work inside a Lean project, use `/project`.

## 30-Second Version

- **Morph**: open `morph.new/opengauss`, claim or save the session early if Morph offers that option, run `gauss-open-guide` if the guide is not already open, then start with `/chat`, `/managed-chat`, or `/project init`.
- **Local install**: run `./scripts/install.sh`, then `gauss-open-guide` or `gauss`, then start with `/chat`, `/managed-chat`, or `/project init`.
- **Already have a Lean repo**: `cd` into it, run `gauss`, then `/project init`.
- **Need a new Lean repo**: run `gauss`, then `/project create <path> --template-source <template-or-git-url>`.

## Which Command Should I Start With?

- `/chat` turns on onboarding mode, gives you the first useful commands, and lets plain text go straight to the main chat in the current Gauss session.
- `/managed-chat` opens the configured managed backend child session before you choose a project.
- `/project init` tells OpenGauss that the current Lean repository is your working project.
- `/project use <path>` points OpenGauss at an already-initialized project somewhere else on disk.
- `/project create <path> --template-source <template-or-git-url>` creates a new Lean project and registers it.
- `/prove`, `/review`, `/draft`, `/autoprove`, `/formalize`, and `/autoformalize` are the Lean workflow commands you use **after** you have selected a project.

## Morph Path

### First 10 Minutes

1. Open the OpenGauss Morph template.
2. If Morph shows a **Claim**, **Save**, or similar action for the session, use it early.
   The exact button text can change, but temporary sessions are easier to lose than claimed ones.
3. Run `gauss-open-guide` if the browser guide is not already visible.
4. If you want orientation first, type `/chat`, or use `/managed-chat` for the configured managed backend child session.
5. If you want to work on a Lean project, clone or open it and then run `/project init` or `/project use`.

### Making It Persistent

- The safest persistence is still **git**: commit locally and push to a remote.
- If Morph offers **save**, **snapshot**, or **persistent devbox** controls, use them before closing the tab.
- Keep important work in your home directory or in checked-out repositories, not in throwaway temp directories.

### Bringing In An Existing Project

- Best path: push the project to Git and `git clone` it inside Morph.
- Fine for small files: use Morph upload or drag-and-drop if your current view supports it, then move the files into a repository directory.
- For larger projects, an archive plus unpacking in the terminal is usually better than lots of one-off file uploads.

After the project is on the box:

```text
/project init
/prove
```

or

```text
/project use /path/to/project
/review Main.lean
```

## Local Install Path

From a checkout of `math-inc/OpenGauss`:

```bash
./scripts/install.sh
gauss-open-guide
gauss
```

Then:

- use `/chat` if you want a short first-step guide and plain-language chat mode
- use `/managed-chat` if you want the configured managed backend child session first
- use `/project init` if you are already inside a Lean repository
- use `/project create <path> --template-source <template-or-git-url>` if you need a new project

## First Useful Examples

```text
/chat
/chat I have a Lean theorem but I am not sure how to start proving it.
/managed-chat What does `/project init` do?
/prove Show me how to prove that 1 + 1 = 2 in Lean.
/review Main.lean
/draft "State the intermediate value theorem"
```

## What OpenGauss Is Actually Doing

- OpenGauss manages the terminal session and project context for you.
- The Lean proving and formalization workflows come from the staged `lean4-skills` environment.
- You do not need to manually install or wire MCP in order to use the default proving flows.

## If The Interface Feels Like Too Much

Start with this sequence:

1. `/chat`
2. Ask one plain question in English.
3. Let OpenGauss explain the next command.
4. Only after that, run `/project ...`

That path is intentionally slower, but it is the least intimidating way in.
