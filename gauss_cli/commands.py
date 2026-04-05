"""Slash command definitions and autocomplete for the Gauss CLI.

Contains the shared built-in ``COMMANDS`` dict and ``SlashCommandCompleter``.
The completer can optionally include dynamic skill slash commands supplied by the
interactive CLI.
"""

from __future__ import annotations

import difflib
import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from prompt_toolkit.completion import Completer, Completion


# Commands organized by category for better help display
COMMANDS_BY_CATEGORY = {
    "Start Here": {
        "/chat": "Show the first-step guide and enable plain-language chat mode",
        "/project": "Create, convert, inspect, or switch the active Gauss project",
    },
    "Workflow": {
        "/prove": "Spawn a managed backend agent for the guided Lean prove workflow",
        "/draft": "Spawn a managed backend agent for the Lean draft workflow",
        "/review": "Spawn a managed backend agent for the read-only Lean review workflow",
        "/checkpoint": "Spawn a managed backend agent for the Lean checkpoint workflow",
        "/refactor": "Spawn a managed backend agent for the Lean refactor workflow",
        "/golf": "Spawn a managed backend agent for the Lean proof golfing workflow",
        "/autoprove": "Spawn a managed backend agent for the autonomous Lean autoprove workflow",
        "/formalize": "Spawn a managed backend agent for the interactive Lean formalize workflow",
        "/autoformalize": "Spawn a managed backend agent for the autonomous Lean autoformalize workflow",
        "/autoformalize-backend": "Show or change the managed workflow backend",
        "/swarm": "Show workflow agents · /swarm attach <id> · /swarm cancel <id>",
    },
    "Session": {
        "/managed-chat": "Open the configured managed backend child session and return to Gauss when it exits",
        "/new": "Start a new session (fresh session ID + history)",
        "/reset": "Start a new session (alias for /new)",
        "/clear": "Clear screen and start a new session",
        "/history": "Show conversation history",
        "/save": "Save the current conversation",
        "/retry": "Retry the last message (resend to agent)",
        "/undo": "Remove the last user/assistant exchange",
        "/title": "Set a title for the current session (usage: /title My Session Name)",
        "/compress": "Manually compress conversation context (flush memories + summarize)",
        "/rollback": "List or restore filesystem checkpoints (usage: /rollback [number])",
        "/stop": "Kill all running background processes",
    },
    "Configuration": {
        "/config": "Show current configuration",
        "/model": "Show or change the current model",
        "/provider": "Show available providers and current provider",
        "/verbose": "Cycle tool progress display: off → new → all → verbose",
        "/reasoning": "Manage reasoning effort and display (usage: /reasoning [level|show|hide])",
    },
    "Info": {
        "/help": "Show this help message",
        "/usage": "Show token usage for the current session",
        "/paste": "Check clipboard for an image and attach it",
    },
    "Exit": {
        "/quit": "Exit the CLI (also: /exit, /q)",
    },
}

# Flat dict for backwards compatibility and autocomplete
COMMANDS = {}
for category_commands in COMMANDS_BY_CATEGORY.values():
    COMMANDS.update(category_commands)


_FRIENDLY_ENTRY_ALIASES = {
    "chat": "/chat",
    "start": "/chat",
    "begin": "/chat",
    "project": "/project",
    "help": "/help",
}

_FRIENDLY_ENTRY_PHRASES = (
    "get started",
    "getting started",
    "how do i start",
    "how do i get started",
    "what do i do first",
    "what should i do first",
    "where do i start",
)

_FRIENDLY_FUZZY_TARGETS = {
    "chat": "/chat",
    "start": "/chat",
    "project": "/project",
    "help": "/help",
}


def _normalize_entry_token(token: str) -> str:
    return re.sub(r"[^a-z]", "", token.lower())


def rewrite_friendly_entry_command(command: str) -> str | None:
    """Rewrite low-friction onboarding inputs into slash commands."""
    if not isinstance(command, str):
        return None
    text = command.strip()
    if not text or text.startswith("/"):
        return None

    lowered = " ".join(text.lower().split())
    if lowered in _FRIENDLY_ENTRY_PHRASES:
        return "/chat"

    parts = text.split(maxsplit=1)
    token = _normalize_entry_token(parts[0])
    remainder = parts[1].strip() if len(parts) > 1 else ""

    direct = _FRIENDLY_ENTRY_ALIASES.get(token)
    if direct:
        return direct if not remainder else f"{direct} {remainder}"

    matches = difflib.get_close_matches(token, _FRIENDLY_FUZZY_TARGETS.keys(), n=1, cutoff=0.75)
    if not matches:
        return None
    corrected = _FRIENDLY_FUZZY_TARGETS[matches[0]]
    return corrected if not remainder else f"{corrected} {remainder}"


