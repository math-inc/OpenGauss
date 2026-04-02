---
sidebar_position: 2
title: "Slash Commands"
description: "The default slash-command surface shipped by Gauss."
---

# Slash Commands

Gauss intentionally ships a small default surface.

## Workflow

| Command | Description |
|---|---|
| `/start [question]` | Show the first-step guide, enable onboarding chat mode, and optionally send a first question. |
| `/chat [question]` | Open the configured managed backend chat session before selecting a project. |
| `/prove [scope or flags]` | Launch the guided managed Lean prove workflow. |
| `/draft [topic or flags]` | Launch the managed Lean draft workflow for declaration skeletons. |
| `/review [scope or flags]` | Launch the read-only managed Lean review workflow. |
| `/checkpoint [scope or flags]` | Launch the managed Lean checkpoint workflow. |
| `/refactor [scope or flags]` | Launch the managed Lean refactor workflow. |
| `/golf [scope or flags]` | Launch the managed Lean proof golfing workflow. |
| `/autoprove [scope or flags]` | Launch the autonomous managed Lean prove workflow. |
| `/formalize [topic or flags]` | Launch the interactive managed Lean formalize workflow. |
| `/autoformalize [topic or flags]` | Launch the autonomous managed Lean autoformalize workflow. |
| `/autoformalize-backend [backend]` | Show or change the managed backend used by the next workflow run. |

## Session

| Command | Description |
|---|---|
| `/new` | Start a new session |
| `/reset` | Alias for `/new` |
| `/clear` | Clear the screen and start a new session |
| `/history` | Show conversation history |
| `/save` | Save the current conversation |
| `/retry` | Retry the last message |
| `/undo` | Remove the last exchange |
| `/title` | Set a title for the current session |
| `/compress` | Compress conversation context |
| `/rollback` | List or restore filesystem checkpoints |
| `/stop` | Stop active background work |

## Configuration

| Command | Description |
|---|---|
| `/config` | Show current configuration |
| `/model` | Show or change the current model |
| `/provider` | Show available providers and the active one |
| `/verbose` | Cycle tool progress display |
| `/reasoning` | Manage reasoning effort and display |

## Info

| Command | Description |
|---|---|
| `/help` | Show help |
| `/usage` | Show token usage |
| `/paste` | Attach an image from the clipboard |

## Exit

| Command | Description |
|---|---|
| `/quit` | Exit the CLI |

## Compatibility Notes

- `/handoff` is no longer a public generic workflow. In compatibility mode it forwards to `/autoformalize`.
- Gauss only lifts a curated subset of the bundled Lean skill surface into public slash commands.
- Inside the interactive CLI, common missing-slash forms such as `prove ...`, `review ...`, and `auto-proof ...` are rewritten to the matching managed workflow command.
- `/reload-mcp` is not part of the default Gauss surface. Managed Lean workflows use their own isolated Lean MCP config.
