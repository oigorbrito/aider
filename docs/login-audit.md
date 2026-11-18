# Audit: aider sign-in, evidence-based (EBSE)

Scope: the sign-in by subscription (ChatGPT Plus/Pro through `aider --login openai`) and the
example `examples/subscription_login.py`, compared with the reference
`OpenHands/software-agent-sdk/examples/01_standalone_sdk/35_subscription_login.py`.

Goals, in order: empirical evidence, reproducibility, operational reliability, usability.
Evidence levels follow AGENTS.md section 5 (L0 compiles, L1 unit tests, L2 lint, L3 operational).

## 1. Reproduce

Environment: Windows (`platform.system()` = Windows), Python 3.12.10, project venv `../aider_venv` (has `git`, `rich`, `litellm`).
Use the project venv. The system Python lacks `git` and `rich`, so tests fail on collection.
Run from the repo root.

```bash
AIDER_ANALYTICS=false ../aider_venv/Scripts/python.exe -m pytest -q \
  tests/basic/test_auth.py tests/basic/test_login_cli.py tests/basic/test_openai_login.py \
  tests/basic/test_anthropic_login.py tests/basic/test_openrouter_login.py \
  tests/basic/test_oauth_runtime.py tests/basic/test_credential_store.py tests/basic/test_onboarding.py
# -> 125 passed, 2 skipped (exit 0)

../aider_venv/Scripts/python.exe scripts/audit_login.py     # operational probe
# -> 21/21 passed (exit 0) after the F1/F2 fixes. Before them: 18/21 (exit 1), the 3 findings in section 4.

../aider_venv/Scripts/python.exe -m flake8 examples/subscription_login.py scripts/audit_login.py
# -> exit 0
```

Example scenarios (each with an isolated `USERPROFILE`/`HOME`, so the real `~/.aider` is not touched):

| # | Scenario | Command | Result |
|---|---|---|---|
| S1 | Saved sign-in, verify only | `SUBSCRIPTION_LOGIN_ONLY=1 python examples/subscription_login.py` | exit 0, one JSON line of evidence, no JWT in output |
| S2 | Corrupt `auth.json` | same | exit 1, one-line message, file not overwritten |
| S3 | Empty model name | `AIDER_SUBSCRIPTION_MODEL=" "` | exit 2, one-line message |
| S4 | Expired token, refresh cannot run | same as S1 | exit 1, "Run aider --login openai again." |
| S5 | Old example, `OPENAI_API_KEY` set, no saved sign-in | `examples/subscription_login.py` before the rewrite | exit 1, raw traceback (`AuthError`); login was skipped |

Before the rewrite, S5 showed the root cause: the old example asked `resolve() is None` before
logging in. An environment key made `resolve()` non-empty, so login was skipped.

`test_main.py`: 3 failures (`test_default_model_selection`, `test_model_precedence`,
`test_reasoning_effort_option`) with `OPENROUTER_API_KEY` set in the shell. With that variable
unset, all 3 pass (exit 0). These are environment-dependent tests, not login defects. This closes
the "pending" classification in `docs/login-roadmap.md` P0.

## 2. Comparison with the reference model

| Reference (OpenHands) | aider now | Evidence |
|---|---|---|
| `LLM.subscription_login(vendor, model)` | `login_provider` + `Model("chatgpt/<model>")` | S1 |
| OAuth PKCE in the browser, state checked | Yes. Wrong `state` gets HTTP 400. Callback on port 1455 | probe 7, 8 |
| Device code for remote machines | Automatic when headless (SSH, Linux without display) | probe 10 |
| Device code chosen by the user (`auth_method`) | **Not available** on a local machine. `--method` accepts only `browser`/`key` | `aider/args.py` |
| Credential cache (`~/.openhands/auth/`) | `~/.aider/auth.json`, 0600 on POSIX, atomic write, file lock | probe 4-6; 0600 is not enforced on Windows (documented) |
| Automatic refresh | Yes, under the file lock, with a 5-minute margin | S4 |
| `FORCE_LOGIN` | `AIDER_SUBSCRIPTION_FORCE_LOGIN=1` | code |
| `open_browser=False` | **Not available**. The browser opens whenever the callback listener starts | `openai_login.py` |
| `is_subscription` flag | JSON evidence line (`signed_in`, `source`, `access_expires_at`) | S1 |
| Verify-only mode | `SUBSCRIPTION_LOGIN_ONLY=1` | S1 |

