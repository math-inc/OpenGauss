from pathlib import Path

import yaml


TEMPLATE_PATH = Path(__file__).resolve().parents[1] / ".github" / "morph" / "opengauss-template.yaml"


def _load_template() -> dict:
    return yaml.safe_load(TEMPLATE_PATH.read_text(encoding="utf-8"))


def test_morph_template_restores_optional_provider_secret_staging():
    template = _load_template()
    steps = template["steps"]
    step_ids = [step["id"] for step in steps]
    by_id = {step["id"]: step for step in steps}

    assert "stages optional provider keys" in template["description"]

    expected_secret_steps = {
        "optional-openrouter": "OPENROUTER_API_KEY",
        "optional-openai": "OPENAI_API_KEY",
        "optional-anthropic": "ANTHROPIC_API_KEY",
    }
    for step_id, secret_name in expected_secret_steps.items():
        step = by_id[step_id]
        assert step["type"] == "exportSecret"
        assert step["name"] == secret_name
        assert step["optional"] is True
        assert f'{secret_name}=' in step["run"]

    finalize = by_id["finalize-provider-selection"]
    assert finalize["type"] == "command"
    assert "gauss-configure-main-provider" in finalize["run"]
    assert step_ids.index("optional-openrouter") < step_ids.index("finalize-provider-selection")
    assert step_ids.index("optional-openai") < step_ids.index("finalize-provider-selection")
    assert step_ids.index("optional-anthropic") < step_ids.index("finalize-provider-selection")
    assert step_ids.index("finalize-provider-selection") < step_ids.index("start-guide-server")
