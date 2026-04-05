"""Welcome banner, workflow summary, and update check for the CLI.

Pure display functions with no Gauss CLI state dependency.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

from rich import box as rich_box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from prompt_toolkit import print_formatted_text as _pt_print
from prompt_toolkit.formatted_text import ANSI as _PT_ANSI
from gauss_cli.colors import render_terminal_text, supports_ansi, supports_unicode

logger = logging.getLogger(__name__)


# =========================================================================
# ANSI building blocks for conversation display
# =========================================================================

_GOLD = "\033[1;33m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RST = "\033[0m"


def cprint(text: str):
    """Print ANSI-colored text through prompt_toolkit's renderer."""
    ansi_enabled = supports_ansi()
    rendered = render_terminal_text(
        text,
        allow_ansi=ansi_enabled,
        allow_unicode=supports_unicode(),
    )
    if ansi_enabled:
        _pt_print(_PT_ANSI(rendered))
    else:
        _pt_print(rendered)


# =========================================================================
# Skin-aware color helpers
# =========================================================================

def _skin_color(key: str, fallback: str) -> str:
    """Get a color from the active skin, or return fallback."""
    try:
        from gauss_cli.skin_engine import get_active_skin
        return get_active_skin().get_color(key, fallback)
    except Exception:
        return fallback


def _skin_branding(key: str, fallback: str) -> str:
    """Get a branding string from the active skin, or return fallback."""
    try:
        from gauss_cli.skin_engine import get_active_skin
        return get_active_skin().get_branding(key, fallback)
    except Exception:
        return fallback


# =========================================================================
# ASCII Art & Branding
# =========================================================================

from gauss_cli import __version__ as VERSION, __release_date__ as RELEASE_DATE

GAUSS_AGENT_LOGO = """[bold #FFD700] ██████╗  █████╗ ██╗   ██╗███████╗███████╗[/]
[bold #FFD700]██╔════╝ ██╔══██╗██║   ██║██╔════╝██╔════╝[/]
[#FFBF00]██║  ███╗███████║██║   ██║███████╗███████╗[/]
[#FFBF00]██║   ██║██╔══██║██║   ██║╚════██║╚════██║[/]
[#CD7F32]╚██████╔╝██║  ██║╚██████╔╝███████║███████║[/]
[#CD7F32] ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚══════╝[/]"""

GAUSS_AGENT_LOGO_ASCII = """[bold #FFD700]  ____    _    _   _ ____ ____[/]
[bold #FFD700] / ___|  / \\  | | | / ___/ ___|[/]
[#FFBF00]| |  _  / _ \\ | | | \\___ \\___ \\[/]
[#FFBF00]| |_| |/ ___ \\| |_| |___) |__) |[/]
[#CD7F32] \\____/_/   \\_\\\\___/|____/____/[/]"""

GAUSS_CADUCEUS = """[#CD7F32]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡀⠀⣀⣀⠀⢀⣀⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#CD7F32]⠀⠀⠀⠀⠀⠀⢀⣠⣴⣾⣿⣿⣇⠸⣿⣿⠇⣸⣿⣿⣷⣦⣄⡀⠀⠀⠀⠀⠀⠀[/]
[#FFBF00]⠀⢀⣠⣴⣶⠿⠋⣩⡿⣿⡿⠻⣿⡇⢠⡄⢸⣿⠟⢿⣿⢿⣍⠙⠿⣶⣦⣄⡀⠀[/]
[#FFBF00]⠀⠀⠉⠉⠁⠶⠟⠋⠀⠉⠀⢀⣈⣁⡈⢁⣈⣁⡀⠀⠉⠀⠙⠻⠶⠈⠉⠉⠀⠀[/]
[#FFD700]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣴⣿⡿⠛⢁⡈⠛⢿⣿⣦⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#FFD700]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠿⣿⣦⣤⣈⠁⢠⣴⣿⠿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#FFBF00]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠉⠻⢿⣿⣦⡉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#FFBF00]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⢷⣦⣈⠛⠃⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#CD7F32]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⣴⠦⠈⠙⠿⣦⡄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#CD7F32]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠸⣿⣤⡈⠁⢤⣿⠇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#B8860B]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠛⠷⠄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#B8860B]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⠑⢶⣄⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#B8860B]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣿⠁⢰⡆⠈⡿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#B8860B]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠳⠈⣡⠞⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#B8860B]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]"""