def rewrite_friendly_slash_command(command: str) -> str | None:
    """Rewrite obvious misspelled onboarding slash commands."""
    if not isinstance(command, str):
        return None
    text = command.strip()
    if not text.startswith("/"):
        return None

    parts = text.split(maxsplit=1)
    token = parts[0].strip().lower()
    remainder = parts[1].strip() if len(parts) > 1 else ""
    if token == "/start":
        return None
    if token in COMMANDS:
        return None

    normalized = _normalize_entry_token(token.lstrip("/"))
    matches = difflib.get_close_matches(normalized, _FRIENDLY_FUZZY_TARGETS.keys(), n=1, cutoff=0.75)
    if not matches:
        return None
    corrected = _FRIENDLY_FUZZY_TARGETS[matches[0]]
    return corrected if not remainder else f"{corrected} {remainder}"


class SlashCommandCompleter(Completer):
    """Autocomplete for built-in slash commands and optional skill commands."""

    def __init__(
        self,
        skill_commands_provider: Callable[[], Mapping[str, dict[str, Any]]] | None = None,
    ) -> None:
        self._skill_commands_provider = skill_commands_provider

    def _iter_skill_commands(self) -> Mapping[str, dict[str, Any]]:
        if self._skill_commands_provider is None:
            return {}
        try:
            return self._skill_commands_provider() or {}
        except Exception:
            return {}

    @staticmethod
    def _completion_text(cmd_name: str, word: str) -> str:
        """Return replacement text for a completion.

        When the user has already typed the full command exactly (``/help``),
        returning ``help`` would be a no-op and prompt_toolkit suppresses the
        menu. Appending a trailing space keeps the dropdown visible and makes
        backspacing retrigger it naturally.
        """
        return f"{cmd_name} " if cmd_name == word else cmd_name

    @staticmethod
    def _extract_path_word(text: str) -> str | None:
        """Extract the current word if it looks like a file path.

        Returns the path-like token under the cursor, or None if the
        current word doesn't look like a path.  A word is path-like when
        it starts with ``./``, ``../``, ``~/``, ``/``, or contains a
        ``/`` separator (e.g. ``src/main.py``).
        """
        if not text:
            return None
        # Walk backwards to find the start of the current "word".
        # Words are delimited by spaces, but paths can contain almost anything.
        i = len(text) - 1
        while i >= 0 and text[i] != " ":
            i -= 1
        word = text[i + 1:]
        if not word:
            return None
        # Only trigger path completion for path-like tokens
        if word.startswith(("./", "../", "~/", "/")) or "/" in word:
            return word
        return None

    @staticmethod
    def _path_completions(word: str, limit: int = 30):
        """Yield Completion objects for file paths matching *word*."""
        expanded = os.path.expanduser(word)
        # Split into directory part and prefix to match inside it
        if expanded.endswith("/"):
            search_dir = expanded
            prefix = ""
        else:
            search_dir = os.path.dirname(expanded) or "."
            prefix = os.path.basename(expanded)

        try:
            entries = os.listdir(search_dir)
        except OSError:
            return

        count = 0
        prefix_lower = prefix.lower()
        for entry in sorted(entries):
            if prefix and not entry.lower().startswith(prefix_lower):
                continue
            if count >= limit:
                break

            full_path = os.path.join(search_dir, entry)
            is_dir = os.path.isdir(full_path)

            # Build the completion text (what replaces the typed word)
            if word.startswith("~"):
                display_path = "~/" + os.path.relpath(full_path, os.path.expanduser("~"))
            elif os.path.isabs(word):
                display_path = full_path
            else:
                # Keep relative
                display_path = os.path.relpath(full_path)

            if is_dir:
                display_path += "/"

            suffix = "/" if is_dir else ""
            meta = "dir" if is_dir else _file_size_label(full_path)

            yield Completion(
                display_path,
                start_position=-len(word),
                display=entry + suffix,
                display_meta=meta,
            )
            count += 1

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            # Try file path completion for non-slash input
            path_word = self._extract_path_word(text)
            if path_word is not None:
                yield from self._path_completions(path_word)
            return

        word = text[1:]

        for cmd, desc in COMMANDS.items():
            cmd_name = cmd[1:]
            if cmd_name.startswith(word):
                yield Completion(
                    self._completion_text(cmd_name, word),
                    start_position=-len(word),
                    display=cmd,
                    display_meta=desc,
                )

        for cmd, info in self._iter_skill_commands().items():
            cmd_name = cmd[1:]
            if cmd_name.startswith(word):
                description = str(info.get("description", "Skill command"))
                short_desc = description[:50] + ("..." if len(description) > 50 else "")
                yield Completion(
                    self._completion_text(cmd_name, word),
                    start_position=-len(word),
                    display=cmd,
                    display_meta=f"⚡ {short_desc}",
                )


def _file_size_label(path: str) -> str:
    """Return a compact human-readable file size, or '' on error."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f}K"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f}M"
    return f"{size / (1024 * 1024 * 1024):.1f}G"
