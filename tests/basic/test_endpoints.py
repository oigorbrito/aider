import pytest

import aider.models as models_module
from aider import endpoints
from aider.endpoints import EndpointError, check_endpoint, check_environment, is_local_host
from aider.main import main
from aider.models import Model
from tests.basic.test_main import DummyInput, DummyOutput

# Addresses from RFC 6890 and RFC 1918, and public addresses for contrast.
LOCAL_ADDRESSES = [
    "127.0.0.1", "127.200.1.9", "::1", "10.1.2.3", "172.16.0.1", "172.31.255.255",
    "192.168.0.10", "169.254.10.10", "0.0.0.0",
]
PUBLIC_ADDRESSES = ["8.8.8.8", "2606:4700::1111", "172.32.0.1"]


@pytest.mark.parametrize("address", LOCAL_ADDRESSES)
def test_local_addresses_are_local(address):
    assert is_local_host(address)


@pytest.mark.parametrize("address", PUBLIC_ADDRESSES)
def test_public_addresses_are_not_local(address):
    assert not is_local_host(address)


@pytest.mark.parametrize("name", ["localhost", "LOCALHOST.", "api.localhost"])
def test_localhost_names_are_local(name):
    assert is_local_host(name)


def test_a_name_that_resolves_to_loopback_is_local(monkeypatch):
    monkeypatch.setattr(
        endpoints.socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("127.0.0.1", 0))]
    )
    is_local_host.cache_clear()
    assert is_local_host("gateway.example.test")


def test_a_name_that_resolves_to_a_public_address_is_not_local(monkeypatch):
    monkeypatch.setattr(
        endpoints.socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("8.8.8.8", 0))]
    )
    is_local_host.cache_clear()
    assert not is_local_host("gateway.example.test")


def test_cloud_endpoint_is_accepted():
    check_endpoint("https://8.8.8.8/v1", "--openai-api-base")  # no exception


def test_local_endpoint_is_refused():
    with pytest.raises(EndpointError, match="points to this machine"):
        check_endpoint("http://127.0.0.1:1234/v1", "--openai-api-base")


def test_empty_endpoint_is_ignored():
    check_endpoint("", "OPENAI_API_BASE")
    check_endpoint(None, "OPENAI_API_BASE")


def test_environment_is_checked_for_any_base_url_variable():
    with pytest.raises(EndpointError, match="AZURE_API_BASE"):
        check_environment({"AZURE_API_BASE": "http://localhost:8080"})
    with pytest.raises(EndpointError, match="SOME_BASE_URL"):
        check_environment({"SOME_BASE_URL": "http://192.168.1.5"})
    check_environment({"OPENAI_API_BASE": "https://8.8.8.8/v1", "UNRELATED": "http://127.0.0.1"})


def test_model_settings_api_base_is_checked_before_any_request(monkeypatch):
    def must_not_be_called(**kwargs):
        raise AssertionError("litellm was called for a local api_base")

    monkeypatch.setattr(models_module.litellm, "completion", must_not_be_called)
    model = Model("openai/gpt-4o")
    model.extra_params = {"api_base": "http://127.0.0.1:1234/v1"}
    with pytest.raises(EndpointError):
        model.send_completion([{"role": "user", "content": "hi"}], None, False)


def test_main_refuses_openai_api_base_pointing_to_this_machine(capsys):
    status = main(
        ["--openai-api-base", "http://127.0.0.1:1234/v1", "--no-git", "--no-check-update",
         "--yes", "--exit"],
        input=DummyInput(),
        output=DummyOutput(),
    )
    assert status == 1
    assert "points to this machine" in capsys.readouterr().out


def test_main_refuses_a_local_base_url_in_the_environment(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_BASE", "http://127.0.0.1:1234/v1")
    status = main(
        ["--model", "openai/gpt-4o", "--no-git", "--no-check-update", "--yes", "--exit"],
        input=DummyInput(),
        output=DummyOutput(),
    )
    assert status == 1
    assert "OPENAI_API_BASE points to this machine" in capsys.readouterr().out
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
