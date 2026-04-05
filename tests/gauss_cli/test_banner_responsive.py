from types import SimpleNamespace


def test_select_banner_art_uses_full_assets_on_wide_terminals():
    from gauss_cli.banner import GAUSS_AGENT_LOGO, GAUSS_CADUCEUS, _select_banner_art

    layout, logo, hero = _select_banner_art(140, None)

    assert layout == "split"
    assert logo == GAUSS_AGENT_LOGO
    assert hero == GAUSS_CADUCEUS


def test_select_banner_art_prefers_full_skin_assets_on_normal_terminal_widths():
    from gauss_cli.banner import _select_banner_art

    skin = SimpleNamespace(
        banner_logo="full-logo",
        banner_logo_compact="compact-logo",
        banner_hero="full-hero",
        banner_hero_compact="compact-hero",
    )

    layout, logo, hero = _select_banner_art(100, skin)

    assert layout == "stack"
    assert logo == "full-logo"
    assert hero == "full-hero"


def test_select_banner_art_stacks_and_hides_hero_on_narrow_terminals():
    from gauss_cli.banner import _select_banner_art

    skin = SimpleNamespace(
        banner_logo="full-logo",
        banner_logo_compact="compact-logo",
        banner_hero="full-hero",
        banner_hero_compact="compact-hero",
    )

    layout, logo, hero = _select_banner_art(80, skin)

    assert layout == "stack"
    assert logo == "compact-logo"
    assert hero == ""


def test_select_banner_art_keeps_compact_logo_on_small_terminals():
    from gauss_cli.banner import _select_banner_art

    skin = SimpleNamespace(
        banner_logo="full-logo",
        banner_logo_compact="compact-logo",
        banner_hero="full-hero",
        banner_hero_compact="compact-hero",
    )

    layout, logo, hero = _select_banner_art(40, skin)

    assert layout == "stack"
    assert logo == "compact-logo"
    assert hero == ""


def test_select_banner_art_hides_logo_on_very_small_terminals():
    from gauss_cli.banner import _select_banner_art

    skin = SimpleNamespace(
        banner_logo="full-logo",
        banner_logo_compact="compact-logo",
        banner_hero="full-hero",
        banner_hero_compact="compact-hero",
    )

    layout, logo, hero = _select_banner_art(30, skin)

    assert layout == "stack"
    assert logo == ""
    assert hero == ""


def test_select_banner_art_uses_ascii_assets_when_unicode_is_disabled():
    from gauss_cli.banner import (
        GAUSS_AGENT_LOGO_ASCII,
        GAUSS_CADUCEUS_ASCII,
        _select_banner_art,
    )

    layout, logo, hero = _select_banner_art(140, None, unicode_enabled=False)

    assert layout == "split"
    assert logo == GAUSS_AGENT_LOGO_ASCII
    assert hero == GAUSS_CADUCEUS_ASCII


def test_build_welcome_banner_renders_selected_logo(monkeypatch):
    from rich.console import Console

    from gauss_cli.banner import build_welcome_banner

    skin = SimpleNamespace(
        banner_logo="full-logo",
        banner_logo_compact="compact-logo",
        banner_hero="",
        banner_hero_compact="",
    )

    monkeypatch.setattr(
        "gauss_cli.skin_engine.get_active_skin",
        lambda: skin,
    )
    monkeypatch.setattr(
        "gauss_cli.banner.shutil.get_terminal_size",
        lambda *_args, **_kwargs: __import__("os").terminal_size((48, 24)),
    )

    console = Console(record=True, width=48)
    build_welcome_banner(
        console=console,
        model="anthropic/claude-opus-4.1",
        cwd="/root/GaussWorkspace",
        session_id=None,
        context_length=None,
    )

    assert "compact-logo" in console.export_text()


def test_build_welcome_banner_uses_full_logo_once_on_medium_widths(monkeypatch):
    from rich.console import Console

    from gauss_cli.banner import build_welcome_banner

    skin = SimpleNamespace(
        banner_logo="full-logo",
        banner_logo_compact="compact-logo",
        banner_hero="full-hero",
        banner_hero_compact="compact-hero",
    )

    monkeypatch.setattr(
        "gauss_cli.skin_engine.get_active_skin",
        lambda: skin,
    )
    monkeypatch.setattr(
        "gauss_cli.banner.shutil.get_terminal_size",
        lambda *_args, **_kwargs: __import__("os").terminal_size((100, 24)),
    )

    console = Console(record=True, width=100)
    build_welcome_banner(
        console=console,
        model="anthropic/claude-opus-4.1",
        cwd="/root/GaussWorkspace",
        session_id=None,
        context_length=None,
    )

    exported = console.export_text()
    assert exported.count("full-logo") == 1
    assert "full-hero" in exported
    assert "compact-logo" not in exported


def test_build_welcome_banner_hides_tool_inventory(monkeypatch):
    from rich.console import Console

    from gauss_cli.banner import build_welcome_banner

    monkeypatch.setattr(
        "gauss_cli.banner.shutil.get_terminal_size",
        lambda *_args, **_kwargs: __import__("os").terminal_size((120, 24)),
    )

    console = Console(record=True, width=120)
    build_welcome_banner(
        console=console,
        model="anthropic/claude-opus-4.1",
        cwd="/root/GaussWorkspace",
        session_id=None,
        context_length=None,
    )

    exported = console.export_text()
    assert "Available Tools" not in exported


def test_build_welcome_banner_mentions_swarm_in_primary_workflow(monkeypatch):
    from rich.console import Console

    from gauss_cli.banner import build_welcome_banner

    monkeypatch.setattr(
        "gauss_cli.banner.shutil.get_terminal_size",
        lambda *_args, **_kwargs: __import__("os").terminal_size((120, 24)),
    )

    console = Console(record=True, width=120)
    build_welcome_banner(
        console=console,
        model="anthropic/claude-opus-4.1",
        cwd="/root/GaussWorkspace",
        session_id=None,
        context_length=None,
    )

    exported = console.export_text()
    assert "/chat" in exported
    assert "turn on onboarding mode" in exported
    assert "/swarm" in exported
    assert "track, attach, or cancel workflow agents" in exported


def test_build_welcome_banner_falls_back_to_ascii_panel(monkeypatch):
    from rich.console import Console

    from gauss_cli.banner import build_welcome_banner

    monkeypatch.setattr("gauss_cli.banner.supports_unicode", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("gauss_cli.banner.get_update_result", lambda timeout=0.5: 6)
    monkeypatch.setattr(
        "gauss_cli.banner.shutil.get_terminal_size",
        lambda *_args, **_kwargs: __import__("os").terminal_size((120, 24)),
    )

    console = Console(record=True, width=120)
    build_welcome_banner(
        console=console,
        model="anthropic/claude-opus-4.1",
        cwd="/root/GaussWorkspace",
        session_id=None,
        context_length=None,
    )

    exported = console.export_text()
    assert "!" in exported
    assert "|" in exported
    assert "⚠" not in exported
    assert "·" not in exported
    assert "│" not in exported


def test_cprint_strips_ansi_when_terminal_lacks_color(monkeypatch):
    from gauss_cli.banner import cprint

    captured = []
    monkeypatch.setattr("gauss_cli.banner.supports_ansi", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("gauss_cli.banner.supports_unicode", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("gauss_cli.banner._pt_print", lambda value: captured.append(value))

    cprint("\033[1;33m⚠ Managed /prove — needs project first\033[0m")

    assert captured == ["! Managed /prove - needs project first"]
