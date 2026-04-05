"""Tests for shared slash command definitions and autocomplete."""

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from gauss_cli.commands import (
    COMMANDS,
    SlashCommandCompleter,
    rewrite_friendly_entry_command,
    rewrite_friendly_slash_command,
)


# All commands that must be present in the shared COMMANDS dict.
EXPECTED_COMMANDS = {
    "/chat",
    "/managed-chat",
    "/project",
    "/prove",
    "/draft",
    "/review",
    "/checkpoint",
    "/refactor",
    "/golf",
    "/autoprove",
    "/formalize",
    "/autoformalize",
    "/autoformalize-backend",
    "/swarm",
    "/new",
    "/reset",
    "/clear",
    "/history",
    "/save",
    "/retry",
    "/undo",
    "/title",
    "/compress",
    "/rollback",
    "/stop",
    "/config",
    "/model",
    "/provider",
    "/verbose",
    "/reasoning",
    "/help",
    "/usage",
    "/paste",
    "/quit",
}


def _completions(completer: SlashCommandCompleter, text: str):
    return list(
        completer.get_completions(
            Document(text=text),
            CompleteEvent(completion_requested=True),
        )
    )


class TestCommands:
    def test_shared_commands_include_project_and_workflow_entries(self):
        """Gauss ships project management plus managed workflow commands."""
        assert COMMANDS["/paste"] == "Check clipboard for an image and attach it"
        assert COMMANDS["/chat"] == "Show the first-step guide and enable plain-language chat mode"
        assert COMMANDS["/managed-chat"] == "Open the configured managed backend child session and return to Gauss when it exits"
        assert COMMANDS["/project"] == "Create, convert, inspect, or switch the active Gauss project"
        assert COMMANDS["/prove"] == "Spawn a managed backend agent for the guided Lean prove workflow"
        assert COMMANDS["/draft"] == "Spawn a managed backend agent for the Lean draft workflow"
        assert COMMANDS["/review"] == "Spawn a managed backend agent for the read-only Lean review workflow"
        assert COMMANDS["/checkpoint"] == "Spawn a managed backend agent for the Lean checkpoint workflow"
        assert COMMANDS["/refactor"] == "Spawn a managed backend agent for the Lean refactor workflow"
        assert COMMANDS["/golf"] == "Spawn a managed backend agent for the Lean proof golfing workflow"
        assert COMMANDS["/autoprove"] == "Spawn a managed backend agent for the autonomous Lean autoprove workflow"
        assert COMMANDS["/formalize"] == "Spawn a managed backend agent for the interactive Lean formalize workflow"
        assert COMMANDS["/autoformalize"] == "Spawn a managed backend agent for the autonomous Lean autoformalize workflow"
        assert COMMANDS["/autoformalize-backend"] == "Show or change the managed workflow backend"
        assert COMMANDS["/swarm"].startswith("Show workflow agents")

    def test_all_expected_commands_present(self):
        """Regression guard — the default Gauss slash surface stays minimal."""
        assert set(COMMANDS.keys()) == EXPECTED_COMMANDS

    def test_every_command_has_nonempty_description(self):
        for cmd, desc in COMMANDS.items():
            assert isinstance(desc, str) and len(desc) > 0, f"{cmd} has empty description"


class TestSlashCommandCompleter:
    # -- basic prefix completion -----------------------------------------

    def test_builtin_prefix_completion_uses_shared_registry(self):
        completions = _completions(SlashCommandCompleter(), "/re")
        texts = {item.text for item in completions}

        assert "reset" in texts
        assert "retry" in texts
        assert "reasoning" in texts

    def test_builtin_completion_display_meta_shows_description(self):
        completions = _completions(SlashCommandCompleter(), "/help")
        assert len(completions) == 1
        assert completions[0].display_meta_text == "Show this help message"

    # -- exact-match trailing space --------------------------------------

    def test_exact_match_completion_adds_trailing_space(self):
        completions = _completions(SlashCommandCompleter(), "/help")

        assert [item.text for item in completions] == ["help "]

    def test_partial_match_does_not_add_trailing_space(self):
        completions = _completions(SlashCommandCompleter(), "/hel")

        assert [item.text for item in completions] == ["help"]

    # -- non-slash input returns nothing ---------------------------------

    def test_no_completions_for_non_slash_input(self):
        assert _completions(SlashCommandCompleter(), "help") == []

    def test_no_completions_for_empty_input(self):
        assert _completions(SlashCommandCompleter(), "") == []

    def test_friendly_entry_rewriter_handles_exact_and_fuzzy_inputs(self):
        assert rewrite_friendly_entry_command("chat explain /project init") == "/chat explain /project init"
        assert rewrite_friendly_entry_command("start") == "/chat"
        assert rewrite_friendly_entry_command("strat") == "/chat"
        assert rewrite_friendly_entry_command("caht hello") == "/chat hello"
        assert rewrite_friendly_entry_command("get started") == "/chat"

    def test_friendly_slash_rewriter_handles_obvious_typos(self):
        assert rewrite_friendly_slash_command("/strat") == "/chat"
        assert rewrite_friendly_slash_command("/caht hello") == "/chat hello"
        assert rewrite_friendly_slash_command("/start") is None
        assert rewrite_friendly_slash_command("/project use .") is None

    # -- skill commands via provider ------------------------------------

    def test_skill_commands_are_completed_from_provider(self):
        completer = SlashCommandCompleter(
            skill_commands_provider=lambda: {
                "/gif-search": {"description": "Search for GIFs across providers"},
            }
        )

        completions = _completions(completer, "/gif")

        assert len(completions) == 1
        assert completions[0].text == "gif-search"
        assert completions[0].display_text == "/gif-search"
        assert completions[0].display_meta_text == "⚡ Search for GIFs across providers"

    def test_skill_exact_match_adds_trailing_space(self):
        completer = SlashCommandCompleter(
            skill_commands_provider=lambda: {
                "/gif-search": {"description": "Search for GIFs"},
            }
        )

        completions = _completions(completer, "/gif-search")

        assert len(completions) == 1
        assert completions[0].text == "gif-search "

    def test_no_skill_provider_means_no_skill_completions(self):
        """Default (None) provider should not blow up or add completions."""
        completer = SlashCommandCompleter()
        completions = _completions(completer, "/gif")
        # /gif doesn't match any builtin command
        assert completions == []

    def test_skill_provider_exception_is_swallowed(self):
        """A broken provider should not crash autocomplete."""
        completer = SlashCommandCompleter(
            skill_commands_provider=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        # Should return builtin matches only, no crash
        completions = _completions(completer, "/he")
        texts = {item.text for item in completions}
        assert "help" in texts

    def test_skill_description_truncated_at_50_chars(self):
        long_desc = "A" * 80
        completer = SlashCommandCompleter(
            skill_commands_provider=lambda: {
                "/long-skill": {"description": long_desc},
            }
        )
        completions = _completions(completer, "/long")
        assert len(completions) == 1
        meta = completions[0].display_meta_text
        # "⚡ " prefix + 50 chars + "..."
        assert meta == f"⚡ {'A' * 50}..."

    def test_skill_missing_description_uses_fallback(self):
        completer = SlashCommandCompleter(
            skill_commands_provider=lambda: {
                "/no-desc": {},
            }
        )
        completions = _completions(completer, "/no-desc")
        assert len(completions) == 1
        assert "Skill command" in completions[0].display_meta_text
