"""Provider authentication in the Pi pattern: API key or browser OAuth.

A provider declares the ways it can be signed in to (`api_key` and/or `oauth`).
Stored credentials own a provider: resolve() only consults the environment when
nothing is stored (decision D1). Resolved keys reach litellm through
os.environ, because that is how aider already passes keys (decision D9).

Explicit --api-key flags are applied later in main() and still win.
"""

import time
from dataclasses import dataclass, field

from aider.anthropic_login import AnthropicOAuth
from aider.openai_login import OpenAICodexOAuth
from aider.openrouter_login import LoginError, OpenRouterOAuth

RENEW_MARGIN_MS = 5 * 60 * 1000  # renew this long before expiry, so litellm never needs to
SOURCE_STORED = "stored"
SOURCE_ENV = "env"

API_KEY_PROVIDERS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


class AuthError(Exception):
    """Unknown provider or method. The message is safe to show to the user."""


@dataclass
class ProviderAuth:
    id: str
    name: str
    env_var: str
    oauth: object = None
    methods: tuple = field(default=("key",))


OAUTH_PROVIDERS = {
    "openrouter": OpenRouterOAuth,
    "anthropic": AnthropicOAuth,
    "openai": OpenAICodexOAuth,
}


def _build_providers():
    providers = {}
    for provider_id, env_var in API_KEY_PROVIDERS.items():
        oauth_class = OAUTH_PROVIDERS.get(provider_id)
        providers[provider_id] = ProviderAuth(
            id=provider_id,
            name=provider_id,
            env_var=env_var,
            oauth=oauth_class() if oauth_class else None,
            methods=("browser", "key") if oauth_class else ("key",),
        )
    return providers


PROVIDERS = _build_providers()


def get_provider(provider_id):
    try:
        return PROVIDERS[provider_id]
    except KeyError:
        raise AuthError(f"Unknown provider '{provider_id}'. Choose one of: {', '.join(PROVIDERS)}.")


@dataclass
class Resolved:
    api_key: str
    source: str


def resolve(provider_id, store, environ):
    """Return the key to use for a provider, or None.

    A stored credential wins. The environment is consulted only when nothing is
    stored (D1).
    """
    provider = get_provider(provider_id)
    stored = store.read(provider_id)
    if stored:
        if stored.get("type") == "oauth" and provider.oauth is not None:
            stored = _renewed(provider, store, stored)
            key = provider.oauth.to_auth(stored).get("apiKey")
        else:
            key = stored.get("key")
        if key:
            return Resolved(api_key=key, source=SOURCE_STORED)
    value = environ.get(provider.env_var)
    if value:
        return Resolved(api_key=value, source=SOURCE_ENV)
    return None


def _needs_renewal(credential):
    expires = credential.get("expires")
    if not credential.get("refresh") or not isinstance(expires, (int, float)):
        return False
    return expires <= time.time() * 1000 + RENEW_MARGIN_MS


def _renewed(provider, store, credential):
    """Refresh an expired OAuth credential and store the new one (FineCode rule).

    The check runs again under the store's file lock. If another process has
    already renewed the sign-in, its token is used and the provider is not
    called again. Refresh tokens may rotate, so a second refresh could invalidate
    the first. A credential without a refresh token (OpenRouter keys) is returned as is.
    """
    if not _needs_renewal(credential):
        return credential
    result = {}

    def renew_under_lock(current):
        if current is None or current.get("type") != "oauth":
            return current  # logged out meanwhile
        if not _needs_renewal(current):
            result["credential"] = current  # renewed by another process
            return current
        renewed = provider.oauth.refresh(current)
        result["credential"] = renewed
        return renewed

    try:
        store.modify(provider.id, renew_under_lock)
    except LoginError:
        raise AuthError(
            f"Saved {provider.name} sign-in expired and could not be renewed."
            f" Run aider --login {provider.id} again."
        ) from None
    if "credential" not in result:
        raise AuthError(
            f"The saved {provider.name} sign-in was removed. Run aider --login {provider.id}."
        )
    return result["credential"]


def apply_stored_keys(store, environ, report=None):
    """Copy stored keys into environ so litellm sees them (D9).

    Only stored credentials are copied. Environment values are already there.
    A stored sign-in that cannot be used (for example an expired token that
    cannot be renewed) is skipped, and report(message) is called with the
    reason, so the user sees what to do instead of a generic missing-key error.
    Returns the environment variable names that were set.
    """
    applied = []
    for provider_id in store.list():
        if provider_id not in PROVIDERS:
            continue
        stored = store.read(provider_id)
        if provider_id == "openai" and stored and stored.get("type") == "oauth":
            continue  # A ChatGPT token is not an OpenAI API key; models.py uses the auth file.
        try:
            resolved = resolve(provider_id, store, environ)
        except AuthError as error:
            # A stored sign-in owns the provider (FineCode rule): when it cannot be
            # used, the environment key must not be used silently in its place.
            environ.pop(PROVIDERS[provider_id].env_var, None)
            if report is not None:
                report(str(error))
            continue
        if resolved and resolved.source == SOURCE_STORED:
            name = PROVIDERS[provider_id].env_var
            environ[name] = resolved.api_key
            applied.append(name)
    return applied


def login_api_key(provider_id, store, key):
    """Store an API key typed by the user, in the Pi api_key shape."""
    provider = get_provider(provider_id)
    key = (key or "").strip()
    if not key:
        raise AuthError("The API key is empty.")
    store.modify(provider.id, lambda current: {"type": "api_key", "key": key})
    return provider.env_var


def login_oauth(provider_id, store, interaction, environ):
    """Run the browser or headless OAuth flow and store the credential."""
    provider = get_provider(provider_id)
    if provider.oauth is None:
        raise AuthError(f"{provider.name} does not support browser sign-in. Use an API key.")
    credential = provider.oauth.login(interaction, environ=environ)
    store.modify(provider.id, lambda current: credential)
    return provider.env_var


def logout(provider_id, store):
    """Remove the stored credential only. Environment variables are not touched (Pi rule)."""
    get_provider(provider_id)
    if store.read(provider_id) is None:
        return False
    store.delete(provider_id)
    return True
