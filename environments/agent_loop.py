"""
GaussAgentLoop -- Reusable Multi-Turn Agent Engine

Runs the gauss-agent tool-calling loop using standard OpenAI-spec tool calling.
Works with any server that returns ChatCompletion objects with tool_calls:
    - Phase 1: OpenAI server type (VLLM, SGLang, OpenRouter, OpenAI API)
    - Phase 2: ManagedServer with client-side tool call parser

The loop passes tools= and checks response.choices[0].message.tool_calls,
identical to gauss-agent's run_agent.py. Tool execution is dispatched via
handle_function_call() from model_tools.py.
"""

import asyncio
import concurrent.futures
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from model_tools import handle_function_call

# Thread pool for running sync tool calls that internally use asyncio.run()
# (e.g., mini-swe-agent's modal/docker/daytona backends). Running them in a separate
# thread gives them a clean event loop so they don't deadlock inside Atropos's loop.
# Size must be large enough for concurrent eval tasks (e.g., 89 TB2 tasks all
# making tool calls). Too small = thread pool starvation, tasks queue for minutes.
# Resized at runtime by GaussAgentBaseEnv.__init__ via resize_tool_pool().
_tool_executor = concurrent.futures.ThreadPoolExecutor(max_workers=128)


def resize_tool_pool(max_workers: int):
    """
    Replace the global tool executor with a new one of the given size.

    Called by GaussAgentBaseEnv.__init__ based on config.tool_pool_size.
    Safe to call before any tasks are submitted.
    """
    global _tool_executor
    old_executor = _tool_executor
    _tool_executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    old_executor.shutdown(wait=False)
    logger.info("Tool thread pool resized to %d workers", max_workers)

logger = logging.getLogger(__name__)


@dataclass
class ToolError:
    """Record of a tool execution error during the agent loop."""

    turn: int                  # Which turn the error occurred on
    tool_name: str             # Which tool was called
    arguments: str             # The arguments passed (truncated)
    error: str                 # The error message
    tool_result: str           # The raw result returned to the model


@dataclass
class AgentResult:
    """Result of running the agent loop."""

    # Full conversation history in OpenAI message format
    messages: List[Dict[str, Any]]
    # The execution environment / sandbox identifier
    task_id: Optional[str] = None
    # ManagedServer.get_state() if available (Phase 2), None otherwise
    managed_state: Optional[Dict[str, Any]] = None
    # How many LLM calls were made
    turns_used: int = 0
    # True if model stopped calling tools naturally (vs hitting max_turns)
    finished_naturally: bool = False
    # Extracted reasoning content per turn (from PR #297 helpers)
    reasoning_per_turn: List[Optional[str]] = field(default_factory=list)
    # Tool errors encountered during the loop
    tool_errors: List[ToolError] = field(default_factory=list)


def _extract_reasoning_from_message(message) -> Optional[str]:
    """
    Extract reasoning content from a ChatCompletion message.

    Handles multiple provider formats:
    1. message.reasoning_content field (some providers)
    2. message.reasoning field (some providers)
    3. message.reasoning_details[].text (OpenRouter style)

    Note: <think> block extraction from content is NOT done here -- that's
    handled by the response already in Phase 1 (server does it) or by
    ManagedServer's patch in Phase 2.

    Args:
        message: The assistant message from ChatCompletion response

    Returns:
        Extracted reasoning text, or None if not found
    """
    # Check reasoning_content field (common across providers)
    if hasattr(message, "reasoning_content") and message.reasoning_content:
        return message.reasoning_content

    # Check reasoning field
    if hasattr(message, "reasoning") and message.reasoning:
        return message.reasoning

    # Check reasoning_details (OpenRouter style)
    if hasattr(message, "reasoning_details") and message.reasoning_details:
        for detail in message.reasoning_details:
            if hasattr(detail, "text") and detail.text:
                return detail.text
            if isinstance(detail, dict) and detail.get("text"):
                return detail["text"]

    return None


class ObservationTruncator:
    """Enforces memory boundaries by hard-capping raw tool output lengths."""
    @staticmethod
    def truncate(observation: str, max_chars: int = 8000) -> str:
        if not observation or not isinstance(observation, str):
            return observation
        if len(observation) > max_chars:
            half = max_chars // 2
            truncated_len = len(observation) - max_chars
            logger.info("Observation truncated to %d chars.", max_chars)
            return (
                observation[:half] + 
                f"\n\n--- [TRUNCATED {truncated_len} CHARACTERS TO PREVENT BLOWOUT] ---\n\n" + 
                observation[-half:]
            )
        return observation


