#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import time
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gauss_cli.autoformalize import resolve_autoformalize_request
from gauss_cli.config import load_config
from gauss_cli.project import initialize_gauss_project
from swarm_manager import SwarmManager, SwarmTask


def _resolve_backend_and_auth(explicit_backend: str | None) -> tuple[str, str]:
    if explicit_backend:
        backend = explicit_backend.strip().lower()
        if backend not in {"claude-code", "codex"}:
            raise SystemExit(f"unsupported backend override: {explicit_backend}")
        return backend, "api-key"

    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return "claude-code", "api-key"
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return "codex", "api-key"
    raise SystemExit("set ANTHROPIC_API_KEY or OPENAI_API_KEY before running this smoke")


def _tail_output(task: SwarmTask, limit: int = 80) -> list[str]:
    lines = task._output_lines or []
    return lines[-limit:]


def _print_task_diagnostics(task: SwarmTask) -> None:
    print(f"task_id={task.task_id}")
    print(f"status={task.status}")
    print(f"progress={task.progress}")
    if task.error:
        print(f"error={task.error}")
    if task.result:
        print("result:")
        print(task.result)
    lines = _tail_output(task)
    if lines:
        print("output_tail:")
        for line in lines:
            print(line)


def _wait_for_task(
    manager: SwarmManager,
    task: SwarmTask,
    *,
    project_dir: Path,
    target_path: Path,
    timeout_seconds: int,
) -> bool:
    """Wait for an interactive managed workflow to land a proof or terminate.

    `/prove` sessions are interactive and can stay open after the proof lands, so
    this smoke treats "target file no longer contains sorry and lake build passes"
    as success and then terminates the managed session.
    """
    thread = task.thread
    if thread is None:
        raise RuntimeError("managed workflow did not create a task thread")

    deadline = time.time() + timeout_seconds
    last_report_at = 0.0
    while True:
        if target_path.is_file():
            rendered = target_path.read_text(encoding="utf-8")
            if "sorry" not in rendered:
                _run_lake_build(project_dir)
                if task.status not in {"complete", "failed", "cancelled"}:
                    print("==> Proof landed; stopping interactive managed session")
                    manager.cancel(task.task_id)
                    thread.join(timeout=10)
                return True

        if task.status in {"complete", "failed", "cancelled"} and not thread.is_alive():
            return False

        remaining = deadline - time.time()
        if remaining <= 0:
            raise TimeoutError(
                f"managed /prove workflow did not finish within {timeout_seconds}s"
            )

        now = time.time()
        if now - last_report_at >= 10:
            print(f"heartbeat: status={task.status} progress={task.progress}")
            last_lines = _tail_output(task, limit=8)
            if last_lines:
                print("heartbeat_output_tail:")
                for line in last_lines:
                    print(line)
            last_report_at = now

        thread.join(timeout=min(1.0, remaining))


def _run_lake_build(project_dir: Path) -> None:
    subprocess.run(
        ["lake", "build"],
        cwd=project_dir,
        env=dict(os.environ),
        check=True,
        text=True,
    )


