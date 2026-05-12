"""
TerminalBench2Env -- Terminal-Bench 2.0 Evaluation Environment

Evaluates agentic LLMs on challenging terminal tasks from Terminal-Bench 2.0.
Each task provides a unique Docker environment (pre-built on Docker Hub), a natural
language instruction, and a test suite for verification. The agent uses terminal +
file tools to complete the task, then the test suite runs inside the same sandbox.

This is an eval-only environment (not a training environment). It is designed to
be run via the `evaluate` subcommand:

    python environments/terminalbench2_env.py evaluate \\
        --env.dataset_name NousResearch/terminal-bench-2

The evaluate flow:
    1. setup()     -- Loads the TB2 dataset from HuggingFace
    2. evaluate()  -- Iterates over all tasks, running each through:
        a. rollout_and_score_eval()  -- Per-task agent loop + test verification
            - Resolves Docker image (pre-built Hub image or Dockerfile fallback)
            - Registers per-task Modal sandbox via register_task_env_overrides()
            - Runs the GaussAgentLoop (terminal + file tools)
            - Uploads test suite and runs test.sh in the same sandbox
            - Returns binary pass/fail result
        b. Aggregates per-task, per-category, and overall pass rates
        c. Logs results via evaluate_log() and wandb

Key features:
  - Per-task Modal sandboxes using pre-built Docker Hub images
  - Binary reward: 1.0 if all tests pass, 0.0 otherwise
  - Concurrency-controlled parallel evaluation via asyncio.Semaphore
  - Per-task, per-category, and aggregate pass rate tracking
"""

import asyncio
import base64
import io
import json
import logging
import os
import shutil
import sys
import tarfile
import tempfile
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# Ensure repo root is on sys.path for imports
_repo_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from pydantic import Field

from atroposlib.envs.base import EvalHandlingEnum
from atroposlib.envs.server_handling.server_manager import APIServerConfig