GAUSS_CADUCEUS_ASCII = """[dim #B8860B]Lean workspace and managed proof workflows[/]"""

COMPACT_BANNER = """[bold #FFD700]GAUSS[/] [dim #B8860B]· Lean Autoformalization[/]"""
COMPACT_BANNER_ASCII = """[bold #FFD700]GAUSS[/] [dim #B8860B]- Lean Autoformalization[/]"""

FULL_BANNER_MIN_WIDTH = 124
FULL_STACK_BANNER_MIN_WIDTH = 88
MINI_BANNER_MIN_WIDTH = 34
SIMPLIFIED_BANNER_WIDTH = 72


_MARKUP_TAG_RE = re.compile(r"\[[^\]]+\]")


def _markup_is_ascii(markup: str) -> bool:
    plain = _MARKUP_TAG_RE.sub("", markup or "")
    try:
        plain.encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def _select_banner_art(
    term_width: int,
    skin: Optional[Any],
    unicode_enabled: Optional[bool] = None,
) -> Tuple[str, str, str]:
    """Return ``(layout_mode, logo_markup, hero_markup)`` for the current width."""
    unicode_enabled = supports_unicode() if unicode_enabled is None else unicode_enabled
    if unicode_enabled:
        full_logo = skin.banner_logo if skin and getattr(skin, "banner_logo", "") else GAUSS_AGENT_LOGO
        compact_logo = (
            skin.banner_logo_compact
            if skin and getattr(skin, "banner_logo_compact", "")
            else COMPACT_BANNER
        )
        full_hero = skin.banner_hero if skin and getattr(skin, "banner_hero", "") else GAUSS_CADUCEUS
        compact_hero = (
            skin.banner_hero_compact
            if skin and getattr(skin, "banner_hero_compact", "")
            else ""
        )
    else:
        full_logo = (
            skin.banner_logo
            if skin and getattr(skin, "banner_logo", "") and _markup_is_ascii(skin.banner_logo)
            else GAUSS_AGENT_LOGO_ASCII
        )
        compact_logo = (
            skin.banner_logo_compact
            if skin
            and getattr(skin, "banner_logo_compact", "")
            and _markup_is_ascii(skin.banner_logo_compact)
            else COMPACT_BANNER_ASCII
        )
        full_hero = (
            skin.banner_hero
            if skin and getattr(skin, "banner_hero", "") and _markup_is_ascii(skin.banner_hero)
            else GAUSS_CADUCEUS_ASCII
        )
        compact_hero = (
            skin.banner_hero_compact
            if skin
            and getattr(skin, "banner_hero_compact", "")
            and _markup_is_ascii(skin.banner_hero_compact)
            else ""
        )

    if term_width >= FULL_BANNER_MIN_WIDTH:
        return "split", full_logo, full_hero
    if term_width >= FULL_STACK_BANNER_MIN_WIDTH:
        return "stack", full_logo, full_hero or compact_hero
    if term_width >= MINI_BANNER_MIN_WIDTH:
        return "stack", compact_logo, ""
    return "stack", "", ""


