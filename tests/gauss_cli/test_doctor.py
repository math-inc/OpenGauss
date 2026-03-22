"""Tests for gauss_cli.doctor."""

import json
import os
import sys
import types
from argparse import Namespace
from pathlib import Path

import pytest

import gauss_cli.doctor as doctor
import gauss_cli.gateway as gateway_cli
from gauss_cli import doctor as doctor_mod
from gauss_cli.doctor import _has_provider_env_config
from gauss_cli.project import initialize_gauss_project


class TestProviderEnvDetection:
    def test_detects_openai_api_key(self):
        content = "OPENAI_BASE_URL=http://localhost:1234/v1\nOPENAI_API_KEY=***"
        assert _has_provider_env_config(content)

    def test_detects_custom_endpoint_without_openrouter_key(self):
        content = "OPENAI_BASE_URL=http://localhost:8080/v1\n"
        assert _has_provider_env_config(content)

    def test_returns_false_when_no_provider_settings(self):
        content = "TERMINAL_ENV=local\n"
        assert not _has_provider_env_config(content)


class TestDoctorToolAvailabilityOverrides:
    def test_passthrough(self):
        available = ["memory"]
        unavailable = [{"name": "skills", "env_vars": [], "tools": ["skill_view"]}]
        updated_available, updated_unavailable = doctor._apply_doctor_tool_availability_overrides(
            available,
            unavailable,
        )

        assert updated_available == available
        assert updated_unavailable == unavailable


def test_run_doctor_sets_interactive_env_for_tool_checks(monkeypatch, tmp_path):
    """Doctor should present CLI-gated tools as available in CLI context."""
    project_root = tmp_path / "project"
    gauss_home = tmp_path / ".gauss"
    project_root.mkdir()
    gauss_home.mkdir()

    monkeypatch.setattr(doctor_mod, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(doctor_mod, "GAUSS_HOME", gauss_home)
    monkeypatch.delenv("GAUSS_INTERACTIVE", raising=False)

    seen = {}

    def fake_check_tool_availability(*args, **kwargs):
        seen["interactive"] = os.getenv("GAUSS_INTERACTIVE")
        raise SystemExit(0)

    fake_model_tools = types.SimpleNamespace(
        check_tool_availability=fake_check_tool_availability,
        TOOLSET_REQUIREMENTS={},
    )
    monkeypatch.setitem(sys.modules, "model_tools", fake_model_tools)

    with pytest.raises(SystemExit):
        doctor_mod.run_doctor(Namespace(fix=False))

    assert seen["interactive"] == "1"


def test_check_gateway_service_linger_warns_when_disabled(monkeypatch, tmp_path, capsys):
    unit_path = tmp_path / "gauss-gateway.service"
    unit_path.write_text("[Unit]\n")

    monkeypatch.setattr(gateway_cli, "is_linux", lambda: True)
    monkeypatch.setattr(gateway_cli, "get_systemd_unit_path", lambda: unit_path)
    monkeypatch.setattr(gateway_cli, "get_systemd_linger_status", lambda: (False, ""))

    issues = []
    doctor._check_gateway_service_linger(issues)

    out = capsys.readouterr().out
    assert "Gateway Service" in out
    assert "Systemd linger disabled" in out
    assert "loginctl enable-linger" in out
    assert issues == [
        "Enable linger for the gateway user service: sudo loginctl enable-linger $USER"
    ]


def test_check_gateway_service_linger_skips_when_service_not_installed(monkeypatch, tmp_path, capsys):
    unit_path = tmp_path / "missing.service"

    monkeypatch.setattr(gateway_cli, "is_linux", lambda: True)
    monkeypatch.setattr(gateway_cli, "get_systemd_unit_path", lambda: unit_path)

    issues = []
    doctor._check_gateway_service_linger(issues)

    out = capsys.readouterr().out
    assert out == ""
    assert issues == []


def _write_executable(path: Path) -> None:
    path.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)


def test_check_managed_workflow_requirements_reports_healthy_claude_setup(tmp_path, capsys):
    bin_dir = tmp_path / "bin"
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    bin_dir.mkdir()
    (home_dir / ".claude").mkdir(parents=True)
    for name in ("claude", "uvx", "lake"):
        _write_executable(bin_dir / name)

    (home_dir / ".claude" / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "token"}}),
        encoding="utf-8",
    )
    project_root.mkdir()
    (project_root / "lakefile.lean").write_text("-- lean project\n", encoding="utf-8")
    initialize_gauss_project(project_root, name="Demo Project")

    issues: list[str] = []
    doctor._check_managed_workflow_requirements(
        issues,
        config={
            "gauss": {
                "autoformalize": {"backend": "claude-code", "auth_mode": "auto"},
                "project": {"template_source": "https://example.com/template.git"},
            }
        },
        env={"PATH": str(bin_dir), "HOME": str(home_dir)},
        active_cwd=project_root,
        cli_name="gauss",
    )

    output = capsys.readouterr().out
    assert "Managed Lean Workflows" in output
    assert "Managed backend" in output
    assert "claude-code" in output
    assert "Active Gauss project" in output
    assert "Demo Project" in output
    assert "Claude auth" in output
    assert "local Claude login" in output
    assert issues == []


def test_check_managed_workflow_requirements_reports_missing_codex_prereqs(tmp_path, capsys):
    missing_project_dir = tmp_path / "outside"
    home_dir = tmp_path / "home"
    missing_project_dir.mkdir()
    home_dir.mkdir()

    issues: list[str] = []
    doctor._check_managed_workflow_requirements(
        issues,
        config={
            "gauss": {
                "autoformalize": {"backend": "codex", "auth_mode": "api-key"},
            }
        },
        env={"PATH": "", "HOME": str(home_dir)},
        active_cwd=missing_project_dir,
        cli_name="gauss",
    )

    output = capsys.readouterr().out
    assert "Codex CLI" in output
    assert "Codex API-key auth" in output
    assert "Active Gauss project" in output
    assert "uv / uvx" in output
    assert "Lean toolchain (lake)" in output
    assert any("Codex CLI" in issue or "OpenAI Codex CLI" in issue for issue in issues)
    assert any("OPENAI_API_KEY" in issue for issue in issues)
    assert any("/project init" in issue for issue in issues)
    assert any("uv" in issue for issue in issues)
    assert any("lake" in issue for issue in issues)
