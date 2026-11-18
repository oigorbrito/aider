"""Example: use a ChatGPT Plus/Pro subscription with aider, without API credits.

Adapted from OpenHands software-agent-sdk
examples/01_standalone_sdk/35_subscription_login.py, with evidence-based checks.

The sign-in is the one `aider --login openai` uses (aider/openai_login.py):
- Browser OAuth (PKCE) with a local callback on port 1455. If that port is busy,
  paste the redirect URL when asked.
- Headless machines (SSH, Linux without a display) use the device-code flow.
- The credential is stored in ~/.aider/auth.json (mode 0600 on POSIX) and renewed
  before it expires. The chatgpt/ models read it from there.

Design choices (from the OpenHands example, changed on purpose):
- A stored ChatGPT sign-in decides whether login is needed. An OPENAI_API_KEY in
  the environment does NOT count as a subscription login. The old check
  (`resolve() is None`) skipped login whenever that variable was set.
- The example does not copy the ChatGPT token into OPENAI_API_KEY. The chatgpt/
  models do not read that variable.
- Failures print one actionable line and exit 1. No traceback for expected cases.
- SUBSCRIPTION_LOGIN_ONLY=1 prints one JSON line of evidence (no secrets) and exits 0.

Requirements:
- Active ChatGPT Plus or Pro subscription.
- A browser on this machine, or another device for the device-code flow.

Environment variables:
- AIDER_SUBSCRIPTION_MODEL: Codex model (default: gpt-5.2-codex).
- AIDER_SUBSCRIPTION_FORCE_LOGIN: "1" removes the stored sign-in and signs in again.
- SUBSCRIPTION_LOGIN_ONLY: "1" verifies the sign-in and exits without a chat.

Exit codes: 0 success, 1 sign-in or model problem, 2 invalid configuration.

Run from the repository you want to edit:
    python examples/subscription_login.py
"""

import getpass
import json
import os
import platform
import sys
import time

from aider import auth
from aider.credential_store import CredentialStoreError
from aider.login_cli import (
    TerminalInteraction,
    default_store,
    login_provider,
    logout_provider,
)
from aider.openrouter_login import LoginError

PROVIDER = "openai"
DEFAULT_MODEL = "gpt-5.2-codex"
# Models named by the OpenHands example. A warning only: aider decides what it accepts.
KNOWN_SUBSCRIPTION_MODELS = ("gpt-5.2-codex", "gpt-5.2", "gpt-5.1-codex-max", "gpt-5.1-codex-mini")

EXIT_OK, EXIT_SIGN_IN, EXIT_CONFIG = 0, 1, 2


def fail(message, code=EXIT_SIGN_IN):
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def stored_subscription(store):
    """The stored ChatGPT sign-in, or None. Secrets are not printed."""
    stored = store.read(PROVIDER)
    if stored and stored.get("type") == "oauth" and stored.get("accountId"):
        return stored
    return None


def evidence(store, model_name):
    """Facts about the sign-in that can be compared across runs and machines."""
    stored = stored_subscription(store) or {}
    expires = stored.get("expires")
    return {
        "check": "subscription_login",
        "platform": platform.system(),
        "python": platform.python_version(),
        "model": model_name,
        "signed_in": bool(stored),
        "account_id_present": bool(stored.get("accountId")),
        "access_expires_at": (
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires / 1000))
            if isinstance(expires, (int, float))
            else None
        ),
        "source": "stored" if stored else "none",
        "openai_api_key_in_env": bool(os.environ.get("OPENAI_API_KEY")),
    }


def main():
    model_name = os.getenv("AIDER_SUBSCRIPTION_MODEL", DEFAULT_MODEL).strip()
    if not model_name:
        fail("AIDER_SUBSCRIPTION_MODEL is empty.", EXIT_CONFIG)
    if model_name not in KNOWN_SUBSCRIPTION_MODELS:
        print(
            (
                f"Warning: '{model_name}' is not in the list of subscription models"
                f" ({', '.join(KNOWN_SUBSCRIPTION_MODELS)}). Continuing."
            ),
            file=sys.stderr,
        )
    force_login = os.getenv("AIDER_SUBSCRIPTION_FORCE_LOGIN") == "1"
    verify_only = os.getenv("SUBSCRIPTION_LOGIN_ONLY") == "1"

    try:
        store = default_store()
        if force_login:
            logout_provider(PROVIDER, store)
        if stored_subscription(store) is None:
            print(
                "No ChatGPT sign-in saved. Starting sign-in (browser, or device code if headless)."
            )
            login_provider(
                PROVIDER, "browser", store, TerminalInteraction(), os.environ, getpass.getpass
            )
        if stored_subscription(store) is None:
            fail("Sign-in finished without a ChatGPT credential. Run aider --login openai again.")

        # Import after sign-in: Model() checks the stored sign-in and renews it if needed.
        from aider.models import Model

        model = Model(f"chatgpt/{model_name}")
    except CredentialStoreError as error:
        fail(str(error))
    except (auth.AuthError, LoginError) as error:
        fail(str(error))

    print(json.dumps(evidence(store, model.name), sort_keys=True))

    if verify_only:
        print("Login verified; skipping the chat because SUBSCRIPTION_LOGIN_ONLY=1.")
        return EXIT_OK

    from aider.coders import Coder
    from aider.io import InputOutput

    io = InputOutput(yes=True)
    coder = Coder.create(main_model=model, io=io, fnames=[])
    coder.run("List the files in the current directory.")
    print("Done!")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