def _shorten_middle(text: str, max_len: int) -> str:
    """Keep both ends of a long string visible within a narrow banner."""
    if max_len <= 0 or len(text) <= max_len:
        return text
    if max_len <= 7:
        return text[:max_len]
    head = max(2, (max_len - 3) // 2)
    tail = max(2, max_len - head - 3)
    return f"{text[:head]}...{text[-tail:]}"


# =========================================================================
# Skills scanning
# =========================================================================

def get_available_skills() -> Dict[str, List[str]]:
    """Scan the active Gauss home `skills/` directory and group skills by category."""
    import os

    gauss_home = Path(
        os.getenv("GAUSS_HOME")
        or os.getenv("GAUSS_HOME")
        or (Path.home() / ".gauss")
    )
    skills_dir = gauss_home / "skills"
    skills_by_category = {}

    if not skills_dir.exists():
        return skills_by_category

    for skill_file in skills_dir.rglob("SKILL.md"):
        rel_path = skill_file.relative_to(skills_dir)
        parts = rel_path.parts
        if len(parts) >= 2:
            category = parts[0]
            skill_name = parts[-2]
        else:
            category = "general"
            skill_name = skill_file.parent.name
        skills_by_category.setdefault(category, []).append(skill_name)

    return skills_by_category


# =========================================================================
# Update check
# =========================================================================

# Cache update check results for 6 hours to avoid repeated git fetches
_UPDATE_CHECK_CACHE_SECONDS = 6 * 3600


def check_for_updates() -> Optional[int]:
    """Check how many commits behind origin/main the local repo is.

    Does a ``git fetch`` at most once every 6 hours (cached to
    ``~/.gauss/.update_check``).  Returns the number of commits behind,
    or ``None`` if the check fails or isn't applicable.
    """
    if os.getenv("GAUSS_SKIP_UPDATE_CHECK", "").strip().lower() in {"1", "true", "yes", "on"}:
        return None

    from gauss_cli.config import get_gauss_home, get_installed_repo_root

    gauss_home = get_gauss_home()
    cache_file = gauss_home / ".update_check"

    candidate_repo_dirs: list[Path] = []
    installed_repo_root = get_installed_repo_root()
    if installed_repo_root is not None:
        candidate_repo_dirs.append(installed_repo_root.expanduser())
    candidate_repo_dirs.append(gauss_home / "opengauss")
    candidate_repo_dirs.append(gauss_home / "opengauss-dev")
    candidate_repo_dirs.append(Path(__file__).parent.parent.resolve())

    repo_dir = None
    seen: set[Path] = set()
    for candidate in candidate_repo_dirs:
        resolved = candidate.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / ".git").exists():
            repo_dir = resolved
            break

    if repo_dir is None:
        return None

    # Read cache
    now = time.time()
    try:
        if cache_file.exists():
            cached = json.loads(cache_file.read_text())
            if now - cached.get("ts", 0) < _UPDATE_CHECK_CACHE_SECONDS:
                return cached.get("behind")
    except Exception:
        pass

    # Fetch latest refs (fast — only downloads ref metadata, no files)
    try:
        subprocess.run(
            ["git", "fetch", "origin", "--quiet"],
            capture_output=True, timeout=10,
            cwd=str(repo_dir),
        )
    except Exception:
        pass  # Offline or timeout — use stale refs, that's fine

    # Determine the upstream ref to compare against
    upstream_ref = "origin/main"
    try:
        tracking = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            capture_output=True, text=True, timeout=5,
            cwd=str(repo_dir),
        )
        if tracking.returncode == 0 and tracking.stdout.strip():
            upstream_ref = tracking.stdout.strip()
    except Exception:
        pass

    # Count commits behind
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", f"HEAD..{upstream_ref}"],
            capture_output=True, text=True, timeout=5,
            cwd=str(repo_dir),
        )
        if result.returncode == 0:
            behind = int(result.stdout.strip())
        else:
            behind = None
    except Exception:
        behind = None

    # Write cache
    try:
        cache_file.write_text(json.dumps({"ts": now, "behind": behind}))
    except Exception:
        pass

    return behind


# =========================================================================
# Non-blocking update check
# =========================================================================

_update_result: Optional[int] = None
_update_check_done = threading.Event()


def prefetch_update_check():
    """Kick off update check in a background daemon thread."""
    def _run():
        global _update_result
        _update_result = check_for_updates()
        _update_check_done.set()
    t = threading.Thread(target=_run, daemon=True)
    t.start()


def get_update_result(timeout: float = 0.5) -> Optional[int]:
    """Get result of prefetched check. Returns None if not ready."""
    _update_check_done.wait(timeout=timeout)
    return _update_result


