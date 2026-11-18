from aider import commands
from aider.credential_store import CredentialStore


def _store(tmp_path):
    return CredentialStore(tmp_path / "auth.json")


def test_hints_without_sign_in_point_to_login(tmp_path):
    text = commands.model_hints(store=_store(tmp_path))
    assert "/login" in text
    assert "anthropic/claude-haiku-4-5" not in text


def test_hints_list_examples_only_for_signed_in_providers(tmp_path):
    store = _store(tmp_path)
    store.modify("anthropic", lambda current: {"type": "oauth", "access": "a", "refresh": "r", "expires": 1})
    text = commands.model_hints(store=store)
    assert "anthropic/claude-haiku-4-5" in text
    assert "chatgpt/gpt-5.6-terra" not in text


def test_search_hint_explains_filtering(tmp_path):
    text = commands.model_hints(search=True, store=_store(tmp_path))
    assert "/models <text>" in text


def test_unreadable_store_does_not_break_the_hint(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text("not json", encoding="utf-8")
    text = commands.model_hints(store=CredentialStore(path))
    assert "/login" in text


def test_default_model_prefers_a_signed_in_provider(tmp_path):
    from aider import onboarding

    store = _store(tmp_path)
    assert onboarding.signed_in_default_model(store) is None
    store.modify("openai", lambda current: {"type": "oauth", "access": "a", "refresh": "r", "expires": 1, "accountId": "x"})
    assert onboarding.signed_in_default_model(store) == "chatgpt/gpt-5.6-terra"
    store.modify("anthropic", lambda current: {"type": "oauth", "access": "a", "refresh": "r", "expires": 1})
    assert onboarding.signed_in_default_model(store) == "anthropic/claude-haiku-4-5"
