"""
Turn-Aware Credit Assignment (TRACE) Engine for Tree-GRPO.

Provides deterministic, O(N) heuristic mask computation for multi-turn trajectories,
effectively replacing expensive LLM-as-a-judge Process Reward Models (PRMs) and
preventing advantage collapse in Group Relative setups.
"""

import logging
from typing import List
from environments.agent_loop import AgentResult

logger = logging.getLogger(__name__)


class TRACERewardEngine:
    """
    TRACERewardEngine calculates sparse binary outcome backpropagation using Turn-Aware Masks.

    By utilizing deterministic, rule-based heuristics to evaluate execution health,
    TRACE eliminates the inference latency of iterative process evaluation while maintaining
    clear, high-fidelity gradient signals for action optimization.
    """

    def __init__(self, step_penalty: float = 0.01):
        """
        Initialize the TRACE Engine.

        Args:
            step_penalty (float): Penalty lambda subtracted at each turn to discourage
                                  endless loops or excessive computation. Defaults to 0.01.
        """
        self.step_penalty = step_penalty

    def compute_rewards(self, trajectory: AgentResult, final_outcome: float) -> List[float]:
        """
        Computes heuristic step rewards based on Turn-Aware Credit Assignment (TRACE).

        Evaluates the trajectory conversation chunks and telemetry to derive a
        mask array M_t. The stepwise reward is modeled as:
            r_t = (final_outcome * M_t) - lambda

        Args:
            trajectory: The completed AgentResult object containing the trajectory messages
                        and tool_error lists.
            final_outcome: The binary final unit-test outcome, 1.0 for resolution or 0.0 for failure.

        Returns:
            List[float]: An ordered list of scalar rewards [r_1, r_2, ..., r_T].
        """
        rewards: List[float] = []
        messages = trajectory.messages
        
        # 1. Pre-index error turn numbers for O(1) lookup efficiency
        # Note: ToolError.turn is 1-indexed as set in environments/agent_loop.py
        error_turns = {error.turn for error in (trajectory.tool_errors or [])}
        
        current_turn = 0
        idx = 0
        n_msgs = len(messages)
        
        # 2. Traverse trajectory sequentially to extract and evaluate discrete turns
        while idx < n_msgs:
            msg = messages[idx]
            role = msg.get("role")
            
            # In GaussAgentLoop 2.0, a discrete turn begins with the 'assistant' action
            if role == "assistant":
                current_turn += 1
                
                has_tool_calls = bool(msg.get("tool_calls"))
                
                # Gather all adjacent tool-response logs that follow this action
                tool_responses: List[str] = []
                next_idx = idx + 1
                while next_idx < n_msgs and messages[next_idx].get("role") == "tool":
                    content = messages[next_idx].get("content", "")
                    if isinstance(content, str):
                        tool_responses.append(content)
                    next_idx += 1
                
                # Jump search cursor to the next action/boundary
                idx = next_idx
                
                # --- TRACE MASKING HEURISTIC (M_t) ---
                # Default to Full Credit
                mask = 1.0
                
                # CRITERIA A: Registered execution/syntax tool errors (syntax, args, command crash)
                if current_turn in error_turns:
                    logger.debug("Turn %d: Mask=0.0 due to registered execution error.", current_turn)
                    mask = 0.0
                    
                # CRITERIA B: Empty or whitespace-only stdout/stderr (useless execution)
                elif has_tool_calls and (not tool_responses or any(not r or not r.strip() for r in tool_responses)):
                    logger.debug("Turn %d: Mask=0.0 due to empty or blank tool response.", current_turn)
                    mask = 0.0
                    
                # CRITERIA C: Memory-boundary overflow indicator injected by ObservationTruncator
                elif has_tool_calls and any("[TRUNCATED" in r for r in tool_responses):
                    logger.debug("Turn %d: Mask=0.0 due to observation buffer truncation.", current_turn)
                    mask = 0.0
                
                # 3. Advantage Derivation: r_t = (R_final * M_t) - lambda
                r_t = (final_outcome * mask) - self.step_penalty
                rewards.append(r_t)
                
            else:
                # Step through prefix context roles (system, user)
                idx += 1
                
        return rewards