# =========================================================================
# Welcome banner
# =========================================================================

def _format_context_length(tokens: int) -> str:
    """Format a token count for display (e.g. 128000 → '128K', 1048576 → '1M')."""
    if tokens >= 1_000_000:
        val = tokens / 1_000_000
        return f"{val:g}M"
    elif tokens >= 1_000:
        val = tokens / 1_000
        return f"{val:g}K"
    return str(tokens)


def build_welcome_banner(console: Console, model: str, cwd: str,
                         session_id: str = None,
                         context_length: int = None,
                         project_label: str | None = None):
    """Build and print a welcome banner with caduceus on left and info on right.

    Args:
        console: Rich Console instance.
        model: Current model name.
        cwd: Current working directory.
        session_id: Session identifier.
        context_length: Model's context window size in tokens.
        project_label: Active project summary, when available.
    """
    term_width = shutil.get_terminal_size().columns
    simplified = term_width < SIMPLIFIED_BANNER_WIDTH
    unicode_enabled = supports_unicode()
    separator = "·" if unicode_enabled else "|"
    long_dash = "—" if unicode_enabled else "-"
    warn_mark = "⚠" if unicode_enabled else "!"

    # Resolve skin colors once for the entire banner
    accent = _skin_color("banner_accent", "#FFBF00")
    dim = _skin_color("banner_dim", "#B8860B")
    text = _skin_color("banner_text", "#FFF8DC")
    session_color = _skin_color("session_border", "#8B8682")

    # Use skin's custom caduceus art if provided
    try:
        from gauss_cli.skin_engine import get_active_skin
        _bskin = get_active_skin()
    except Exception:
        _bskin = None

    layout_mode, _logo, _hero = _select_banner_art(term_width, _bskin, unicode_enabled=unicode_enabled)

    layout_table = Table.grid(padding=(0, 1 if layout_mode == "stack" else 2))
    if layout_mode == "split":
        layout_table.add_column("left", justify="left")
        layout_table.add_column("right", justify="left")
    else:
        layout_table.add_column("content", justify="left")

    left_lines = []
    if _hero:
        left_lines.extend([_hero, ""])
    model_short = model.split("/")[-1] if "/" in model else model
    model_max = 18 if simplified else 28
    if len(model_short) > model_max:
        model_short = model_short[: model_max - 3] + "..."
    ctx_str = ""
    if context_length and not simplified:
        ctx_str = f" [dim {dim}]{separator}[/] [dim {dim}]{_format_context_length(context_length)} context[/]"
    if simplified:
        left_lines.append(f"[{accent}]{model_short}[/] [dim {dim}]{separator}[/] [dim {dim}]Math Inc.[/]")
        left_lines.append(f"[dim {dim}]{_shorten_middle(cwd, max(18, term_width - 10))}[/]")
    else:
        left_lines.append(f"[{accent}]{model_short}[/]{ctx_str} [dim {dim}]{separator}[/] [dim {dim}]Math Inc.[/]")
        left_lines.append(f"[dim {dim}]{cwd}[/]")
    if project_label:
        left_lines.append(f"[dim {dim}]Project: {project_label}[/]")
    if session_id and not simplified:
        left_lines.append(f"[dim {session_color}]Session: {session_id}[/]")
    left_content = "\n".join(left_lines)

    right_lines = []

    if simplified:
        right_lines.append(f"[bold {accent}]Start Here[/]")
        right_lines.append(f"[{text}]`/chat`[/] [dim {dim}]turn on onboarding mode and see the first steps[/]")
        right_lines.append(f"[{text}]`/project`[/] [dim {dim}]select or create a Gauss project[/]")
        right_lines.append(f"[{text}]`/prove`[/] [dim {dim}]guided Lean workflow[/]")
        right_lines.append(f"[{text}]`/review`[/] [dim {dim}]review, checkpoint, refactor, golf[/]")
        right_lines.append(f"[{text}]`/draft`[/] [dim {dim}]draft Lean declaration skeletons[/]")
        right_lines.append(f"[{text}]`/autoprove`[/] [dim {dim}]autonomous Lean workflow[/]")
        right_lines.append(f"[{text}]`/formalize`[/] [dim {dim}]interactive draft plus prove[/]")
        right_lines.append(f"[{text}]`/autoformalize`[/] [dim {dim}]autonomous draft plus autoprove[/]")
        right_lines.append(f"[{text}]`/swarm`[/] [dim {dim}]track, attach, or cancel workflow agents[/]")
        right_lines.append(f"[{text}]`/help`[/] [dim {dim}]commands and diagnostics[/]")
    else:
        right_lines.append(f"[bold {accent}]Start Here[/]")
        right_lines.append(f"[{text}]`/chat`[/] [dim {dim}]{long_dash} turn on onboarding mode and show the first steps[/]")
        right_lines.append(f"[{text}]`/project`[/] [dim {dim}]{long_dash} create, convert, inspect, or switch the active project[/]")
        right_lines.append(f"[{text}]`/prove`[/] [dim {dim}]{long_dash} spawn a guided managed proving agent[/]")
        right_lines.append(f"[{text}]`/review`[/] [dim {dim}]{long_dash} review, checkpoint, refactor, or golf Lean proofs[/]")
        right_lines.append(f"[{text}]`/draft`[/] [dim {dim}]{long_dash} draft Lean declaration skeletons[/]")
        right_lines.append(f"[{text}]`/autoprove`[/] [dim {dim}]{long_dash} spawn an autonomous managed proving agent[/]")
        right_lines.append(f"[{text}]`/formalize`[/] [dim {dim}]{long_dash} spawn an interactive managed formalization agent[/]")
        right_lines.append(f"[{text}]`/autoformalize`[/] [dim {dim}]{long_dash} spawn an autonomous managed formalization agent[/]")
        right_lines.append(f"[{text}]`/swarm`[/] [dim {dim}]{long_dash} track, attach, or cancel workflow agents[/]")
        right_lines.append(f"[{text}]`/help`[/] [dim {dim}]{long_dash} session and diagnostics commands[/]")
        right_lines.append(f"[dim {dim}]You can ignore MCP and plugin internals at first; Open Gauss stages the default Lean workflow setup for you.[/]")

    try:
        from swarm_manager import SwarmManager
        swarm_line = SwarmManager().summary_line()
        if swarm_line:
            right_lines.append(swarm_line)
    except Exception:
        pass

    right_lines.append("")
    summary_parts = [
        "/chat",
        "/project",
        "/prove",
        "/review",
        "/draft",
        "/autoprove",
        "/formalize",
        "/autoformalize",
        "/swarm",
        "/help",
    ]
    summary_parts.append("/help for commands")
    right_lines.append(f"[dim {dim}]{f' {separator} '.join(summary_parts)}[/]")

    # Update check — use prefetched result if available
    try:
        behind = get_update_result(timeout=0.5)
        if behind and behind > 0:
            commits_word = "commit" if behind == 1 else "commits"
            right_lines.append(
                f"[bold yellow]{warn_mark} {behind} {commits_word} behind[/]"
                f"[dim yellow] {long_dash} run [bold]gauss update[/bold] to update[/]"
            )
    except Exception:
        pass  # Never break the banner over an update check

    right_content = "\n".join(right_lines)
    if layout_mode == "split":
        layout_table.add_row(left_content, right_content)
    else:
        layout_table.add_row(left_content)
        layout_table.add_row("")
        layout_table.add_row(right_content)

    agent_name = _skin_branding("agent_name", "Gauss")
    title_color = _skin_color("banner_title", "#FFD700")
    border_color = _skin_color("banner_border", "#CD7F32")
    outer_panel = Panel(
        layout_table,
        title=f"[bold {title_color}]{agent_name} v{VERSION} ({RELEASE_DATE})[/]",
        border_style=border_color,
        padding=(0, 2),
        box=rich_box.ROUNDED if unicode_enabled else rich_box.ASCII,
    )

    console.print()
    if _logo:
        console.print(_logo, justify="center")
        console.print()
    console.print(outer_panel)
