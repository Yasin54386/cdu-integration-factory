"""Task 4: invoke_copilot now delegates to GitHub Models API (not copilot CLI)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pipeline.stages.generate import GenerateError, invoke_copilot


def test_invoke_copilot_calls_models_api(monkeypatch):
    monkeypatch.setenv("GH_PIPELINE_TOKEN", "tok-pipeline")

    with patch("pipeline.core.models_api.urllib.request.urlopen") as mock_open:
        import json
        from unittest.mock import MagicMock

        resp = MagicMock()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        resp.read.return_value = json.dumps(
            {"choices": [{"message": {"content": "SELECT 1 FROM dual;"}}]}
        ).encode()
        mock_open.return_value = resp

        result = invoke_copilot("generate some SQL")

    assert result == "SELECT 1 FROM dual;"
    # Verify the call went to the Models API endpoint, not a subprocess
    call_url = mock_open.call_args[0][0].full_url
    assert "models.inference.ai.azure.com" in call_url


def test_invoke_copilot_uses_gpt4o_mini_by_default(monkeypatch):
    monkeypatch.setenv("GH_PIPELINE_TOKEN", "tok")
    monkeypatch.delenv("CDU_MODEL", raising=False)

    captured = {}

    def fake_call(*, user_prompt, system_prompt, token, model, **kw):
        captured["model"] = model
        return "output"

    with patch("pipeline.core.models_api.call", side_effect=fake_call):
        invoke_copilot("prompt")

    assert captured["model"] == "gpt-4o-mini"


def test_invoke_copilot_respects_cdu_model_env_var(monkeypatch):
    monkeypatch.setenv("GH_PIPELINE_TOKEN", "tok")
    monkeypatch.setenv("CDU_MODEL", "gpt-4o")

    captured = {}

    def fake_call(*, user_prompt, system_prompt, token, model, **kw):
        captured["model"] = model
        return "output"

    with patch("pipeline.core.models_api.call", side_effect=fake_call):
        invoke_copilot("prompt")

    assert captured["model"] == "gpt-4o"


def test_invoke_copilot_raises_generate_error_when_no_token(monkeypatch):
    for var in ("GH_PIPELINE_TOKEN", "GITHUB_TOKEN", "COPILOT_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(GenerateError, match="No GitHub token found"):
        invoke_copilot("some prompt")


def test_invoke_copilot_wraps_api_error_as_generate_error(monkeypatch):
    monkeypatch.setenv("GH_PIPELINE_TOKEN", "tok")

    from pipeline.core.models_api import ModelsAPIError

    with patch("pipeline.core.models_api.call", side_effect=ModelsAPIError("HTTP 429")):
        with pytest.raises(GenerateError, match="GitHub Models API error"):
            invoke_copilot("some prompt")


def test_invoke_copilot_passes_token_to_api(monkeypatch):
    monkeypatch.setenv("GH_PIPELINE_TOKEN", "my-secret-token")
    for var in ("GITHUB_TOKEN", "COPILOT_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(var, raising=False)

    captured = {}

    def fake_call(*, user_prompt, system_prompt, token, model, **kw):
        captured["token"] = token
        return "output"

    with patch("pipeline.core.models_api.call", side_effect=fake_call):
        invoke_copilot("prompt")

    assert captured["token"] == "my-secret-token"
