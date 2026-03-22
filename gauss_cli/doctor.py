"""
Doctor command for gauss CLI.

Diagnoses issues with Gauss Agent setup.
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path
from typing import Any, Mapping

from gauss_cli.branding import get_cli_command_name, get_product_name, rewrite_cli_references
from gauss_cli.config import get_project_root, get_gauss_home, get_env_path

PROJECT_ROOT = get_project_root()
GAUSS_HOME = get_gauss_home()

# Load environment variables from ~/.gauss/.env so API key checks work
from dotenv import load_dotenv
_env_path = get_env_path()
if _env_path.exists():
    try:
        load_dotenv(_env_path, encoding="utf-8")
    except UnicodeDecodeError:
        load_dotenv(_env_path, encoding="latin-1")
# Also try project .env as dev fallback
load_dotenv(PROJECT_ROOT / ".env", override=False, encoding="utf-8")

# Point mini-swe-agent at ~/.gauss/ so it shares our config
os.environ.setdefault("MSWEA_GLOBAL_CONFIG_DIR", str(GAUSS_HOME))
os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")

from gauss_cli.colors import Colors, color
from gauss_constants import OPENROUTER_MODELS_URL


_PROVIDER_ENV_HINTS = (
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_TOKEN",
    "OPENAI_BASE_URL",
    "GLM_API_KEY",
    "ZAI_API_KEY",
    "Z_AI_API_KEY",
    "KIMI_API_KEY",
    "MINIMAX_API_KEY",
    "MINIMAX_CN_API_KEY",
)


def _has_provider_env_config(content: str) -> bool:
    """Return True when ~/.gauss/.env contains provider auth/base URL settings."""
    return any(key in content for key in _PROVIDER_ENV_HINTS)


def _apply_doctor_tool_availability_overrides(available: list[str], unavailable: list[dict]) -> tuple[list[str], list[dict]]:
    """Adjust runtime-gated tool availability for doctor diagnostics."""
    return available, unavailable


def check_ok(text: str, detail: str = ""):
    print(f"  {color('✓', Colors.GREEN)} {text}" + (f" {color(detail, Colors.DIM)}" if detail else ""))

def check_warn(text: str, detail: str = ""):
    print(f"  {color('⚠', Colors.YELLOW)} {text}" + (f" {color(detail, Colors.DIM)}" if detail else ""))

def check_fail(text: str, detail: str = ""):
    print(f"  {color('✗', Colors.RED)} {text}" + (f" {color(detail, Colors.DIM)}" if detail else ""))

def check_info(text: str):
    print(f"    {color('→', Colors.CYAN)} {text}")


def _check_gateway_service_linger(issues: list[str]) -> None:
    """Warn when a systemd user gateway service will stop after logout."""
    try:
        from gauss_cli.gateway import (
            get_systemd_linger_status,
            get_systemd_unit_path,
            is_linux,
        )
    except Exception as e:
        check_warn("Gateway service linger", f"(could not import gateway helpers: {e})")
        return

    if not is_linux():
        return

    unit_path = get_systemd_unit_path()
    if not unit_path.exists():
        return

    print()
    print(color("◆ Gateway Service", Colors.CYAN, Colors.BOLD))

    linger_enabled, linger_detail = get_systemd_linger_status()
    if linger_enabled is True:
        check_ok("Systemd linger enabled", "(gateway service survives logout)")
    elif linger_enabled is False:
        check_warn("Systemd linger disabled", "(gateway may stop after logout)")
        check_info("Run: sudo loginctl enable-linger $USER")
        issues.append("Enable linger for the gateway user service: sudo loginctl enable-linger $USER")
    else:
        check_warn("Could not verify systemd linger", f"({linger_detail})")


def _check_managed_workflow_requirements(
    issues: list[str],
    *,
    config: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    active_cwd: str | Path | None = None,
    cli_name: str | None = None,
) -> None:
    """Report Lean workflow prerequisites that are specific to Open Gauss."""
    try:
        from gauss_cli.autoformalize import (
            CODEX_AUTOFORMALIZE_BACKEND,
            DEFAULT_AUTOFORMALIZE_BACKEND,
            _codex_auth_payload_has_api_key,
            _codex_auth_payload_is_valid,
            _has_local_claude_api_key,
            _has_local_claude_login,
            _load_local_codex_auth_payload,
            _resolve_auth_mode,
            _resolve_backend_name,
            _resolve_claude_auth_env,
            _resolve_codex_api_key,
            _resolve_uv_runner,
        )
        from gauss_cli.project import (
            ProjectManifestError,
            ProjectNotFoundError,
            discover_gauss_project,
            resolve_template_source,
        )
    except Exception as exc:
        print()
        print(color("◆ Managed Lean Workflows", Colors.CYAN, Colors.BOLD))
        check_warn("Managed workflow diagnostics unavailable", f"({exc})")
        return

    from gauss_cli.config import load_config

    cli_name = cli_name or get_cli_command_name()
    environment = dict(env or os.environ)
    resolved_config = config if isinstance(config, Mapping) else load_config()

    print()
    print(color("◆ Managed Lean Workflows", Colors.CYAN, Colors.BOLD))

    try:
        backend_name = _resolve_backend_name(resolved_config, environment)
        auth_mode = _resolve_auth_mode(resolved_config, environment)
    except Exception as exc:
        check_fail("Managed workflow config", f"({exc})")
        issues.append(f"Fix gauss.autoformalize config: {exc}")
        return

    check_ok("Managed backend", f"({backend_name})")
    check_ok("Managed auth mode", f"({auth_mode})")

    template_source = resolve_template_source(resolved_config, environment)
    if template_source:
        check_ok("Project template source", f"({template_source})")
    else:
        check_warn(
            "Project template source",
            "(`/project create` needs `--template-source` or `gauss.project.template_source`)",
        )

    try:
        uv_runner = _resolve_uv_runner(environment)
        check_ok("uv / uvx", f"({Path(uv_runner[0]).name})")
    except Exception as exc:
        check_fail("uv / uvx", f"({exc})")
        issues.append("Install uv so Gauss can launch the managed Lean MCP server")

    lake_executable = shutil.which("lake", path=environment.get("PATH"))
    if lake_executable:
        check_ok("Lean toolchain (lake)", f"({lake_executable})")
    else:
        check_fail("Lean toolchain (lake)", "(required for managed Lean workflows)")
        issues.append("Install elan / Lean so `lake` is available on PATH")

    active_dir = Path(active_cwd or environment.get("TERMINAL_CWD") or os.getcwd()).expanduser().resolve()
    if active_dir.exists():
        check_ok("Active working directory", f"({active_dir})")
        try:
            project = discover_gauss_project(active_dir)
            check_ok("Active Gauss project", f"({project.label})")
        except ProjectNotFoundError:
            check_warn("Active Gauss project", f"(none under {active_dir})")
            issues.append(
                "Create or select a Gauss project with `/project init`, `/project convert`, or `/project use <path>`"
            )
        except ProjectManifestError as exc:
            check_fail("Gauss project manifest", f"({exc})")
            issues.append(f"Repair the active Gauss project manifest: {exc}")
    else:
        check_fail("Active working directory", f"({active_dir} does not exist)")
        issues.append(f"Fix the managed workflow working directory: {active_dir}")

    real_home = Path(environment.get("HOME", str(Path.home()))).expanduser().resolve()

    if backend_name == DEFAULT_AUTOFORMALIZE_BACKEND:
        claude_executable = shutil.which("claude", path=environment.get("PATH"))
        if claude_executable:
            check_ok("Claude Code CLI", f"({claude_executable})")
        else:
            check_fail("Claude Code CLI", "(install with `npm install -g @anthropic-ai/claude-code`)")
            issues.append("Install the Claude Code CLI for managed Lean workflows")

        has_local_login = _has_local_claude_login(real_home)
        has_local_api_key = _has_local_claude_api_key(real_home)
        staged_auth_env = _resolve_claude_auth_env(environment, include_persisted_env=True)
        staged_keys = ", ".join(sorted(staged_auth_env))

        if auth_mode == "auto":
            if has_local_login:
                check_ok("Claude auth", "(local Claude login)")
            elif has_local_api_key:
                check_ok("Claude auth", "(local Claude API key)")
            elif staged_auth_env:
                check_ok("Claude auth", f"(staged via {staged_keys})")
            else:
                check_fail("Claude auth", "(not found)")
                issues.append(
                    "Run `claude auth login`, save `ANTHROPIC_API_KEY`, or set `gauss.autoformalize.auth_mode: login`"
                )
        elif auth_mode == "login":
            if has_local_login:
                check_ok("Claude auth", "(local Claude login)")
            elif has_local_api_key:
                check_ok("Claude auth", "(local Claude API key)")
            else:
                check_warn("Claude auth", "(login mode will prompt on first launch)")
        else:
            if staged_auth_env:
                check_ok("Claude API-key auth", f"(staged via {staged_keys})")
            elif has_local_api_key:
                check_ok("Claude API-key auth", "(local Claude API key)")
            else:
                check_fail("Claude API-key auth", "(not found)")
                issues.append(
                    "Save `ANTHROPIC_API_KEY` / `ANTHROPIC_TOKEN` / `CLAUDE_CODE_OAUTH_TOKEN`, or switch `gauss.autoformalize.auth_mode` back to `auto` or `login`"
                )
        return

    if backend_name == CODEX_AUTOFORMALIZE_BACKEND:
        codex_executable = shutil.which("codex", path=environment.get("PATH"))
        if codex_executable:
            check_ok("Codex CLI", f"({codex_executable})")
        else:
            check_fail("Codex CLI", "(install the OpenAI Codex CLI)")
            issues.append("Install the OpenAI Codex CLI for managed Lean workflows")

        local_auth_payload = _load_local_codex_auth_payload(real_home, environment)
        has_local_auth = _codex_auth_payload_is_valid(local_auth_payload)
        has_local_api_key = _codex_auth_payload_has_api_key(local_auth_payload)
        staged_api_key = _resolve_codex_api_key(environment, include_persisted_env=True)

        if auth_mode == "auto":
            if has_local_auth:
                check_ok("Codex auth", "(local Codex auth)")
            elif staged_api_key:
                check_ok("Codex auth", "(staged OPENAI_API_KEY)")
            else:
                check_fail("Codex auth", "(not found)")
                issues.append(
                    "Run `codex login`, save `OPENAI_API_KEY`, or set `gauss.autoformalize.auth_mode: login`"
                )
        elif auth_mode == "login":
            if has_local_auth:
                check_ok("Codex auth", "(local Codex auth)")
            else:
                check_warn("Codex auth", "(login mode will prompt on first launch)")
        else:
            if staged_api_key:
                check_ok("Codex API-key auth", "(staged OPENAI_API_KEY)")
            elif has_local_api_key:
                check_ok("Codex API-key auth", "(local Codex API key)")
            else:
                check_fail("Codex API-key auth", "(not found)")
                issues.append(
                    "Save `OPENAI_API_KEY`, or switch `gauss.autoformalize.auth_mode` back to `auto` or `login`"
                )


def run_doctor(args):
    """Run diagnostic checks."""
    should_fix = getattr(args, 'fix', False)
    cli_name = get_cli_command_name()
    product_name = get_product_name()

    # Doctor runs from the interactive CLI, so CLI-gated tool availability
    # checks (like cronjob management) should see the same context as `gauss`.
    os.environ.setdefault("GAUSS_INTERACTIVE", "1")
    
    issues = []
    manual_issues = []  # issues that can't be auto-fixed
    fixed_count = 0
    
    print()
    print(color("┌─────────────────────────────────────────────────────────┐", Colors.CYAN))
    print(color(f"│ {f'{product_name} Doctor':^55} │", Colors.CYAN))
    print(color("└─────────────────────────────────────────────────────────┘", Colors.CYAN))
    
    # =========================================================================
    # Check: Python version
    # =========================================================================
    print()
    print(color("◆ Python Environment", Colors.CYAN, Colors.BOLD))
    
    py_version = sys.version_info
    if py_version >= (3, 11):
        check_ok(f"Python {py_version.major}.{py_version.minor}.{py_version.micro}")
    elif py_version >= (3, 10):
        check_ok(f"Python {py_version.major}.{py_version.minor}.{py_version.micro}")
        check_warn("Python 3.11+ recommended for RL Training tools (tinker requires >= 3.11)")
    elif py_version >= (3, 8):
        check_warn(f"Python {py_version.major}.{py_version.minor}.{py_version.micro}", "(3.10+ recommended)")
    else:
        check_fail(f"Python {py_version.major}.{py_version.minor}.{py_version.micro}", "(3.10+ required)")
        issues.append("Upgrade Python to 3.10+")
    
    # Check if in virtual environment
    in_venv = sys.prefix != sys.base_prefix
    if in_venv:
        check_ok("Virtual environment active")
    else:
        check_warn("Not in virtual environment", "(recommended)")
    
    # =========================================================================
    # Check: Required packages
    # =========================================================================
    print()
    print(color("◆ Required Packages", Colors.CYAN, Colors.BOLD))
    
    required_packages = [
        ("openai", "OpenAI SDK"),
        ("rich", "Rich (terminal UI)"),
        ("dotenv", "python-dotenv"),
        ("yaml", "PyYAML"),
        ("httpx", "HTTPX"),
    ]
    
    optional_packages = [
        ("croniter", "Croniter (cron expressions)"),
        ("telegram", "python-telegram-bot"),
        ("discord", "discord.py"),
    ]
    
    for module, name in required_packages:
        try:
            __import__(module)
            check_ok(name)
        except ImportError:
            check_fail(name, "(missing)")
            issues.append(f"Install {name}: uv pip install {module}")
    
    for module, name in optional_packages:
        try:
            __import__(module)
            check_ok(name, "(optional)")
        except ImportError:
            check_warn(name, "(optional, not installed)")
    
    # =========================================================================
    # Check: Configuration files
    # =========================================================================
    print()
    print(color("◆ Configuration Files", Colors.CYAN, Colors.BOLD))
    
    # Check ~/.gauss/.env (primary location for user config)
    env_path = GAUSS_HOME / '.env'
    if env_path.exists():
        check_ok("~/.gauss/.env file exists")
        
        # Check for common issues
        content = env_path.read_text()
        if _has_provider_env_config(content):
            check_ok("API key or custom endpoint configured")
        else:
            check_warn("No API key found in ~/.gauss/.env")
            issues.append(f"Run '{cli_name} setup' to configure API keys")
    else:
        # Also check project root as fallback
        fallback_env = PROJECT_ROOT / '.env'
        if fallback_env.exists():
            check_ok(".env file exists (in project directory)")
        else:
            check_fail("~/.gauss/.env file missing")
            if should_fix:
                env_path.parent.mkdir(parents=True, exist_ok=True)
                env_path.touch()
                check_ok("Created empty ~/.gauss/.env")
                check_info(f"Run '{cli_name} setup' to configure API keys")
                fixed_count += 1
            else:
                check_info(f"Run '{cli_name} setup' to create one")
                issues.append(f"Run '{cli_name} setup' to create .env")
    
    # Check ~/.gauss/config.yaml (primary) or project cli-config.yaml (fallback)
    config_path = GAUSS_HOME / 'config.yaml'
    if config_path.exists():
        check_ok("~/.gauss/config.yaml exists")
    else:
        fallback_config = PROJECT_ROOT / 'cli-config.yaml'
        if fallback_config.exists():
            check_ok("cli-config.yaml exists (in project directory)")
        else:
            example_config = PROJECT_ROOT / 'cli-config.yaml.example'
            if should_fix and example_config.exists():
                config_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(example_config), str(config_path))
                check_ok("Created ~/.gauss/config.yaml from cli-config.yaml.example")
                fixed_count += 1
            elif should_fix:
                check_warn("config.yaml not found and no example to copy from")
                manual_issues.append("Create ~/.gauss/config.yaml manually")
            else:
                check_warn("config.yaml not found", "(using defaults)")
    
    # =========================================================================
    # Check: Auth providers
    # =========================================================================
    print()
    print(color("◆ Auth Providers", Colors.CYAN, Colors.BOLD))

    try:
        from gauss_cli.auth import get_nous_auth_status, get_codex_auth_status

        nous_status = get_nous_auth_status()
        if nous_status.get("logged_in"):
            check_ok("Nous Portal auth", "(logged in)")
        else:
            check_warn("Nous Portal auth", "(not logged in)")

        codex_status = get_codex_auth_status()
        if codex_status.get("logged_in"):
            check_ok("OpenAI Codex auth", "(logged in)")
        else:
            check_warn("OpenAI Codex auth", "(not logged in)")
            if codex_status.get("error"):
                check_info(rewrite_cli_references(codex_status["error"]))
    except Exception as e:
        check_warn("Auth provider status", f"(could not check: {e})")

    if shutil.which("codex"):
        check_ok("codex CLI")
    else:
        check_warn("codex CLI not found", "(required for openai-codex login)")

    # =========================================================================
    # Check: Directory structure
    # =========================================================================
    print()
    print(color("◆ Directory Structure", Colors.CYAN, Colors.BOLD))
    
    gauss_home = GAUSS_HOME
    if gauss_home.exists():
        check_ok("~/.gauss directory exists")
    else:
        if should_fix:
            gauss_home.mkdir(parents=True, exist_ok=True)
            check_ok("Created ~/.gauss directory")
            fixed_count += 1
        else:
            check_warn("~/.gauss not found", "(will be created on first use)")
    
    # Check expected subdirectories
    expected_subdirs = ["cron", "sessions", "logs", "skills", "memories"]
    for subdir_name in expected_subdirs:
        subdir_path = gauss_home / subdir_name
        if subdir_path.exists():
            check_ok(f"~/.gauss/{subdir_name}/ exists")
        else:
            if should_fix:
                subdir_path.mkdir(parents=True, exist_ok=True)
                check_ok(f"Created ~/.gauss/{subdir_name}/")
                fixed_count += 1
            else:
                check_warn(f"~/.gauss/{subdir_name}/ not found", "(will be created on first use)")
    
    # Check for SOUL.md persona file
    soul_path = gauss_home / "SOUL.md"
    if soul_path.exists():
        content = soul_path.read_text(encoding="utf-8").strip()
        # Check if it's just the template comments (no real content)
        lines = [l for l in content.splitlines() if l.strip() and not l.strip().startswith(("<!--", "-->", "#"))]
        if lines:
            check_ok("~/.gauss/SOUL.md exists (persona configured)")
        else:
            check_info("~/.gauss/SOUL.md exists but is empty — edit it to customize personality")
    else:
        check_warn("~/.gauss/SOUL.md not found", "(create it to give Gauss a custom voice)")
        if should_fix:
            soul_path.parent.mkdir(parents=True, exist_ok=True)
            soul_path.write_text(
                "# Gauss Voice\n\n"
                "<!-- Edit this file to customize how Gauss communicates. -->\n\n"
                "You are Gauss. Be direct, useful, and brief about your identity.\n",
                encoding="utf-8",
            )
            check_ok("Created ~/.gauss/SOUL.md with basic template")
            fixed_count += 1
    
    # Check memory directory
    memories_dir = gauss_home / "memories"
    if memories_dir.exists():
        check_ok("~/.gauss/memories/ directory exists")
        memory_file = memories_dir / "MEMORY.md"
        user_file = memories_dir / "USER.md"
        if memory_file.exists():
            size = len(memory_file.read_text(encoding="utf-8").strip())
            check_ok(f"MEMORY.md exists ({size} chars)")
        else:
            check_info("MEMORY.md not created yet (will be created when the agent first writes a memory)")
        if user_file.exists():
            size = len(user_file.read_text(encoding="utf-8").strip())
            check_ok(f"USER.md exists ({size} chars)")
        else:
            check_info("USER.md not created yet (will be created when the agent first writes a memory)")
    else:
        check_warn("~/.gauss/memories/ not found", "(will be created on first use)")
        if should_fix:
            memories_dir.mkdir(parents=True, exist_ok=True)
            check_ok("Created ~/.gauss/memories/")
            fixed_count += 1
    
    # Check SQLite session store
    state_db_path = gauss_home / "state.db"
    if state_db_path.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(str(state_db_path))
            cursor = conn.execute("SELECT COUNT(*) FROM sessions")
            count = cursor.fetchone()[0]
            conn.close()
            check_ok(f"~/.gauss/state.db exists ({count} sessions)")
        except Exception as e:
            check_warn(f"~/.gauss/state.db exists but has issues: {e}")
    else:
        check_info("~/.gauss/state.db not created yet (will be created on first session)")

    _check_gateway_service_linger(issues)
    
    # =========================================================================
    # Check: External tools
    # =========================================================================
    print()
    print(color("◆ External Tools", Colors.CYAN, Colors.BOLD))
    
    # Git
    if shutil.which("git"):
        check_ok("git")
    else:
        check_warn("git not found", "(optional)")
    
    # ripgrep (optional, for faster file search)
    if shutil.which("rg"):
        check_ok("ripgrep (rg)", "(faster file search)")
    else:
        check_warn("ripgrep (rg) not found", "(file search uses grep fallback)")
        check_info("Install for faster search: sudo apt install ripgrep")
    
    # Docker (optional)
    terminal_env = os.getenv("TERMINAL_ENV", "local")
    if terminal_env == "docker":
        if shutil.which("docker"):
            # Check if docker daemon is running
            result = subprocess.run(["docker", "info"], capture_output=True)
            if result.returncode == 0:
                check_ok("docker", "(daemon running)")
            else:
                check_fail("docker daemon not running")
                issues.append("Start Docker daemon")
        else:
            check_fail("docker not found", "(required for TERMINAL_ENV=docker)")
            issues.append("Install Docker or change TERMINAL_ENV")
    else:
        if shutil.which("docker"):
            check_ok("docker", "(optional)")
        else:
            check_warn("docker not found", "(optional)")
    
    # SSH (if using ssh backend)
    if terminal_env == "ssh":
        ssh_host = os.getenv("TERMINAL_SSH_HOST")
        if ssh_host:
            # Try to connect
            result = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", ssh_host, "echo ok"],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                check_ok(f"SSH connection to {ssh_host}")
            else:
                check_fail(f"SSH connection to {ssh_host}")
                issues.append(f"Check SSH configuration for {ssh_host}")
        else:
            check_fail("TERMINAL_SSH_HOST not set", "(required for TERMINAL_ENV=ssh)")
            issues.append("Set TERMINAL_SSH_HOST in .env")
    
    # Daytona (if using daytona backend)
    if terminal_env == "daytona":
        daytona_key = os.getenv("DAYTONA_API_KEY")
        if daytona_key:
            check_ok("Daytona API key", "(configured)")
        else:
            check_fail("DAYTONA_API_KEY not set", "(required for TERMINAL_ENV=daytona)")
            issues.append("Set DAYTONA_API_KEY environment variable")
        try:
            from daytona import Daytona
            check_ok("daytona SDK", "(installed)")
        except ImportError:
            check_fail("daytona SDK not installed", "(pip install daytona)")
            issues.append("Install daytona SDK: pip install daytona")

    # Node.js + agent-browser (for browser automation tools)
    if shutil.which("node"):
        check_ok("Node.js")
        # Check if agent-browser is installed
        agent_browser_path = PROJECT_ROOT / "node_modules" / "agent-browser"
        if agent_browser_path.exists():
            check_ok("agent-browser (Node.js)", "(browser automation)")
        else:
            check_warn("agent-browser not installed", "(run: npm install)")
    else:
        check_warn("Node.js not found", "(optional, needed for browser tools)")
    
    # npm audit for all Node.js packages
    if shutil.which("npm"):
        npm_dirs = [
            (PROJECT_ROOT, "Browser tools (agent-browser)"),
            (PROJECT_ROOT / "scripts" / "whatsapp-bridge", "WhatsApp bridge"),
        ]
        for npm_dir, label in npm_dirs:
            if not (npm_dir / "node_modules").exists():
                continue
            try:
                audit_result = subprocess.run(
                    ["npm", "audit", "--json"],
                    cwd=str(npm_dir),
                    capture_output=True, text=True, timeout=30,
                )
                import json as _json
                audit_data = _json.loads(audit_result.stdout) if audit_result.stdout.strip() else {}
                vuln_count = audit_data.get("metadata", {}).get("vulnerabilities", {})
                critical = vuln_count.get("critical", 0)
                high = vuln_count.get("high", 0)
                moderate = vuln_count.get("moderate", 0)
                total = critical + high + moderate
                if total == 0:
                    check_ok(f"{label} deps", "(no known vulnerabilities)")
                elif critical > 0 or high > 0:
                    check_warn(
                        f"{label} deps",
                        f"({critical} critical, {high} high, {moderate} moderate — run: cd {npm_dir} && npm audit fix)"
                    )
                    issues.append(f"{label} has {total} npm vulnerability(ies)")
                else:
                    check_ok(f"{label} deps", f"({moderate} moderate vulnerability(ies))")
            except Exception:
                pass

    _check_managed_workflow_requirements(issues, cli_name=cli_name)

    # =========================================================================
    # Check: API connectivity
    # =========================================================================
    print()
    print(color("◆ API Connectivity", Colors.CYAN, Colors.BOLD))
    
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if openrouter_key:
        print("  Checking OpenRouter API...", end="", flush=True)
        try:
            import httpx
            response = httpx.get(
                OPENROUTER_MODELS_URL,
                headers={"Authorization": f"Bearer {openrouter_key}"},
                timeout=10
            )
            if response.status_code == 200:
                print(f"\r  {color('✓', Colors.GREEN)} OpenRouter API                          ")
            elif response.status_code == 401:
                print(f"\r  {color('✗', Colors.RED)} OpenRouter API {color('(invalid API key)', Colors.DIM)}                ")
                issues.append("Check OPENROUTER_API_KEY in .env")
            else:
                print(f"\r  {color('✗', Colors.RED)} OpenRouter API {color(f'(HTTP {response.status_code})', Colors.DIM)}                ")
        except Exception as e:
            print(f"\r  {color('✗', Colors.RED)} OpenRouter API {color(f'({e})', Colors.DIM)}                ")
            issues.append("Check network connectivity")
    else:
        check_warn("OpenRouter API", "(not configured)")
    
    anthropic_key = os.getenv("ANTHROPIC_TOKEN") or os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key:
        print("  Checking Anthropic API...", end="", flush=True)
        try:
            import httpx
            from agent.anthropic_adapter import _is_oauth_token, _COMMON_BETAS, _OAUTH_ONLY_BETAS

            headers = {"anthropic-version": "2023-06-01"}
            if _is_oauth_token(anthropic_key):
                headers["Authorization"] = f"Bearer {anthropic_key}"
                headers["anthropic-beta"] = ",".join(_COMMON_BETAS + _OAUTH_ONLY_BETAS)
            else:
                headers["x-api-key"] = anthropic_key
            response = httpx.get(
                "https://api.anthropic.com/v1/models",
                headers=headers,
                timeout=10
            )
            if response.status_code == 200:
                print(f"\r  {color('✓', Colors.GREEN)} Anthropic API                           ")
            elif response.status_code == 401:
                print(f"\r  {color('✗', Colors.RED)} Anthropic API {color('(invalid API key)', Colors.DIM)}                 ")
            else:
                msg = "(couldn't verify)"
                print(f"\r  {color('⚠', Colors.YELLOW)} Anthropic API {color(msg, Colors.DIM)}                 ")
        except Exception as e:
            print(f"\r  {color('⚠', Colors.YELLOW)} Anthropic API {color(f'({e})', Colors.DIM)}                 ")

    # -- API-key providers (Z.AI/GLM, Kimi, MiniMax, MiniMax-CN) --
    # Tuple: (name, env_vars, default_url, base_env, supports_models_endpoint)
    # If supports_models_endpoint is False, we skip the health check and just show "configured"
    _apikey_providers = [
        ("Z.AI / GLM",      ("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY"), "https://api.z.ai/api/paas/v4/models", "GLM_BASE_URL", True),
        ("Kimi / Moonshot",  ("KIMI_API_KEY",),                              "https://api.moonshot.ai/v1/models",   "KIMI_BASE_URL", True),
        # MiniMax APIs don't support /models endpoint — https://github.com/NousResearch/gauss-agent/issues/811
        ("MiniMax",          ("MINIMAX_API_KEY",),                            None,                                  "MINIMAX_BASE_URL", False),
        ("MiniMax (China)",  ("MINIMAX_CN_API_KEY",),                         None,                                  "MINIMAX_CN_BASE_URL", False),
    ]
    for _pname, _env_vars, _default_url, _base_env, _supports_health_check in _apikey_providers:
        _key = ""
        for _ev in _env_vars:
            _key = os.getenv(_ev, "")
            if _key:
                break
        if _key:
            _label = _pname.ljust(20)
            # Some providers (like MiniMax) don't support /models endpoint
            if not _supports_health_check:
                print(f"  {color('✓', Colors.GREEN)} {_label} {color('(key configured)', Colors.DIM)}")
                continue
            print(f"  Checking {_pname} API...", end="", flush=True)
            try:
                import httpx
                _base = os.getenv(_base_env, "")
                # Auto-detect Kimi Code keys (sk-kimi-) → api.kimi.com
                if not _base and _key.startswith("sk-kimi-"):
                    _base = "https://api.kimi.com/coding/v1"
                _url = (_base.rstrip("/") + "/models") if _base else _default_url
                _headers = {"Authorization": f"Bearer {_key}"}
                if "api.kimi.com" in _url.lower():
                    _headers["User-Agent"] = "KimiCLI/1.0"
                _resp = httpx.get(
                    _url,
                    headers=_headers,
                    timeout=10,
                )
                if _resp.status_code == 200:
                    print(f"\r  {color('✓', Colors.GREEN)} {_label}                          ")
                elif _resp.status_code == 401:
                    print(f"\r  {color('✗', Colors.RED)} {_label} {color('(invalid API key)', Colors.DIM)}           ")
                    issues.append(f"Check {_env_vars[0]} in .env")
                else:
                    print(f"\r  {color('⚠', Colors.YELLOW)} {_label} {color(f'(HTTP {_resp.status_code})', Colors.DIM)}           ")
            except Exception as _e:
                print(f"\r  {color('⚠', Colors.YELLOW)} {_label} {color(f'({_e})', Colors.DIM)}           ")

    # =========================================================================
    # Check: Submodules
    # =========================================================================
    print()
    print(color("◆ Submodules", Colors.CYAN, Colors.BOLD))
    
    # mini-swe-agent (terminal tool backend)
    mini_swe_dir = PROJECT_ROOT / "mini-swe-agent"
    if mini_swe_dir.exists() and (mini_swe_dir / "pyproject.toml").exists():
        try:
            __import__("minisweagent")
            check_ok("mini-swe-agent", "(terminal backend)")
        except ImportError:
            check_warn("mini-swe-agent found but not installed", "(run: uv pip install -e ./mini-swe-agent)")
            issues.append("Install mini-swe-agent: uv pip install -e ./mini-swe-agent")
    else:
        check_warn("mini-swe-agent not found", "(run: git submodule update --init --recursive)")
    
    # tinker-atropos (RL training backend)
    tinker_dir = PROJECT_ROOT / "tinker-atropos"
    if tinker_dir.exists() and (tinker_dir / "pyproject.toml").exists():
        if py_version >= (3, 11):
            try:
                __import__("tinker_atropos")
                check_ok("tinker-atropos", "(RL training backend)")
            except ImportError:
                check_warn("tinker-atropos found but not installed", "(run: uv pip install -e ./tinker-atropos)")
                issues.append("Install tinker-atropos: uv pip install -e ./tinker-atropos")
        else:
            check_warn("tinker-atropos requires Python 3.11+", f"(current: {py_version.major}.{py_version.minor})")
    else:
        check_warn("tinker-atropos not found", "(run: git submodule update --init --recursive)")
    
    # =========================================================================
    # Check: Tool Availability
    # =========================================================================
    print()
    print(color("◆ Tool Availability", Colors.CYAN, Colors.BOLD))
    
    try:
        # Add project root to path for imports
        sys.path.insert(0, str(PROJECT_ROOT))
        from model_tools import check_tool_availability, TOOLSET_REQUIREMENTS
        
        available, unavailable = check_tool_availability()
        available, unavailable = _apply_doctor_tool_availability_overrides(available, unavailable)
        
        for tid in available:
            info = TOOLSET_REQUIREMENTS.get(tid, {})
            check_ok(info.get("name", tid))
        
        for item in unavailable:
            env_vars = item.get("missing_vars") or item.get("env_vars") or []
            if env_vars:
                vars_str = ", ".join(env_vars)
                check_warn(item["name"], f"(missing {vars_str})")
            else:
                check_warn(item["name"], "(system dependency not met)")

        # Count disabled tools with API key requirements
        api_disabled = [u for u in unavailable if (u.get("missing_vars") or u.get("env_vars"))]
        if api_disabled:
            issues.append(f"Run '{cli_name} setup' to configure missing API keys for full tool access")
    except Exception as e:
        check_warn("Could not check tool availability", f"({e})")
    
    # =========================================================================
    # Check: Skills Hub
    # =========================================================================
    print()
    print(color("◆ Skills Hub", Colors.CYAN, Colors.BOLD))

    hub_dir = GAUSS_HOME / "skills" / ".hub"
    if hub_dir.exists():
        check_ok("Skills Hub directory exists")
        lock_file = hub_dir / "lock.json"
        if lock_file.exists():
            try:
                import json
                lock_data = json.loads(lock_file.read_text())
                count = len(lock_data.get("installed", {}))
                check_ok(f"Lock file OK ({count} hub-installed skill(s))")
            except Exception:
                check_warn("Lock file", "(corrupted or unreadable)")
        quarantine = hub_dir / "quarantine"
        q_count = sum(1 for d in quarantine.iterdir() if d.is_dir()) if quarantine.exists() else 0
        if q_count > 0:
            check_warn(f"{q_count} skill(s) in quarantine", "(pending review)")
    else:
        check_warn("Skills Hub directory not initialized", f"(run: {cli_name} skills list)")

    from gauss_cli.config import get_env_value
    github_token = get_env_value("GITHUB_TOKEN") or get_env_value("GH_TOKEN")
    if github_token:
        check_ok("GitHub token configured (authenticated API access)")
    else:
        check_warn("No GITHUB_TOKEN", "(60 req/hr rate limit — set in ~/.gauss/.env for better rates)")

    # =========================================================================
    # Summary
    # =========================================================================
    print()
    remaining_issues = issues + manual_issues
    if should_fix and fixed_count > 0:
        print(color("─" * 60, Colors.GREEN))
        print(color(f"  Fixed {fixed_count} issue(s).", Colors.GREEN, Colors.BOLD), end="")
        if remaining_issues:
            print(color(f" {len(remaining_issues)} issue(s) require manual intervention.", Colors.YELLOW, Colors.BOLD))
        else:
            print()
        print()
        if remaining_issues:
            for i, issue in enumerate(remaining_issues, 1):
                print(f"  {i}. {issue}")
            print()
    elif remaining_issues:
        print(color("─" * 60, Colors.YELLOW))
        print(color(f"  Found {len(remaining_issues)} issue(s) to address:", Colors.YELLOW, Colors.BOLD))
        print()
        for i, issue in enumerate(remaining_issues, 1):
            print(f"  {i}. {issue}")
        print()
        if not should_fix:
            print(color(f"  Tip: run '{cli_name} doctor --fix' to auto-fix what's possible.", Colors.DIM))
    else:
        print(color("─" * 60, Colors.GREEN))
        print(color("  All checks passed! 🎉", Colors.GREEN, Colors.BOLD))
    
    print()