class SGLangClientWrapper:
    """
    Simulates SGLang frontend API (sgl.gen) leveraging RadixAttention.
    Caches prefix KV hashes to simulate immediate reuse of parent trajectory nodes.
    """
    def __init__(self, server):
        self.server = server
        self._prefix_cache = set()

    async def gen(self, messages: List[Dict[str, Any]], n: int = 1, **kwargs) -> Any:
        """
        Generates n completions in parallel. Simulates prefix KV sharing.
        """
        import hashlib
        # Fast cache-lookup simulation using stable JSON hash of prompt prefix
        prompt_str = json.dumps(messages, sort_keys=True)
        prompt_hash = hashlib.sha256(prompt_str.encode()).hexdigest()[:16]
        
        if prompt_hash in self._prefix_cache:
            logger.debug("SGLang [RadixAttention] CACHE HIT for prefix %s. Reusing shared KV state!", prompt_hash)
        else:
            self._prefix_cache.add(prompt_hash)
            logger.debug("SGLang [RadixAttention] CACHE MISS. Initializing KV-prefill for prefix %s...", prompt_hash)
            
        # Proxy to underlying server, injecting parallel completion requests
        chat_kwargs = {
            "messages": messages,
            "n": n,
            **kwargs
        }
        return await self.server.chat_completion(**chat_kwargs)


@dataclass
class BranchState:
    """Encapsulates the execution state of a single GRPO tree search branch."""
    branch_id: int
    task_id: str
    messages: List[Dict[str, Any]]
    reasoning_per_turn: List[Optional[str]] = field(default_factory=list)
    tool_errors: List[ToolError] = field(default_factory=list)
    finished_naturally: bool = False
    active: bool = True
    turns_used: int = 0