from environments.agent_loop import AgentResult, GaussAgentLoop
from environments.gauss_base_env import GaussAgentBaseEnv, GaussAgentEnvConfig
from environments.tool_context import ToolContext
from tools.terminal_tool import (
    register_task_env_overrides,
    clear_task_env_overrides,
    cleanup_vm,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

class TerminalBench2EvalConfig(GaussAgentEnvConfig):
    """
    Configuration for the Terminal-Bench 2.0 evaluation environment.

    Extends GaussAgentEnvConfig with TB2-specific settings for dataset loading,
    test execution, task filtering, and eval concurrency.
    """

    # --- Dataset ---
    dataset_name: str = Field(
        default="NousResearch/terminal-bench-2",
        description="HuggingFace dataset containing TB2 tasks.",
    )

    # --- Test execution ---
    test_timeout: int = Field(
        default=180,
        description="Timeout in seconds for running the test suite after agent completes.",
    )

    # --- Image strategy ---
    force_build: bool = Field(
        default=False,
        description="If True, always build from Dockerfile (ignore docker_image). "
        "Useful for testing custom Dockerfiles.",
    )

    # --- Task filtering (comma-separated from CLI) ---
    task_filter: Optional[str] = Field(
        default=None,
        description="Comma-separated task names to run (e.g., 'fix-git,git-multibranch'). "
        "If not set, all tasks are run.",
    )
    skip_tasks: Optional[str] = Field(
        default=None,
        description="Comma-separated task names to skip on top of the default skip list.",
    )

    # --- Per-task wall-clock timeout ---
    task_timeout: int = Field(
        default=1800,
        description="Maximum wall-clock seconds per task (agent loop + verification). "
        "Tasks exceeding this are scored as FAIL. Default 30 minutes.",
    )

    # --- Concurrency control ---
    max_concurrent_tasks: int = Field(
        default=8,
        description="Maximum number of tasks to run concurrently. "
        "Limits concurrent Modal sandbox creations to avoid async/threading deadlocks. "
        "Modal has internal limits and creating too many sandboxes simultaneously "
        "causes blocking calls to deadlock inside the thread pool.",
    )

    # --- Eval concurrency ---
    eval_concurrency: int = Field(
        default=0,
        description="Maximum number of tasks to evaluate in parallel. "
        "0 means unlimited (all tasks run concurrently). "
        "Set to 8 for local backends to avoid overwhelming the machine.",
    )


# Tasks that cannot run properly on Modal and are excluded from scoring.
MODAL_INCOMPATIBLE_TASKS = {
    "qemu-startup",        # Needs KVM/hardware virtualization
    "qemu-alpine-ssh",     # Needs KVM/hardware virtualization
    "crack-7z-hash",       # Password brute-force -- too slow for cloud sandbox timeouts
}


# =============================================================================
# Tar extraction helper
# =============================================================================

def _extract_base64_tar(b64_data: str, target_dir: Path):
    """Extract a base64-encoded tar.gz archive into target_dir."""
    if not b64_data:
        return
    raw = base64.b64decode(b64_data)
    buf = io.BytesIO(raw)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        tar.extractall(path=str(target_dir))


# =============================================================================
# Main Environment
# =============================================================================

class TerminalBench2EvalEnv(GaussAgentBaseEnv):
    """
    Terminal-Bench 2.0 evaluation environment (eval-only, no training).

    Inherits from GaussAgentBaseEnv for:
      - Terminal backend setup (os.environ["TERMINAL_ENV"])
      - Tool resolution via _resolve_tools_for_group()
      - Monkey patches for async-safe tool operation
      - Wandb trajectory formatting

    The evaluate flow (triggered by `environment.py evaluate`):
      1. setup()    -- Load dataset from HuggingFace
      2. evaluate() -- Run all tasks through rollout_and_score_eval()

    Each task in rollout_and_score_eval():
      1. Resolve Docker image (pre-built Hub image or Dockerfile fallback)
      2. Register per-task Modal sandbox override
      3. Run GaussAgentLoop with terminal + file tools
      4. Upload test suite and execute test.sh in the same sandbox
      5. Check /logs/verifier/reward.txt for pass/fail
      6. Clean up sandbox, overrides, and temp files
    """

    name = "terminal-bench-2"
    env_config_cls = TerminalBench2EvalConfig

    @classmethod
    def config_init(cls) -> Tuple[TerminalBench2EvalConfig, List[APIServerConfig]]:
        """
        Default configuration for Terminal-Bench 2.0 evaluation.

        Uses eval-only settings:
          - eval_handling=STOP_TRAIN so the eval flow runs cleanly
          - steps_per_eval=1, total_steps=1 so eval triggers immediately
          - group_size=1 (one rollout per group, each task is expensive)

        Uses Modal terminal backend (cloud-isolated sandbox per task) and
        OpenRouter with Claude for inference.
        """
        env_config = TerminalBench2EvalConfig(
            # Terminal + file tools only (the agent interacts via shell commands)
            enabled_toolsets=["terminal", "file"],
            disabled_toolsets=None,
            distribution=None,

            # Agent settings -- TB2 tasks are complex, need many turns
            max_agent_turns=60,
            max_token_length=32000,
            agent_temperature=0.6,
            system_prompt=None,

            # Modal backend for per-task cloud-isolated sandboxes
            terminal_backend="modal",
            terminal_timeout=300,   # 5 min per command (builds, pip install, etc.)

            # Test execution timeout (TB2 test scripts can install deps like pytest)
            test_timeout=180,

            # 89 tasks run in parallel, each needs a thread for tool calls
            tool_pool_size=128,

            # --- Eval-only Atropos settings ---
            # These settings make the env work as an eval-only environment:
            #   - STOP_TRAIN: pauses training during eval (standard for eval envs)
            #   - steps_per_eval=1, total_steps=1: eval triggers immediately
            #   - group_size=1: one rollout per group (each task is expensive)
            eval_handling=EvalHandlingEnum.STOP_TRAIN,
            group_size=1,
            steps_per_eval=1,
            total_steps=1,

            tokenizer_name="NousRe...1-8B",
            use_wandb=True,
            wandb_name="terminal-bench-2",
            ensure_scores_are_not_same=False,  # Binary rewards may all be 0 or 1
        )

        # OpenRouter with Claude -- API key loaded from .env
        server_configs = [
            APIServerConfig(
                base_url="https://openrouter.ai/api/v1",
                model_name="anthropic/claude-sonnet-4",
                server_type="openai",
                api_key=os.getenv("OPENROUTER_API_KEY", ""),
                health_check=False,
            )
        ]

        return env_config, server_configs

    # =========================================================================
    # Setup -- load dataset
    # =========================================================================

    async def setup(self):
        """Load the Terminal-Bench 2.0 dataset from HuggingFace."""
        from datasets import load_dataset

        # Auto-set terminal_lifetime to task_timeout + 120s so sandboxes
        # never get killed during an active task, but still get cleaned up
        # promptly after the task times out.
        lifetime = self.config.task_timeout + 120
        self.config.terminal_lifetime = lifetime
        os.environ["TERMINAL_LIFETIME_SECONDS"] = str(lifetime)
        print(f"  Terminal lifetime auto-set to {lifetime}s (task_timeout + 120s)")

        print(f"Loading TB2 dataset from: {self.config.dataset_name}")
        ds = load_dataset(self.config.dataset_name, split="train")

        # Apply task filters (comma-separated strings from CLI)
        tasks = list(ds)
        if self.config.task_filter:
            allowed = {name.strip() for name in self.config.task_filter.split(",")}
            tasks = [t for t in tasks if t["task_name"] in allowed]
            print(f"  Filtered to {len(tasks)} tasks: {sorted(allowed)}")

        # Skip tasks incompatible with the current backend (e.g., QEMU on Modal)
        # plus any user-specified skip_tasks
        skip = set(MODAL_INCOMPATIBLE_TASKS) if self.config.terminal_backend == "modal" else set()
        if self.config.skip_tasks:
            skip |= {name.strip() for name in self.config.skip_tasks.split(",")}
        if skip:
            before = len(tasks)
            tasks = [t for t in tasks if t["task_name"] not in skip]
            skipped = before - len(tasks)
            if skipped > 0:
                print(f"  Skipped {skipped} incompatible tasks: {sorted(skip & {t['task_name'] for t in ds})}")

        self.all_eval_items = tasks
        self.iter = 0

        # Build category index for per-category metrics
        self.category_index: Dict[str, List[int]] = defaultdict(list)
        for i, task in enumerate(self.all_eval_items):
            self.category_index[task.get("category", "unknown")].append(i)

        # Reward tracking for wandb logging
        self.eval_metrics: List[Tuple[str, float]] = []

        # Streaming JSONL writer -- saves each task's full conversation
        # immediately on completion so data is preserved even on Ctrl+C.
        # Timestamped filename so each run produces a unique file.
        import datetime
        log_dir = os.path.join(os.path.dirname(__file__), "logs")
        os.makedirs(log_dir, exist_ok=True)
        run_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self._streaming_path = os.path.join(log_dir, f"samples_{run_ts}.jsonl")
        self._streaming_file = open(self._streaming_path, "w")
        self._streaming_lock = __import__("threading").Lock()
        print(f"  Streaming results to: {self._streaming_path}")

        print(f"TB2 ready: {len(self.all_eval_items)} tasks across {len(self.category_index)} categories")
        for cat, indices in sorted(self.category_index.items()):
            print(f"  {cat}: {len(indices)} tasks")

    def _save_result(self, result: Dict[str, Any]):
        """Write a single task result to the streaming JSONL file immediately."""
        if not hasattr(self, "_streaming_file") or self._streaming_file.closed:
            return
        with self._streaming_lock:
            self._streaming_file.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
            self._streaming_file.flush()

    # =========================================================================
    # Training pipeline stubs -- NOT used in eval-only mode
    # =========================================================================
    # These satisfy the abstract method requirements from GaussAgentBaseEnv.
    # The evaluate subcommand calls setup() -> evaluate() directly, bypassing
    # the training pipeline entirely.

    async def get_next_item(self):
        """Return next item (stub -- not used in eval-only mode)."""
        item = self.all_eval_items[self.iter % len(self.all_eval_items)]
        self.iter += 1
        return item

    def format_prompt(self, item: Dict[str, Any]) -> str:
        """Return the task's instruction as the user prompt."""
        return item["instruction"]

    async def compute_reward(self, item, result, ctx) -> float:
        """Compute reward (stub -- actual verification is in rollout_and_score_eval)."""
        return 0.0

    async def collect_trajectories(self, item):
        """Collect trajectories (stub -- not used in eval-only mode)."""
        return None, []

    async def score(self, rollout_group_data):
        """Score rollouts (stub -- not used in eval-only mode)."""
        return None

    # =========================================================================
    # Docker image resolution
    # =========================================================================

    def _resolve_task_image(
        self, item: Dict[str, Any], task_name: str
    ) -> Tuple[str, Optional[Path]]:
        """
        Resolve the Docker image for a task, with fallback to Dockerfile.

        Strategy (mirrors Harbor's approach):
        1. If force_build=True, always build from Dockerfile in environment_tar
        2. If docker_image is available, use the pre-built Docker Hub image (fast)
        3. Otherwise, extract Dockerfile from environment_tar and build (slow)

        Returns:
            (modal_image, temp_dir) -- modal_image is a Docker Hub name or a
            Dockerfile path. temp_dir is set if we extracted files that need
            cleanup later.
        """
        docker_image = item.get("docker_image", "")
        environment_tar = item.get("environment_tar", "")

        # Fast path: use pre-built Docker Hub image
        if docker_image and not self.config.force_build:
            logger.info("Task %s: using pre-built image %s", task_name, docker_image)
            return docker_image, None

        # Slow path: extract Dockerfile from environment_tar and build
        if environment_tar:
            task_dir = Path(tempfile.mkdtemp(prefix=f"tb2-{task_name}-"))
            _extract_base64_tar(environment_tar, task_dir)
            dockerfile_path = task_dir / "Dockerfile"
            if dockerfile_path.exists():
                logger.info(
                    "Task %s: building from Dockerfile (force_build=%s, docker_image=%s)",
                    task_name, self.config.force_build, bool(docker_image),
                )
                return str(dockerfile_path), task_dir

        # Neither available -- fall back to Hub image if force_build was True
        if docker_image:
            logger.warning(
                "Task %s: force_build=True but no environment_tar, "
                "falling back to docker_image %s", task_name, docker_image,
            )
            return docker_image, None

        return "", None

    # =========================================================================
    # Per-task evaluation -- agent loop + test verification
    # =========================================================================

    async def rollout_and_score_eval(self, eval_item: Dict[str, Any]) -> Dict:
        """
        Evaluate a single TB2 task: run the agent loop, then verify with tests.

        This is the core evaluation method. For each task it:
        1. Resolves the Docker image and registers the Modal sandbox override
        2. Runs GaussAgentLoop with terminal + file tools
        3. Uploads the test suite into the sandbox
        4. Executes test.sh and checks the result
        5. Cleans up the sandbox and temp files

        Args:
            eval_item: A single TB2 task dict from the dataset

        Returns:
            Dict with 'passed' (bool), 'reward' (float), 'task_name' (str),
            'category' (str), and optional debug info
        """
        task_name = eval_item.get("task_name", "unknown")
        category = eval_item.get("category", "unknown")
        task_id = str(uuid.uuid4())
        task_dir = None  # Set if we extract a Dockerfile (needs cleanup)

        from tqdm import tqdm
        tqdm.write(f"  [START] {task_name} (task_id={task_id[:8]})")
        task_start = time.time()

        try:
            # --- 1. Resolve Docker image ---
            modal_image, task_dir = self._resolve_task_image(eval_item, task_name)
            if not modal_image:
                logger.error("Task %s: no docker_image or environment_tar, skipping", task_name)
                return {
                    "passed": False, "reward": 0.0,
                    "task_name": task_name, "category": category,
                    "error": "no_image",
                }

            # --- 2. Register per-task image override ---
            # Set both modal_image and docker_image so the task image is used
            # regardless of which backend is configured.
            register_task_env_overrides(task_id, {
                "modal_image": modal_image,
                "docker_image": modal_image,
                "cwd": "/app",
            })
            logger.info(
                "Task %s: registered image override for task_id %s",
                task_name, task_id[:8],
            )

            # --- 3. Resolve tools and build messages ---
            tools, valid_names = self._resolve_tools_for_group()

            messages: List[Dict[str, Any]] = []
            if self.config.system_prompt:
                messages.append({"role": "system", "content": self.config.system_prompt})
            messages.append({"role": "user", "content": self.format_prompt(eval_item)})

            # --- 4. Run agent loop ---
            tree_g = int(os.getenv("TREE_SEARCH_G", "1"))
            
            if self._use_managed_server():
                async with self.server.managed_server(
                    tokenizer=self.tokenizer,
                    preserve_think_blocks=bool(self.config.thinking_mode),
                ) as managed:
                    agent = GaussAgentLoop(
                        server=managed,
                        tool_schemas=tools,
                        valid_tool_names=valid_names,
                        max_turns=self.config.max_agent_turns,
                        task_id=task_id,
                        temperature=self.config.agent_temperature,
                        max_tokens=self.config.max_token_length,
                        extra_body=self.config.extra_body,
                    )
                    results_all = await agent.run(messages, G=tree_g)
            else:
                agent = GaussAgentLoop(
                    server=self.server,
                    tool_schemas=tools,
                    valid_tool_names=valid_names,
                    max_turns=self.config.max_agent_turns,
                    task_id=task_id,
                    temperature=self.config.agent_temperature,
                    max_tokens=self.config.max_token_length,
                    extra_body=self.config.extra_body,
                )
                results_all = await agent.run(messages, G=tree_g)

            # --- 5. Tree-Search Result Consolidation & Best-of-N Scoring ---
            # Define core verification runner to execute validation in target sandbox
            def _run_verification_for_context(target_task_id: str, final_result) -> float:
                only_sys_user = all(
                    msg.get("role") in ("system", "user") for msg in final_result.messages
                )
                if final_result.turns_used == 0 or only_sys_user:
                    return 0.0
                    
                ctx = ToolContext(target_task_id)
                try:
                    # 1. Create logs directory
                    ctx.terminal("mkdir -p /logs/verifier", timeout=30)

                    # 2. Upload the verification test suite
                    tests_tar = eval_item.get("tests_tar", "")
                    if tests_tar:
                        temp_tests_dir = Path(tempfile.mkdtemp(prefix=f"tb2-tests-{target_task_id[:8]}-"))
                        try:
                            _extract_base64_tar(tests_tar, temp_tests_dir)
                            local_tar_path = temp_tests_dir / "archive.tar"
                            with tarfile.open(local_tar_path, "w") as tar:
                                for child in temp_tests_dir.iterdir():
                                    if child.name != "archive.tar":
                                        tar.add(child, arcname=child.name)
                            ctx.upload_file(str(local_tar_path), "/tmp/tests_suite.tar")
                            ctx.terminal("mkdir -p /tests && tar -xf /tmp/tests_suite.tar -C /", timeout=60)
                        finally:
                            shutil.rmtree(temp_tests_dir, ignore_errors=True)

                    # 3. Write and run test_sh script
                    test_sh = eval_item.get("test_sh", "")
                    if test_sh:
                        test_sh = test_sh.replace('\r\n', '\n')
                        ctx.write_file("/test.sh", test_sh)
                        ctx.terminal("chmod +x /test.sh", timeout=10)
                        test_result = ctx.terminal("/bin/bash /test.sh", timeout=self.config.test_timeout)
                        logger.info(f"Verification exit code for {task_name} ({target_task_id[:8]}): {test_result.get('exit_code')}")

                    # 4. Harvest reward
                    reward_val = ctx.terminal("cat /logs/verifier/reward.txt", timeout=10)
                    reward_str = reward_val.get("output", "").strip()
                    try:
                        return float(reward_str)
                    except ValueError:
                        return 1.0 if test_sh and test_result.get("exit_code") == 0 else 0.0
                except Exception as verr:
                    logger.error(f"Verification crashed for {task_name} ({target_task_id}): {verr}")
                    return 0.0
                finally:
                    ctx.cleanup()

            # Main routing logic: Consolidated single or multi-branch trajectory analysis
            reward = 0.0
            result = None
            
            if isinstance(results_all, list):
                if len(results_all) > 1:
                    tqdm.write(f"  [TREE] Multi-branch G={len(results_all)} finished. Executing Best-of-N scoring...")
                    best_res = results_all[0]
                    best_rew = -1.0
                    
                    # Validate every parallel branch sandbox in sequence
                    for idx, res in enumerate(results_all):
                        branch_task_id = getattr(res, "task_id", None) or f"{task_id}-branch-{idx}"
                        
                        # Register environment overrides so verification boots the correct image!
                        register_task_env_overrides(branch_task_id, {
                            "modal_image": modal_image,
                            "docker_image": modal_image,
                            "cwd": "/app",
                        })
                        
                        tqdm.write(f"    Scoring branch {idx} ({branch_task_id[:8] if branch_task_id else 'unknown'})...")
                        branch_reward = _run_verification_for_context(branch_task_id, res)
                        tqdm.write(f"      Branch {idx} Reward: {branch_reward}")
                        
                        # Best-of-N Selection logic
                        if branch_reward > best_rew:
                            best_rew = branch_reward
                            best_res = res
                        elif abs(branch_reward - best_rew) < 1e-5 and res.turns_used < best_res.turns_used:
                            best_res = res
                            
                    result = best_res
                    reward = best_rew
                    tqdm.write(f"  [TREE] Best-of-N Selected: Reward={reward:.2f} (Turns={result.turns_used})")
                elif len(results_all) == 1:
                    result = results_all[0]
                    tqdm.write(f"  [VERIFYING] {task_name}...")
                    reward = _run_verification_for_context(task_id, result)
                else:
                    # Empty list edge case
                    raise RuntimeError(f"Agent execution yielded 0 trajectories for {task_name}")
            else:
                result = results_all
                tqdm.write(f"  [VERIFYING] {task_name}...")
                reward = _run_verification_for_context(task_id, result)

            passed = reward >= 1.0
            duration_seconds = time.time() - task_start
            return {
                "passed": passed,
                "reward": reward,
                "task_name": task_name,
                "category": category,
                "turns_used": result.turns_used,
                "duration_seconds": duration_seconds,
            }

        except Exception as exc:
            logger.exception("Task %s failed during evaluation", task_name)
            return {
                "passed": False,
                "reward": 0.0,
                "task_name": task_name,
                "category": category,
                "error": str(exc),
            }

        finally:
            clear_task_env_overrides(task_id)
            if task_dir and Path(task_dir).exists():
                shutil.rmtree(task_dir, ignore_errors=True)

    # =========================================================================
    # Evaluate -- orchestration and concurrent execution
    # =========================================================================

    async def _run_with_timeout(self, item: Dict[str, Any], sem: Optional[asyncio.Semaphore] = None) -> Dict:
        """Wrap a single task rollout with a wall-clock timeout and concurrency semaphore."""
        task_name = item.get("task_name", "unknown")
        category = item.get("category", "unknown")
        
        if sem is not None:
            await sem.acquire()
            
        try:
            return await asyncio.wait_for(
                self.rollout_and_score_eval(item),
                timeout=self.config.task_timeout,
            )
        except asyncio.TimeoutError:
            from tqdm import tqdm
            tqdm.write(f"  [TIMEOUT] {task_name} (exceeded {self.config.task_timeout}s)")
            out = {
                "passed": False,
                "reward": 0.0,
                "task_name": task_name,
                "category": category,
                "turns_used": 0,
                "duration_seconds": float(self.config.task_timeout),
                "error": "timeout",
            }
            self._save_result(out)
            return out
        finally:
            if sem is not None:
                sem.release()

    async def evaluate(self, *args, **kwargs) -> None:
        """
        Run Terminal-Bench 2.0 evaluation over all tasks.
        
        Executes tasks with controlled concurrency via an asyncio.Semaphore,
        bounded by self.config.eval_concurrency (if set) or self.config.max_concurrent_tasks.
        """
        start_time = time.time()
        from tqdm import tqdm
        
        # --- tqdm-compatible logging handler ---
        class _TqdmHandler(logging.Handler):
            def emit(self, record):
                try:
                    tqdm.write(self.format(record))
                except Exception:
                    self.handleError(record)

        root = logging.getLogger()
        handler = _TqdmHandler()
        handler.setFormatter(
            logging.Formatter("%(levelname)s %(name)s: %(message)s")
        )
        # Clean up existing stream handlers to prevent duplicate prints
        for h in list(root.handlers):
            if isinstance(h, logging.StreamHandler):
                root.removeHandler(h)
        root.addHandler(handler)
        for noisy in ("httpx", "openai", "httpcore"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

        # Resolve concurrency limit
        concurrency = self.config.eval_concurrency
        if concurrency == 0:
            concurrency = getattr(self.config, "max_concurrent_tasks", 8)
        
        print(f"\n{'='*60}")
        print(f"Starting {self.name.upper()} Evaluation")
        print(f"{'='*60}")
        print(f"  Total tasks: {len(self.all_eval_items)}")
        print(f"  Concurrency: {concurrency}")
        print(f"  Task timeout: {self.config.task_timeout}s")
        print(f"{'='*60}\n")

        sem = asyncio.Semaphore(concurrency) if concurrency > 0 else None
        
        # Launch all tasks
        tasks = []
        for item in self.all_eval_items:
            task = asyncio.create_task(self._run_with_timeout(item, sem))
            tasks.append(task)

        results = []
        pbar = tqdm(total=len(tasks), desc=self.name.upper(), dynamic_ncols=True)

        try:
            for completed in asyncio.as_completed(tasks):
                res = await completed
                if res:
                    results.append(res)
                    self._save_result(res)
                passed_count = sum(1 for r in results if r.get("passed"))
                pbar.set_postfix_str(f"passed={passed_count}/{len(results)}")
                pbar.update(1)

        except (KeyboardInterrupt, asyncio.CancelledError):
            tqdm.write("\n[INTERRUPTED] Cancelling remaining tasks...")
            for t in tasks:
                if not t.done():
                    t.cancel()
            pbar.close()
            # Cleanup
            if hasattr(self, "_streaming_file") and not self._streaming_file.closed:
                self._streaming_file.close()
            return

        pbar.close()
        end_time = time.time()

        # --- Compute metrics ---
        valid = [r for r in results if r is not None]
        if not valid:
            print("Warning: No valid results produced.")
            return

        total = len(valid)
        passed_total = sum(1 for r in valid if r.get("passed"))
        pass_rate = passed_total / total if total else 0.0
        avg_turns = sum(r.get("turns_used", 0) for r in valid) / total if total else 0.0

        # Category breakdowns
        cat_results = defaultdict(list)
        for r in valid:
            cat = r.get("category", "unknown")
            cat_results[cat].append(r)

        eval_metrics = {
            "eval/pass_rate": pass_rate,
            "eval/total_tasks": total,
            "eval/passed_tasks": passed_total,
            "eval/avg_turns": avg_turns,
            "eval/evaluation_time_seconds": end_time - start_time,
        }

        for cat, items in sorted(cat_results.items()):
            cp = sum(1 for r in items if r.get("passed"))
            ct = len(items)
            eval_metrics[f"eval/pass_rate_{cat.replace('-', '_')}"] = cp / ct if ct else 0.0

        self.eval_metrics = [(k, v) for k, v in eval_metrics.items()]

        # --- Print Summary ---
        print(f"\n{'='*60}")
        print(f"{self.name.upper()} Evaluation Results")
        print(f"{'='*60}")
        print(f"Overall Pass Rate: {pass_rate:.1%} ({passed_total}/{total})")
        print(f"Average Turns: {avg_turns:.2f}")
        print(f"Evaluation Time: {end_time - start_time:.1f}s")
        print("\nPer-category Breakdown:")
        for cat, items in sorted(cat_results.items()):
            cp = sum(1 for r in items if r.get("passed"))
            ct = len(items)
            print(f"  {cat:<25}: {cp:>2}/{ct:<2} passed ({cp/ct:.1%})")
        print(f"{'='*60}\n")

        # --- Log to files ---
        try:
            samples = [{k: v for k, v in r.items() if k != "messages"} for r in valid]
            await self.evaluate_log(
                metrics=eval_metrics,
                samples=samples,
                start_time=start_time,
                end_time=end_time,
                generation_parameters={
                    "temperature": self.config.agent_temperature,
                    "max_tokens": self.config.max_token_length,
                    "max_agent_turns": self.config.max_agent_turns,
                }
            )
        except Exception as e:
            print(f"Error logging results: {e}")

        # Cleanup
        if hasattr(self, "_streaming_file") and not self._streaming_file.closed:
            self._streaming_file.close()
            print(f"Streaming results finalized in: {self._streaming_path}")

        try:
            from tools.terminal_tool import cleanup_all_environments
            cleanup_all_environments()
        except Exception:
            pass

        try:
            from environments.agent_loop import _tool_executor
            _tool_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass

    async def wandb_log(self, wandb_metrics: Optional[Dict] = None):
        """Log TB2-specific metrics to wandb."""
        if wandb_metrics is None:
            wandb_metrics = {}
        for k, v in getattr(self, "eval_metrics", []):
            wandb_metrics[k] = v
        self.eval_metrics = []
        await super().wandb_log(wandb_metrics)
