"""Tests for the managed Gauss Lean workflow launcher."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import gauss_cli.autoformalize as autoformalize
from gauss_cli.project import initialize_gauss_project


def _config(
    *,
    mode: str = "auto",
    auth_mode: str = "auto",
    backend: str = "claude-code",
) -> dict:
    return {
        "gauss": {
            "autoformalize": {
                "backend": backend,
                "handoff_mode": mode,
                "auth_mode": auth_mode,
            }
        }
    }


def _workflow(command: str, args: str = "") -> autoformalize.ManagedWorkflowSpec:
    text = command if not args else f"{command} {args}"
    return autoformalize._parse_managed_workflow_command(text)


def _init_project(tmp_path: Path) -> tuple[autoformalize.GaussProject, Path]:
    project_root = tmp_path / "project"
    active_cwd = project_root / "Math"
    active_cwd.mkdir(parents=True)
    (project_root / "lakefile.lean").write_text("-- lean project\n", encoding="utf-8")
    project = initialize_gauss_project(project_root, name="Demo Project")
    return project, active_cwd


def _shared_bundle(
    tmp_path: Path,
    *,
    backend_name: str = "claude-code",
) -> autoformalize.SharedLeanBundle:
    project, active_cwd = _init_project(tmp_path)
    real_home = tmp_path / "home"
    managed_root = tmp_path / backend_name / "managed"
    assets_root = tmp_path / "assets"
    startup_dir = managed_root / "startup"
    mcp_dir = managed_root / "mcp"
    plugin_source = assets_root / "lean4-skills" / "plugins" / "lean4"
    skill_source = plugin_source / "skills" / "lean4"
    scripts_root = plugin_source / "lib" / "scripts"
    references_root = skill_source / "references"
    commands_root = plugin_source / "commands"

    real_home.mkdir(parents=True)
    startup_dir.mkdir(parents=True)
    mcp_dir.mkdir(parents=True)
    scripts_root.mkdir(parents=True)
    references_root.mkdir(parents=True)
    commands_root.mkdir(parents=True)
    (skill_source / "SKILL.md").write_text("# Lean4\n", encoding="utf-8")
    (scripts_root / "prove.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (references_root / "README.md").write_text("refs\n", encoding="utf-8")
    for command_name in ("prove", "draft", "autoprove", "formalize", "autoformalize"):
        (commands_root / f"{command_name}.md").write_text(
            f"# {command_name}\n",
            encoding="utf-8",
        )

    return autoformalize.SharedLeanBundle(
        backend_name=backend_name,
        managed_root=managed_root,
        assets_root=assets_root,
        startup_dir=startup_dir,
        mcp_dir=mcp_dir,
        project=project,
        project_root=project.root,
        lean_root=project.lean_root,
        active_cwd=active_cwd,
        real_home=real_home,
        plugin_source=plugin_source,
        skill_source=skill_source,
        scripts_root=scripts_root,
        references_root=references_root,
        uv_runner=("/usr/bin/uvx", "--from", autoformalize.LEAN_LSP_MCP_SPEC, "lean-lsp-mcp"),
        skill_revision="lean4-test-revision",
    )


@pytest.mark.parametrize(
    ("command", "kind", "canonical", "backend"),
    [
        ("/prove File.lean", "prove", "/prove", "/lean4:prove File.lean"),
        ("/draft Theorem 3.2", "draft", "/draft", "/lean4:draft Theorem 3.2"),
        ("/autoprove --max-cycles=4", "autoprove", "/autoprove", "/lean4:autoprove --max-cycles=4"),
        ("/auto_proof Main.lean", "autoprove", "/autoprove", "/lean4:autoprove Main.lean"),
        ("/formalize --source ./paper.pdf", "formalize", "/formalize", "/lean4:formalize --source ./paper.pdf"),
        ("/autoformalize --source ./paper.pdf --claim-select=first --out=Paper.lean", "autoformalize", "/autoformalize", "/lean4:autoformalize --source ./paper.pdf --claim-select=first --out=Paper.lean"),
    ],
)
def test_parse_managed_workflow_command_normalizes_aliases(
    command: str,
    kind: str,
    canonical: str,
    backend: str,
):
    spec = autoformalize._parse_managed_workflow_command(command)

    assert spec.workflow_kind == kind
    assert spec.canonical_command == canonical
    assert spec.backend_command == backend


def test_write_mcp_config_uses_managed_mcp_servers_payload(tmp_path: Path):
    config_path = tmp_path / "lean-lsp.mcp.json"
    lean_root = tmp_path / "project"

    autoformalize._write_mcp_config(
        mcp_config_path=config_path,
        uv_runner=("/usr/bin/uvx", "--from", autoformalize.LEAN_LSP_MCP_SPEC, "lean-lsp-mcp"),
        lean_root=lean_root,
    )

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload == {
        "mcpServers": {
            "lean-lsp": {
                "type": "stdio",
                "command": "/usr/bin/uvx",
                "args": [
                    "--from",
                    autoformalize.LEAN_LSP_MCP_SPEC,
                    "lean-lsp-mcp",
                ],
                "env": {
                    "LEAN_PROJECT_PATH": str(lean_root),
                },
            }
        }
    }


def test_install_managed_claude_plugin_registers_marketplace_and_returns_install_path(monkeypatch, tmp_path: Path):
    backend_home = tmp_path / "claude-home"
    marketplace_source = tmp_path / "lean4-skills"
    plugin_source = marketplace_source / "plugins" / "lean4"
    install_path = backend_home / ".claude" / "plugins" / "cache" / "lean4-skills" / "lean4" / "4.4.0"
    (marketplace_source / ".claude-plugin").mkdir(parents=True)
    (plugin_source / ".claude-plugin").mkdir(parents=True)
    (marketplace_source / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"name": "lean4-skills"}),
        encoding="utf-8",
    )
    (plugin_source / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "lean4", "version": "4.4.0"}),
        encoding="utf-8",
    )

    calls: list[tuple[list[str], dict[str, str] | None]] = []

    def fake_run(argv, *, error_prefix, env=None, cwd=None):
        calls.append((list(argv), dict(env) if env is not None else None))
        stdout = ""
        if list(argv)[1:3] == ["plugin", "install"]:
            install_path.mkdir(parents=True, exist_ok=True)
        if list(argv)[-2:] == ["list", "--json"]:
            stdout = json.dumps(
                [
                    {
                        "id": "lean4@lean4-skills",
                        "installPath": str(install_path),
                    }
                ]
            )
        return SimpleNamespace(stdout=stdout)

    monkeypatch.setattr(autoformalize, "_run", fake_run)

    result = autoformalize._install_managed_claude_plugin(
        claude_executable="/usr/bin/claude",
        backend_home=backend_home,
        base_environment={"PATH": "/usr/bin"},
        marketplace_source=marketplace_source,
        plugin_source=plugin_source,
    )

    assert result == install_path.resolve()
    assert calls[0][0] == [
        "/usr/bin/claude",
        "plugin",
        "marketplace",
        "add",
        str(marketplace_source),
    ]
    assert calls[1][0] == [
        "/usr/bin/claude",
        "plugin",
        "install",
        "lean4",
    ]
    assert calls[2][0] == [
        "/usr/bin/claude",
        "plugin",
        "list",
        "--json",
    ]
    assert calls[0][1]["HOME"] == str(backend_home)


def test_resolve_uv_runner_uses_default_user_scoped_package(monkeypatch):
    monkeypatch.setattr(
        autoformalize.shutil,
        "which",
        lambda name, path=None: f"/usr/bin/{name}" if name == "uvx" else None,
    )

    runner = autoformalize._resolve_uv_runner({"PATH": "/usr/bin"})

    assert runner == ("/usr/bin/uvx", "--from", autoformalize.LEAN_LSP_MCP_SPEC, "lean-lsp-mcp")


def test_resolve_uv_runner_allows_package_override(monkeypatch):
    monkeypatch.setattr(
        autoformalize.shutil,
        "which",
        lambda name, path=None: f"/usr/bin/{name}" if name == "uvx" else None,
    )

    runner = autoformalize._resolve_uv_runner(
        {
            "PATH": "/usr/bin",
            autoformalize.LEAN_LSP_MCP_SPEC_ENV: "git+https://example.com/lean-lsp-mcp.git@abc123",
        }
    )

    assert runner == (
        "/usr/bin/uvx",
        "--from",
        "git+https://example.com/lean-lsp-mcp.git@abc123",
        "lean-lsp-mcp",
    )


def test_write_codex_config_includes_instructions_and_lean_root(tmp_path: Path):
    config_path = tmp_path / "config.toml"
    instructions_path = tmp_path / "instructions.md"
    lean_root = tmp_path / "project"
    instructions_path.write_text("# Instructions\n", encoding="utf-8")

    autoformalize._write_codex_config(
        config_path=config_path,
        instructions_path=instructions_path,
        uv_runner=("/usr/bin/uvx", "--from", autoformalize.LEAN_LSP_MCP_SPEC, "lean-lsp-mcp"),
        lean_root=lean_root,
    )

    content = config_path.read_text(encoding="utf-8")
    assert "model_instructions_file" in content
    assert str(instructions_path) in content
    assert "[mcp_servers.lean-lsp]" in content
    assert f'LEAN_PROJECT_PATH = "{lean_root}"' in content


def test_write_startup_context_writes_workflow_metadata_without_forwarded_args(tmp_path: Path):
    project, active_cwd = _init_project(tmp_path)

    path = autoformalize._write_startup_context(
        startup_dir=tmp_path,
        backend_name="claude-code",
        project_root=project.root,
        lean_root=project.lean_root,
        active_cwd=active_cwd,
        user_instruction="",
        workflow=_workflow("/formalize"),
        plugin_root=tmp_path / "plugin",
        mcp_config_path=tmp_path / "lean-lsp.mcp.json",
    )

    assert path is not None
    content = path.read_text(encoding="utf-8")
    assert "# Gauss Managed Lean Workflow Session" in content
    assert "/lean4:formalize" in content
    assert str(project.root) in content
    assert str(project.lean_root) in content
    assert "## Forwarded Arguments" not in content


def test_write_startup_context_includes_forwarded_arguments_and_arxiv(tmp_path: Path):
    project, active_cwd = _init_project(tmp_path)
    workflow = _workflow("/formalize", "Formalize Tao's result")
    plugin_root = tmp_path / "plugin"
    (plugin_root / "commands").mkdir(parents=True)
    (plugin_root / "commands" / "formalize.md").write_text("# formalize\n", encoding="utf-8")

    path = autoformalize._write_startup_context(
        startup_dir=tmp_path,
        backend_name="codex",
        project_root=project.root,
        lean_root=project.lean_root,
        active_cwd=active_cwd,
        user_instruction=workflow.workflow_args,
        workflow=workflow,
        plugin_root=plugin_root,
        mcp_config_path=tmp_path / "lean-lsp.mcp.json",
        backend_config_path=tmp_path / "config.toml",
        skills_root=tmp_path / "skills",
    )

    assert path is not None
    content = path.read_text(encoding="utf-8")
    assert "## Workflow Request" in content
    assert workflow.backend_command in content
    assert "## Forwarded Arguments" in content
    assert "Formalize Tao's result" in content
    assert "## Codex Skill Notes" in content
    assert "$lean4" in content
    assert "not a shell executable" in content
    assert str(plugin_root / "commands" / "formalize.md") in content
    assert "## arXiv Search" in content
    assert "export.arxiv.org" in content


def test_arxiv_search_script_resolves():
    result = autoformalize._arxiv_search_script()
    assert result is not None
    assert result.endswith("search_arxiv.py")


def test_resolve_backend_name_accepts_claude_and_codex_aliases():
    assert autoformalize._resolve_backend_name(_config(backend="claude_code"), {}) == "claude-code"
    assert autoformalize._resolve_backend_name(_config(backend="openai_codex"), {}) == "codex"
    assert autoformalize._resolve_backend_name(
        _config(),
        {"GAUSS_AUTOFORMALIZE_BACKEND": "openai-codex"},
    ) == "codex"


def test_resolve_backend_name_rejects_unknown_backend():
    with pytest.raises(autoformalize.AutoformalizeConfigError, match="gauss.autoformalize.backend"):
        autoformalize._resolve_backend_name(_config(backend="not-a-backend"), {})


def test_resolve_autoformalize_request_builds_managed_launch_plan(monkeypatch, tmp_path: Path):
    shared_bundle = _shared_bundle(tmp_path)
    managed_context = autoformalize.ManagedContext(
        backend_name="claude-code",
        managed_root=tmp_path / "managed",
        project_root=shared_bundle.project.root,
        lean_root=shared_bundle.project.lean_root,
        backend_home=tmp_path / "managed" / "claude-home",
        plugin_root=tmp_path / "managed" / "claude-home" / ".claude" / "plugins" / "lean4",
        mcp_config_path=tmp_path / "managed" / "mcp" / "lean-lsp.mcp.json",
        startup_context_path=tmp_path / "managed" / "startup" / "context.md",
        assets_root=tmp_path / "assets",
        project_manifest_path=shared_bundle.project.manifest_path,
        backend_config_path=tmp_path / "managed" / "claude-home" / ".claude.json",
    )
    runtime = autoformalize.AutoformalizeBackendRuntime(
        argv=["/usr/bin/claude", "--model", autoformalize.CLAUDE_MODEL],
        child_env={"HOME": str(managed_context.backend_home), "PATH": "/usr/bin"},
        managed_context=managed_context,
    )

    captured: dict[str, object] = {}

    def fake_build_handoff_request(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(autoformalize, "_require_executable", lambda name, _msg, _env: f"/usr/bin/{name}")
    monkeypatch.setattr(
        autoformalize,
        "_resolve_uv_runner",
        lambda _env: ("/usr/bin/uvx", "--from", autoformalize.LEAN_LSP_MCP_SPEC, "lean-lsp-mcp"),
    )
    monkeypatch.setattr(autoformalize, "_prepare_shared_bundle", lambda **_kwargs: shared_bundle)
    monkeypatch.setattr(autoformalize, "_resolve_backend_runtime", lambda **_kwargs: runtime)
    monkeypatch.setattr(autoformalize, "build_handoff_request", fake_build_handoff_request)

    plan = autoformalize.resolve_autoformalize_request(
        "/prove --repair-only",
        _config(mode="helper"),
        active_cwd=str(shared_bundle.project.root),
        base_env={"HOME": str(tmp_path / "home"), "PATH": "/usr/bin"},
    )

    assert plan.user_instruction == "--repair-only"
    assert plan.project == shared_bundle.project
    assert plan.workflow_kind == "prove"
    assert plan.frontend_command == "/prove"
    assert plan.canonical_command == "/prove"
    assert plan.backend_command == "/lean4:prove --repair-only"
    assert plan.managed_context == managed_context
    assert captured["argv"] == runtime.argv
    assert captured["env"] == runtime.child_env
    assert captured["cwd"] == str(shared_bundle.project.root)
    assert captured["requested_mode"] == "helper"
    assert captured["label"] == "Gauss prove session"
    assert captured["source"] == "gauss:prove"


def test_resolve_autoformalize_request_requires_active_gauss_project(monkeypatch, tmp_path: Path):
    missing_project_root = tmp_path / "no-project"
    missing_project_root.mkdir()

    monkeypatch.setattr(autoformalize, "_require_executable", lambda name, _msg, _env: f"/usr/bin/{name}")
    monkeypatch.setattr(
        autoformalize,
        "_resolve_uv_runner",
        lambda _env: ("/usr/bin/uvx", "--from", autoformalize.LEAN_LSP_MCP_SPEC, "lean-lsp-mcp"),
    )

    with pytest.raises(autoformalize.AutoformalizePreflightError, match="Run `/project init`"):
        autoformalize.resolve_autoformalize_request(
            "/prove",
            _config(),
            active_cwd=str(missing_project_root),
            base_env={"HOME": str(tmp_path / "home"), "PATH": "/usr/bin"},
        )


def test_build_claude_runtime_with_local_login_stages_managed_env_and_prompt(monkeypatch, tmp_path: Path):
    shared_bundle = _shared_bundle(tmp_path)
    workflow = _workflow("/prove", "File.lean")
    installed_plugin_root = (
        tmp_path
        / "managed"
        / "claude-home"
        / ".claude"
        / "plugins"
        / "cache"
        / "lean4-skills"
        / "lean4"
        / "4.4.0"
    )
    (installed_plugin_root / "skills" / "lean4").mkdir(parents=True)
    (installed_plugin_root / "skills" / "lean4" / "SKILL.md").write_text("# Lean4\n", encoding="utf-8")
    credentials_dir = shared_bundle.real_home / ".claude"
    credentials_dir.mkdir(parents=True)
    (credentials_dir / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "token"}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(autoformalize, "_require_executable", lambda name, _msg, _env: f"/usr/bin/{name}")
    monkeypatch.setattr(autoformalize, "_claude_permission_args", lambda: ("--dangerously-skip-permissions",))
    monkeypatch.setattr(autoformalize, "_install_managed_claude_plugin", lambda **_kwargs: installed_plugin_root)

    runtime = autoformalize._build_claude_runtime(
        auth_mode="auto",
        user_instruction=workflow.workflow_args,
        workflow=workflow,
        base_environment={"HOME": str(shared_bundle.real_home), "PATH": "/usr/bin"},
        include_persisted_env=False,
        shared_bundle=shared_bundle,
    )

    managed_context = runtime.managed_context
    expected_prompt = autoformalize._build_startup_prompt(
        managed_context,
        workflow=workflow,
        user_instruction=workflow.workflow_args,
    )

    assert runtime.argv == [
        "/usr/bin/claude",
        "--model",
        autoformalize.CLAUDE_MODEL,
        "--dangerously-skip-permissions",
        expected_prompt,
    ]
    assert runtime.child_env["HOME"] == str(managed_context.backend_home)
    assert runtime.child_env["GAUSS_AUTOFORMALIZE_BACKEND"] == "claude-code"
    assert runtime.child_env["GAUSS_PROJECT_ROOT"] == str(shared_bundle.project.root)
    assert runtime.child_env["GAUSS_PROJECT_MANIFEST"] == str(shared_bundle.project.manifest_path)
    assert runtime.child_env["LEAN_PROJECT_PATH"] == str(shared_bundle.project.lean_root)
    assert runtime.child_env["CLAUDE_PLUGIN_ROOT"] == str(installed_plugin_root)
    assert runtime.child_env["LEAN4_PLUGIN_ROOT"] == str(installed_plugin_root)
    assert runtime.child_env["LEAN4_SCRIPTS"] == str(installed_plugin_root / "lib" / "scripts")
    assert runtime.child_env["LEAN4_REFS"] == str(installed_plugin_root / "skills" / "lean4" / "references")
    assert runtime.child_env["GAUSS_YOLO_MODE"] == "1"
    assert runtime.child_env["GAUSS_AUTOFORMALIZE_CONTEXT"] == str(managed_context.startup_context_path)
    assert "ANTHROPIC_API_KEY" not in runtime.child_env
    assert (managed_context.backend_home / ".claude" / ".credentials.json").exists()
    assert (managed_context.plugin_root / "skills" / "lean4" / "SKILL.md").exists()
    payload = json.loads((managed_context.backend_home / ".claude.json").read_text(encoding="utf-8"))
    assert payload["mcpServers"]["lean-lsp"]["env"]["LEAN_PROJECT_PATH"] == str(shared_bundle.project.lean_root)


def test_build_claude_runtime_falls_back_to_macos_keychain_when_credentials_file_absent(monkeypatch, tmp_path: Path):
    shared_bundle = _shared_bundle(tmp_path)
    workflow = _workflow("/prove", "File.lean")
    installed_plugin_root = (
        tmp_path
        / "managed"
        / "claude-home"
        / ".claude"
        / "plugins"
        / "cache"
        / "lean4-skills"
        / "lean4"
        / "4.4.0"
    )
    (installed_plugin_root / "skills" / "lean4").mkdir(parents=True)
    (installed_plugin_root / "skills" / "lean4" / "SKILL.md").write_text("# Lean4\n", encoding="utf-8")
    keychain_payload = json.dumps({"claudeAiOauth": {"accessToken": "sk-ant-oat01-test"}})
    monkeypatch.setattr(autoformalize, "_require_executable", lambda name, _msg, _env: f"/usr/bin/{name}")
    monkeypatch.setattr(autoformalize, "_claude_permission_args", lambda: ("--dangerously-skip-permissions",))
    monkeypatch.setattr(autoformalize, "_install_managed_claude_plugin", lambda **_kwargs: installed_plugin_root)
    monkeypatch.setattr(autoformalize, "_read_keychain_claude_credentials", lambda: keychain_payload)

    runtime = autoformalize._build_claude_runtime(
        auth_mode="auto",
        user_instruction=workflow.workflow_args,
        workflow=workflow,
        base_environment={"HOME": str(shared_bundle.real_home), "PATH": "/usr/bin"},
        include_persisted_env=False,
        shared_bundle=shared_bundle,
    )

    managed_credentials = runtime.managed_context.backend_home / ".claude" / ".credentials.json"
    assert managed_credentials.exists()
    staged = json.loads(managed_credentials.read_text(encoding="utf-8"))
    assert staged["claudeAiOauth"]["accessToken"] == "sk-ant-oat01-test"
    assert "ANTHROPIC_API_KEY" not in runtime.child_env


def test_has_local_claude_login_detects_macos_keychain_when_credentials_file_absent(monkeypatch, tmp_path: Path):
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    keychain_payload = json.dumps({"claudeAiOauth": {"accessToken": "sk-ant-oat01-test"}})
    monkeypatch.setattr(autoformalize, "_read_keychain_claude_credentials", lambda: keychain_payload)

    assert autoformalize._has_local_claude_login(real_home) is True


def test_build_claude_runtime_accepts_anthropic_api_key(monkeypatch, tmp_path: Path):
    shared_bundle = _shared_bundle(tmp_path)
    workflow = _workflow("/formalize")
    installed_plugin_root = (
        tmp_path
        / "managed"
        / "claude-home"
        / ".claude"
        / "plugins"
        / "cache"
        / "lean4-skills"
        / "lean4"
        / "4.4.0"
    )
    (installed_plugin_root / "skills" / "lean4").mkdir(parents=True)
    monkeypatch.setattr(autoformalize, "_require_executable", lambda name, _msg, _env: f"/usr/bin/{name}")
    monkeypatch.setattr(autoformalize, "_claude_permission_args", lambda: ("--dangerously-skip-permissions",))
    monkeypatch.setattr(autoformalize, "_install_managed_claude_plugin", lambda **_kwargs: installed_plugin_root)
    monkeypatch.setattr(autoformalize, "_read_keychain_claude_credentials", lambda: None)

    runtime = autoformalize._build_claude_runtime(
        auth_mode="auto",
        user_instruction=workflow.workflow_args,
        workflow=workflow,
        base_environment={
            "HOME": str(shared_bundle.real_home),
            "PATH": "/usr/bin",
            "ANTHROPIC_API_KEY": "sk-ant-api03-test",
        },
        include_persisted_env=False,
        shared_bundle=shared_bundle,
    )

    payload = json.loads((runtime.managed_context.backend_home / ".claude.json").read_text(encoding="utf-8"))
    assert payload["primaryApiKey"] == "sk-ant-api03-test"
    assert payload["mcpServers"]["lean-lsp"]["type"] == "stdio"
    assert runtime.child_env["ANTHROPIC_API_KEY"] == "sk-ant-api03-test"
    assert runtime.child_env["HOME"] == str(runtime.managed_context.backend_home)
    assert runtime.child_env["GAUSS_YOLO_MODE"] == "1"


def test_build_claude_runtime_login_mode_strips_auth_env(monkeypatch, tmp_path: Path):
    shared_bundle = _shared_bundle(tmp_path)
    workflow = _workflow("/formalize")
    installed_plugin_root = (
        tmp_path
        / "managed"
        / "claude-home"
        / ".claude"
        / "plugins"
        / "cache"
        / "lean4-skills"
        / "lean4"
        / "4.4.0"
    )
    (installed_plugin_root / "skills" / "lean4").mkdir(parents=True)
    monkeypatch.setattr(autoformalize, "_require_executable", lambda name, _msg, _env: f"/usr/bin/{name}")
    monkeypatch.setattr(autoformalize, "_claude_permission_args", lambda: ("--dangerously-skip-permissions",))
    monkeypatch.setattr(autoformalize, "_install_managed_claude_plugin", lambda **_kwargs: installed_plugin_root)

    runtime = autoformalize._build_claude_runtime(
        auth_mode="login",
        user_instruction=workflow.workflow_args,
        workflow=workflow,
        base_environment={
            "HOME": str(shared_bundle.real_home),
            "PATH": "/usr/bin",
            "ANTHROPIC_API_KEY": "sk-ant-api03-test",
        },
        include_persisted_env=False,
        shared_bundle=shared_bundle,
    )

    payload = json.loads((runtime.managed_context.backend_home / ".claude.json").read_text(encoding="utf-8"))
    assert "primaryApiKey" not in payload
    assert "ANTHROPIC_API_KEY" not in runtime.child_env


def test_build_codex_runtime_stages_skill_mcp_and_api_key_auth(monkeypatch, tmp_path: Path):
    shared_bundle = _shared_bundle(tmp_path, backend_name="codex")
    workflow = _workflow("/autoprove", "--max-cycles=4")
    monkeypatch.setattr(autoformalize, "_require_executable", lambda name, _msg, _env: f"/usr/bin/{name}")

    runtime = autoformalize._build_codex_runtime(
        auth_mode="auto",
        user_instruction=workflow.workflow_args,
        workflow=workflow,
        base_environment={
            "HOME": str(shared_bundle.real_home),
            "PATH": "/usr/bin",
            "OPENAI_API_KEY": "sk-openai-test",
        },
        include_persisted_env=False,
        shared_bundle=shared_bundle,
    )

    managed_context = runtime.managed_context
    expected_prompt = autoformalize._build_startup_prompt(
        managed_context,
        workflow=workflow,
        user_instruction=workflow.workflow_args,
    )

    assert runtime.argv == [
        "/usr/bin/codex",
        "--dangerously-bypass-approvals-and-sandbox",
        expected_prompt,
    ]
    assert runtime.child_env["HOME"] == str(managed_context.backend_home)
    assert runtime.child_env["CODEX_HOME"] == str(managed_context.backend_home / ".codex")
    assert runtime.child_env["GAUSS_AUTOFORMALIZE_BACKEND"] == "codex"
    assert runtime.child_env["GAUSS_PROJECT_ROOT"] == str(shared_bundle.project.root)
    assert runtime.child_env["LEAN_PROJECT_PATH"] == str(shared_bundle.project.lean_root)
    assert runtime.child_env["LEAN4_PLUGIN_ROOT"] == str(shared_bundle.plugin_source)
    assert runtime.child_env["LEAN4_SCRIPTS"] == str(shared_bundle.scripts_root)
    assert runtime.child_env["LEAN4_REFS"] == str(managed_context.skills_root / "references")
    assert runtime.child_env["GAUSS_AUTOFORMALIZE_SKILLS_ROOT"] == str(managed_context.skills_root)
    assert runtime.child_env["GAUSS_AUTOFORMALIZE_INSTRUCTIONS"] == str(managed_context.instructions_path)
    assert "OPENAI_API_KEY" not in runtime.child_env
    assert expected_prompt is not None
    assert "$lean4" in expected_prompt
    assert "not shell commands" in expected_prompt

    auth_payload = json.loads((managed_context.backend_home / ".codex" / "auth.json").read_text(encoding="utf-8"))
    assert auth_payload == {
        "auth_mode": "apikey",
        "OPENAI_API_KEY": "sk-openai-test",
    }
    assert (managed_context.skills_root / "SKILL.md").exists()
    config_content = managed_context.backend_config_path.read_text(encoding="utf-8")
    assert "model_instructions_file" in config_content
    assert "LEAN_PROJECT_PATH" in config_content
    instructions_content = managed_context.instructions_path.read_text(encoding="utf-8")
    assert "Installed Lean4 skill" in instructions_content
    assert str(managed_context.skills_root) in instructions_content
    assert "$lean4" in instructions_content
    assert "not shell commands" in instructions_content
    assert str(shared_bundle.plugin_source / "commands" / "autoprove.md") in instructions_content
    startup_content = managed_context.startup_context_path.read_text(encoding="utf-8")
    assert workflow.backend_command in startup_content
    assert str(managed_context.skills_root) in startup_content
    assert "$lean4" in startup_content
    assert "not a shell executable" in startup_content


def test_build_codex_runtime_login_mode_allows_empty_managed_auth(monkeypatch, tmp_path: Path):
    shared_bundle = _shared_bundle(tmp_path, backend_name="codex")
    workflow = _workflow("/formalize")
    monkeypatch.setattr(autoformalize, "_require_executable", lambda name, _msg, _env: f"/usr/bin/{name}")

    runtime = autoformalize._build_codex_runtime(
        auth_mode="login",
        user_instruction=workflow.workflow_args,
        workflow=workflow,
        base_environment={"HOME": str(shared_bundle.real_home), "PATH": "/usr/bin"},
        include_persisted_env=False,
        shared_bundle=shared_bundle,
    )

    expected_prompt = autoformalize._build_startup_prompt(
        runtime.managed_context,
        workflow=workflow,
        user_instruction=workflow.workflow_args,
    )
    assert runtime.argv == ["/usr/bin/codex", "--dangerously-bypass-approvals-and-sandbox", expected_prompt]
    assert not (runtime.managed_context.backend_home / ".codex" / "auth.json").exists()
    assert "OPENAI_API_KEY" not in runtime.child_env


def test_build_codex_runtime_prefers_existing_local_auth_over_api_key(monkeypatch, tmp_path: Path):
    shared_bundle = _shared_bundle(tmp_path, backend_name="codex")
    workflow = _workflow("/formalize")
    local_codex_home = shared_bundle.real_home / ".codex"
    local_codex_home.mkdir(parents=True)
    local_payload = {
        "auth_mode": "chatgpt",
        "tokens": {
            "access_token": "access",
            "refresh_token": "refresh",
            "id_token": "id",
        },
    }
    (local_codex_home / "auth.json").write_text(json.dumps(local_payload), encoding="utf-8")
    monkeypatch.setattr(autoformalize, "_require_executable", lambda name, _msg, _env: f"/usr/bin/{name}")

    runtime = autoformalize._build_codex_runtime(
        auth_mode="auto",
        user_instruction=workflow.workflow_args,
        workflow=workflow,
        base_environment={
            "HOME": str(shared_bundle.real_home),
            "PATH": "/usr/bin",
            "OPENAI_API_KEY": "sk-openai-test",
        },
        include_persisted_env=False,
        shared_bundle=shared_bundle,
    )

    staged_payload = json.loads((runtime.managed_context.backend_home / ".codex" / "auth.json").read_text(encoding="utf-8"))
    assert staged_payload == local_payload
    assert "OPENAI_API_KEY" not in runtime.child_env


def test_stage_claude_credentials_synthesizes_managed_key_from_api_key(tmp_path: Path):
    real_home = tmp_path / "home"
    claude_home = tmp_path / "managed-home"
    real_home.mkdir()

    autoformalize._stage_claude_credentials(
        real_home=real_home,
        claude_home=claude_home,
        auth_env={"ANTHROPIC_API_KEY": "sk-ant-api03-test"},
    )

    payload = json.loads((claude_home / ".claude.json").read_text(encoding="utf-8"))
    assert payload["primaryApiKey"] == "sk-ant-api03-test"
    assert payload["theme"] == autoformalize.DEFAULT_MANAGED_CLAUDE_THEME
    assert payload["hasCompletedOnboarding"] is True


def test_stage_claude_credentials_merges_existing_managed_config_and_mcp_servers(tmp_path: Path):
    real_home = tmp_path / "home"
    claude_home = tmp_path / "managed-home"
    real_home.mkdir()
    claude_home.mkdir()
    (claude_home / ".claude.json").write_text(
        json.dumps(
            {
                "firstStartTime": "2026-03-18T00:00:00Z",
                "mcpServers": {
                    "existing": {
                        "type": "stdio",
                        "command": "true",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    autoformalize._stage_claude_credentials(
        real_home=real_home,
        claude_home=claude_home,
        auth_env={"ANTHROPIC_API_KEY": "sk-ant-api03-test"},
        mcp_servers={
            "lean-lsp": {
                "type": "stdio",
                "command": "uvx",
            }
        },
    )

    payload = json.loads((claude_home / ".claude.json").read_text(encoding="utf-8"))
    assert payload["firstStartTime"] == "2026-03-18T00:00:00Z"
    assert payload["primaryApiKey"] == "sk-ant-api03-test"
    assert payload["mcpServers"]["existing"]["command"] == "true"
    assert payload["mcpServers"]["lean-lsp"]["command"] == "uvx"


def test_stage_claude_credentials_can_strip_stale_auth_files(tmp_path: Path):
    real_home = tmp_path / "home"
    claude_home = tmp_path / "managed-home"
    real_home.mkdir()
    (real_home / ".claude").mkdir(parents=True)
    (real_home / ".claude" / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "token"}}),
        encoding="utf-8",
    )
    (real_home / ".claude.json").write_text(
        json.dumps({"firstStartTime": "2026-03-17T00:00:00Z", "primaryApiKey": "sk-ant-api03-local"}),
        encoding="utf-8",
    )
    (claude_home / ".claude").mkdir(parents=True)
    (claude_home / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")

    autoformalize._stage_claude_credentials(
        real_home=real_home,
        claude_home=claude_home,
        auth_env={},
        copy_oauth_credentials=False,
        copy_local_api_key=False,
    )

    payload = json.loads((claude_home / ".claude.json").read_text(encoding="utf-8"))
    assert payload["firstStartTime"] == "2026-03-17T00:00:00Z"
    assert "primaryApiKey" not in payload
    assert payload["theme"] == autoformalize.DEFAULT_MANAGED_CLAUDE_THEME
    assert payload["hasCompletedOnboarding"] is True
    assert not (claude_home / ".claude" / ".credentials.json").exists()


def test_claude_permission_args_respects_effective_root(monkeypatch):
    monkeypatch.setattr(autoformalize, "_is_effective_root", lambda: True)
    assert autoformalize._claude_permission_args() == ("--permission-mode", "bypassPermissions")
    monkeypatch.setattr(autoformalize, "_is_effective_root", lambda: False)
    assert autoformalize._claude_permission_args() == ("--dangerously-skip-permissions",)


def test_ensure_lean4_checkout_assets_reuses_prewarmed_checkout_without_refresh(monkeypatch, tmp_path: Path):
    assets_root = tmp_path / "assets"
    checkout_root = assets_root / "lean4-skills"
    plugin_source = checkout_root / "plugins" / "lean4"
    skill_source = plugin_source / "skills" / "lean4"
    scripts_root = plugin_source / "lib" / "scripts"
    references_root = skill_source / "references"
    references_root.mkdir(parents=True)
    scripts_root.mkdir(parents=True)
    (skill_source / "SKILL.md").write_text("# Lean4\n", encoding="utf-8")
    (checkout_root / autoformalize.LEAN4_CHECKOUT_REVISION_FILE).write_text(
        "prewarmed-revision\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        autoformalize,
        "_ensure_git_checkout",
        lambda **_kwargs: pytest.fail("expected prewarmed checkout to skip git refresh"),
    )

    resolved_root, revision = autoformalize._ensure_lean4_checkout_assets(
        assets_root=assets_root,
        env={"PATH": "/usr/bin"},
        git_executable="/usr/bin/git",
        refresh=False,
    )

    assert resolved_root == checkout_root
    assert revision == "prewarmed-revision"


def test_ensure_claude_user_plugin_state_enables_plugin_and_autoupdate(monkeypatch, tmp_path: Path):
    real_home = tmp_path / "home"
    plugin_root = real_home / ".claude" / "plugins" / "cache" / "lean4-skills" / "lean4" / "4.4.5"
    calls: list[list[str]] = []

    def fake_run(argv, *, error_prefix, env=None, cwd=None):
        del error_prefix, cwd
        calls.append(list(argv))
        assert env is not None
        assert env["HOME"] == str(real_home)
        if list(argv)[1:4] == ["plugin", "marketplace", "add"]:
            known_marketplaces_path = real_home / ".claude" / "plugins" / "known_marketplaces.json"
            known_marketplaces_path.parent.mkdir(parents=True, exist_ok=True)
            known_marketplaces_path.write_text(
                json.dumps(
                    {
                        "lean4-skills": {
                            "source": {"source": "github", "repo": "cameronfreer/lean4-skills"},
                            "installLocation": str(real_home / ".claude" / "plugins" / "marketplaces" / "lean4-skills"),
                            "lastUpdated": "2026-03-27T00:00:00Z",
                        }
                    }
                ),
                encoding="utf-8",
            )
        if list(argv)[1:3] == ["plugin", "install"]:
            plugin_root.mkdir(parents=True, exist_ok=True)
            installed_plugins_path = real_home / ".claude" / "plugins" / "installed_plugins.json"
            installed_plugins_path.parent.mkdir(parents=True, exist_ok=True)
            installed_plugins_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "plugins": {
                            "lean4@lean4-skills": [
                                {
                                    "scope": "user",
                                    "installPath": str(plugin_root),
                                    "version": "4.4.5",
                                    "installedAt": "2026-03-27T00:00:01Z",
                                    "lastUpdated": "2026-03-27T00:00:01Z",
                                    "gitCommitSha": "a1cef7dc21d9b68b79311d57cd78ecf6b7ca22bf",
                                }
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(autoformalize, "_run", fake_run)

    install_path = autoformalize._ensure_claude_user_plugin_state(
        claude_executable="/usr/bin/claude",
        real_home=real_home,
        base_environment={"PATH": "/usr/bin"},
    )

    assert install_path == plugin_root.resolve()
    settings = json.loads((real_home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["extraKnownMarketplaces"]["lean4-skills"]["source"] == {
        "source": "github",
        "repo": "cameronfreer/lean4-skills",
    }
    assert settings["extraKnownMarketplaces"]["lean4-skills"]["autoUpdate"] is True
    assert settings["enabledPlugins"]["lean4@lean4-skills"] is True

    known_marketplaces = json.loads(
        (real_home / ".claude" / "plugins" / "known_marketplaces.json").read_text(encoding="utf-8")
    )
    assert known_marketplaces["lean4-skills"]["autoUpdate"] is True
    assert calls[0] == [
        "/usr/bin/claude",
        "plugin",
        "marketplace",
        "add",
        "cameronfreer/lean4-skills",
    ]
    assert calls[1] == [
        "/usr/bin/claude",
        "plugin",
        "install",
        "lean4",
    ]


def test_install_claude_plugin_target_falls_back_to_plugin_id(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(argv, *, error_prefix, env=None, cwd=None):
        del error_prefix, env, cwd
        calls.append(list(argv))
        if list(argv)[-1] == "lean4":
            raise autoformalize.AutoformalizeStagingError("plugin name unavailable")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(autoformalize, "_run", fake_run)

    autoformalize._install_claude_plugin_target(
        claude_executable="/usr/bin/claude",
        cli_env={"HOME": "/tmp/home"},
        plugin_name="lean4",
        plugin_id="lean4@lean4-skills",
        error_prefix="Failed to install the Lean4 Claude plugin in the user profile",
    )

    assert calls == [
        ["/usr/bin/claude", "plugin", "install", "lean4"],
        ["/usr/bin/claude", "plugin", "install", "lean4@lean4-skills"],
    ]


def test_ensure_claude_user_plugin_state_subprocess_uses_documented_cli_commands(tmp_path: Path):
    real_home = tmp_path / "home"
    log_path = tmp_path / "claude-commands.jsonl"
    claude_stub = tmp_path / "claude"
    claude_stub.write_text(
        """#!__PYTHON__
