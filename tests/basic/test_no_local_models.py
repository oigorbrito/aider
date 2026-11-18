import pytest

import aider.models as models_module
from aider.main import main
from aider.models import Model, UnsupportedModelError
from tests.basic.test_main import DummyInput, DummyOutput

LOCAL = [
    "ollama/llama3",
    "ollama_chat/llama3",
    "lm_studio/llama-3-8b",
    "llamafile/model",
    "vllm/some-model",
    "hosted_vllm/some-model",
]


@pytest.mark.parametrize("name", LOCAL)
def test_local_runtime_models_are_refused(name):
    with pytest.raises(UnsupportedModelError, match="cloud providers"):
        Model(name)


def test_cloud_model_is_accepted():
    assert Model("anthropic/claude-haiku-4-5").name == "anthropic/claude-haiku-4-5"


def test_local_prefix_is_not_a_substring_match():
    # "ollama" in the middle of a cloud model name is not a local provider.
    assert Model("openrouter/ollama/some-model").name == "openrouter/ollama/some-model"


def test_main_refuses_a_local_model_without_any_network_call(monkeypatch, capsys):
    def must_not_be_called(**kwargs):
        raise AssertionError("litellm was called for a local model")

    monkeypatch.setattr(models_module.litellm, "completion", must_not_be_called)
    status = main(
        ["--model", "ollama/llama3", "--no-git", "--no-check-update", "--yes", "--message", "ok"],
        input=DummyInput(),
        output=DummyOutput(),
    )
    assert status == 1
    assert "runs a model on this machine" in capsys.readouterr().out


def test_main_refuses_the_removed_endpoint_flag(capsys):
    status = main(
        [
            "--openai-api-base", "http://127.0.0.1:1234/v1",
            "--no-git", "--no-check-update", "--yes", "--exit",
        ],
        input=DummyInput(),
        output=DummyOutput(),
    )
    assert status == 1
    assert "--openai-api-base was removed" in capsys.readouterr().out
