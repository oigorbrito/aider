import base64
import json
import urllib.parse

import pytest

from aider.oauth_flow import LoginError
from aider.openai_login import (
    AUTHORIZE_URL,
    CLIENT_ID,
    JWT_CLAIM_PATH,
    REDIRECT_URI,
    TOKEN_URL,
    OpenAICodexOAuth,
)

HEADLESS = {"SSH_CONNECTION": "10.0.0.1 22 10.0.0.2 2222"}


class FakeInteraction:
    def __init__(self, paste=None):
        self.events = []
        self.paste = paste

    def notify(self, event):
        self.events.append(event)

    def prompt(self, message):
        return self.paste(self) if callable(self.paste) else self.paste


def _jwt(account_id="acc-123"):
    def b64(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    claims = {JWT_CLAIM_PATH: {"chatgpt_account_id": account_id}} if account_id else {}
    return f"{b64({'alg': 'none'})}.{b64(claims)}.sig"


def _paste_for(interaction, code="c1"):
    state = _state_from(interaction)[1]["state"][0]
    return f"{REDIRECT_URI}?code={code}&state={state}"


def _state_from(interaction):
    url = next(e["url"] for e in interaction.events if e["type"] == "auth_url")
    return url, urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)


def _tokens(access=None, **overrides):
    data = {"access_token": access or _jwt(), "refresh_token": "r-new", "expires_in": 3600}
    data.update(overrides)
    return data


def test_authorization_url_matches_finecode_parameters():
    interaction = FakeInteraction(paste=_paste_for)
    OpenAICodexOAuth().login(
        interaction, environ=HEADLESS, post_status=NO_DEVICE, post=lambda *a: _tokens()
    )
    url, query = _state_from(interaction)

    assert url.startswith(AUTHORIZE_URL + "?")
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == [REDIRECT_URI]
    assert query["code_challenge_method"] == ["S256"]
    assert query["codex_cli_simplified_flow"] == ["true"]
    assert query["originator"] == ["pi"]
    assert len(query["state"][0]) == 32


def test_paste_exchanges_code_with_verifier_and_stores_account_id():
    calls = []

    def post(url, body, form):
        calls.append((url, body, form))
        return _tokens()

    interaction = FakeInteraction(paste=_paste_for)
    credential = OpenAICodexOAuth().login(
        interaction, environ=HEADLESS, post_status=NO_DEVICE, post=post
    )

    url, body, form = calls[0]
    assert url == TOKEN_URL and form is True
    assert body["grant_type"] == "authorization_code"
    assert body["code"] == "c1"
    assert body["redirect_uri"] == REDIRECT_URI
    assert body["code_verifier"]
    assert credential["type"] == "oauth"
    assert credential["refresh"] == "r-new"
    assert credential["accountId"] == "acc-123"


def test_paste_with_wrong_state_is_rejected():
    interaction = FakeInteraction(paste=REDIRECT_URI + "?code=c1&state=wrong")
    with pytest.raises(LoginError, match="does not match"):
        OpenAICodexOAuth().login(
            interaction, environ=HEADLESS, post_status=NO_DEVICE, post=lambda *a: _tokens()
        )


def test_token_without_account_id_is_rejected():
    interaction = FakeInteraction(paste=_paste_for)
    with pytest.raises(LoginError, match="account ID"):
        OpenAICodexOAuth().login(
            interaction,
            environ=HEADLESS,
            post_status=NO_DEVICE,
            post=lambda *a: _tokens(access=_jwt(account_id=None)),
        )


def test_refresh_posts_form_with_refresh_token():
    calls = []

    def post(url, body, form):
        calls.append((body, form))
        return _tokens()

    old = {
        "type": "oauth",
        "access": "old",
        "refresh": "r-old",
        "expires": 0,
        "accountId": "acc-123",
    }
    renewed = OpenAICodexOAuth().refresh(old, post=post)

    body, form = calls[0]
    assert form is True
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "r-old"
    assert body["client_id"] == CLIENT_ID
    assert renewed["accountId"] == "acc-123"


def test_to_auth_returns_access_token_as_api_key():
    credential = {"type": "oauth", "access": "tok", "refresh": "r", "expires": 1, "accountId": "a"}
    assert OpenAICodexOAuth().to_auth(credential) == {"apiKey": "tok"}


# --- device-code login (headless) -------------------------------------------------


def _device_disabled():
    """The server does not offer device login, so headless login uses the paste flow."""
    return _scripted({DEVICE_USER_CODE_URL_FOR_TESTS: [(404, {})]})


DEVICE_USER_CODE_URL_FOR_TESTS = "https://auth.openai.com/api/accounts/deviceauth/usercode"

