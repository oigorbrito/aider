import litellm
import pytest

import aider.models as models_module
from aider import auth
from aider.credential_store import CredentialStore
from aider.main import main
from aider.openrouter_login import LoginError
from tests.basic.test_main import DummyInput, DummyOutput


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point the credential store at a temporary home, on Windows and POSIX."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


def test_expired_sign_in_that_cannot_renew_tells_the_user_what_to_do(home, monkeypatch, capsys):
    CredentialStore(home / ".aider" / "auth.json").modify(
        "anthropic",
        lambda current: {
            "type": "oauth",
            "access": "sk-ant-oat01-old",
            "refresh": "r-old",
            "expires": 1,
        },
    )

    def refresh_refused(credential):
        raise LoginError("Token request to platform.claude.com failed (HTTP 400).")

    monkeypatch.setattr(auth.get_provider("anthropic").oauth, "refresh", refresh_refused)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    main(
        [
            "--model", "anthropic/claude-haiku-4-5",
            "--no-git", "--no-check-update", "--yes", "--exit",
        ],
        input=DummyInput(),
        output=DummyOutput(),
    )
    err = capsys.readouterr().err
    assert "sign-in expired and could not be renewed" in err
    assert "aider --login anthropic" in err


def test_fatal_api_error_with_message_exits_non_zero(home, monkeypatch):
    store = CredentialStore(home / ".aider" / "auth.json")
    store.modify("deepseek", lambda current: {"type": "api_key", "key": "sk-test-not-real"})

    def refused(*args, **kwargs):
        raise litellm.AuthenticationError(
            message="invalid api key", llm_provider="deepseek", model="deepseek-chat"
        )

    monkeypatch.setattr(models_module.litellm, "completion", refused)
    status = main(
        [
            "--model", "deepseek/deepseek-chat",
            "--no-git", "--no-check-update", "--yes",
            "--message", "ok",
        ],
        input=DummyInput(),
        output=DummyOutput(),
    )
    assert status == 1


def test_python_dash_m_passes_the_exit_status_to_the_shell(tmp_path):
    """`python -m aider` must exit non-zero when main() returns 1 (a fatal API error)."""
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    env.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path), AIDER_ANALYTICS="false")
    result = subprocess.run(
        [
            sys.executable, "-m", "aider",
            "--model", "anthropic/claude-haiku-4-5",
            "--no-git", "--no-check-update", "--yes", "--message", "ok",
        ],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        timeout=600,
    )
    assert result.returncode == 1, result.stderr[-500:]
