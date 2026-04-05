"""Tests for managed workflow dispatch and the legacy `/handoff` alias."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _stub_module(name: str, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


sys.modules.setdefault("run_agent", _stub_module("run_agent", AIAgent=object))
sys.modules.setdefault(
    "model_tools",
    _stub_module(
        "model_tools",
        get_tool_definitions=lambda *args, **kwargs: [],
        get_toolset_for_tool=lambda *args, **kwargs: None,
    ),
)
sys.modules.setdefault(
    "toolsets",
    _stub_module(
        "toolsets",
        get_all_toolsets=lambda *args, **kwargs: [],
        get_toolset_info=lambda *args, **kwargs: {},
        resolve_toolset=lambda *args, **kwargs: None,
        validate_toolset=lambda *args, **kwargs: True,
    ),
)
sys.modules.setdefault("cron", _stub_module("cron", get_job=lambda *args, **kwargs: None))
tools_pkg = sys.modules.setdefault("tools", types.ModuleType("tools"))
tools_pkg.__path__ = getattr(tools_pkg, "__path__", [])
sys.modules.setdefault(
    "tools.terminal_tool",
    _stub_module(
        "tools.terminal_tool",
        cleanup_all_environments=lambda *args, **kwargs: None,
        set_sudo_password_callback=lambda *args, **kwargs: None,
        set_approval_callback=lambda *args, **kwargs: None,
    ),
)
sys.modules.setdefault(
    "tools.skills_tool",
    _stub_module(
        "tools.skills_tool",
        set_secret_capture_callback=lambda *args, **kwargs: None,
    ),
)
sys.modules.setdefault(
    "tools.browser_tool",
    _stub_module(
        "tools.browser_tool",
        _emergency_cleanup_all_sessions=lambda *args, **kwargs: None,
    ),
)

import cli as cli_mod
from cli import GaussCLI


def _make_cli():
    cli_obj = GaussCLI.__new__(GaussCLI)
    cli_obj.config = {"gauss": {"autoformalize": {"handoff_mode": "helper"}}}
    cli_obj.console = MagicMock()
    cli_obj.agent = None
    cli_obj.conversation_history = []
    cli_obj.session_id = None
    cli_obj._pending_input = MagicMock()
    cli_obj._command_running = False
    cli_obj._command_status = ""
    cli_obj._last_invalidate = 0.0
    cli_obj._app = None
    cli_obj._input_area = None
    cli_obj._invalidate = MagicMock()
    cli_obj._active_handoff_cwd = lambda: "/tmp"
    cli_obj._project_override_root = None
    return cli_obj


def test_autoformalize_command_dispatches_to_runner():
    cli_obj = _make_cli()
    plan = SimpleNamespace(
        user_instruction="prove the main theorem",
        handoff_request=SimpleNamespace(
            argv=["/usr/bin/codex", "--dangerously-bypass-approvals-and-sandbox"],
            cwd="/tmp/project",
            env={"HOME": "/tmp/home"},
        ),
        managed_context=SimpleNamespace(startup_context_path=None, backend_name="codex"),
        workflow_kind="autoformalize",
        backend_command="/lean4:autoformalize prove the main theorem",
        project=SimpleNamespace(label="Demo Project", root="/tmp/project"),
    )
    task = SimpleNamespace(task_id="task-123")
    swarm = MagicMock()
    swarm.spawn_interactive.return_value = task
    chat_console = MagicMock()

    with patch.object(cli_mod, "resolve_autoformalize_request", return_value=plan) as mock_resolve, \
         patch.object(cli_obj, "_setup_swarm_completion_callback") as mock_setup, \
         patch.object(cli_mod, "SwarmManager", return_value=swarm), \
         patch.object(cli_mod, "ChatConsole", return_value=chat_console):
        assert cli_obj.process_command("/autoformalize prove the main theorem") is True

    mock_resolve.assert_called_once_with(
        "/autoformalize prove the main theorem",
        cli_obj.config,
        active_cwd="/tmp",
    )
    mock_setup.assert_called_once_with()
    swarm.spawn_interactive.assert_called_once_with(
        theorem="prove the main theorem",
        description="prove the main theorem",
        argv=["/usr/bin/codex", "--dangerously-bypass-approvals-and-sandbox"],
        cwd="/tmp/project",
        env={"HOME": "/tmp/home"},
        workflow_kind="autoformalize",
        workflow_command="/lean4:autoformalize prove the main theorem",
        project_name="Demo Project",
        project_root="/tmp/project",
        backend_name="codex",
    )
    assert chat_console.print.call_count == 2


def test_autoformalize_resolution_error_does_not_run_child(capsys):
    cli_obj = _make_cli()
    swarm = MagicMock()

    with patch.object(
        cli_mod,
        "resolve_autoformalize_request",
        side_effect=cli_mod.AutoformalizeError("Lean project root not found"),
    ), patch.object(cli_mod, "SwarmManager", return_value=swarm):
        assert cli_obj.process_command("/autoformalize prove something") is True

    swarm.spawn_interactive.assert_not_called()
    captured = capsys.readouterr()
    assert "Lean project root not found" in captured.out


def test_project_override_is_used_for_workflow_launch_cwd():
    cli_obj = _make_cli()
    selected_root = "/tmp/selected-project"
    cli_obj._active_handoff_cwd = lambda: "/tmp/outside"
    cli_obj._project_state = MagicMock(
        return_value=(SimpleNamespace(root=selected_root), "override", None)
    )
    plan = SimpleNamespace(
        user_instruction="Main.lean",
        handoff_request=SimpleNamespace(
            argv=["/usr/bin/claude", "--model", "claude-opus-4-6"],
            cwd=selected_root,
            env={"HOME": "/tmp/home"},
        ),
        managed_context=SimpleNamespace(startup_context_path=None, backend_name="claude-code"),
        workflow_kind="prove",
        backend_command="/lean4:prove Main.lean",
        project=SimpleNamespace(label="Selected Project", root=selected_root),
    )
    swarm = MagicMock()
    swarm.spawn_interactive.return_value = SimpleNamespace(task_id="af-001")

    with patch.object(cli_mod, "resolve_autoformalize_request", return_value=plan) as mock_resolve, \
         patch.object(cli_mod, "SwarmManager", return_value=swarm), \
         patch.object(cli_mod, "ChatConsole", return_value=MagicMock()):
        assert cli_obj.process_command("/prove Main.lean") is True

    mock_resolve.assert_called_once_with(
        "/prove Main.lean",
        cli_obj.config,
        active_cwd=selected_root,
    )


def test_ambient_project_root_is_used_for_workflow_launch_cwd():
    cli_obj = _make_cli()
    ambient_root = "/tmp/ambient-project"
    cli_obj._active_handoff_cwd = lambda: f"{ambient_root}/Math"
    cli_obj._project_state = MagicMock(
        return_value=(SimpleNamespace(root=ambient_root), "ambient", None)
    )
    plan = SimpleNamespace(
        user_instruction="Main.lean",
        handoff_request=SimpleNamespace(
            argv=["/usr/bin/claude", "--model", "claude-opus-4-6"],
            cwd=ambient_root,
            env={"HOME": "/tmp/home"},
        ),
        managed_context=SimpleNamespace(startup_context_path=None, backend_name="claude-code"),
        workflow_kind="prove",
        backend_command="/lean4:prove Main.lean",
        project=SimpleNamespace(label="Ambient Project", root=ambient_root),
    )
    swarm = MagicMock()
    swarm.spawn_interactive.return_value = SimpleNamespace(task_id="af-001")

    with patch.object(cli_mod, "resolve_autoformalize_request", return_value=plan) as mock_resolve, \
         patch.object(cli_mod, "SwarmManager", return_value=swarm), \
         patch.object(cli_mod, "ChatConsole", return_value=MagicMock()):
        assert cli_obj.process_command("/prove Main.lean") is True

    mock_resolve.assert_called_once_with(
        "/prove Main.lean",
        cli_obj.config,
        active_cwd=ambient_root,
    )


def test_review_command_dispatches_to_interactive_runner():
    cli_obj = _make_cli()
    plan = SimpleNamespace(
        user_instruction="Main.lean",
        handoff_request=SimpleNamespace(
            argv=["/usr/bin/claude", "--model", "claude-opus-4-6"],
            cwd="/tmp/project",
            env={"HOME": "/tmp/home"},
        ),
        managed_context=SimpleNamespace(startup_context_path=None, backend_name="claude-code"),
        workflow_kind="review",
        backend_command="/lean4:review Main.lean",
        project=SimpleNamespace(label="Demo Project", root="/tmp/project"),
    )
    swarm = MagicMock()
    swarm.spawn_interactive.return_value = SimpleNamespace(task_id="af-001")

    with patch.object(cli_mod, "resolve_autoformalize_request", return_value=plan) as mock_resolve, \
         patch.object(cli_mod, "SwarmManager", return_value=swarm), \
         patch.object(cli_mod, "ChatConsole", return_value=MagicMock()):
        assert cli_obj.process_command("/review Main.lean") is True

    mock_resolve.assert_called_once_with(
        "/review Main.lean",
        cli_obj.config,
        active_cwd="/tmp",
    )
    swarm.spawn_interactive.assert_called_once()


def test_project_init_flow_routes_prove_from_initialized_project_root(tmp_path):
    cli_obj = _make_cli()
    cli_obj._app = object()
    repo = tmp_path / "Demo"
    nested = repo / "Math"
    nested.mkdir(parents=True)
    (repo / "lakefile.lean").write_text("-- lean project\n", encoding="utf-8")
    cli_obj._active_handoff_cwd = lambda: str(nested)

    plan = SimpleNamespace(
        user_instruction="Main.lean",
        handoff_request=SimpleNamespace(
            argv=["/usr/bin/codex", "--dangerously-bypass-approvals-and-sandbox"],
            cwd=str(repo),
            env={"HOME": str(tmp_path / "home")},
        ),
        managed_context=SimpleNamespace(startup_context_path=None, backend_name="codex"),
        workflow_kind="prove",
        backend_command="/lean4:prove Main.lean",
        project=SimpleNamespace(label="Demo Project", root=str(repo)),
    )
    swarm = MagicMock()
    swarm.spawn_interactive.return_value = SimpleNamespace(task_id="af-001")

    with patch.object(cli_mod, "resolve_autoformalize_request", return_value=plan) as mock_resolve, \
         patch.object(cli_obj, "_setup_swarm_completion_callback"), \
         patch.object(cli_mod, "SwarmManager", return_value=swarm), \
         patch.object(cli_mod, "ChatConsole", return_value=MagicMock()):
        assert cli_obj.process_command('/project init --name "Demo Project"') is True
        assert cli_obj.process_command("/prove Main.lean") is True

    assert cli_obj._project_override_root == repo
    assert (repo / ".gauss" / "project.yaml").is_file()
    mock_resolve.assert_called_once_with(
        "/prove Main.lean",
        cli_obj.config,
        active_cwd=str(repo),
    )


def test_project_create_flow_routes_formalize_from_created_project_root(tmp_path):
    cli_obj = _make_cli()
    cli_obj._app = object()
    cli_obj._active_handoff_cwd = lambda: str(tmp_path)

    template = tmp_path / "Template"
    template.mkdir()
    (template / "lakefile.lean").write_text("-- template lean project\n", encoding="utf-8")
    (template / "templates").mkdir()
    (template / "templates" / "blueprint.yml").write_text("name: demo\n", encoding="utf-8")
    target = tmp_path / "Generated"

    plan = SimpleNamespace(
        user_instruction="--source paper.pdf",
        handoff_request=SimpleNamespace(
            argv=["/usr/bin/codex", "--dangerously-bypass-approvals-and-sandbox"],
            cwd=str(target),
            env={"HOME": str(tmp_path / "home")},
        ),
        managed_context=SimpleNamespace(startup_context_path=None, backend_name="codex"),
        workflow_kind="formalize",
        backend_command="/lean4:formalize --source paper.pdf",
        project=SimpleNamespace(label="Generated Project", root=str(target)),
    )
    swarm = MagicMock()
    swarm.spawn_interactive.return_value = SimpleNamespace(task_id="af-001")

    with patch.object(cli_mod, "resolve_autoformalize_request", return_value=plan) as mock_resolve, \
         patch.object(cli_obj, "_setup_swarm_completion_callback"), \
         patch.object(cli_mod, "SwarmManager", return_value=swarm), \
         patch.object(cli_mod, "ChatConsole", return_value=MagicMock()):
        assert (
            cli_obj.process_command(
                f'/project create {target} --template-source {template} --name "Generated Project"'
            )
            is True
        )
        assert cli_obj.process_command("/formalize --source paper.pdf") is True

    assert cli_obj._project_override_root == target
    assert (target / ".gauss" / "project.yaml").is_file()
    assert (target / "templates" / "blueprint.yml").is_file()
    mock_resolve.assert_called_once_with(
        "/formalize --source paper.pdf",
        cli_obj.config,
        active_cwd=str(target),
    )


def test_project_lock_blocks_workflow_commands_before_project_selection():
    cli_obj = _make_cli()
    cli_obj._app = object()
    cli_obj._project_state = MagicMock(
        return_value=(None, "ambient", "No active Gauss project found.")
    )

    with patch.object(cli_mod, "resolve_autoformalize_request") as mock_resolve:
        assert cli_obj.process_command("/prove Main.lean") is True

    mock_resolve.assert_not_called()
    rendered = "\n".join(call.args[0] for call in cli_obj.console.print.call_args_list)
    assert "Gauss needs an active project before `/prove`." in rendered
    assert "/project init" in rendered
    assert "/chat" in rendered
    assert "/managed-chat" in rendered


def test_managed_chat_command_dispatches_to_managed_interactive_runner():
    cli_obj = _make_cli()
    cli_obj._app = object()
    task = SimpleNamespace(task_id="chat-001", status="running", pty_master_fd=99)
    swarm = MagicMock()
    swarm.spawn_interactive.return_value = task
    plan = SimpleNamespace(
        backend_name="codex",
        handoff_request=SimpleNamespace(
            argv=["/usr/bin/codex", "--dangerously-bypass-approvals-and-sandbox", "prompt"],
            cwd="/tmp",
            env={"PATH": "/usr/bin", "CODEX_HOME": "/tmp/codex-home"},
        ),
    )

    with patch.object(cli_mod, "resolve_managed_chat_request", return_value=plan), \
         patch.object(cli_mod, "SwarmManager", return_value=swarm), \
         patch.object(cli_obj, "_attach_to_swarm_task") as mock_attach:
        assert cli_obj.process_command("/managed-chat") is True

    swarm.spawn_interactive.assert_called_once()
    kwargs = swarm.spawn_interactive.call_args.kwargs
    assert kwargs["theorem"] == "managed chat"
    assert kwargs["description"] == "managed chat"
    assert kwargs["argv"] == ["/usr/bin/codex", "--dangerously-bypass-approvals-and-sandbox", "prompt"]
    assert kwargs["cwd"] == "/tmp"
    assert kwargs["workflow_kind"] == "chat"
    assert kwargs["workflow_command"] == "/managed-chat"
    assert kwargs["backend_name"] == "codex"
    assert kwargs["env"]["CODEX_HOME"] == "/tmp/codex-home"
    mock_attach.assert_called_once_with("chat-001")


def test_managed_chat_command_with_payload_forwards_startup_message():
    cli_obj = _make_cli()
    cli_obj._app = object()
    task = SimpleNamespace(task_id="chat-002", status="running", pty_master_fd=99)
    swarm = MagicMock()
    swarm.spawn_interactive.return_value = task
    plan = SimpleNamespace(
        backend_name="claude-code",
        handoff_request=SimpleNamespace(
            argv=["/usr/bin/claude", "managed prompt with payload"],
            cwd="/tmp",
            env={"PATH": "/usr/bin", "HOME": "/tmp/home"},
        ),
    )

    with patch.object(cli_mod, "resolve_managed_chat_request", return_value=plan) as mock_resolve, \
         patch.object(cli_mod, "SwarmManager", return_value=swarm), \
         patch.object(cli_obj, "_attach_to_swarm_task") as mock_attach:
        assert cli_obj.process_command("/managed-chat Explain what /project init does") is True

    mock_resolve.assert_called_once_with(
        "Explain what /project init does",
        cli_obj.config,
        active_cwd="/tmp",
    )
    kwargs = swarm.spawn_interactive.call_args.kwargs
    assert kwargs["theorem"] == "Explain what /project init does"
    assert kwargs["description"] == "Explain what /project init does"
    assert kwargs["argv"] == ["/usr/bin/claude", "managed prompt with payload"]
    assert kwargs["backend_name"] == "claude-code"
    mock_attach.assert_called_once_with("chat-002")


def test_chat_command_enables_chat_mode_and_shows_first_steps():
    cli_obj = _make_cli()
    cli_obj._app = object()
    cli_obj._project_state = MagicMock(
        return_value=(None, "ambient", "No active Gauss project found.")
    )

    assert cli_obj.process_command("/chat") is True

    assert cli_obj._chat_mode_enabled is True
    rendered = "\n".join(call.args[0] for call in cli_obj.console.print.call_args_list)
    assert "`/chat` is on." in rendered
    assert "/project use <path>" in rendered
    assert "/managed-chat" in rendered
    assert "/prove" in rendered


def test_chat_command_with_payload_queues_plain_message():
    cli_obj = _make_cli()
    cli_obj._app = object()
    cli_obj._pending_input = MagicMock()

    assert cli_obj.process_command("/chat I have a theorem but no project yet") is True

    assert cli_obj._chat_mode_enabled is True
    cli_obj._pending_input.put.assert_called_once_with("I have a theorem but no project yet")


def test_chat_status_reports_inline_chat_mode_state():
    cli_obj = _make_cli()
    cli_obj._app = object()

    assert cli_obj.process_command("/chat status") is True

    rendered = "\n".join(call.args[0] for call in cli_obj.console.print.call_args_list)
    assert "`/chat` is off." in rendered
    assert "/chat off" in rendered
    assert "/managed-chat" in rendered


def test_managed_chat_status_explains_child_session_semantics():
    cli_obj = _make_cli()
    cli_obj._app = object()
    swarm = MagicMock()

    with patch.object(cli_mod, "SwarmManager", return_value=swarm), \
         patch.object(cli_obj, "_active_managed_backend_name", return_value="codex"):
        assert cli_obj.process_command("/managed-chat status") is True

    swarm.spawn_interactive.assert_not_called()
    rendered = "\n".join(call.args[0] for call in cli_obj.console.print.call_args_list)
    assert "managed backend child session" in rendered
    assert "codex" in rendered
    assert "/autoformalize-backend" in rendered
    assert "/chat" in rendered


def test_start_alias_enables_same_inline_chat_mode():
    cli_obj = _make_cli()
    cli_obj._app = object()

    assert cli_obj.process_command("/start") is True

    assert cli_obj._chat_mode_enabled is True
    rendered = "\n".join(call.args[0] for call in cli_obj.console.print.call_args_list)
    assert "`/chat` is on." in rendered


def test_misspelled_onboarding_slash_command_autocorrects():
    cli_obj = _make_cli()
    cli_obj._app = object()
    with patch.object(cli_obj, "_handle_chat_command") as mock_chat:
        assert cli_obj.process_command("/strat") is True

    mock_chat.assert_called_once_with("/chat")


def test_handoff_alias_rewrites_to_autoformalize():
    cli_obj = _make_cli()

    with patch.object(cli_obj, "_handle_autoformalize_command") as mock_handle:
        assert cli_obj.process_command("/handoff prove the base case") is True

    cli_obj.console.print.assert_called_once()
    assert "/handoff" in cli_obj.console.print.call_args[0][0]
    assert "/autoformalize" in cli_obj.console.print.call_args[0][0]


def test_swarm_attach_without_id_defaults_to_latest_running_interactive_task():
    cli_obj = _make_cli()
    task = SimpleNamespace(task_id="af-002", status="running", pty_master_fd=99)
    swarm = MagicMock()
    swarm.latest_task.return_value = task

    with patch.object(cli_mod, "SwarmManager", return_value=swarm), \
         patch.object(cli_mod, "ChatConsole", return_value=MagicMock()), \
         patch.object(cli_mod, "attach_to_task") as mock_attach:
        assert cli_obj.process_command("/swarm attach") is True

    swarm.latest_task.assert_called_once_with(status="running", require_pty=True)
    mock_attach.assert_called_once_with(task)


def test_swarm_attach_without_id_reports_when_no_attachable_session_exists():
    cli_obj = _make_cli()
    chat_console = MagicMock()
    swarm = MagicMock()
    swarm.latest_task.return_value = None

    with patch.object(cli_mod, "SwarmManager", return_value=swarm), \
         patch.object(cli_mod, "ChatConsole", return_value=chat_console), \
         patch.object(cli_mod, "attach_to_task") as mock_attach:
        assert cli_obj.process_command("/swarm attach") is True

    mock_attach.assert_not_called()
    rendered = "\n".join(call.args[0] for call in chat_console.print.call_args_list if call.args)
    assert "No running interactive swarm task to attach to." in rendered


def test_autoformalize_backend_command_shows_active_and_configured_backend(capsys):
    cli_obj = _make_cli()
    cli_obj.config["gauss"]["autoformalize"]["backend"] = "claude-code"

    with patch.dict(cli_mod.os.environ, {"GAUSS_AUTOFORMALIZE_BACKEND": "codex"}, clear=True):
        assert cli_obj.process_command("/autoformalize-backend") is True

    captured = capsys.readouterr()
    assert "Autoformalize backend: codex" in captured.out
    assert "Config backend:" in captured.out
    assert "claude-code" in captured.out
    assert "Available:" in captured.out


def test_autoformalize_backend_command_updates_config_and_session_env(capsys):
    cli_obj = _make_cli()

    with patch.dict(cli_mod.os.environ, {}, clear=True), \
         patch.object(cli_mod, "save_config_value", return_value=True) as mock_save:
        assert cli_obj.process_command("/autoformalize-backend codex") is True
        assert cli_mod.os.environ["GAUSS_AUTOFORMALIZE_BACKEND"] == "codex"

    assert cli_obj.config["gauss"]["autoformalize"]["backend"] == "codex"
    mock_save.assert_called_once_with("gauss.autoformalize.backend", "codex")
    captured = capsys.readouterr()
    assert "Autoformalize backend set to: codex" in captured.out
    assert "active now" in captured.out


def test_autoformalize_backend_command_rejects_unknown_backend(capsys):
    cli_obj = _make_cli()

    with patch.dict(cli_mod.os.environ, {}, clear=True), \
         patch.object(cli_mod, "save_config_value") as mock_save:
        assert cli_obj.process_command("/autoformalize-backend not-real") is True

    mock_save.assert_not_called()
    captured = capsys.readouterr()
    assert "gauss.autoformalize.backend" in captured.out
    assert "claude-code" in captured.out
    assert "codex" in captured.out