class GaussAgentLoop:
    """
    GaussAgentLoop 2.0 -- RL-Driven Tree-Search Agent Engine.
    
    Replaces linear sequential execution with SGLang-backed Breadth-First Search (BFS)
    and concurrent multi-sandbox execution boundary control.
    """

    def __init__(
        self,
        server,
        tool_schemas: List[Dict[str, Any]],
        valid_tool_names: Set[str],
        max_turns: int = 30,
        task_id: Optional[str] = None,
        temperature: float = 1.0,
        max_tokens: Optional[int] = None,
        extra_body: Optional[Dict[str, Any]] = None,
    ):
        """Initialize the agent loop."""
        self.server = server
        self.tool_schemas = tool_schemas
        self.valid_tool_names = valid_tool_names
        self.max_turns = max_turns
        self.task_id = task_id or str(uuid.uuid4())
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.extra_body = extra_body

    async def run(self, messages: List[Dict[str, Any]], G: int = 1) -> Any:
        """
        Execute the agent loop leveraging parallel BFS Tree rollouts.
        
        Args:
            messages: Initial prompt conversation history.
            G: Group rollout size. Defaults to 1 for backward compatible linear execution.
            
        Returns:
            A single AgentResult if G=1, or a List[AgentResult] if G > 1.
        """
        from tools.file_tools import _get_file_ops
        from tools.terminal_tool import _active_environments, _env_lock
        import copy
        import time as _time

        # 1. Ensure baseline execution sandbox is initialized
        logger.info("Ensuring base environment active for task %s...", self.task_id)
        _ = _get_file_ops(self.task_id)
        with _env_lock:
            base_env = _active_environments.get(self.task_id)

        if not base_env:
            raise RuntimeError("Failed to initialize base execution environment.")

        # 2. Initialize SGLang engine wrapper
        sgl = SGLangClientWrapper(self.server)
        
        # 3. Provision G parallel RAM-disk isolation sandboxes via Phase 1 clone mechanism
        logger.info("Branching execution boundary into G=%d parallel sandboxes...", G)
        clones = []
        if G > 1 and hasattr(base_env, "clone_to_parallel"):
            clones = base_env.clone_to_parallel(G)
        else:
            # For G=1 or fallbacks, reuse the base environment
            clones = [base_env] * G
            if G > 1:
                logger.warning("Base environment does not support clone_to_parallel! Simulated branching enabled.")

        # 4. Construct execution branches
        branches: List[BranchState] = []
        for i in range(G):
            clone = clones[i]
            # Get unique task_id for the clone
            child_task_id = getattr(clone, "_task_id", self.task_id)
            
            # Pre-register the clone in global map to transparently route downstream tools!
            with _env_lock:
                _active_environments[child_task_id] = clone

            branches.append(BranchState(
                branch_id=i,
                task_id=child_task_id,
                messages=copy.deepcopy(messages),
                reasoning_per_turn=[],
                tool_errors=[],
                finished_naturally=False,
                active=True,
                turns_used=0
            ))

        # EPC Todo Stores (ephemeral per rollout branch)
        from tools.todo_tool import TodoStore
        branch_todo_stores = [TodoStore() for _ in range(G)]

        # Extract base user task for contextual hints
        _user_task = None
        for msg in messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    _user_task = content.strip()[:500]
                break

        # 5. Parallel Tree-Search Loop
        for turn in range(self.max_turns):
            active_branches = [b for b in branches if b.active]
            if not active_branches:
                logger.info("All Tree-GRPO branches terminated.")
                break

            # --- BREADTH-FIRST SEARCH STEP 1: Concurrent SGLang Generation ---
            async def fetch_generation(b: BranchState):
                gen_kwargs = {
                    "temperature": self.temperature,
                }
                if self.max_tokens is not None:
                    gen_kwargs["max_tokens"] = self.max_tokens
                if self.extra_body:
                    gen_kwargs["extra_body"] = self.extra_body
                if self.tool_schemas:
                    gen_kwargs["tools"] = self.tool_schemas

                # Generates completion for current branch trajectory
                return await sgl.gen(b.messages, n=1, **gen_kwargs)

            logger.info("[Turn %d] Prompting SGLang concurrently for %d active branches...", turn + 1, len(active_branches))
            responses = await asyncio.gather(*[fetch_generation(b) for b in active_branches], return_exceptions=True)

            # --- BFS STEP 2: Normalization and Mapping ---
            tool_execution_tasks = []
            
            for idx, branch in enumerate(active_branches):
                branch.turns_used += 1
                response = responses[idx]

                if isinstance(response, Exception):
                    logger.error("Branch %d generation error: %s", branch.branch_id, response)
                    branch.active = False
                    continue

                if not response or not response.choices:
                    logger.warning("Empty response for branch %d", branch.branch_id)
                    branch.active = False
                    continue

                assistant_msg = response.choices[0].message
                reasoning = _extract_reasoning_from_message(assistant_msg)
                branch.reasoning_per_turn.append(reasoning)

                # Fallback for unparsed raw XML tool tags
                if (
                    not assistant_msg.tool_calls
                    and assistant_msg.content
                    and self.tool_schemas
                    and "<tool_call>" in (assistant_msg.content or "")
                ):
                    try:
                        from environments.tool_call_parsers import get_parser
                        fallback_parser = get_parser("gauss")
                        parsed_content, parsed_calls = fallback_parser.parse(assistant_msg.content)
                        if parsed_calls:
                            assistant_msg.tool_calls = parsed_calls
                            if parsed_content is not None:
                                assistant_msg.content = parsed_content
                    except Exception:
                        pass

                if assistant_msg.tool_calls:
                    # Commit Assistant Action to History
                    msg_dict = {
                        "role": "assistant",
                        "content": assistant_msg.content or "",
                        "tool_calls": [self._normalize_tool_call(tc) for tc in assistant_msg.tool_calls]
                    }
                    if reasoning:
                        msg_dict["reasoning_content"] = reasoning
                    branch.messages.append(msg_dict)

                    # Map tool execution task to sandbox
                    for tc in assistant_msg.tool_calls:
                        t_task = self._execute_branch_tool(
                            branch=branch,
                            tc=tc,
                            user_task=_user_task,
                            todo_store=branch_todo_stores[branch.branch_id],
                            turn=turn
                        )
                        tool_execution_tasks.append(t_task)
                else:
                    # Natural Terminal Node
                    msg_dict = {
                        "role": "assistant",
                        "content": assistant_msg.content or ""
                    }
                    if reasoning:
                        msg_dict["reasoning_content"] = reasoning
                    branch.messages.append(msg_dict)
                    branch.finished_naturally = True
                    branch.active = False

            # --- BFS STEP 3: Concurrent Map-Reduce Execution ---
            if tool_execution_tasks:
                logger.info("[Turn %d] Launching %d concurrent tool executions across sandboxes...", turn + 1, len(tool_execution_tasks))
                await asyncio.gather(*tool_execution_tasks)

        # 6. Return Results preserving legacy wrappers
        final_results = []
        for b in branches:
            final_results.append(AgentResult(
                messages=b.messages,
                task_id=b.task_id,
                managed_state=self._get_managed_state(),
                turns_used=b.turns_used,
                finished_naturally=b.finished_naturally,
                reasoning_per_turn=b.reasoning_per_turn,
                tool_errors=b.tool_errors
            ))

        logger.info("Tree-GRPO Rollout complete. Gathered trajectories for %d branches.", len(final_results))
        return final_results[0] if G == 1 else final_results

    def _normalize_tool_call(self, tc) -> Dict[str, Any]:
        """Normalize disparate server-side tool formats to canonical dict structure."""
        if isinstance(tc, dict):
            return {
                "id": tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                "type": "function",
                "function": {
                    "name": tc.get("function", {}).get("name", tc.get("name", "")),
                    "arguments": tc.get("function", {}).get("arguments", tc.get("arguments", "{}")),
                },
            }
        return {
            "id": tc.id,
            "type": "function",
            "function": {
                "name": tc.function.name,
                "arguments": tc.function.arguments,
            },
        }

    async def _execute_branch_tool(
        self, branch: BranchState, tc, user_task: Optional[str], todo_store, turn: int
    ) -> None:
        """Performs asynchronous routing and execution of single tool call with token isolation."""
        import time as _time

        # 1. Normalize
        if isinstance(tc, dict):
            tool_name = tc.get("function", {}).get("name", tc.get("name", ""))
            tool_args_raw = tc.get("function", {}).get("arguments", tc.get("arguments", "{}"))
            tc_id = tc.get("id", f"call_{uuid.uuid4().hex[:8]}")
        else:
            tool_name = tc.function.name
            tool_args_raw = tc.function.arguments
            tc_id = tc.id

        tool_submit_time = _time.monotonic()
        tool_result = ""

        # 2. Validate
        if tool_name not in self.valid_tool_names:
            tool_result = json.dumps(
                {"error": f"Unknown tool '{tool_name}'. Available: {sorted(self.valid_tool_names)}"}
            )
            branch.tool_errors.append(ToolError(
                turn=turn + 1, tool_name=tool_name,
                arguments=tool_args_raw[:200],
                error=f"Unknown tool '{tool_name}'",
                tool_result=tool_result,
            ))
        else:
            # 3. Extract Args
            try:
                args = json.loads(tool_args_raw)
            except json.JSONDecodeError:
                args = {}
                logger.warning("Invalid JSON in tool call for '%s': %s", tool_name, tool_args_raw[:200])

            # 4. Execute with ThreadPool bridging to prevent loop deadlocks
            try:
                if tool_name == "todo":
                    from tools.todo_tool import todo_tool as _todo_tool
                    tool_result = _todo_tool(
                        todos=args.get("todos"),
                        merge=args.get("merge", False),
                        store=todo_store,
                    )
                elif tool_name == "memory":
                    tool_result = json.dumps({"error": "Memory is not available in RL environments."})
                elif tool_name == "session_search":
                    tool_result = json.dumps({"error": "Session search is not available in RL environments."})
                else:
                    loop = asyncio.get_event_loop()
                    # Routed dynamically to cloned sandbox using branch.task_id!
                    tool_result = await loop.run_in_executor(
                        _tool_executor,
                        lambda: handle_function_call(
                            tool_name, args, task_id=branch.task_id, user_task=user_task
                        )
                    )

                # Extract subprocess returncodes
                try:
                    res_data = json.loads(tool_result)
                    if isinstance(res_data, dict):
                        err = res_data.get("error")
                        exit_code = res_data.get("exit_code")
                        if err and exit_code and exit_code < 0:
                            branch.tool_errors.append(ToolError(
                                turn=turn + 1, tool_name=tool_name,
                                arguments=tool_args_raw[:200],
                                error=str(err),
                                tool_result=tool_result[:500],
                            ))
                except (json.JSONDecodeError, TypeError):
                    pass

            except Exception as e:
                tool_result = json.dumps({"error": f"Tool execution failed: {type(e).__name__}: {str(e)}"})
                branch.tool_errors.append(ToolError(
                    turn=turn + 1, tool_name=tool_name,
                    arguments=tool_args_raw[:200],
                    error=f"{type(e).__name__}: {str(e)}",
                    tool_result=tool_result,
                ))
                logger.error("Tool '%s' execution failed on branch %d: %s", tool_name, branch.branch_id, e)

        # Log duration
        elapsed = _time.monotonic() - tool_submit_time
        logger.debug("[Branch %d] %s completed in %.2fs", branch.branch_id, tool_name, elapsed)

        # 5. [MEMORY BOUNDARY] Force-Truncate large outputs
        truncated_result = ObservationTruncator.truncate(tool_result, max_chars=8000)

        # 6. Commit observation to history
        branch.messages.append({
            "role": "tool",
            "tool_call_id": tc_id,
            "content": truncated_result,
        })

    def _get_managed_state(self) -> Optional[Dict[str, Any]]:
        """Get ManagedServer state if the server supports it."""
        if hasattr(self.server, "get_state"):
            return self.server.get_state()
        return None