## 3. What works (evidence)

- The 125 login tests pass (L1). They use stubs for the network, so no real account is exercised.
- Saved sign-in beats the environment; the environment is used only when nothing is saved (probe 2).
- Logout removes only the stored entry and leaves the environment alone (probe 6).
- A corrupt store raises `CredentialStoreError` and is not overwritten (probe 4, S2).
- A failed write leaves the previous value intact (probe 5).
- The callback rejects a wrong `state`, accepts the right one, and a busy port triggers the paste
  fallback (probes 7, 8).
- Pasted redirect URLs are checked for `state`; a sign-in link pasted by mistake gets an
  explanation instead of a silent failure (probe 9).
- Errors name the next command and do not print tokens (`post_request` shows only the HTTP status).

## 4. Findings

Severity: High = wrong credential used or failure hidden. Medium = confusing or silent failure.
Low = usability gap.

### F1 (Medium) `use_now()` puts the ChatGPT token into `OPENAI_API_KEY`

- Evidence: probe 1 (`login_cli.use_now("openai", ...)` sets `OPENAI_API_KEY` to the access token).
- Impact: `use_now` runs after `/login` in chat (`aider/commands.py:614`) and in the old example.
  In that process, an `openai/...` model would send the ChatGPT token to `api.openai.com`, which
  rejects it. The `chatgpt/` path reads the store, so it is not affected. `apply_stored_keys`
  already skips this case, so the two code paths disagree.
- **Fixed** in `aider/login_cli.py` (`use_now`): returns without setting the variable when the stored
  OpenAI credential is an OAuth token. Test: `test_use_now_does_not_expose_chatgpt_token_as_openai_api_key`.
  Previous fix text: in `use_now`, return without
  setting the variable when the stored credential is an OpenAI OAuth token.
- The rewritten example no longer calls `use_now`. `oauth_runtime.py` says litellm ignores the key
  for `chatgpt/`.

### F2 (Medium) A broken ChatGPT sign-in is silent at startup

- Evidence: probe 2 (`openai report at startup: []`) against Anthropic (`reported`). For OpenAI,
  `apply_stored_keys` skips the provider with `continue`, so the `report` callback never runs.
  `OPENAI_API_KEY` from the environment is then used silently.
- Impact: the user only learns about the broken sign-in when a `chatgpt/` model is first used
  (`prepare_chatgpt_auth` raises the correct message then). Until then the run uses the API-key
  account, which may be a different billing account.
- **Fixed** in `aider/auth.py` (`apply_stored_keys`): the OpenAI OAuth branch now renews the sign-in
  and reports a failure through `report`. The API-key precedence is unchanged. Tests:
  `test_apply_stored_keys_reports_broken_chatgpt_sign_in_at_startup` and
  `test_apply_stored_keys_renews_chatgpt_sign_in_without_reporting`. Previous wording: report the
  expired-sign-in case for OpenAI, too, without changing the
  precedence of the API key. Add a test to `test_auth.py`.

### F3 (Medium, fixed in the example) The old example skipped login when `OPENAI_API_KEY` was set

- Evidence: S5. Exit 1 with a traceback.
- Fixed: `examples/subscription_login.py` checks for a stored ChatGPT credential
  (`type == oauth` and `accountId`), not for `resolve()`. Errors exit 1 with one line.
- Remaining gap: the "no sign-in, env key set" path (starts an interactive login) was not run
  automatically because it opens a browser and waits. It is listed under section 6.

### F4 (Low) The old example's success line could be true for an API key

- Evidence: `resolve()` returns `source="env"` for `OPENAI_API_KEY` (checked). The old example
  printed `Using ChatGPT subscription login: {resolve() is not None}`, which is true for an API key.
  In S5 the line was not reached, because `Model()` failed first, so the effect was latent.