from aider import openai_login as ol  # noqa: E402
from aider.openai_login import DeviceCodeUnavailable  # noqa: E402


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def now_fn(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _scripted(responses):
    """post_status double: responses maps a URL to a list of (status, body) returned in order."""
    calls = []

    def post_status(url, body, form):
        calls.append((url, body, form))
        queue = responses[url]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    post_status.calls = calls
    return post_status


NO_DEVICE = _device_disabled()  # reusable: the single scripted response repeats


def test_start_accepts_the_interval_sent_as_text():
    post_status = _scripted(
        {
            ol.DEVICE_USER_CODE_URL: [
                (200, {"device_auth_id": "dev-1", "user_code": "ABCD-1234", "interval": "5"})
            ]
        }
    )
    device = ol.start_device_code(post_status)
    assert device == {"device_auth_id": "dev-1", "user_code": "ABCD-1234", "interval": 5.0}


def test_start_reports_a_server_without_device_login():
    post_status = _scripted({ol.DEVICE_USER_CODE_URL: [(404, {})]})
    with pytest.raises(DeviceCodeUnavailable):
        ol.start_device_code(post_status)


def test_poll_waits_while_pending_and_backs_off_on_slow_down():
    clock = FakeClock()
    post_status = _scripted(
        {
            ol.DEVICE_TOKEN_URL: [
                (403, {}),
                (400, {"error": "deviceauth_authorization_pending"}),
                (400, {"error": {"code": "slow_down"}}),
                (200, {"authorization_code": "auth-code", "code_verifier": "verifier"}),
            ]
        }
    )
    device = {"device_auth_id": "dev-1", "user_code": "ABCD-1234", "interval": 5.0}

    code, verifier = ol.poll_device_code(device, post_status, clock.sleep, clock.now_fn)

    assert (code, verifier) == ("auth-code", "verifier")
    assert clock.sleeps == [5, 5, 10]  # pending keeps 5 s; slow_down adds 5 s


def test_poll_times_out_with_a_clear_message():
    clock = FakeClock()
    post_status = _scripted({ol.DEVICE_TOKEN_URL: [(403, {})]})
    device = {"device_auth_id": "dev-1", "user_code": "ABCD-1234", "interval": 5.0}
    with pytest.raises(LoginError, match="timed out"):
        ol.poll_device_code(device, post_status, clock.sleep, clock.now_fn, timeout=12)


def test_poll_stops_on_a_real_failure():
    clock = FakeClock()
    post_status = _scripted({ol.DEVICE_TOKEN_URL: [(400, {"error": "access_denied"})]})
    device = {"device_auth_id": "dev-1", "user_code": "ABCD-1234", "interval": 5.0}
    with pytest.raises(LoginError, match="failed"):
        ol.poll_device_code(device, post_status, clock.sleep, clock.now_fn)


def test_headless_login_uses_the_device_code_and_exchanges_with_the_device_redirect():
    clock = FakeClock()
    post_status = _scripted(
        {
            ol.DEVICE_USER_CODE_URL: [
                (200, {"device_auth_id": "dev-1", "user_code": "ABCD-1234", "interval": "5"})
            ],
            ol.DEVICE_TOKEN_URL: [(200, {"authorization_code": "auth-code", "code_verifier": "v"})],
        }
    )
    token_calls = []

    def post(url, body, form):
        token_calls.append((url, body, form))
        return _tokens()

    interaction = FakeInteraction()
    credential = OpenAICodexOAuth().login(
        interaction,
        environ=HEADLESS,
        post=post,
        post_status=post_status,
        sleep=clock.sleep,
        now=clock.now_fn,
    )

    device_event = next(e for e in interaction.events if e["type"] == "device_code")
    assert device_event["userCode"] == "ABCD-1234"
    assert device_event["verificationUri"] == ol.DEVICE_VERIFICATION_URI
    url, body, form = token_calls[0]
    assert url == TOKEN_URL and form is True
    assert body["code"] == "auth-code" and body["code_verifier"] == "v"
    assert body["redirect_uri"] == ol.DEVICE_REDIRECT_URI
    assert credential["accountId"] == "acc-123"


def test_headless_login_falls_back_to_pasting_when_device_login_is_off():
    post_status = _scripted({ol.DEVICE_USER_CODE_URL: [(404, {})]})
    token_calls = []

    def post(url, body, form):
        token_calls.append(body)
        return _tokens()

    def paste(interaction):
        state = _state_from(interaction)[1]["state"][0]
        return REDIRECT_URI + "?code=c1&state=" + state

    interaction = FakeInteraction(paste=paste)
    credential = OpenAICodexOAuth().login(
        interaction,
        environ=HEADLESS,
        post=post,
        post_status=post_status,
    )
    assert token_calls[0]["code"] == "c1"
    assert credential["accountId"] == "acc-123"
    assert any("not offered here" in e.get("message", "") for e in interaction.events)
