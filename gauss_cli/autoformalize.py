"""Managed backend session launchers for Gauss."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gauss_cli.config import get_env_value, get_gauss_home
from gauss_cli.handoff import HandoffRequest, build_handoff_request
from gauss_cli.project import (
    GaussProject,
    ProjectManifestError,
    ProjectNotFoundError,
    discover_gauss_project,
)

AUTOFORMALIZE_USAGE = (
    "Usage: /prove [scope or flags] | /draft [topic or flags] | "
    "/review [target or flags] | /checkpoint [target or flags] | "
    "/refactor [target or flags] | /golf [target or flags] | "
    "/autoprove [scope or flags] | /formalize [topic or flags] | "
    "/autoformalize [topic or flags]"
)
CLAUDE_MODEL = "claude-opus-4-6"
DEFAULT_MANAGED_CLAUDE_THEME = "dark"
LEAN4_SKILLS_URL = "https://github.com/cameronfreer/lean4-skills.git"
LEAN4_SKILLS_REF_ENV = "GAUSS_AUTOFORMALIZE_LEAN4_SKILLS_REF"
LEAN_LSP_MCP_SPEC = "lean-lsp-mcp"
LEAN_LSP_MCP_SPEC_ENV = "GAUSS_AUTOFORMALIZE_LEAN_LSP_MCP_SPEC"
LEAN4_CLAUDE_MARKETPLACE_REPO = "cameronfreer/lean4-skills"
LEAN4_CLAUDE_MARKETPLACE_NAME = "lean4-skills"
LEAN4_CLAUDE_PLUGIN_NAME = "lean4"
LEAN4_CLAUDE_PLUGIN_ID = f"{LEAN4_CLAUDE_PLUGIN_NAME}@{LEAN4_CLAUDE_MARKETPLACE_NAME}"
LEAN4_CHECKOUT_REVISION_FILE = ".gauss-managed-revision"
CLAUDE_AUTH_ENV_KEYS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_TOKEN", "ANTHROPIC_API_KEY")
CODEX_AUTH_ENV_KEYS = ("OPENAI_API_KEY",)
DEFAULT_AUTOFORMALIZE_BACKEND = "claude-code"
CODEX_AUTOFORMALIZE_BACKEND = "codex"
_SUPPORTED_AUTOFORMALIZE_BACKENDS = (
    DEFAULT_AUTOFORMALIZE_BACKEND,
    CODEX_AUTOFORMALIZE_BACKEND,
)
_AUTOFORMALIZE_BACKEND_ALIASES = {
    "claude": DEFAULT_AUTOFORMALIZE_BACKEND,
    "claude-code": DEFAULT_AUTOFORMALIZE_BACKEND,
    "codex": CODEX_AUTOFORMALIZE_BACKEND,
    "codex-cli": CODEX_AUTOFORMALIZE_BACKEND,
    "openai-codex": CODEX_AUTOFORMALIZE_BACKEND,
}

_WORKFLOW_ALIAS_MAP = {
    "/prove": ("prove", "/prove", "/lean4:prove"),
    "/draft": ("draft", "/draft", "/lean4:draft"),
    "/review": ("review", "/review", "/lean4:review"),
    "/checkpoint": ("checkpoint", "/checkpoint", "/lean4:checkpoint"),
    "/refactor": ("refactor", "/refactor", "/lean4:refactor"),
    "/golf": ("golf", "/golf", "/lean4:golf"),
    "/autoprove": ("autoprove", "/autoprove", "/lean4:autoprove"),
    "/auto-proof": ("autoprove", "/autoprove", "/lean4:autoprove"),
    "/auto_proof": ("autoprove", "/autoprove", "/lean4:autoprove"),
    "/formalize": ("formalize", "/formalize", "/lean4:formalize"),
    "/autoformalize": ("autoformalize", "/autoformalize", "/lean4:autoformalize"),
    "/auto-formalize": ("autoformalize", "/autoformalize", "/lean4:autoformalize"),
    "/auto_formalize": ("autoformalize", "/autoformalize", "/lean4:autoformalize"),
}

_FORGIVING_WORKFLOW_ALIAS_MAP = {
    "prove": "/prove",
    "draft": "/draft",
    "review": "/review",
    "checkpoint": "/checkpoint",
    "refactor": "/refactor",
    "golf": "/golf",
    "autoprove": "/autoprove",
    "auto-proof": "/autoprove",
    "formalize": "/formalize",
    "autoformalize": "/autoformalize",
    "auto-formalize": "/autoformalize",
}


def supported_autoformalize_backends() -> tuple[str, ...]:
    """Return the supported managed workflow backend identifiers."""
    return _SUPPORTED_AUTOFORMALIZE_BACKENDS


def normalize_autoformalize_backend_name(value: str) -> str:
    """Normalize a backend identifier and validate that it is supported."""
    normalized = str(value or "").strip().lower()
    normalized = normalized.replace("_", "-").replace("/", "-")
    normalized = _AUTOFORMALIZE_BACKEND_ALIASES.get(normalized, normalized)
    if normalized not in _SUPPORTED_AUTOFORMALIZE_BACKENDS:
        supported = ", ".join(_SUPPORTED_AUTOFORMALIZE_BACKENDS)
        raise AutoformalizeConfigError(
            f"`gauss.autoformalize.backend` must be one of: {supported}."
        )
    return normalized


class AutoformalizeError(RuntimeError):
    """Base class for managed autoformalization launcher failures."""


class AutoformalizeUsageError(AutoformalizeError):
    """Raised when the slash command input is malformed."""


class AutoformalizeConfigError(AutoformalizeError):
    """Raised when Gauss autoformalize config is malformed."""


class AutoformalizePreflightError(AutoformalizeError):
    """Raised when a local prerequisite is missing."""


class AutoformalizeStagingError(AutoformalizeError):
    """Raised when managed assets could not be staged."""


@dataclass(frozen=True)
class ManagedWorkflowSpec:
    """Normalized Gauss workflow command metadata."""

    workflow_kind: str
    frontend_command: str
    canonical_command: str
    backend_command: str
    workflow_args: str


@dataclass(frozen=True)
class ManagedContext:
    """Staged paths and metadata for a managed autoformalization backend session."""

    backend_name: str
    managed_root: Path
    project_root: Path
    lean_root: Path
    backend_home: Path
    plugin_root: Path
    mcp_config_path: Path
    startup_context_path: Path | None
    assets_root: Path
    project_manifest_path: Path | None = None
    backend_config_path: Path | None = None
    skills_root: Path | None = None
    instructions_path: Path | None = None

    @property
    def claude_home(self) -> Path:
        """Backward-compatible alias for legacy tests/callers."""
        return self.backend_home


@dataclass(frozen=True)
class AutoformalizeLaunchPlan:
    """Managed launch plan for a Gauss workflow command."""

    handoff_request: HandoffRequest
    managed_context: ManagedContext
    user_instruction: str
    project: GaussProject
    workflow_kind: str
    frontend_command: str
    canonical_command: str
    backend_command: str

    def staged_paths(self) -> dict[str, str]:
        """Return the most useful managed paths for diagnostics/tests."""
        return {
            "backend_name": self.managed_context.backend_name,
            "workflow_kind": self.workflow_kind,
            "frontend_command": self.frontend_command,
            "backend_command": self.backend_command,
            "managed_root": str(self.managed_context.managed_root),
            "project_root": str(self.managed_context.project_root),
            "lean_root": str(self.managed_context.lean_root),
            "backend_home": str(self.managed_context.backend_home),
            "claude_home": str(self.managed_context.backend_home),
            "plugin_root": str(self.managed_context.plugin_root),
            "mcp_config_path": str(self.managed_context.mcp_config_path),
            "startup_context_path": (
                str(self.managed_context.startup_context_path)
                if self.managed_context.startup_context_path
                else ""
            ),
            "backend_config_path": (
                str(self.managed_context.backend_config_path)
                if self.managed_context.backend_config_path
                else ""
            ),
            "project_manifest_path": (
                str(self.managed_context.project_manifest_path)
                if self.managed_context.project_manifest_path
                else ""
            ),
            "skills_root": (
                str(self.managed_context.skills_root)
                if self.managed_context.skills_root
                else ""
            ),
            "instructions_path": (
                str(self.managed_context.instructions_path)
                if self.managed_context.instructions_path
                else ""
            ),
        }


@dataclass(frozen=True)
class ManagedChatRuntime:
    """Backend-specific launch arguments and environment for `/chat`."""

    argv: list[str]
    child_env: dict[str, str]
    backend_name: str


@dataclass(frozen=True)
class ManagedChatLaunchPlan:
    """Managed launch plan for `/chat`."""

    handoff_request: HandoffRequest
    backend_name: str
    user_instruction: str
    active_cwd: Path

    def staged_paths(self) -> dict[str, str]:
        """Return the most useful launch metadata for diagnostics/tests."""
        return {
            "backend_name": self.backend_name,
            "cwd": str(self.active_cwd),
            "argv0": self.handoff_request.argv[0] if self.handoff_request.argv else "",
        }


@dataclass(frozen=True)
class SharedLeanBundle:
    """Shared Lean assets and paths for a managed autoformalization run."""

    backend_name: str
    managed_root: Path
    assets_root: Path
    startup_dir: Path
    mcp_dir: Path
    project: GaussProject
    project_root: Path
    lean_root: Path
    active_cwd: Path
    real_home: Path
    plugin_source: Path
    skill_source: Path
    scripts_root: Path
    references_root: Path
    uv_runner: tuple[str, ...]
    skill_revision: str


@dataclass(frozen=True)
class AutoformalizeBackendRuntime:
    """Backend-specific launch arguments, environment, and managed context."""

    argv: list[str]
    child_env: dict[str, str]
    managed_context: ManagedContext


def cli_only_managed_workflow_message(command_label: str = "/autoformalize") -> str:
    """Return a messaging-safe explanation for managed Lean workflows."""
    return (
        f"`{command_label}` is only available in the interactive Gauss CLI. "
        "It launches a managed Lean workflow session in your local terminal "
        "and then returns you to the same Gauss session."
    )


def cli_only_autoformalize_message() -> str:
    """Return the messaging-safe `/autoformalize` explanation."""
    return cli_only_managed_workflow_message("/autoformalize")


def resolve_managed_chat_request(
    user_instruction: str,
    config: Mapping[str, Any] | None,
    *,
    active_cwd: str | None = None,
    base_env: Mapping[str, str] | None = None,
) -> ManagedChatLaunchPlan:
    """Resolve `/chat` into a managed backend interactive session."""
    base_environment = dict(base_env or os.environ)
    active_dir = Path(active_cwd or base_environment.get("TERMINAL_CWD") or os.getcwd()).expanduser().resolve()
    if not active_dir.exists():
        raise AutoformalizePreflightError(f"Active working directory does not exist: {active_dir}")

    backend_name = _resolve_backend_name(config, base_environment)
    requested_mode = _resolve_requested_mode(config)
    runtime = _resolve_managed_chat_runtime(
        backend_name=backend_name,
        user_instruction=str(user_instruction or "").strip(),
        base_environment=base_environment,
        active_cwd=active_dir,
    )
    handoff_request = build_handoff_request(
        argv=runtime.argv,
        cwd=str(active_dir),
        env=runtime.child_env,
        requested_mode=requested_mode,
        label="Gauss chat session",
        source="gauss:chat",
    )
    return ManagedChatLaunchPlan(
        handoff_request=handoff_request,
        backend_name=runtime.backend_name,
        user_instruction=str(user_instruction or "").strip(),
        active_cwd=active_dir,
    )


def rewrite_forgiving_managed_command(command: str) -> str | None:
    """Rewrite obvious managed-workflow intents like ``prove`` into slash commands."""
    if not isinstance(command, str):
        return None
    text = command.strip()
    if not text or text.startswith("/"):
        return None

    parts = text.split(maxsplit=1)
    command_name = parts[0].strip().lower().replace("_", "-")
    remainder = parts[1].strip() if len(parts) > 1 else ""
    canonical = _FORGIVING_WORKFLOW_ALIAS_MAP.get(command_name)
    if not canonical:
        return None
    return canonical if not remainder else f"{canonical} {remainder}"


def resolve_autoformalize_request(
    command: str,
    config: Mapping[str, Any] | None,
    *,
    active_cwd: str | None = None,
    base_env: Mapping[str, str] | None = None,
) -> AutoformalizeLaunchPlan:
    """Resolve a managed Gauss workflow command into a staged backend handoff request."""
    if not isinstance(command, str):
        raise AutoformalizeUsageError(AUTOFORMALIZE_USAGE)

    include_persisted_env = base_env is None
    workflow = _parse_managed_workflow_command(command)
    user_instruction = workflow.workflow_args
    base_environment = dict(base_env or os.environ)
    active_dir = Path(active_cwd or base_environment.get("TERMINAL_CWD") or os.getcwd()).expanduser().resolve()
    if not active_dir.exists():
        raise AutoformalizePreflightError(f"Active working directory does not exist: {active_dir}")

    backend_name = _resolve_backend_name(config, base_environment)
    requested_mode = _resolve_requested_mode(config)
    auth_mode = _resolve_auth_mode(config, base_environment)

    git_exe = _require_executable(
        "git",
        "Git is required to stage the managed Lean workflow assets.",
        base_environment,
    )
    uv_runner = _resolve_uv_runner(base_environment)
    _require_executable(
        "rg",
        "ripgrep (`rg`) is required for Lean local search in managed workflows. Install it and try again.",
        base_environment,
    )
    try:
        project = discover_gauss_project(active_dir)
    except ProjectNotFoundError as exc:
        raise AutoformalizePreflightError(
            f"{exc} Run `/project init`, `/project convert`, or `/project use <path>` first."
        ) from exc
    except ProjectManifestError as exc:
        raise AutoformalizePreflightError(str(exc)) from exc

    real_home = Path(base_environment.get("HOME", str(Path.home()))).expanduser().resolve()
    shared_bundle = _prepare_shared_bundle(
        backend_name=backend_name,
        config=config,
        env=base_environment,
        project=project,
        project_root=project.root,
        lean_root=project.lean_root,
        active_cwd=active_dir,
        real_home=real_home,
        git_executable=git_exe,
        uv_runner=uv_runner,
    )
    runtime = _resolve_backend_runtime(
        backend_name=backend_name,
        auth_mode=auth_mode,
        user_instruction=user_instruction,
        workflow=workflow,
        base_environment=base_environment,
        include_persisted_env=include_persisted_env,
        shared_bundle=shared_bundle,
    )

    workflow_id = workflow.canonical_command.lstrip("/")
    handoff_request = build_handoff_request(
        argv=runtime.argv,
        cwd=str(active_dir),
        env=runtime.child_env,
        requested_mode=requested_mode,
        label=f"Gauss {workflow_id} session",
        source=f"gauss:{workflow_id}",
    )
    return AutoformalizeLaunchPlan(
        handoff_request=handoff_request,
        managed_context=runtime.managed_context,
        user_instruction=user_instruction,
        project=project,
        workflow_kind=workflow.workflow_kind,
        frontend_command=workflow.frontend_command,
        canonical_command=workflow.canonical_command,
        backend_command=workflow.backend_command,
    )


def _parse_managed_workflow_command(command: str) -> ManagedWorkflowSpec:
    text = command.strip()
    if not text.startswith("/"):
        raise AutoformalizeUsageError(AUTOFORMALIZE_USAGE)

    parts = text.split(maxsplit=1)
    command_name = parts[0].strip().lower()
    if command_name == "/handoff":
        command_name = "/autoformalize"
    workflow_args = parts[1].strip() if len(parts) > 1 else ""

    try:
        workflow_kind, canonical_command, backend_command = _WORKFLOW_ALIAS_MAP[command_name]
    except KeyError as exc:
        raise AutoformalizeUsageError(AUTOFORMALIZE_USAGE) from exc

    return ManagedWorkflowSpec(
        workflow_kind=workflow_kind,
        frontend_command=command_name,
        canonical_command=canonical_command,
        backend_command=backend_command if not workflow_args else f"{backend_command} {workflow_args}",
        workflow_args=workflow_args,
    )


def _strip_autoformalize_prefix(command: str) -> str:
    workflow = _parse_managed_workflow_command(command)
    if workflow.canonical_command != "/autoformalize":
        raise AutoformalizeUsageError(AUTOFORMALIZE_USAGE)
    return workflow.workflow_args


def _resolve_requested_mode(config: Mapping[str, Any] | None) -> str:
    gauss_cfg = _mapping_get(config, "gauss")
    auto_cfg = _mapping_get(gauss_cfg, "autoformalize")
    configured = auto_cfg.get("handoff_mode", "auto") if isinstance(auto_cfg, Mapping) else "auto"
    value = str(configured or "auto").strip().lower()
    if value not in {"auto", "helper", "strict"}:
        raise AutoformalizeConfigError(
            "`gauss.autoformalize.handoff_mode` must be one of: auto, helper, strict."
        )
    return value


def _resolve_backend_name(config: Mapping[str, Any] | None, env: Mapping[str, str]) -> str:
    override = str(env.get("GAUSS_AUTOFORMALIZE_BACKEND", "") or "").strip().lower()
    if override:
        value = override
    else:
        gauss_cfg = _mapping_get(config, "gauss")
        auto_cfg = _mapping_get(gauss_cfg, "autoformalize")
        configured = (
            auto_cfg.get("backend", DEFAULT_AUTOFORMALIZE_BACKEND)
            if isinstance(auto_cfg, Mapping)
            else DEFAULT_AUTOFORMALIZE_BACKEND
        )
        value = str(configured or DEFAULT_AUTOFORMALIZE_BACKEND).strip().lower()
    return normalize_autoformalize_backend_name(value)


def _resolve_auth_mode(config: Mapping[str, Any] | None, env: Mapping[str, str]) -> str:
    override = str(env.get("GAUSS_AUTOFORMALIZE_AUTH_MODE", "") or "").strip().lower()
    if override:
        value = override
    else:
        gauss_cfg = _mapping_get(config, "gauss")
        auto_cfg = _mapping_get(gauss_cfg, "autoformalize")
        configured = auto_cfg.get("auth_mode", "auto") if isinstance(auto_cfg, Mapping) else "auto"
        value = str(configured or "auto").strip().lower()
    value = value.replace("_", "-")
    if value not in {"auto", "login", "api-key"}:
        raise AutoformalizeConfigError(
            "`gauss.autoformalize.auth_mode` must be one of: auto, login, api-key."
        )
    return value


def _resolve_managed_state_base(config: Mapping[str, Any] | None, env: Mapping[str, str]) -> Path:
    override = str(env.get("GAUSS_AUTOFORMALIZE_MANAGED_STATE_DIR", "") or "").strip()
    if not override:
        gauss_cfg = _mapping_get(config, "gauss")
        auto_cfg = _mapping_get(gauss_cfg, "autoformalize")
        configured = auto_cfg.get("managed_state_dir", "") if isinstance(auto_cfg, Mapping) else ""
        override = str(configured or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (get_gauss_home() / "autoformalize").expanduser().resolve()


def _mapping_get(obj: Mapping[str, Any] | None, key: str) -> Mapping[str, Any]:
    if not isinstance(obj, Mapping):
        return {}
    value = obj.get(key)
    return value if isinstance(value, Mapping) else {}


def _require_executable(name: str, error_message: str, env: Mapping[str, str]) -> str:
    resolved = shutil.which(name, path=env.get("PATH"))
    if resolved is None:
        raise AutoformalizePreflightError(error_message)
    return resolved


def _resolve_uv_runner(env: Mapping[str, str]) -> tuple[str, ...]:
    spec = _resolve_lean_lsp_mcp_spec(env)
    uvx = shutil.which("uvx", path=env.get("PATH"))
    if uvx:
        return (uvx, "--from", spec, "lean-lsp-mcp")

    uv = shutil.which("uv", path=env.get("PATH"))
    if uv:
        return (uv, "x", "--from", spec, "lean-lsp-mcp")

    raise AutoformalizePreflightError(
        "Neither `uvx` nor `uv` is available. Install uv so Gauss can run the managed Lean MCP server."
    )


def _resolve_lean_lsp_mcp_spec(env: Mapping[str, str]) -> str:
    spec = str(env.get(LEAN_LSP_MCP_SPEC_ENV, "") or "").strip()
    return spec or LEAN_LSP_MCP_SPEC


def _resolve_lean4_skills_ref(env: Mapping[str, str]) -> str | None:
    value = str(env.get(LEAN4_SKILLS_REF_ENV, "") or "").strip()
    return value or None


def _find_lean_project_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / "lakefile.lean").exists() or (candidate / "lakefile.toml").exists():
            return candidate
    return None


def _is_effective_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    if callable(geteuid):
        try:
            return geteuid() == 0
        except OSError:
            pass
    getuid = getattr(os, "getuid", None)
    if callable(getuid):
        try:
            return getuid() == 0
        except OSError:
            pass
    return False


def _claude_permission_args() -> tuple[str, ...]:
    # Managed Claude child sessions should always run without interactive
    # permission prompts so swarm-spawned workflows stay fully attachable.
    # Claude Code blocks --dangerously-skip-permissions under root/sudo.
    # dontAsk suppresses prompts but does not grant the same MCP/tool access
    # as a true approvals bypass.
    if _is_effective_root():
        return ("--permission-mode", "bypassPermissions")
    return ("--dangerously-skip-permissions",)


def _ensure_project_tool_permissions(project_root: Path) -> None:
    """Write a permissive allow list into the project's CC settings.

    CC reads `<cwd>/.claude/settings.local.json` for project-scoped
    permissions.  Under root (where --dangerously-skip-permissions is
    blocked) this is the only way to avoid interactive tool prompts.
    """
    settings_dir = project_root / ".claude"
    settings_dir.mkdir(parents=True, exist_ok=True)
    settings_path = settings_dir / "settings.local.json"
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    except (json.JSONDecodeError, OSError):
        data = {}
    permissions = data.setdefault("permissions", {})
    allow = permissions.setdefault("allow", [])
    needed = [
        "Read", "Write", "Edit",
        "Bash(*)", "mcp__*",
        "WebSearch", "WebFetch",
    ]
    changed = False
    for tool in needed:
        if tool not in allow:
            allow.append(tool)
            changed = True
    if changed:
        settings_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _resolve_claude_auth_env(
    env: Mapping[str, str],
    *,
    include_persisted_env: bool,
) -> dict[str, str]:
    for key in CLAUDE_AUTH_ENV_KEYS:
        value = str(env.get(key, "") or "").strip()
        if not value and include_persisted_env:
            value = str(get_env_value(key) or "").strip()
        if value:
            return {key: value}
    return {}


def _resolve_codex_api_key(
    env: Mapping[str, str],
    *,
    include_persisted_env: bool,
) -> str:
    value = str(env.get("OPENAI_API_KEY", "") or "").strip()
    if not value and include_persisted_env:
        value = str(get_env_value("OPENAI_API_KEY") or "").strip()
    return value


def _has_local_claude_auth(real_home: Path) -> bool:
    return _has_local_claude_login(real_home) or _has_local_claude_api_key(real_home)


def _read_keychain_claude_credentials() -> str | None:
    """Return the Claude Code OAuth credentials JSON from the macOS Keychain, or ``None``."""
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        payload = result.stdout.strip()
        if not payload:
            return None
        data = json.loads(payload)
        oauth = data.get("claudeAiOauth")
        if isinstance(oauth, Mapping) and str(oauth.get("accessToken", "")).strip():
            return payload
    except Exception:
        pass
    return None


def _has_local_claude_login(real_home: Path) -> bool:
    credentials = real_home / ".claude" / ".credentials.json"
    if credentials.exists():
        try:
            data = json.loads(credentials.read_text(encoding="utf-8"))
            oauth_data = data.get("claudeAiOauth")
            if isinstance(oauth_data, Mapping) and str(oauth_data.get("accessToken", "")).strip():
                return True
        except Exception:
            pass
    return _read_keychain_claude_credentials() is not None


def _load_local_claude_config(real_home: Path) -> dict[str, Any]:
    return _load_json_dict(real_home / ".claude.json")


def _load_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json_dict(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2) + "\n", encoding="utf-8")


def _utc_now_isoformat() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _claude_settings_path(home: Path) -> Path:
    return home / ".claude" / "settings.json"


def _claude_plugins_root(home: Path) -> Path:
    return home / ".claude" / "plugins"


def _claude_known_marketplaces_path(home: Path) -> Path:
    return _claude_plugins_root(home) / "known_marketplaces.json"


def _claude_installed_plugins_path(home: Path) -> Path:
    return _claude_plugins_root(home) / "installed_plugins.json"


def _claude_marketplaces_root(home: Path) -> Path:
    return _claude_plugins_root(home) / "marketplaces"


def _claude_marketplace_root(home: Path) -> Path:
    return _claude_marketplaces_root(home) / LEAN4_CLAUDE_MARKETPLACE_NAME


def _claude_plugin_cache_root(home: Path) -> Path:
    return _claude_plugins_root(home) / "cache" / LEAN4_CLAUDE_MARKETPLACE_NAME / LEAN4_CLAUDE_PLUGIN_NAME


def _path_text_within_root(path_text: str, root: Path) -> bool:
    value = str(path_text or "").strip()
    if not value:
        return False
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        return False
    try:
        candidate.relative_to(root.expanduser())
        return True
    except ValueError:
        return False


def _lean4_checkout_root(assets_root: Path) -> Path:
    return assets_root / "lean4-skills"


def _lean4_checkout_paths(checkout_root: Path) -> tuple[Path, Path, Path, Path]:
    plugin_source = checkout_root / "plugins" / LEAN4_CLAUDE_PLUGIN_NAME
    skill_source = plugin_source / "skills" / "lean4"
    scripts_root = plugin_source / "lib" / "scripts"
    references_root = skill_source / "references"
    return plugin_source, skill_source, scripts_root, references_root


def _lean4_checkout_missing_paths(checkout_root: Path) -> list[Path]:
    plugin_source, skill_source, scripts_root, references_root = _lean4_checkout_paths(checkout_root)
    return [
        path
        for path in (plugin_source, skill_source, scripts_root, references_root)
        if not path.is_dir()
    ]


def _lean4_checkout_is_complete(checkout_root: Path) -> bool:
    return not _lean4_checkout_missing_paths(checkout_root)


def _lean4_checkout_revision_path(checkout_root: Path) -> Path:
    return checkout_root / LEAN4_CHECKOUT_REVISION_FILE


def _write_lean4_checkout_revision(checkout_root: Path, revision: str) -> None:
    _lean4_checkout_revision_path(checkout_root).write_text(f"{revision.strip()}\n", encoding="utf-8")


def _read_lean4_checkout_revision(checkout_root: Path) -> str | None:
    path = _lean4_checkout_revision_path(checkout_root)
    if not path.is_file():
        return None
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _current_git_revision(destination: Path, git_executable: str) -> str:
    return _run(
        [git_executable, "-C", str(destination), "rev-parse", "HEAD"],
        error_prefix="Failed to read managed asset revision",
    ).stdout.strip()


def _ensure_lean4_checkout_assets(
    *,
    assets_root: Path,
    env: Mapping[str, str],
    git_executable: str,
    refresh: bool,
) -> tuple[Path, str]:
    checkout_root = _lean4_checkout_root(assets_root)
    requested_ref = _resolve_lean4_skills_ref(env)
    needs_refresh = refresh or requested_ref is not None or not _lean4_checkout_is_complete(checkout_root)
    if needs_refresh:
        revision = _ensure_git_checkout(
            repo_url=LEAN4_SKILLS_URL,
            revision=requested_ref,
            destination=checkout_root,
            git_executable=git_executable,
        )
        _write_lean4_checkout_revision(checkout_root, revision)
    else:
        revision = _read_lean4_checkout_revision(checkout_root)
        if revision is None:
            revision = _current_git_revision(checkout_root, git_executable)
            _write_lean4_checkout_revision(checkout_root, revision)

    missing_paths = _lean4_checkout_missing_paths(checkout_root)
    if missing_paths:
        rendered = ", ".join(str(path) for path in missing_paths)
        raise AutoformalizeStagingError(
            f"Managed Lean bundle is incomplete after checkout: {rendered}"
        )
    return checkout_root, revision


def _warm_lean_lsp_mcp_cache(uv_runner: Sequence[str]) -> None:
    _run(
        [*uv_runner, "--help"],
        error_prefix="Failed to prewarm the managed Lean MCP runtime",
    )


def _claude_marketplace_source() -> dict[str, str]:
    return {
        "source": "github",
        "repo": LEAN4_CLAUDE_MARKETPLACE_REPO,
    }


def _merge_claude_marketplace_settings(real_home: Path) -> None:
    settings_path = _claude_settings_path(real_home)
    settings_payload = _load_json_dict(settings_path)

    marketplaces = settings_payload.get("extraKnownMarketplaces")
    if not isinstance(marketplaces, dict):
        marketplaces = {}
    marketplace_entry = marketplaces.get(LEAN4_CLAUDE_MARKETPLACE_NAME)
    if not isinstance(marketplace_entry, dict):
        marketplace_entry = {}
    marketplace_entry["source"] = _claude_marketplace_source()
    marketplace_entry["autoUpdate"] = True
    marketplaces[LEAN4_CLAUDE_MARKETPLACE_NAME] = marketplace_entry
    settings_payload["extraKnownMarketplaces"] = marketplaces

    enabled_plugins = settings_payload.get("enabledPlugins")
    if not isinstance(enabled_plugins, dict):
        enabled_plugins = {}
    enabled_plugins[LEAN4_CLAUDE_PLUGIN_ID] = True
    settings_payload["enabledPlugins"] = enabled_plugins

    _write_json_dict(settings_path, settings_payload)


def _upsert_claude_known_marketplace_entry(
    home: Path,
    *,
    install_location: Path | None = None,
    template_entry: Mapping[str, Any] | None = None,
) -> None:
    known_marketplaces_path = _claude_known_marketplaces_path(home)
    payload = _load_json_dict(known_marketplaces_path)
    if template_entry is not None:
        entry = dict(template_entry)
    else:
        entry = payload.get(LEAN4_CLAUDE_MARKETPLACE_NAME)
        if not isinstance(entry, dict):
            entry = {}
    entry["source"] = _claude_marketplace_source()
    entry["autoUpdate"] = True
    if install_location is not None:
        entry["installLocation"] = str(install_location)
    entry.setdefault("lastUpdated", _utc_now_isoformat())
    payload[LEAN4_CLAUDE_MARKETPLACE_NAME] = entry
    _write_json_dict(known_marketplaces_path, payload)


def _read_claude_known_marketplace_entry(home: Path) -> dict[str, Any] | None:
    payload = _load_json_dict(_claude_known_marketplaces_path(home))
    entry = payload.get(LEAN4_CLAUDE_MARKETPLACE_NAME)
    return dict(entry) if isinstance(entry, Mapping) else None


def _extract_claude_path(entry: Mapping[str, Any], field_name: str) -> Path | None:
    value = str(entry.get(field_name, "") or "").strip()
    if not value:
        return None
    return Path(value).expanduser()


def _extract_claude_plugin_version(entry: Mapping[str, Any]) -> str:
    return str(entry.get("version", "") or "").strip()


def _select_claude_installed_plugin_entry(
    payload: Mapping[str, Any],
    *,
    require_existing_path: bool = True,
) -> dict[str, Any] | None:
    plugins = payload.get("plugins")
    if not isinstance(plugins, Mapping):
        return None
    entry = plugins.get(LEAN4_CLAUDE_PLUGIN_ID)
    if isinstance(entry, list):
        for candidate in entry:
            if isinstance(candidate, Mapping):
                install_path = _extract_claude_path(candidate, "installPath")
                if install_path is not None and (not require_existing_path or install_path.exists()):
                    return dict(candidate)
        return dict(entry[0]) if entry and isinstance(entry[0], Mapping) else None
    if isinstance(entry, Mapping):
        return dict(entry)
    return None


def _find_claude_installed_plugin_root(real_home: Path) -> Path | None:
    payload = _load_json_dict(_claude_installed_plugins_path(real_home))
    entry = _select_claude_installed_plugin_entry(payload)
    if entry is None:
        return None
    install_path = _extract_claude_path(entry, "installPath")
    if install_path is None:
        return None
    if not install_path.exists():
        return None
    return install_path


def _write_claude_installed_plugin_entry(
    home: Path,
    *,
    install_path: Path,
    version: str,
    template_entry: Mapping[str, Any] | None = None,
) -> None:
    installed_plugins_path = _claude_installed_plugins_path(home)
    payload = _load_json_dict(installed_plugins_path)
    plugins = payload.get("plugins")
    if not isinstance(plugins, dict):
        plugins = {}
    entry = dict(template_entry or {})
    entry["scope"] = str(entry.get("scope", "") or "user")
    entry["installPath"] = str(install_path)
    entry["version"] = version
    entry.setdefault("installedAt", _utc_now_isoformat())
    entry.setdefault("lastUpdated", entry["installedAt"])
    plugins[LEAN4_CLAUDE_PLUGIN_ID] = [entry]
    payload["version"] = 2
    payload["plugins"] = plugins
    _write_json_dict(installed_plugins_path, payload)


def _delete_claude_known_marketplace_entry(home: Path) -> None:
    known_marketplaces_path = _claude_known_marketplaces_path(home)
    payload = _load_json_dict(known_marketplaces_path)
    if LEAN4_CLAUDE_MARKETPLACE_NAME not in payload:
        return
    del payload[LEAN4_CLAUDE_MARKETPLACE_NAME]
    _write_json_dict(known_marketplaces_path, payload)


def _delete_claude_installed_plugin_entry(home: Path) -> None:
    installed_plugins_path = _claude_installed_plugins_path(home)
    payload = _load_json_dict(installed_plugins_path)
    plugins = payload.get("plugins")
    if not isinstance(plugins, dict):
        return
    if LEAN4_CLAUDE_PLUGIN_ID not in plugins:
        return
    del plugins[LEAN4_CLAUDE_PLUGIN_ID]
    payload["plugins"] = plugins
    if "version" not in payload:
        payload["version"] = 2
    _write_json_dict(installed_plugins_path, payload)


def _scrub_claude_lean_plugin_state(home: Path) -> None:
    _delete_claude_known_marketplace_entry(home)
    _delete_claude_installed_plugin_entry(home)
    _remove_existing_path(_claude_marketplace_root(home))
    _remove_existing_path(_claude_plugin_cache_root(home))


def _claude_user_plugin_state_is_healthy(real_home: Path) -> bool:
    marketplace_entry = _read_claude_known_marketplace_entry(real_home)
    if marketplace_entry is None:
        return False
    if marketplace_entry.get("source") != _claude_marketplace_source():
        return False
    marketplace_root = _extract_claude_path(marketplace_entry, "installLocation")
    if marketplace_root is None:
        return False
    if not _path_text_within_root(str(marketplace_root), _claude_marketplaces_root(real_home)):
        return False
    if not marketplace_root.exists():
        return False

    payload = _load_json_dict(_claude_installed_plugins_path(real_home))
    plugin_entry = _select_claude_installed_plugin_entry(payload)
    if plugin_entry is None:
        return False
    install_path = _extract_claude_path(plugin_entry, "installPath")
    if install_path is None:
        return False
    if not _path_text_within_root(str(install_path), _claude_plugin_cache_root(real_home)):
        return False
    return install_path.exists()


def _find_claude_marketplace_root(home: Path) -> Path | None:
    entry = _read_claude_known_marketplace_entry(home)
    if entry is not None:
        install_location = _extract_claude_path(entry, "installLocation")
        if install_location is not None and install_location.exists():
            return install_location
    marketplace_root = _claude_marketplace_root(home)
    if marketplace_root.exists():
        return marketplace_root
    return None


def _is_claude_already_installed_error(exc: AutoformalizeStagingError) -> bool:
    message = str(exc).lower()
    return "already installed" in message


def _add_claude_marketplace(
    *,
    claude_executable: str,
    cli_env: Mapping[str, str],
    marketplace_target: str,
    error_prefix: str,
) -> None:
    try:
        _run(
            [
                claude_executable,
                "plugin",
                "marketplace",
                "add",
                marketplace_target,
            ],
            env=cli_env,
            error_prefix=error_prefix,
        )
    except AutoformalizeStagingError as exc:
        if _is_claude_already_installed_error(exc):
            return
        raise


def _install_claude_plugin_target(
    *,
    claude_executable: str,
    cli_env: Mapping[str, str],
    plugin_name: str,
    plugin_id: str,
    error_prefix: str,
) -> None:
    failures: list[str] = []
    for plugin_target in (plugin_name, plugin_id):
        try:
            _run(
                [
                    claude_executable,
                    "plugin",
                    "install",
                    plugin_target,
                ],
                env=cli_env,
                error_prefix=error_prefix,
            )
            return
        except AutoformalizeStagingError as exc:
            if _is_claude_already_installed_error(exc):
                return
            failures.append(str(exc))
    raise AutoformalizeStagingError(failures[-1])


def _ensure_claude_user_plugin_state(
    *,
    claude_executable: str,
    real_home: Path,
    base_environment: Mapping[str, str],
) -> Path:
    real_home.mkdir(parents=True, exist_ok=True)
    _merge_claude_marketplace_settings(real_home)
    existing_install_path = _find_claude_installed_plugin_root(real_home)
    if existing_install_path is not None and _claude_user_plugin_state_is_healthy(real_home):
        _upsert_claude_known_marketplace_entry(
            real_home,
            install_location=_claude_marketplace_root(real_home),
        )
        return existing_install_path

    _scrub_claude_lean_plugin_state(real_home)
    cli_env = dict(base_environment)
    cli_env["HOME"] = str(real_home)
    _add_claude_marketplace(
        claude_executable=claude_executable,
        cli_env=cli_env,
        marketplace_target=LEAN4_CLAUDE_MARKETPLACE_REPO,
        error_prefix="Failed to register the Lean4 Claude marketplace in the user profile",
    )
    _install_claude_plugin_target(
        claude_executable=claude_executable,
        cli_env=cli_env,
        plugin_name=LEAN4_CLAUDE_PLUGIN_NAME,
        plugin_id=LEAN4_CLAUDE_PLUGIN_ID,
        error_prefix="Failed to install the Lean4 Claude plugin in the user profile",
    )

    _merge_claude_marketplace_settings(real_home)
    _upsert_claude_known_marketplace_entry(
        real_home,
        install_location=_claude_marketplace_root(real_home),
    )

    install_path = _find_claude_installed_plugin_root(real_home)
    if install_path is None:
        raise AutoformalizeStagingError(
            f"Managed Lean Claude plugin is not installed after configuration: {LEAN4_CLAUDE_PLUGIN_ID}"
        )
    return install_path


def _replace_tree_link(destination: Path, source: Path) -> None:
    _remove_existing_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        destination.symlink_to(source, target_is_directory=source.is_dir())
    except OSError:
        shutil.copytree(source, destination, symlinks=True)


def _remove_existing_path(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    shutil.rmtree(path)


def _sync_prewarmed_claude_plugin(
    *,
    real_home: Path,
    backend_home: Path,
) -> Path | None:
    plugin_root = _find_claude_installed_plugin_root(real_home)
    settings_path = _claude_settings_path(real_home)
    if plugin_root is None or not settings_path.is_file():
        return None

    payload = _load_json_dict(_claude_installed_plugins_path(real_home))
    plugin_entry = _select_claude_installed_plugin_entry(payload)
    if plugin_entry is None:
        return None
    plugin_version = _extract_claude_plugin_version(plugin_entry)
    if not plugin_version:
        plugin_version = plugin_root.name

    managed_plugins_root = _claude_plugins_root(backend_home)
    _remove_existing_path(managed_plugins_root)
    managed_claude_dir = backend_home / ".claude"
    managed_claude_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(settings_path, managed_claude_dir / "settings.json")
    managed_install_path = _claude_plugin_cache_root(backend_home) / plugin_version
    _replace_tree_link(managed_install_path, plugin_root)

    marketplace_entry = _read_claude_known_marketplace_entry(real_home)
    marketplace_root = _find_claude_marketplace_root(real_home)
    managed_marketplace_root = _claude_marketplace_root(backend_home)
    if marketplace_root is not None:
        _replace_tree_link(managed_marketplace_root, marketplace_root)
    else:
        managed_marketplace_root.mkdir(parents=True, exist_ok=True)

    _merge_claude_marketplace_settings(backend_home)
    _upsert_claude_known_marketplace_entry(
        backend_home,
        install_location=managed_marketplace_root,
        template_entry=marketplace_entry,
    )
    _write_claude_installed_plugin_entry(
        backend_home,
        install_path=managed_install_path,
        version=plugin_version,
        template_entry=plugin_entry,
    )
    return managed_install_path


def prepare_managed_runtime_assets(
    *,
    config: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    base_environment = dict(env or os.environ)
    managed_state_base = _resolve_managed_state_base(config, base_environment)
    assets_root = managed_state_base / "assets"
    assets_root.mkdir(parents=True, exist_ok=True)

    git_executable = _require_executable(
        "git",
        "Git is required to prewarm the managed Lean workflow assets.",
        base_environment,
    )
    uv_runner = _resolve_uv_runner(base_environment)
    checkout_root, skill_revision = _ensure_lean4_checkout_assets(
        assets_root=assets_root,
        env=base_environment,
        git_executable=git_executable,
        refresh=True,
    )
    _warm_lean_lsp_mcp_cache(uv_runner)

    result = {
        "lean4_checkout_root": str(checkout_root),
        "skill_revision": skill_revision,
        "lean_lsp_mcp_spec": _resolve_lean_lsp_mcp_spec(base_environment),
    }

    claude_executable = shutil.which("claude", path=base_environment.get("PATH"))
    if claude_executable:
        real_home = Path(base_environment.get("HOME", str(Path.home()))).expanduser().resolve()
        plugin_root = _ensure_claude_user_plugin_state(
            claude_executable=claude_executable,
            real_home=real_home,
            base_environment=base_environment,
        )
        result["claude_plugin_root"] = str(plugin_root)
    else:
        result["claude_plugin_root"] = ""

    return result


def _read_claude_marketplace_name(marketplace_root: Path) -> str:
    payload = _load_json_dict(marketplace_root / ".claude-plugin" / "marketplace.json")
    name = str(payload.get("name", "") or "").strip()
    if not name:
        raise AutoformalizeStagingError(
            f"Managed Lean marketplace manifest is missing a name: {marketplace_root / '.claude-plugin' / 'marketplace.json'}"
        )
    return name


def _read_claude_plugin_identity(plugin_source: Path) -> tuple[str, str]:
    payload = _load_json_dict(plugin_source / ".claude-plugin" / "plugin.json")
    name = str(payload.get("name", "") or "").strip()
    version = str(payload.get("version", "") or "").strip()
    if not name or not version:
        raise AutoformalizeStagingError(
            f"Managed Lean plugin manifest is missing name/version: {plugin_source / '.claude-plugin' / 'plugin.json'}"
        )
    return name, version


def _has_local_claude_api_key(real_home: Path) -> bool:
    data = _load_local_claude_config(real_home)
    return bool(str(data.get("primaryApiKey", "")).strip())


def _local_codex_auth_path(real_home: Path, env: Mapping[str, str]) -> Path:
    configured_home = str(env.get("CODEX_HOME", "") or "").strip()
    if configured_home:
        codex_home = Path(configured_home).expanduser()
    else:
        codex_home = real_home / ".codex"
    return codex_home / "auth.json"


def _load_local_codex_auth_payload(real_home: Path, env: Mapping[str, str]) -> dict[str, Any]:
    auth_path = _local_codex_auth_path(real_home, env)
    if not auth_path.is_file():
        return {}
    try:
        payload = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _codex_auth_payload_is_valid(payload: Mapping[str, Any]) -> bool:
    auth_mode = str(payload.get("auth_mode", "") or "").strip().lower()
    if auth_mode == "apikey":
        return bool(str(payload.get("OPENAI_API_KEY", "")).strip())

    tokens = payload.get("tokens")
    if not isinstance(tokens, Mapping):
        return False
    return all(
        str(tokens.get(key, "") or "").strip()
        for key in ("access_token", "refresh_token", "id_token")
    )


def _codex_auth_payload_has_api_key(payload: Mapping[str, Any]) -> bool:
    auth_mode = str(payload.get("auth_mode", "") or "").strip().lower()
    return auth_mode == "apikey" and bool(str(payload.get("OPENAI_API_KEY", "")).strip())


def _prepare_shared_bundle(
    *,
    backend_name: str,
    config: Mapping[str, Any] | None,
    env: Mapping[str, str],
    project: GaussProject,
    project_root: Path,
    lean_root: Path,
    active_cwd: Path,
    real_home: Path,
    git_executable: str,
    uv_runner: Sequence[str],
) -> SharedLeanBundle:
    managed_state_base = _resolve_managed_state_base(config, env)
    managed_root = _managed_root(managed_state_base, backend_name)
    assets_root = managed_state_base / "assets"
    startup_dir = managed_root / "startup"
    mcp_dir = managed_root / "mcp"

    for path in (managed_root, assets_root, startup_dir, mcp_dir):
        path.mkdir(parents=True, exist_ok=True)

    lean4_checkout, skill_revision = _ensure_lean4_checkout_assets(
        assets_root=assets_root,
        env=env,
        git_executable=git_executable,
        refresh=False,
    )
    plugin_source, skill_source, scripts_root, references_root = _lean4_checkout_paths(
        lean4_checkout
    )

    return SharedLeanBundle(
        backend_name=backend_name,
        managed_root=managed_root,
        assets_root=assets_root,
        startup_dir=startup_dir,
        mcp_dir=mcp_dir,
        project=project,
        project_root=project_root,
        lean_root=lean_root,
        active_cwd=active_cwd,
        real_home=real_home,
        plugin_source=plugin_source,
        skill_source=skill_source,
        scripts_root=scripts_root,
        references_root=references_root,
        uv_runner=tuple(uv_runner),
        skill_revision=skill_revision,
    )


def _resolve_backend_runtime(
    *,
    backend_name: str,
    auth_mode: str,
    user_instruction: str,
    workflow: ManagedWorkflowSpec,
    base_environment: Mapping[str, str],
    include_persisted_env: bool,
    shared_bundle: SharedLeanBundle,
) -> AutoformalizeBackendRuntime:
    if backend_name == DEFAULT_AUTOFORMALIZE_BACKEND:
        return _build_claude_runtime(
            auth_mode=auth_mode,
            user_instruction=user_instruction,
            workflow=workflow,
            base_environment=base_environment,
            include_persisted_env=include_persisted_env,
            shared_bundle=shared_bundle,
        )
    if backend_name == CODEX_AUTOFORMALIZE_BACKEND:
        return _build_codex_runtime(
            auth_mode=auth_mode,
            user_instruction=user_instruction,
            workflow=workflow,
            base_environment=base_environment,
            include_persisted_env=include_persisted_env,
            shared_bundle=shared_bundle,
        )
    raise AutoformalizeConfigError(f"Unsupported autoformalize backend: {backend_name}")


def _resolve_managed_chat_runtime(
    *,
    backend_name: str,
    user_instruction: str,
    base_environment: Mapping[str, str],
    active_cwd: Path,
) -> ManagedChatRuntime:
    if backend_name == DEFAULT_AUTOFORMALIZE_BACKEND:
        return _build_claude_chat_runtime(
            user_instruction=user_instruction,
            base_environment=base_environment,
            active_cwd=active_cwd,
        )
    if backend_name == CODEX_AUTOFORMALIZE_BACKEND:
        return _build_codex_chat_runtime(
            user_instruction=user_instruction,
            base_environment=base_environment,
            active_cwd=active_cwd,
        )
    raise AutoformalizeConfigError(f"Unsupported autoformalize backend: {backend_name}")


def _build_claude_runtime(
    *,
    auth_mode: str,
    user_instruction: str,
    workflow: ManagedWorkflowSpec,
    base_environment: Mapping[str, str],
    include_persisted_env: bool,
    shared_bundle: SharedLeanBundle,
) -> AutoformalizeBackendRuntime:
    claude_exe = _require_executable(
        "claude",
        "Claude Code CLI not found. Install it with `npm install -g @anthropic-ai/claude-code`.",
        base_environment,
    )
    real_home = shared_bundle.real_home
    has_local_login = _has_local_claude_login(real_home)
    has_local_api_key = _has_local_claude_api_key(real_home)
    resolved_auth_env = _resolve_claude_auth_env(
        base_environment,
        include_persisted_env=include_persisted_env,
    )
    copy_oauth_credentials = False
    copy_local_api_key = False
    strip_child_auth_env = False
    auth_env: dict[str, str] = {}

    if auth_mode == "auto":
        if has_local_login or has_local_api_key:
            copy_oauth_credentials = has_local_login
            copy_local_api_key = has_local_api_key
            strip_child_auth_env = True
        elif resolved_auth_env:
            auth_env = resolved_auth_env
        else:
            raise AutoformalizePreflightError(
                "Claude Code auth not found. Run `claude auth login`, save `ANTHROPIC_API_KEY`, "
                "or set `gauss.autoformalize.auth_mode: login` "
                "(or `GAUSS_AUTOFORMALIZE_AUTH_MODE=login`) to launch the normal Claude login flow."
            )
    elif auth_mode == "login":
        copy_oauth_credentials = has_local_login
        strip_child_auth_env = True
    else:
        strip_child_auth_env = True
        if resolved_auth_env:
            auth_env = resolved_auth_env
        elif has_local_api_key:
            copy_local_api_key = True
        else:
            raise AutoformalizePreflightError(
                "Claude Code API-key auth not found. Save `ANTHROPIC_API_KEY`, "
                "`ANTHROPIC_TOKEN`, or `CLAUDE_CODE_OAUTH_TOKEN`, or switch "
                "`gauss.autoformalize.auth_mode` back to `auto` or `login`."
            )

    backend_home = shared_bundle.managed_root / "claude-home"
    backend_config_path = backend_home / ".claude.json"
    mcp_config_path = shared_bundle.mcp_dir / "lean-lsp.mcp.json"
    marketplace_source = shared_bundle.assets_root / "lean4-skills"
    for path in (backend_home, mcp_config_path.parent):
        path.mkdir(parents=True, exist_ok=True)

    mcp_server = _managed_claude_mcp_server_payload(
        uv_runner=shared_bundle.uv_runner,
        lean_root=shared_bundle.lean_root,
    )
    _write_mcp_config(
        mcp_config_path=mcp_config_path,
        uv_runner=shared_bundle.uv_runner,
        lean_root=shared_bundle.lean_root,
    )
    plugin_root = _sync_prewarmed_claude_plugin(
        real_home=real_home,
        backend_home=backend_home,
    )
    if plugin_root is None:
        plugin_root = _install_managed_claude_plugin(
            claude_executable=claude_exe,
            backend_home=backend_home,
            base_environment=base_environment,
            marketplace_source=marketplace_source,
            plugin_source=shared_bundle.plugin_source,
        )
    skills_root = plugin_root / "skills" / "lean4"
    _stage_claude_credentials(
        real_home=real_home,
        claude_home=backend_home,
        auth_env=auth_env,
        copy_oauth_credentials=copy_oauth_credentials,
        copy_local_api_key=copy_local_api_key,
        mcp_servers={"lean-lsp": mcp_server},
    )
    startup_context_path = _write_startup_context(
        startup_dir=shared_bundle.startup_dir,
        backend_name=shared_bundle.backend_name,
        project_root=shared_bundle.project_root,
        lean_root=shared_bundle.lean_root,
        active_cwd=shared_bundle.active_cwd,
        user_instruction=user_instruction,
        workflow=workflow,
        plugin_root=plugin_root,
        mcp_config_path=mcp_config_path,
        backend_config_path=backend_config_path,
        skills_root=skills_root,
    )

    managed_context = ManagedContext(
        backend_name=shared_bundle.backend_name,
        managed_root=shared_bundle.managed_root,
        project_root=shared_bundle.project_root,
        lean_root=shared_bundle.lean_root,
        backend_home=backend_home,
        plugin_root=plugin_root,
        mcp_config_path=mcp_config_path,
        startup_context_path=startup_context_path,
        assets_root=shared_bundle.assets_root,
        project_manifest_path=shared_bundle.project.manifest_path,
        backend_config_path=backend_config_path,
        skills_root=skills_root,
    )

    child_env = dict(base_environment)
    if strip_child_auth_env:
        for key in CLAUDE_AUTH_ENV_KEYS:
            child_env.pop(key, None)
    child_env.update(auth_env)
    child_env.update(
        _base_child_env(
            managed_context=managed_context,
            real_home=real_home,
        )
    )
    child_env.update(
        {
            "HOME": str(backend_home),
            "CLAUDE_PLUGIN_ROOT": str(plugin_root),
            "LEAN4_PLUGIN_ROOT": str(plugin_root),
            "LEAN4_SCRIPTS": str(plugin_root / "lib" / "scripts"),
            "LEAN4_REFS": str(skills_root / "references"),
            "GAUSS_YOLO_MODE": "1",
        }
    )
    if startup_context_path is not None:
        child_env["GAUSS_AUTOFORMALIZE_CONTEXT"] = str(startup_context_path)

    _ensure_project_tool_permissions(shared_bundle.project_root)

    argv = [
        claude_exe,
        "--model",
        CLAUDE_MODEL,
    ]
    argv.extend(_claude_permission_args())
    startup_prompt = _build_startup_prompt(
        managed_context,
        workflow=workflow,
        user_instruction=user_instruction,
    )
    if startup_prompt:
        argv.append(startup_prompt)

    return AutoformalizeBackendRuntime(
        argv=argv,
        child_env=child_env,
        managed_context=managed_context,
    )


def _managed_chat_backend_label(backend_name: str) -> str:
    if backend_name == DEFAULT_AUTOFORMALIZE_BACKEND:
        return "Claude Code"
    if backend_name == CODEX_AUTOFORMALIZE_BACKEND:
        return "Codex"
    return backend_name


def _build_managed_chat_prompt(
    *,
    backend_name: str,
    active_cwd: Path,
    user_instruction: str,
) -> str:
    backend_label = _managed_chat_backend_label(backend_name)
    prompt_parts = [
        f"You are {backend_label} in a Gauss-managed interactive chat session.",
        "The user launched `/chat` from the main Gauss CLI and will return there when this session exits.",
        f"Current working directory: {active_cwd}.",
        "Use this session for onboarding, planning, repository questions, and general discussion before a specific Lean workflow is selected.",
        "Use any skills and MCP tools that are already configured in this backend session when they are helpful.",
        "If the user is ready to work in Lean, tell them to return to the main Gauss session and use `/project init`, `/project use`, or `/project create`, then `/prove`, `/review`, `/draft`, `/autoprove`, `/formalize`, or `/autoformalize`.",
    ]
    normalized_instruction = user_instruction.strip()
    if normalized_instruction:
        prompt_parts.append(f"Initial user request: {normalized_instruction}")
    else:
        prompt_parts.append("Start by asking what the user wants to do in Open Gauss.")
    return " ".join(prompt_parts)


def _build_claude_chat_runtime(
    *,
    user_instruction: str,
    base_environment: Mapping[str, str],
    active_cwd: Path,
) -> ManagedChatRuntime:
    claude_exe = _require_executable(
        "claude",
        "Claude Code CLI not found. Install it with `npm install -g @anthropic-ai/claude-code`.",
        base_environment,
    )
    child_env = dict(base_environment)
    child_env.update(
        {
            "GAUSS_MANAGED_CHAT": "1",
            "GAUSS_MANAGED_CHAT_BACKEND": DEFAULT_AUTOFORMALIZE_BACKEND,
            "GAUSS_CHAT_CWD": str(active_cwd),
            "GAUSS_YOLO_MODE": "1",
        }
    )
    argv = [claude_exe]
    prompt = _build_managed_chat_prompt(
        backend_name=DEFAULT_AUTOFORMALIZE_BACKEND,
        active_cwd=active_cwd,
        user_instruction=user_instruction,
    )
    if prompt:
        argv.append(prompt)
    return ManagedChatRuntime(
        argv=argv,
        child_env=child_env,
        backend_name=DEFAULT_AUTOFORMALIZE_BACKEND,
    )


def _build_codex_runtime(
    *,
    auth_mode: str,
    user_instruction: str,
    workflow: ManagedWorkflowSpec,
    base_environment: Mapping[str, str],
    include_persisted_env: bool,
    shared_bundle: SharedLeanBundle,
) -> AutoformalizeBackendRuntime:
    codex_exe = _require_executable(
        "codex",
        "Codex CLI not found. Install the OpenAI Codex CLI and try again.",
        base_environment,
    )
    real_home = shared_bundle.real_home
    local_auth_path = _local_codex_auth_path(real_home, base_environment)
    local_auth_payload = _load_local_codex_auth_payload(real_home, base_environment)
    has_local_auth = _codex_auth_payload_is_valid(local_auth_payload)
    has_local_api_key = _codex_auth_payload_has_api_key(local_auth_payload)
    openai_api_key = _resolve_codex_api_key(
        base_environment,
        include_persisted_env=include_persisted_env,
    )
    copy_local_auth = False
    staged_api_key = ""

    if auth_mode == "auto":
        if has_local_auth:
            copy_local_auth = True
        elif openai_api_key:
            staged_api_key = openai_api_key
        else:
            raise AutoformalizePreflightError(
                "Codex auth not found. Run `codex login`, save `OPENAI_API_KEY`, "
                "or set `gauss.autoformalize.auth_mode: login` "
                "(or `GAUSS_AUTOFORMALIZE_AUTH_MODE=login`) to launch the normal Codex login flow."
            )
    elif auth_mode == "login":
        copy_local_auth = has_local_auth
    else:
        if openai_api_key:
            staged_api_key = openai_api_key
        elif has_local_api_key:
            copy_local_auth = True
        else:
            raise AutoformalizePreflightError(
                "Codex API-key auth not found. Save `OPENAI_API_KEY`, or switch "
                "`gauss.autoformalize.auth_mode` back to `auto` or `login`."
            )

    backend_home = shared_bundle.managed_root / "codex-home"
    codex_home = backend_home / ".codex"
    skills_root = backend_home / ".agents" / "skills" / "lean4"
    codex_config_path = codex_home / "config.toml"
    instructions_path = codex_home / "gauss-autoformalize-instructions.md"
    for path in (backend_home, codex_home, skills_root.parent):
        path.mkdir(parents=True, exist_ok=True)

    _stage_tree(
        source=shared_bundle.skill_source,
        destination=skills_root,
        revision=shared_bundle.skill_revision,
    )
    _stage_codex_auth(
        codex_home=codex_home,
        source_auth_path=local_auth_path if copy_local_auth else None,
        api_key=staged_api_key,
    )

    startup_context_path = _write_startup_context(
        startup_dir=shared_bundle.startup_dir,
        backend_name=shared_bundle.backend_name,
        project_root=shared_bundle.project_root,
        lean_root=shared_bundle.lean_root,
        active_cwd=shared_bundle.active_cwd,
        user_instruction=user_instruction,
        workflow=workflow,
        plugin_root=shared_bundle.plugin_source,
        mcp_config_path=codex_config_path,
        backend_config_path=codex_config_path,
        skills_root=skills_root,
    )
    _write_codex_instructions(
        instructions_path=instructions_path,
        startup_context_path=startup_context_path,
        project_root=shared_bundle.project_root,
        lean_root=shared_bundle.lean_root,
        active_cwd=shared_bundle.active_cwd,
        skills_root=skills_root,
        plugin_root=shared_bundle.plugin_source,
        scripts_root=shared_bundle.scripts_root,
        references_root=skills_root / "references",
        workflow=workflow,
    )
    _write_codex_config(
        config_path=codex_config_path,
        instructions_path=instructions_path,
        uv_runner=shared_bundle.uv_runner,
        lean_root=shared_bundle.lean_root,
    )

    managed_context = ManagedContext(
        backend_name=shared_bundle.backend_name,
        managed_root=shared_bundle.managed_root,
        project_root=shared_bundle.project_root,
        lean_root=shared_bundle.lean_root,
        backend_home=backend_home,
        plugin_root=shared_bundle.plugin_source,
        mcp_config_path=codex_config_path,
        startup_context_path=startup_context_path,
        assets_root=shared_bundle.assets_root,
        project_manifest_path=shared_bundle.project.manifest_path,
        backend_config_path=codex_config_path,
        skills_root=skills_root,
        instructions_path=instructions_path,
    )

    child_env = dict(base_environment)
    for key in CODEX_AUTH_ENV_KEYS:
        child_env.pop(key, None)
    child_env.update(
        _base_child_env(
            managed_context=managed_context,
            real_home=real_home,
        )
    )
    child_env.update(
        {
            "HOME": str(backend_home),
            "CODEX_HOME": str(codex_home),
            "LEAN4_PLUGIN_ROOT": str(shared_bundle.plugin_source),
            "LEAN4_SCRIPTS": str(shared_bundle.scripts_root),
            "LEAN4_REFS": str(skills_root / "references"),
            "GAUSS_AUTOFORMALIZE_SKILLS_ROOT": str(skills_root),
        }
    )
    if startup_context_path is not None:
        child_env["GAUSS_AUTOFORMALIZE_CONTEXT"] = str(startup_context_path)
    child_env["GAUSS_AUTOFORMALIZE_INSTRUCTIONS"] = str(instructions_path)

    argv = [
        codex_exe,
        "--dangerously-bypass-approvals-and-sandbox",
    ]
    startup_prompt = _build_startup_prompt(
        managed_context,
        workflow=workflow,
        user_instruction=user_instruction,
    )
    if startup_prompt:
        argv.append(startup_prompt)

    return AutoformalizeBackendRuntime(
        argv=argv,
        child_env=child_env,
        managed_context=managed_context,
    )


def _build_codex_chat_runtime(
    *,
    user_instruction: str,
    base_environment: Mapping[str, str],
    active_cwd: Path,
) -> ManagedChatRuntime:
    codex_exe = _require_executable(
        "codex",
        "Codex CLI not found. Install the OpenAI Codex CLI and try again.",
        base_environment,
    )
    child_env = dict(base_environment)
    child_env.update(
        {
            "GAUSS_MANAGED_CHAT": "1",
            "GAUSS_MANAGED_CHAT_BACKEND": CODEX_AUTOFORMALIZE_BACKEND,
            "GAUSS_CHAT_CWD": str(active_cwd),
            "GAUSS_YOLO_MODE": "1",
        }
    )
    argv = [
        codex_exe,
        "--dangerously-bypass-approvals-and-sandbox",
    ]
    prompt = _build_managed_chat_prompt(
        backend_name=CODEX_AUTOFORMALIZE_BACKEND,
        active_cwd=active_cwd,
        user_instruction=user_instruction,
    )
    if prompt:
        argv.append(prompt)
    return ManagedChatRuntime(
        argv=argv,
        child_env=child_env,
        backend_name=CODEX_AUTOFORMALIZE_BACKEND,
    )


def _base_child_env(
    *,
    managed_context: ManagedContext,
    real_home: Path,
) -> dict[str, str]:
    env = {
        "GAUSS_AUTOFORMALIZE": "1",
        "GAUSS_AUTOFORMALIZE_BACKEND": managed_context.backend_name,
        "GAUSS_PROJECT_ROOT": str(managed_context.project_root),
        "GAUSS_PROJECT_MANIFEST": (
            str(managed_context.project_manifest_path)
            if managed_context.project_manifest_path is not None
            else ""
        ),
        "LEAN_PROJECT_PATH": str(managed_context.lean_root),
        "GAUSS_MANAGED_STATE_DIR": str(managed_context.managed_root),
        "GAUSS_REAL_HOME": str(real_home),
    }
    if managed_context.skills_root is not None:
        env["GAUSS_AUTOFORMALIZE_SKILLS_ROOT"] = str(managed_context.skills_root)
    return env


def _managed_root(managed_state_base: Path, backend_name: str) -> Path:
    default_root = (get_gauss_home() / "autoformalize").expanduser().resolve()
    backend_root = managed_state_base / backend_name / "managed"
    legacy_root = default_root / "managed"
    if (
        backend_name == DEFAULT_AUTOFORMALIZE_BACKEND
        and managed_state_base == default_root
        and legacy_root.exists()
        and not backend_root.exists()
    ):
        return legacy_root
    return backend_root


def _ensure_git_checkout(
    *,
    repo_url: str,
    revision: str | None,
    destination: Path,
    git_executable: str,
) -> str:
    if destination.exists() and not destination.is_dir():
        raise AutoformalizeStagingError(f"Managed asset path is not a directory: {destination}")

    if (destination / ".git").exists():
        fetch_target = revision or "HEAD"
        _run(
            [git_executable, "-C", str(destination), "fetch", "--depth", "1", "origin", fetch_target],
            error_prefix="Failed to refresh managed asset checkout",
        )
        _run(
            [git_executable, "-C", str(destination), "checkout", "--force", "FETCH_HEAD"],
            error_prefix="Failed to check out the managed asset revision",
        )
    else:
        _remove_existing_path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if revision:
            _run(
                [git_executable, "clone", "--filter=blob:none", "--no-checkout", repo_url, str(destination)],
                error_prefix=f"Failed to clone managed asset from {repo_url}",
            )
            _run(
                [git_executable, "-C", str(destination), "fetch", "--depth", "1", "origin", revision],
                error_prefix="Failed to fetch the managed asset revision",
            )
            _run(
                [git_executable, "-C", str(destination), "checkout", "--force", "FETCH_HEAD"],
                error_prefix="Failed to check out the managed asset revision",
            )
        else:
            _run(
                [git_executable, "clone", "--depth", "1", "--filter=blob:none", repo_url, str(destination)],
                error_prefix=f"Failed to clone managed asset from {repo_url}",
            )

    return _run(
        [git_executable, "-C", str(destination), "rev-parse", "HEAD"],
        error_prefix="Failed to read managed asset revision",
    ).stdout.strip()


def _run(
    argv: Sequence[str],
    *,
    error_prefix: str,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            check=True,
            shell=False,
            env=dict(env) if env is not None else None,
            cwd=str(cwd) if cwd is not None else None,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or exc.stdout or "").strip()
        suffix = f": {stderr}" if stderr else ""
        raise AutoformalizeStagingError(f"{error_prefix}{suffix}") from exc
    except OSError as exc:
        raise AutoformalizeStagingError(f"{error_prefix}: {exc}") from exc
    return result


def _stage_tree(*, source: Path, destination: Path, revision: str) -> None:
    revision_file = destination / ".gauss-managed-revision"
    if revision_file.exists() and revision_file.read_text(encoding="utf-8").strip() == revision:
        return

    _remove_existing_path(destination)
    shutil.copytree(source, destination)
    revision_file.write_text(f"{revision}\n", encoding="utf-8")


def _stage_claude_credentials(
    *,
    real_home: Path,
    claude_home: Path,
    auth_env: Mapping[str, str],
    copy_oauth_credentials: bool = True,
    copy_local_api_key: bool = True,
    mcp_servers: Mapping[str, Any] | None = None,
) -> None:
    managed_claude_dir = claude_home / ".claude"
    managed_claude_dir.mkdir(parents=True, exist_ok=True)
    managed_key_path = claude_home / ".claude.json"
    source_credentials = real_home / ".claude" / ".credentials.json"
    destination_credentials = managed_claude_dir / ".credentials.json"
    if destination_credentials.exists():
        destination_credentials.unlink()
    if copy_oauth_credentials:
        if source_credentials.exists():
            shutil.copy2(source_credentials, destination_credentials)
        else:
            keychain_payload = _read_keychain_claude_credentials()
            if keychain_payload:
                destination_credentials.write_text(keychain_payload, encoding="utf-8")

    payload = _load_json_dict(managed_key_path)
    payload.update(_load_local_claude_config(real_home))
    if not copy_local_api_key:
        payload.pop("primaryApiKey", None)

    api_key = str(auth_env.get("ANTHROPIC_API_KEY", "") or "").strip()
    if api_key:
        payload["primaryApiKey"] = api_key
    if mcp_servers:
        existing_servers = payload.get("mcpServers")
        merged_servers = dict(existing_servers) if isinstance(existing_servers, dict) else {}
        for name, server_payload in mcp_servers.items():
            if isinstance(server_payload, Mapping):
                merged_servers[str(name)] = dict(server_payload)
        payload["mcpServers"] = merged_servers
    payload.setdefault("theme", DEFAULT_MANAGED_CLAUDE_THEME)
    payload["hasCompletedOnboarding"] = True
    managed_key_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    # Pre-approve all tools so managed sessions never prompt for permissions.
    settings_local = managed_claude_dir / "settings.local.json"
    try:
        settings_data = json.loads(settings_local.read_text(encoding="utf-8")) if settings_local.exists() else {}
    except (json.JSONDecodeError, OSError):
        settings_data = {}
    permissions = settings_data.setdefault("permissions", {})
    allow = permissions.setdefault("allow", [])
    required_tools = [
        "Read", "Write", "Edit", "Bash(*)", "WebSearch", "WebFetch",
        "mcp__*", "Bash",
    ]
    for tool in required_tools:
        if tool not in allow:
            allow.append(tool)
    settings_local.write_text(json.dumps(settings_data, indent=2) + "\n", encoding="utf-8")


def _stage_codex_auth(
    *,
    codex_home: Path,
    source_auth_path: Path | None,
    api_key: str,
) -> None:
    codex_home.mkdir(parents=True, exist_ok=True)
    destination = codex_home / "auth.json"
    if destination.exists():
        destination.unlink()
    if source_auth_path is not None and source_auth_path.is_file():
        shutil.copy2(source_auth_path, destination)
        return
    if api_key:
        payload = {
            "auth_mode": "apikey",
            "OPENAI_API_KEY": api_key,
        }
        destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _managed_claude_mcp_server_payload(
    *,
    uv_runner: Sequence[str],
    lean_root: Path,
) -> dict[str, Any]:
    if len(uv_runner) < 2:
        raise AutoformalizeStagingError("Invalid uv runner configuration for managed Lean MCP server.")
    return {
        "type": "stdio",
        "command": uv_runner[0],
        "args": list(uv_runner[1:]),
        "env": {
            "LEAN_PROJECT_PATH": str(lean_root),
        },
    }


def _write_mcp_config(
    *,
    mcp_config_path: Path,
    uv_runner: Sequence[str],
    lean_root: Path,
) -> None:
    payload = {
        "mcpServers": {
            "lean-lsp": _managed_claude_mcp_server_payload(
                uv_runner=uv_runner,
                lean_root=lean_root,
            )
        }
    }
    mcp_config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _install_managed_claude_plugin(
    *,
    claude_executable: str,
    backend_home: Path,
    base_environment: Mapping[str, str],
    marketplace_source: Path,
    plugin_source: Path,
) -> Path:
    marketplace_name = _read_claude_marketplace_name(marketplace_source)
    plugin_name, _plugin_version = _read_claude_plugin_identity(plugin_source)
    plugin_id = f"{plugin_name}@{marketplace_name}"

    plugin_state_root = backend_home / ".claude" / "plugins"
    _remove_existing_path(plugin_state_root)

    cli_env = dict(base_environment)
    cli_env["HOME"] = str(backend_home)

    _add_claude_marketplace(
        claude_executable=claude_executable,
        cli_env=cli_env,
        marketplace_target=str(marketplace_source),
        error_prefix="Failed to register the managed Lean Claude marketplace",
    )
    _install_claude_plugin_target(
        claude_executable=claude_executable,
        cli_env=cli_env,
        plugin_name=plugin_name,
        plugin_id=plugin_id,
        error_prefix="Failed to install the managed Lean Claude plugin",
    )
    result = _run(
        [claude_executable, "plugin", "list", "--json"],
        env=cli_env,
        error_prefix="Failed to inspect the managed Lean Claude plugin state",
    )
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise AutoformalizeStagingError(
            "Failed to parse the managed Lean Claude plugin state."
        ) from exc
    if not isinstance(payload, list):
        raise AutoformalizeStagingError("Managed Lean Claude plugin state is not a JSON list.")

    for entry in payload:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("id", "")).strip() != plugin_id:
            continue
        install_path = Path(str(entry.get("installPath", "")).strip()).expanduser()
        if not install_path.exists():
            raise AutoformalizeStagingError(
                f"Managed Lean Claude plugin install path does not exist: {install_path}"
            )
        return install_path.resolve()

    raise AutoformalizeStagingError(
        f"Managed Lean Claude plugin is not installed after configuration: {plugin_id}"
    )


def _arxiv_search_script() -> str | None:
    """Return the absolute path to the bundled arXiv search script, if available."""
    candidate = Path(__file__).resolve().parent.parent / "skills" / "research" / "arxiv" / "scripts" / "search_arxiv.py"
    if candidate.is_file():
        return str(candidate)
    return None


def _managed_workflow_doc_path(plugin_root: Path, workflow_kind: str) -> Path | None:
    """Return the managed workflow markdown doc for the requested workflow, if present."""
    candidate = plugin_root / "commands" / f"{workflow_kind}.md"
    if candidate.is_file():
        return candidate
    return None


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _write_codex_config(
    *,
    config_path: Path,
    instructions_path: Path,
    uv_runner: Sequence[str],
    lean_root: Path,
) -> None:
    if len(uv_runner) < 2:
        raise AutoformalizeStagingError("Invalid uv runner configuration for managed Lean MCP server.")
    lines = [
        f"model_instructions_file = {_toml_string(str(instructions_path))}",
        "",
        "[mcp_servers.lean-lsp]",
        f"command = {_toml_string(uv_runner[0])}",
        f"args = [{', '.join(_toml_string(arg) for arg in uv_runner[1:])}]",
        "",
        "[mcp_servers.lean-lsp.env]",
        f"LEAN_PROJECT_PATH = {_toml_string(str(lean_root))}",
        "",
    ]
    config_path.write_text("\n".join(lines), encoding="utf-8")


def _write_codex_instructions(
    *,
    instructions_path: Path,
    startup_context_path: Path | None,
    project_root: Path,
    lean_root: Path,
    active_cwd: Path,
    skills_root: Path,
    plugin_root: Path,
    scripts_root: Path,
    references_root: Path,
    workflow: ManagedWorkflowSpec | None = None,
) -> None:
    skill_doc_path = skills_root / "SKILL.md"
    workflow_doc_path = (
        _managed_workflow_doc_path(plugin_root, workflow.workflow_kind)
        if workflow is not None
        else None
    )
    lines = [
        "# Gauss Managed Lean Workflow Instructions",
        "",
        "You are in a Gauss-managed Lean workflow session.",
        "",
        f"- Project root: `{project_root}`",
        f"- Lean root: `{lean_root}`",
        f"- Active working directory: `{active_cwd}`",
        f"- Installed Lean4 skill: `{skills_root}`",
        f"- Lean4 plugin root: `{plugin_root}`",
        f"- Lean4 scripts: `{scripts_root}`",
        f"- Lean4 references: `{references_root}`",
    ]
    if startup_context_path is not None:
        lines.append(f"- Startup context: `{startup_context_path}`")
    lines.extend(
        [
            "",
            "## Codex Skill Contract",
            "- The staged Lean workflow is exposed as the `$lean4` skill.",
            f"- Skill entrypoint: `{skill_doc_path}`",
        ]
    )
    if workflow_doc_path is not None:
        lines.append(f"- Requested workflow guide: `{workflow_doc_path}`")
    lines.extend(
        [
            "- `/lean4:*` names in the startup context are workflow labels from the skill docs, not shell commands.",
            "- Start by invoking `$lean4`, then follow the matching workflow guide before running project commands.",
            "",
            "## Session Contract",
            "- Work inside the current Lean project.",
            "- Use the installed `lean4` skill when you need proving workflow guidance.",
            "- Prefer Lean/LSP-first workflows and use the `lean-lsp` MCP server for navigation and diagnostics.",
            "- Keep changes reproducible and explain blockers clearly if the formalization cannot proceed.",
            "",
        ]
    )
    instructions_path.write_text("\n".join(lines), encoding="utf-8")


def _write_startup_context(
    *,
    startup_dir: Path,
    backend_name: str,
    project_root: Path,
    lean_root: Path,
    active_cwd: Path,
    user_instruction: str,
    workflow: ManagedWorkflowSpec,
    plugin_root: Path,
    mcp_config_path: Path,
    backend_config_path: Path | None = None,
    skills_root: Path | None = None,
) -> Path | None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = startup_dir / f"{stamp}-{workflow.workflow_kind}.md"
    workflow_doc_path = _managed_workflow_doc_path(plugin_root, workflow.workflow_kind)
    lines = [
        "# Gauss Managed Lean Workflow Session",
        "",
        f"- Managed backend: `{backend_name}`",
        f"- Gauss command: `{workflow.canonical_command}`",
        f"- Backend command: `{workflow.backend_command}`",
        f"- Workflow kind: `{workflow.workflow_kind}`",
        f"- Project root: `{project_root}`",
        f"- Lean root: `{lean_root}`",
        f"- Active working directory: `{active_cwd}`",
        f"- Managed Lean asset root: `{plugin_root}`",
    ]
    if workflow_doc_path is not None:
        lines.append(f"- Managed Lean workflow guide: `{workflow_doc_path}`")
    if skills_root is not None:
        lines.append(f"- Managed Lean skill root: `{skills_root}`")
    if backend_config_path is not None and backend_config_path == mcp_config_path:
        lines.append(f"- Managed backend + MCP config: `{mcp_config_path}`")
    else:
        if backend_config_path is not None:
            lines.append(f"- Managed backend config: `{backend_config_path}`")
        lines.append(f"- Managed Lean MCP config: `{mcp_config_path}`")
    lines.extend(
        [
            "",
            "## Workflow Request",
            workflow.backend_command,
            "",
        ]
    )
    if backend_name == CODEX_AUTOFORMALIZE_BACKEND:
        lines.extend(
            [
                "## Codex Skill Notes",
                "- In Codex, the `/lean4:*` workflow request is a skill-level workflow name, not a shell executable.",
                "- Invoke `$lean4` explicitly before following this workflow.",
                "",
            ]
        )
    lines.extend(
        [
            "## Session Contract",
            "- Work inside the current Lean project.",
            "- Prefer Lean/LSP-first workflows and use the managed Lean MCP server.",
            (
                "- Run the managed backend workflow command exactly as requested before improvising."
                if backend_name != CODEX_AUTOFORMALIZE_BACKEND
                else "- Treat the managed backend workflow command as the requested workflow contract and follow it through the `$lean4` skill."
            ),
            "- Keep changes reproducible and explain blockers clearly if the workflow cannot proceed.",
            "",
        ]
    )
    if user_instruction:
        lines.extend(
            [
                "## Forwarded Arguments",
                user_instruction.strip(),
                "",
            ]
        )
    arxiv_script = _arxiv_search_script()
    lines.extend(
        [
            "## arXiv Search",
            "",
            "You have access to arXiv paper search. Use this when the user references a",
            "paper, theorem, or result you need to look up before formalizing.",
            "",
        ]
    )
    if arxiv_script:
        lines.extend(
            [
                f"Bundled helper script: `{arxiv_script}`",
                "",
                "```bash",
                f'python3 "{arxiv_script}" "sphere packing"              # keyword search',
                f'python3 "{arxiv_script}" --author "Terence Tao"        # author search',
                f'python3 "{arxiv_script}" --category math.CO --sort date  # category',
                f'python3 "{arxiv_script}" --id 2402.03300               # fetch by ID',
                "```",
                "",
            ]
        )
    lines.extend(
        [
            "Direct API (no dependencies):",
            "",
            "```bash",
            'curl -s "https://export.arxiv.org/api/query?search_query=all:QUERY&max_results=5"',
            "```",
            "",
            "To read a paper abstract: fetch `https://arxiv.org/abs/<ID>`",
            "To read a full paper PDF: fetch `https://arxiv.org/pdf/<ID>`",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _build_startup_prompt(
    managed_context: ManagedContext,
    *,
    workflow: ManagedWorkflowSpec,
    user_instruction: str,
) -> str | None:
    if managed_context.startup_context_path is None:
        return None
    quoted_context = shlex.quote(str(managed_context.startup_context_path))
    if managed_context.backend_name == CODEX_AUTOFORMALIZE_BACKEND:
        prompt_parts = [
            "You are in a Gauss-managed Lean workflow session.",
            f"Read the startup context at {quoted_context} first.",
            "Then explicitly invoke the `$lean4` skill and follow the requested Lean workflow in the active project.",
            "Important: `/lean4:*` entries in the startup context are skill workflow names, not shell commands, so do not try to execute them in bash.",
        ]
        skill_doc_path = (
            managed_context.skills_root / "SKILL.md"
            if managed_context.skills_root is not None
            else None
        )
        if skill_doc_path is not None and skill_doc_path.is_file():
            prompt_parts.append(
                f"The staged skill entrypoint is {shlex.quote(str(skill_doc_path))}."
            )
        workflow_doc_path = _managed_workflow_doc_path(
            managed_context.plugin_root,
            workflow.workflow_kind,
        )
        if workflow_doc_path is not None:
            prompt_parts.append(
                f"The matching workflow guide is {shlex.quote(str(workflow_doc_path))}."
            )
        return " ".join(prompt_parts)
    return (
        "You are in a Gauss-managed Lean workflow session. "
        f"Read the startup context at {quoted_context} first. "
        f"Then run this command inside the active project as your first workflow action: {workflow.backend_command}"
    )