- Fixed: the evidence line reports `source` ("stored" or "none") and `openai_api_key_in_env`
  separately.

### F5 (Low, usability) No way to choose the device code on a local machine

- The OpenHands example has `auth_method="device_code"`. In aider the device flow runs only when
  the machine is headless. A user at a local desktop who prefers to approve on a phone has no flag.
- Proposal: `--method device` for `openai` (the code exists in `_login_device`). Needs CLI tests.

### F6 (Low, usability) `--logout` choices are a hard-coded list

- `aider/args.py` lists `openrouter, openai, anthropic, deepseek, gemini`, while
  `API_KEY_PROVIDERS` is the source of truth. A new provider would silently fail `--logout`.
- Proposal: build the list from `auth.PROVIDERS`.

### F7 (Low, test isolation) `test_main` depends on the shell environment

- Evidence: section 1. Three tests fail when `OPENROUTER_API_KEY` is set and pass when it is not.
- Proposal: clear the provider variables in these tests' setup (`monkeypatch`/`patch.dict`).
  This is not a product defect, but it makes the suite fail on developer machines.

### F8 (Low) `open_browser=False` is not available

- The reference has this option. Proposal: a `--no-browser` flag or the `AIDER_NO_BROWSER`
  variable. Only needed if users ask for it.

## 5. Usability of the adapted example

The new `examples/subscription_login.py`:

- Uses one environment variable per decision (model, forced login, verify only). Documented in the header.
- Exit codes: 0 success, 1 sign-in or model problem, 2 invalid configuration. Scripts can rely on them.
- Verify-only mode prints one JSON line with no secrets, so two machines can be compared (S1).
- Warns, but does not block, on a model name outside the list from the reference.

## 6. Not verified (L3 is partial)

| Item | Why it is open | How to close it |
|---|---|---|
| Real browser sign-in to OpenAI on Windows (callback on 127.0.0.1:1455) | No account in this environment, and the client ID belongs to another application (see `docs/login-roadmap.md`) | Run `aider --login openai` on a machine with a ChatGPT subscription. Record the output. |
| Device-code login against the real server | Same | Run `aider --login openai` over SSH. Record the output. |
| Real `chatgpt/` call (token accepted by the backend) | Same | `SUBSCRIPTION_LOGIN_ONLY=0 python examples/subscription_login.py` with a real sign-in |
| Example with no sign-in and `OPENAI_API_KEY` set | Opens a browser and waits | Run it on a machine with a browser and check the message |
| 0600 file mode | Windows does not enforce it | Check the ACL on Windows, or run on Linux |
| Full suite and pre-commit (black, isort, codespell) | Not run in this audit. `test_main` needs `OPENROUTER_API_KEY` unset | `env -u OPENROUTER_API_KEY pytest` and `pre-commit run --all-files` |

## 7. Changes made in this audit

- `examples/subscription_login.py` rewritten (F3, F4, section 5). It is still untracked, like
  the rest of `examples/`.
- `scripts/audit_login.py` added: the probe in section 1. It runs without network and does not
  touch the real `~/.aider`.
- `docs/login-audit.md`: this report.
- `aider/login_cli.py` and `aider/auth.py`: F1 and F2 fixed, with 4 new tests (red before the fix, green after).
  Full login suite: 128 passed, 2 skipped. `test_main` with `OPENROUTER_API_KEY` unset: 80 passed.
- F5, F6, F7 and F8 are still proposals that need a decision.

The working tree has other uncommitted files from earlier work (`after-*.txt`, `*-flake8.txt`,
`pytest-p9.txt`, `new-tests.txt`). They are not part of this audit and should be removed or ignored
before a commit.

## 8. Next steps, in order

1. Run the L3 items in section 6 on a real machine. F1 and F2 are verified only by unit tests and the probe.
2. Decide on F5 (device-code choice) and F6 (derive the `--logout` list from `auth.PROVIDERS`).
3. F7: isolate `test_main` from provider variables in the shell.