import json
import os
import sys
from pathlib import Path

argv = sys.argv[1:]
log_path = Path(os.environ["GAUSS_TEST_LOG"])
with log_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(argv) + "\\n")

if "--scope" in argv:
    print("error: unknown option '--scope'", file=sys.stderr)
    raise SystemExit(1)

home = Path(os.environ["HOME"])
plugins_root = home / ".claude" / "plugins"

if argv[:4] == ["plugin", "marketplace", "add", "cameronfreer/lean4-skills"]:
    (plugins_root / "known_marketplaces.json").parent.mkdir(parents=True, exist_ok=True)
    (plugins_root / "known_marketplaces.json").write_text(
        json.dumps(
            {
                "lean4-skills": {
                    "source": {"source": "github", "repo": "cameronfreer/lean4-skills"}
                }
            }
        ),
        encoding="utf-8",
    )
    raise SystemExit(0)

if argv[:3] == ["plugin", "install", "lean4"]:
    plugin_root = plugins_root / "cache" / "lean4-skills" / "lean4" / "4.4.5"
    plugin_root.mkdir(parents=True, exist_ok=True)
    (plugins_root / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    "lean4@lean4-skills": [
                        {
                            "scope": "user",
                            "installPath": str(plugin_root),
                            "version": "4.4.5",
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    raise SystemExit(0)

print(f"unexpected argv: {argv}", file=sys.stderr)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    claude_stub.write_text(
        claude_stub.read_text(encoding="utf-8").replace("__PYTHON__", sys.executable, 1),
        encoding="utf-8",
    )
    claude_stub.chmod(0o755)

    install_path = autoformalize._ensure_claude_user_plugin_state(
        claude_executable=str(claude_stub),
        real_home=real_home,
        base_environment={
            "PATH": str(tmp_path),
            "GAUSS_TEST_LOG": str(log_path),
        },
    )

    commands = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert commands == [
        ["plugin", "marketplace", "add", "cameronfreer/lean4-skills"],
        ["plugin", "install", "lean4"],
    ]
    assert install_path == (
        real_home / ".claude" / "plugins" / "cache" / "lean4-skills" / "lean4" / "4.4.5"
    ).resolve()


def test_sync_prewarmed_claude_plugin_links_plugin_state_into_managed_home(tmp_path: Path):
    real_home = tmp_path / "real-home"
    backend_home = tmp_path / "backend-home"
    plugin_root = real_home / ".claude" / "plugins" / "cache" / "lean4-skills" / "lean4" / "4.4.5"
    plugin_root.mkdir(parents=True)
    settings_path = real_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "extraKnownMarketplaces": {
                    "lean4-skills": {
                        "source": {"source": "github", "repo": "cameronfreer/lean4-skills"},
                        "autoUpdate": True,
                    }
                },
                "enabledPlugins": {"lean4@lean4-skills": True},
            }
        ),
        encoding="utf-8",
    )
    installed_plugins_path = real_home / ".claude" / "plugins" / "installed_plugins.json"
    installed_plugins_path.parent.mkdir(parents=True, exist_ok=True)
    installed_plugins_path.write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    "lean4@lean4-skills": [
                        {
                            "scope": "user",
                            "installPath": str(plugin_root),
                            "version": "4.4.5",
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    staged_root = autoformalize._sync_prewarmed_claude_plugin(
        real_home=real_home,
        backend_home=backend_home,
    )

    assert staged_root == plugin_root.resolve()
    assert (backend_home / ".claude" / "settings.json").exists()
    plugins_entry = backend_home / ".claude" / "plugins"
    assert plugins_entry.exists()
    assert plugins_entry.is_symlink() or plugins_entry.is_dir()


def test_build_claude_runtime_prefers_prewarmed_user_plugin(monkeypatch, tmp_path: Path):
    shared_bundle = _shared_bundle(tmp_path)
    workflow = _workflow("/prove", "File.lean")
    prewarmed_plugin_root = (
        tmp_path / "real-home" / ".claude" / "plugins" / "cache" / "lean4-skills" / "lean4" / "4.4.5"
    )
    (prewarmed_plugin_root / "skills" / "lean4").mkdir(parents=True)
    (prewarmed_plugin_root / "skills" / "lean4" / "SKILL.md").write_text("# Lean4\n", encoding="utf-8")
    credentials_dir = shared_bundle.real_home / ".claude"
    credentials_dir.mkdir(parents=True)
    (credentials_dir / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "token"}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(autoformalize, "_require_executable", lambda name, _msg, _env: f"/usr/bin/{name}")
    monkeypatch.setattr(autoformalize, "_claude_permission_args", lambda: ("--dangerously-skip-permissions",))
    monkeypatch.setattr(
        autoformalize,
        "_sync_prewarmed_claude_plugin",
        lambda **_kwargs: prewarmed_plugin_root,
    )
    monkeypatch.setattr(
        autoformalize,
        "_install_managed_claude_plugin",
        lambda **_kwargs: pytest.fail("expected prewarmed Claude plugin to avoid managed install"),
    )

    runtime = autoformalize._build_claude_runtime(
        auth_mode="auto",
        user_instruction=workflow.workflow_args,
        workflow=workflow,
        base_environment={"HOME": str(shared_bundle.real_home), "PATH": "/usr/bin"},
        include_persisted_env=False,
        shared_bundle=shared_bundle,
    )

    assert runtime.managed_context.plugin_root == prewarmed_plugin_root
    assert runtime.child_env["CLAUDE_PLUGIN_ROOT"] == str(prewarmed_plugin_root)


def test_prepare_managed_runtime_assets_uses_install_time_prewarm(monkeypatch, tmp_path: Path):
    gauss_home = tmp_path / "gauss-home"
    checkout_root = gauss_home / "autoformalize" / "assets" / "lean4-skills"
    plugin_root = tmp_path / "home" / ".claude" / "plugins" / "cache" / "lean4-skills" / "lean4" / "4.4.5"
    warmed_uv: list[tuple[str, ...]] = []

    monkeypatch.setattr(autoformalize, "get_gauss_home", lambda: gauss_home)
    monkeypatch.setattr(autoformalize, "_require_executable", lambda name, _msg, _env: f"/usr/bin/{name}")
    monkeypatch.setattr(
        autoformalize,
        "_resolve_uv_runner",
        lambda _env: ("/usr/bin/uvx", "--from", autoformalize.LEAN_LSP_MCP_SPEC, "lean-lsp-mcp"),
    )
    monkeypatch.setattr(
        autoformalize,
        "_ensure_lean4_checkout_assets",
        lambda **_kwargs: (checkout_root, "abc123def4567890"),
    )
    monkeypatch.setattr(autoformalize, "_warm_lean_lsp_mcp_cache", lambda uv_runner: warmed_uv.append(tuple(uv_runner)))
    monkeypatch.setattr(
        autoformalize.shutil,
        "which",
        lambda name, path=None: "/usr/bin/claude" if name == "claude" else None,
    )
    monkeypatch.setattr(
        autoformalize,
        "_ensure_claude_user_plugin_state",
        lambda **_kwargs: plugin_root,
    )

    prepared = autoformalize.prepare_managed_runtime_assets(
        env={"HOME": str(tmp_path / "home"), "PATH": "/usr/bin"},
    )

    assert prepared["lean4_checkout_root"] == str(checkout_root)
    assert prepared["skill_revision"] == "abc123def4567890"
    assert prepared["claude_plugin_root"] == str(plugin_root)
    assert warmed_uv == [
        ("/usr/bin/uvx", "--from", autoformalize.LEAN_LSP_MCP_SPEC, "lean-lsp-mcp")
    ]
