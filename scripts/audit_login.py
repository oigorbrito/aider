"""Operational probe for the aider sign-in. Run from the repo root: python scripts/audit_login.py.

Uses an isolated HOME and never touches the real ~/.aider. Exit 0 = all checks pass;
1 = at least one check fails (a finding, see docs/login-audit.md).
"""

import base64
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

tmp = Path(tempfile.mkdtemp(prefix="aider-probe-"))
os.environ["USERPROFILE"] = str(tmp)
os.environ["HOME"] = str(tmp)
os.environ["AIDER_ANALYTICS"] = "false"

from aider import auth  # noqa: E402
from aider import oauth_runtime  # noqa: E402
from aider.credential_store import CredentialStore, CredentialStoreError  # noqa: E402
from aider.oauth_flow import CallbackListener, is_headless, parse_pasted  # noqa: E402
from aider.openai_login import _account_id  # noqa: E402
from aider.openrouter_login import LoginError  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


def fake_jwt(account="acc_123"):
    def b(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    claims = {"https://api.openai.com/auth": {"chatgpt_account_id": account}}
    return f"{b({'alg': 'none'})}.{b(claims)}.sig"


def store_at(name):
    return CredentialStore(tmp / name / "auth.json")


# 1. Hypothesis: a ChatGPT OAuth token must NOT become OPENAI_API_KEY (apply_stored_keys skips it).
s = store_at("h1")
access = fake_jwt()
s.modify(
    "openai",
    lambda c: {
        "type": "oauth",
        "access": access,
        "refresh": "r",
        "expires": int(time.time() * 1000) + 3600_000,
        "accountId": "acc_123",
    },
)
env = {}
applied = auth.apply_stored_keys(s, env)
check(
    "apply_stored_keys skips ChatGPT token",
    "OPENAI_API_KEY" not in env and applied == [],
    f"applied={applied}",
)
# The example calls use_now() instead; does it leak the token into OPENAI_API_KEY?
from aider.login_cli import use_now  # noqa: E402

env2 = {}
use_now("openai", s, env2)
leaked = env2.get("OPENAI_API_KEY") == access
check(
    "login_cli.use_now() (used by /login in chat) does NOT expose ChatGPT token as OPENAI_API_KEY",
    not leaked,
    f"leaked={leaked}",
)

# 2. Saved credential wins over env (D1)
s = store_at("h2")
s.modify("openrouter", lambda c: {"type": "api_key", "key": "saved"})
check(
    "saved key beats env",
    auth.resolve("openrouter", s, {"OPENROUTER_API_KEY": "env"}).api_key == "saved",
)
check(
    "env used when nothing saved",
    auth.resolve("openrouter", store_at("h3"), {"OPENROUTER_API_KEY": "env"}).source == "env",
)

# 3. Expired OAuth with failing refresh -> clear message, env not used silently
s = store_at("h4")
s.modify(
    "openai",
    lambda c: {"type": "oauth", "access": access, "refresh": "r", "expires": 1, "accountId": "a"},
)


def boom(*a, **k):
    raise LoginError("Token request to auth.openai.com failed (HTTP 400).")


auth.OAUTH_PROVIDERS  # noqa
p = auth.PROVIDERS["openai"]
orig = p.oauth.refresh
p.oauth.refresh = boom
msgs = []
env3 = {"OPENAI_API_KEY": "sk-env"}
applied = auth.apply_stored_keys(s, env3, report=msgs.append)
p.oauth.refresh = orig
check(
    "OpenAI: expired+failed refresh is reported at startup",
    msgs and "aider --login openai" in msgs[0],
    f"msg={msgs}",
)
check(
    "OpenAI: env key still used (API-key precedence kept), and the sign-in problem is reported",
    "OPENAI_API_KEY" in env3 and bool(msgs),
    f"env={'OPENAI_API_KEY' in env3} msgs={len(msgs)}",
)

# 4. Corrupt file: never overwritten
s = store_at("h5")
s.path.parent.mkdir(parents=True, exist_ok=True)
s.path.write_text("{not json", encoding="utf-8")
try:
    s.modify("openai", lambda c: {"type": "api_key", "key": "x"})
    raised = False
except CredentialStoreError:
    raised = True
check(
    "corrupt auth.json raises and is not overwritten",
    raised and s.path.read_text(encoding="utf-8") == "{not json",
)

# 5. Atomic failure: fn raises -> nothing written
s = store_at("h6")
s.modify("openrouter", lambda c: {"type": "api_key", "key": "keep"})
try:
    s.modify("openrouter", lambda c: (_ for _ in ()).throw(RuntimeError("x")))
except RuntimeError:
    pass
check(
    "failed modify leaves previous value",
    s.read("openrouter") == {"type": "api_key", "key": "keep"},
)

# 6. Logout removes only stored; env unchanged
s = store_at("h7")
s.modify("openrouter", lambda c: {"type": "api_key", "key": "k"})
check(
    "logout removes stored", auth.logout("openrouter", s) is True and s.read("openrouter") is None
)
check("logout twice is a no-op", auth.logout("openrouter", s) is False)

# 7. Port 1455 busy -> CallbackListener fails with OSError (login falls back to paste)
import socket  # noqa: E402

busy = socket.socket()
busy.bind(("127.0.0.1", 0))
busy.listen(1)
port = busy.getsockname()[1]
try:
    CallbackListener("127.0.0.1", port, "/auth/callback", "st")
    busy_ok = False
except OSError:
    busy_ok = True
busy.close()
check("busy callback port raises OSError (caller falls back to paste)", busy_ok)

# 8. Callback: wrong state rejected, correct state accepted
import urllib.request  # noqa: E402

lst = CallbackListener("127.0.0.1", 0, "/auth/callback", "good")
base = f"http://127.0.0.1:{lst.port}/auth/callback"
try:
    urllib.request.urlopen(base + "?code=x&state=bad", timeout=5)
    bad_status = 200
except urllib.error.HTTPError as e:
    bad_status = e.code
ok_resp = urllib.request.urlopen(base + "?code=abc&state=good", timeout=5)
code = lst.wait(2)
lst.close()
check("callback rejects wrong state (400)", bad_status == 400, f"status={bad_status}")
check("callback accepts right state and returns code", code == "abc" and ok_resp.status == 200)

# 9. Paste parsing
check(
    "paste: redirect URL with state",
    parse_pasted("http://localhost:1455/auth/callback?code=c1&state=s1", "s1")[0] == "c1",
)
try:
    parse_pasted("http://localhost/?code=c&state=WRONG", "s1")
    mismatch = False
except LoginError:
    mismatch = True
check("paste: state mismatch rejected", mismatch)
try:
    parse_pasted("https://auth.openai.com/oauth/authorize?response_type=code&client_id=x", None)
    link = False
except LoginError as e:
    link = "sign-in link" in str(e)
check("paste: sign-in link explained, not treated as code", link)

# 10. Headless detection
check("SSH env -> headless", is_headless({"SSH_CONNECTION": "1"}) is True)
check("linux w/o DISPLAY -> headless", is_headless({}) is (sys.platform.startswith("linux")))

# 11. JWT account id
check("account id read from JWT claim", _account_id(access) == "acc_123")
check("malformed JWT -> None", _account_id("abc") is None)

# 12. chatgpt prepare without sign-in gives actionable error
try:
    oauth_runtime.prepare_chatgpt_auth(store_at("h8"), {"AIDER_CHATGPT_TOKEN_DIR": str(tmp / "t")})
    msg_ok = False
except auth.AuthError as e:
    msg_ok = "aider --login openai" in str(e)
check("chatgpt/ without sign-in -> actionable AuthError", msg_ok)

# 13. Token file permissions (POSIX only; Windows ACL)
if os.name == "posix":
    p = store_at("h9").path
    check("auth.json is 0600", oct(p.stat().st_mode & 0o777) == "0o600" if p.exists() else True)

passed = sum(ok for _, ok in results)
print(f"\nSUMMARY: {passed}/{len(results)} passed")
for name, ok in results:
    if not ok:
        print("  FAILED:", name)
sys.exit(0 if all(ok for _, ok in results) else 1)
