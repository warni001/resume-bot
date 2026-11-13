# resume-bot

A Microsoft Teams resume bot built on the M365 Agents SDK (Python). The bot *is* the résumé: ask it about John Warnicke's professional experience and it answers from his profile, with more depth than a résumé — or just talk to it about anything.

**Demo:** https://app-resume-bot-jyq4f2yizrb3e.azurewebsites.net/demo (WebChat; a screen-door gate card asks for the developer's name before chatting)

**Status:** scaffold complete; first deploy pending. The spec lives at [`docs/resume-bot-agents-sdk-spec.md`](docs/resume-bot-agents-sdk-spec.md).

## Architecture

| Layer | Choice |
|---|---|
| Language | Python 3.14 |
| Agent SDK | Microsoft 365 Agents SDK for Python, 1.9.0 (pinned) |
| HTTP server | aiohttp, via the SDK's hosting-aiohttp package |
| Auth | MSAL, SingleTenant; connection settings from env / Key Vault |
| State | Azure Blob storage (`microsoft-agents-storage-blob`); full conversation history, no cap |
| LLM | OpenRouter, model from config only (`OPENROUTER_MODEL`) |
| Voice | Azure AI Speech F0: `/api/speech/token` mints short-lived tokens; STT/TTS live in the demo page only |
| Logging | OpenTelemetry → Azure Monitor (Application Insights) |
| Secrets | Env / `.env` locally; Azure Key Vault in prod (no secrets in the repo) |
| Hosting | Azure App Service F1 (free), Azure Bot F0 |

Every SDK name in this codebase was verified against the installed 1.9.0 package, not copied from the docs — the docs and samples drift from the release (the samples' `MsalConnectionManager(config=...)` form, for example, is silently ignored by 1.9.0; the working form unpacks the config dict).

## Layout

```
app.py              # aiohttp app: /api/messages, /health, /demo,
                    #   /api/directline/token, /api/speech/token, /static/
agent.py            # AgentApplication, routes, gate logic, chat dispatch
chat.py             # OpenRouter client (system prompt + profile + history)
config.py           # env/.env first, Key Vault overrides
bot_logging.py      # OTel → Azure Monitor, custom dimensions (NOT logging.py)
profile.md          # About John Warnicke — content outside code
cards/gate.json     # Demo gate Adaptive Card (Action.Execute + email field)
webchat/index.html  # /demo page (WebChat 4.19.2 + Speech SDK 1.53.0 pinned)
static/card-bg.jpg  # Gate card background image
tests/              # pytest, 66 tests
infra/              # main.bicep, deploy.sh (provisioning), deploy-bot.sh (redeploy)
```

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python app.py   # POST /api/messages, GET /health on :3978
```

Configuration comes from env vars or a `.env` file (never committed):

| Variable | Purpose |
|---|---|
| `CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID` | Bot app ID |
| `CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET` | Bot app password |
| `CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID` | Tenant ID (SingleTenant) |
| `KEY_VAULT_URL` | Vault URL — when set, vault values override env |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` | Chat backend (model ID lives only here) |
| `DEMO_GATE_NAME` | Gate answer; unset disables the gate |
| `APP_BASE_URL` | Public base URL, for the gate card's background image |
| `SPEECH_REGION` | Speech region for the voice token endpoint (set by bicep from the deployment location) |
| `BLOB_STORAGE_CONNECTION_STRING` | State backend; unset falls back to in-memory storage |

Test with the Agents Playground: `agentsplayground -e "http://localhost:3978/api/messages" -c "emulator"`. Preview cards in the Adaptive Cards designer or WebChat, not the old Emulator (it renders cards poorly).

## Tests

```bash
.venv/bin/python -m pytest -q   # must pass before any commit
```

CI runs `compileall` + `pytest` on every push/PR to `main`, and auto-deploys pushes to `main` (zip deploy + `/health` canary: all five config flags must be true). `infra/deploy-bot.sh` does the same redeploy from a machine with the Azure CLI, and also applies `DEMO_GATE_NAME` and `OPENROUTER_MODEL` from the local `.env` (warn-and-skip if absent). All four app settings are declared in `infra/main.bicep` (`APP_BASE_URL` and `SPEECH_REGION` derived, the other two as parameters passed by `deploy.sh` from `.env`); the CI job does not manage app settings. Always run `deploy-bot.sh` after a local `deploy.sh` run.

## Conventions

`AGENTS.md` (generated from `AGENTS.project.md` + `ponytail.md`) governs how code is written here: smallest complete change, config in config not code, no secrets anywhere near the repo, verify SDK names against the installed package. Never edit `AGENTS.md` directly — edit `AGENTS.project.md` and regenerate.