def _run_codex_exec(plan, timeout_seconds: int) -> None:
    argv = list(plan.handoff_request.argv)
    if not argv or Path(argv[0]).name != "codex":
        raise SystemExit(f"expected a codex launch argv, got: {argv!r}")

    exec_argv = [argv[0], "exec", "--skip-git-repo-check", *argv[1:]]
    print(f"==> Running Codex exec smoke: {shlex.join(exec_argv)}")

    try:
        completed = subprocess.run(
            exec_argv,
            cwd=plan.handoff_request.cwd,
            env=plan.handoff_request.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(
            f"codex exec did not finish within {timeout_seconds}s"
        ) from exc

    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    if stdout:
        print("codex_stdout_tail:")
        for line in stdout.splitlines()[-80:]:
            print(line)
    if stderr:
        print("codex_stderr_tail:")
        for line in stderr.splitlines()[-80:]:
            print(line)

    if completed.returncode != 0:
        raise SystemExit(f"codex exec failed with exit code {completed.returncode}")


def _assert_staged_artifacts(plan, project_dir: Path, target_path: Path) -> None:
    managed = plan.managed_context
    staged = plan.staged_paths()

    print("==> Verifying staged managed workflow artifacts")

    if managed.project_root != project_dir:
        raise SystemExit(
            f"managed project root mismatch: expected {project_dir}, got {managed.project_root}"
        )
    if managed.lean_root != project_dir:
        raise SystemExit(
            f"managed lean root mismatch: expected {project_dir}, got {managed.lean_root}"
        )
    if managed.project_manifest_path is None or not managed.project_manifest_path.is_file():
        raise SystemExit("managed project manifest was not staged")
    if managed.startup_context_path is None or not managed.startup_context_path.is_file():
        raise SystemExit("managed startup context was not staged")
    if managed.instructions_path is None or not managed.instructions_path.is_file():
        raise SystemExit("managed backend instructions were not staged")
    if managed.skills_root is None or not managed.skills_root.joinpath("SKILL.md").is_file():
        raise SystemExit("managed Lean skill was not staged")
    if not managed.plugin_root.exists():
        raise SystemExit("managed Lean plugin root was not staged")
    if not managed.mcp_config_path.is_file():
        raise SystemExit("managed MCP config was not staged")
    if managed.backend_config_path is not None and not managed.backend_config_path.is_file():
        raise SystemExit("managed backend config path was recorded but missing on disk")

    startup_text = managed.startup_context_path.read_text(encoding="utf-8")
    instructions_text = managed.instructions_path.read_text(encoding="utf-8")
    mcp_text = managed.mcp_config_path.read_text(encoding="utf-8")
    mcp_config = tomllib.loads(mcp_text)

    expected_backend_line = f"- Backend command: `{plan.backend_command}`"
    expected_project_line = f"- Project root: `{project_dir}`"
    expected_target = str(target_path.relative_to(project_dir))

    if expected_backend_line not in startup_text:
        raise SystemExit("startup context is missing the expected backend command")
    if expected_project_line not in startup_text:
        raise SystemExit("startup context is missing the expected project root")
    if expected_target not in startup_text:
        raise SystemExit("startup context is missing the target file path")
    if str(project_dir) not in instructions_text:
        raise SystemExit("managed backend instructions are missing the project root")
    if str(managed.skills_root) not in instructions_text:
        raise SystemExit("managed backend instructions are missing the staged skill path")

    lean_lsp = mcp_config.get("mcp_servers", {}).get("lean-lsp")
    if not isinstance(lean_lsp, dict):
        raise SystemExit("managed MCP config is missing the lean-lsp server entry")
    env = lean_lsp.get("env", {})
    if env.get("LEAN_PROJECT_PATH") != str(project_dir):
        raise SystemExit(
            "managed MCP config points LEAN_PROJECT_PATH at the wrong project root"
        )

    print(f"staged_backend={managed.backend_name}")
    print(f"staged_project_root={managed.project_root}")
    print(f"staged_skill_root={managed.skills_root}")
    print(f"staged_plugin_root={managed.plugin_root}")
    print(f"staged_mcp_config={managed.mcp_config_path}")
    print(f"staged_startup_context={managed.startup_context_path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a managed /prove smoke against a tiny Lean project."
    )
    parser.add_argument("--project-dir", required=True, help="Path to the Lean project copy")
    parser.add_argument(
        "--target",
        default="HelloSorry/Basic.lean",
        help="Relative path forwarded to /prove",
    )
    parser.add_argument(
        "--backend",
        default="",
        help="Optional backend override: claude-code or codex",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=900,
        help="How long to wait for the managed workflow to finish",
    )
    parser.add_argument(
        "--live-run",
        action="store_true",
        help="Actually run the managed backend after staging (best-effort debug path).",
    )
    args = parser.parse_args()

    backend_name, auth_mode = _resolve_backend_and_auth(args.backend or None)

    project_dir = Path(args.project_dir).expanduser().resolve()
    target_path = (project_dir / args.target).resolve()

    if not project_dir.is_dir():
        raise SystemExit(f"project directory not found: {project_dir}")
    if not target_path.is_file():
        raise SystemExit(f"target file not found: {target_path}")

    print(f"==> Initializing Gauss project in {project_dir}")
    project = initialize_gauss_project(project_dir, name="Installer Hello Sorry")
    print(f"==> Project manifest: {project.manifest_path}")

    config = load_config()
    base_env = dict(os.environ)
    base_env["GAUSS_AUTOFORMALIZE_BACKEND"] = backend_name
    base_env["GAUSS_AUTOFORMALIZE_AUTH_MODE"] = auth_mode

    command = f"/prove {args.target}"
    print(f"==> Resolving managed workflow: {command} ({backend_name}, {auth_mode})")
    plan = resolve_autoformalize_request(
        command,
        config,
        active_cwd=str(project_dir),
        base_env=base_env,
    )

    _assert_staged_artifacts(plan, project_dir, target_path)

    if not args.live_run:
        print("==> Staging smoke complete")
        return 0

    if plan.managed_context.backend_name == "codex":
        _run_codex_exec(plan, args.timeout_seconds)
    else:
        SwarmManager.reset()
        manager = SwarmManager()
        description = plan.user_instruction or args.target
        print("==> Spawning managed workflow task")
        task = manager.spawn_interactive(
            theorem=plan.user_instruction or plan.backend_command,
            description=description,
            argv=plan.handoff_request.argv,
            cwd=plan.handoff_request.cwd,
            env=plan.handoff_request.env,
            workflow_kind=plan.workflow_kind,
            workflow_command=plan.backend_command,
            project_name=plan.project.label,
            project_root=str(plan.project.root),
            backend_name=plan.managed_context.backend_name,
        )

        solved = False
        try:
            solved = _wait_for_task(
                manager,
                task,
                project_dir=project_dir,
                target_path=target_path,
                timeout_seconds=args.timeout_seconds,
            )
        except TimeoutError as exc:
            manager.cancel(task.task_id)
            _print_task_diagnostics(task)
            raise SystemExit(str(exc)) from exc

        print("==> Managed workflow finished")
        _print_task_diagnostics(task)

        if not solved and task.status != "complete":
            raise SystemExit(f"managed workflow failed with status={task.status}")

    rendered = target_path.read_text(encoding="utf-8")
    if "sorry" in rendered:
        print("final_target_contents:")
        print(rendered)
        raise SystemExit(f"{target_path} still contains `sorry`")

    print("==> Verifying final Lean build")
    try:
        _run_lake_build(project_dir)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"final lake build failed with exit code {exc.returncode}") from exc

    print(f"==> Success: managed /prove removed sorry from {target_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
