import pytest
from environments.agent_loop import AgentResult, ToolError
from agent.trace_masking import TRACERewardEngine

def test_trace_success_clean_trajectory():
    """Verify full credit and step penalty deduction on clean successful trajectory."""
    engine = TRACERewardEngine(step_penalty=0.01)
    
    # 2 turns, both clean, no errors, final outcome 1.0 (Success)
    messages = [
        {"role": "user", "content": "Fix it"},
        {"role": "assistant", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "Clean output here"},
        {"role": "assistant", "content": "Done"}
    ]
    
    trajectory = AgentResult(
        messages=messages,
        managed_state=None,
        turns_used=2,
        finished_naturally=True,
        reasoning_per_turn=[],
        tool_errors=[]
    )
    
    rewards = engine.compute_rewards(trajectory, 1.0)
    
    # Expected:
    # Turn 1: Mask=1.0 -> (1.0 * 1.0) - 0.01 = 0.99
    # Turn 2: Mask=1.0 -> (1.0 * 1.0) - 0.01 = 0.99
    assert rewards == [0.99, 0.99]

def test_trace_failure_clean_trajectory():
    """Verify time-penalty penalty propagates on failure trajectories."""
    engine = TRACERewardEngine(step_penalty=0.01)
    
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "Clean output"},
        {"role": "assistant", "content": "Done"}
    ]
    
    trajectory = AgentResult(
        messages=messages,
        managed_state=None,
        turns_used=2,
        finished_naturally=True,
        reasoning_per_turn=[],
        tool_errors=[]
    )
    
    rewards = engine.compute_rewards(trajectory, 0.0)
    
    # Expected:
    # Turn 1: Mask=1.0 -> (0.0 * 1.0) - 0.01 = -0.01
    # Turn 2: Mask=1.0 -> (0.0 * 1.0) - 0.01 = -0.01
    assert rewards == [-0.01, -0.01]

def test_trace_masking_heuristics():
    """Verify M_t is masked to 0.0 on empty output, error, or truncation."""
    engine = TRACERewardEngine(step_penalty=0.01)
    
    messages = [
        # Turn 1: Tool Error (Registered)
        {"role": "assistant", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "Error occurred"},
        # Turn 2: Clean
        {"role": "assistant", "tool_calls": [{"id": "2"}]},
        {"role": "tool", "content": "Healthy execution"},
        # Turn 3: Empty output
        {"role": "assistant", "tool_calls": [{"id": "3"}]},
        {"role": "tool", "content": "   "},
        # Turn 4: Truncated output
        {"role": "assistant", "tool_calls": [{"id": "4"}]},
        {"role": "tool", "content": "Some text [TRUNCATED 2000 CHARACTERS TO PREVENT BLOWOUT] end"}
    ]
    
    tool_errors = [
        ToolError(turn=1, tool_name="cmd", arguments="{}", error="Syntax", tool_result="Error occurred")
    ]
    
    trajectory = AgentResult(
        messages=messages,
        managed_state=None,
        turns_used=4,
        finished_naturally=True,
        reasoning_per_turn=[],
        tool_errors=tool_errors
    )
    
    # Evaluate Success scenario (1.0) to clearly see masks in effect
    rewards = engine.compute_rewards(trajectory, 1.0)
    
    # Expected Masks:
    # Turn 1: Mask=0.0 (Error) -> (1 * 0) - 0.01 = -0.01
    # Turn 2: Mask=1.0 (Clean) -> (1 * 1) - 0.01 = 0.99
    # Turn 3: Mask=0.0 (Empty) -> (1 * 0) - 0.01 = -0.01
    # Turn 4: Mask=0.0 (Trunc) -> (1 * 0) - 0.01 = -0.01
    
    assert pytest.approx(rewards[0]) == -0.01
    assert pytest.approx(rewards[1]) == 0.99
    assert pytest.approx(rewards[2]) == -0.01
    assert pytest.approx(rewards[3]) == -0.01
