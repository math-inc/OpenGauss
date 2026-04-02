import sys
from types import SimpleNamespace


def test_top_level_skills_flag_defaults_to_chat(monkeypatch):
    import gauss_cli.main as main_mod

    captured = {}

    def fake_cmd_chat(args):
        captured["skills"] = args.skills
        captured["command"] = args.command

    monkeypatch.setattr(main_mod, "cmd_chat", fake_cmd_chat)
    monkeypatch.setattr(
        sys,
        "argv",
        ["gauss", "-s", "gauss-agent-dev,github-auth"],
    )

    main_mod.main()

    assert captured == {
        "skills": ["gauss-agent-dev,github-auth"],
        "command": None,
    }


def test_chat_subcommand_accepts_skills_flag(monkeypatch):
    import gauss_cli.main as main_mod

    captured = {}

    def fake_cmd_chat(args):
        captured["skills"] = args.skills
        captured["query"] = args.query

    monkeypatch.setattr(main_mod, "cmd_chat", fake_cmd_chat)
    monkeypatch.setattr(
        sys,
        "argv",
        ["gauss", "chat", "-s", "github-auth", "-q", "hello"],
    )

    main_mod.main()

    assert captured == {
        "skills": ["github-auth"],
        "query": "hello",
    }


def test_continue_worktree_and_skills_flags_work_together(monkeypatch):
    import gauss_cli.main as main_mod

    captured = {}

    def fake_cmd_chat(args):
        captured["continue_last"] = args.continue_last
        captured["worktree"] = args.worktree
        captured["skills"] = args.skills
        captured["command"] = args.command

    monkeypatch.setattr(main_mod, "cmd_chat", fake_cmd_chat)
    monkeypatch.setattr(
        sys,
        "argv",
        ["gauss", "-c", "-w", "-s", "gauss-agent-dev"],
    )

    main_mod.main()

    assert captured == {
        "continue_last": True,
        "worktree": True,
        "skills": ["gauss-agent-dev"],
        "command": "chat",
    }


def test_top_level_startup_input_defaults_to_chat(monkeypatch):
    import gauss_cli.main as main_mod

    captured = {}

    def fake_cmd_chat(args):
        captured["startup_input"] = args.startup_input
        captured["command"] = args.command

    monkeypatch.setattr(main_mod, "cmd_chat", fake_cmd_chat)
    monkeypatch.setattr(
        sys,
        "argv",
        ["gauss", "--startup-input", "/chat"],
    )

    main_mod.main()

    assert captured == {
        "startup_input": ["/chat"],
        "command": None,
    }


def test_chat_subcommand_accepts_startup_input_flag(monkeypatch):
    import gauss_cli.main as main_mod

    captured = {}

    def fake_cmd_chat(args):
        captured["startup_input"] = args.startup_input
        captured["query"] = args.query

    monkeypatch.setattr(main_mod, "cmd_chat", fake_cmd_chat)
    monkeypatch.setattr(
        sys,
        "argv",
        ["gauss", "chat", "--startup-input", "/start", "-q", "hello"],
    )

    main_mod.main()

    assert captured == {
        "startup_input": ["/start"],
        "query": "hello",
    }


def test_cmd_chat_forwards_startup_input_to_cli_main(monkeypatch):
    import gauss_cli.main as main_mod

    captured = {}

    def fake_cli_main(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(main_mod, "_has_any_provider_configured", lambda: True)
    monkeypatch.setattr("cli.main", fake_cli_main)

    args = SimpleNamespace(
        model=None,
        provider=None,
        toolsets=None,
        skills=None,
        startup_input=["/chat"],
        verbose=False,
        quiet=False,
        query=None,
        resume=None,
        worktree=False,
        checkpoints=False,
        pass_session_id=False,
        yolo=False,
    )

    main_mod.cmd_chat(args)

    assert captured["startup_input"] == ["/chat"]
