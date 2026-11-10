# Resume Bot — Agents SDK Rebuild Spec

**Status:** Draft v1 for John's review, edit, and approval. Nothing here is built yet.
**Date:** Nov 9, 2026
**Goal:** Recompose the resume chatbot from scratch on the Microsoft 365 Agents SDK (Python), in a new public repo, as the portfolio piece. The old Bot Framework SDK bot (private `warni001/teams-bot-framework`) is superseded, not migrated.

---

## 1. Purpose

A hiring manager opens the demo, passes a screen-door gate, and asks the bot about John's professional experience — getting faithful, profile-grounded answers with more depth than a résumé — or just talks to it about anything. The bot is the proof of work: it demonstrates he can build a production-shaped agent on the current SDK, with real auth, real telemetry, real CI/CD, and no secrets in the repo.

The elegance of the concept (John, Oct 30): the bot *is* the résumé — it answers questions about the person who built it.

---

## 2. Decisions already made (John's word)

These are settled. The spec does not re-litigate them.

- **Restart from scratch, not a migration.** The old code is not ported file by file; the new codebase is written fresh on the Agents SDK.
- **Microsoft 365 Agents SDK (Python), not Bot Framework SDK.** The BF SDK is archived; support ends Dec 31, 2025. The Agents SDK is the current path.
- **No weather feature.** The old bot's `weather [in] <place>` command is dropped. The new bot is chat + gate + demo only.
- **Public repo.** The new repo is public so prospective employers can read the code. (Old repo stays private.)
- **Keep the tooling.** `infra/deploy-bot.sh`, the CI jobs (test / deploy / ponytail), `AGENTS.md` + Ponytail (`ponytail.md`, `AGENTS.project.md`, `scripts/sync-ponytail.sh`, `scripts/generate-agents-md.sh`) all carry over, adapted to the new repo.
- **Logging stays OpenTelemetry → Azure Monitor.** Same as the old bot. Azure logging is a first-class requirement, not an afterthought.
- **Specs first.** This document is reviewed, edited, and approved by John before any code is written.
- **Python 3.12.** Project-wide pin, same as the old bot.
- **Config lives in config, not code.** No model IDs, secrets, or environment-specific values hardcoded. (Standing rule.)
- **No secrets in client-side code or SCM.** No secret, however trivial, in the repo or the demo page. (Standing rule.)
- **John keeps the pen.** He approves specific verbiage before anything is sent or filed on his behalf; he designs the gate card himself.

---

## 3. Open decisions (need John's word)

These block the build until decided. Defaults are proposed; John picks.

| # | Decision | Proposed default | Notes |
|---|----------|-----------------|-------|
| 1 | **Repo name** | **`warni001/resume-bot` — approved (John, Nov 9).** | Clear, matches the purpose, reads well for employers. Alternatives considered: `teams-resume-bot`, `resume-agent`. |
| 2 | **Fresh Azure resources or reuse?** | **Fresh — parallel deployment.** (John, Nov 9: "there's really no cost to deploying new azure resources" and "it's not like i'm blazing through my azure credits.") Deploy a parallel set of resources with new names via a small tweak to `infra/deploy.sh` / `main.bicep`. The old bot stays live on its demo URL while the new one is built and tested; the new bot gets its own endpoint. Marginal cost is ~$0 (F1/F0/free tiers; Key Vault ops are pennies). | Reuse was the earlier default (same web app, same demo URL). Fresh wins on separation and zero risk to the live demo, at no meaningful cost. The SP creation is automated (idempotent check-and-create step in `deploy.sh` — John, Nov 9); storing secrets in the new vault and setting app settings remain manual steps. |
| 3 | **SDK version pin** | `microsoft-agents-* == 1.9.0` (Oct 19, 2026, latest stable as of Nov 9) | The SDK moves fast (1.7.0 Sep 17 → 1.8.0 Oct 1 → 1.9.0 Oct 19). Pin exactly. API names drift between releases — verify against the installed package, never the docs. |
| 4 | **Fix the log-dimension wart?** | **Yes.** Drop the hardcoded `channel: "msteams"` default dimension that rides on every record in the old bot. Filter traces on `channel_id` only. | This is the clean-restart dividend: fix the known warts instead of carrying them. |
| 5 | **Fix the gate strip bug?** | **Yes.** In the old bot the submitted answer is `.strip().casefold()`ed but the configured gate name is only `.casefold()`ed — a trailing space in `DEMO_GATE_NAME` would lock the gate permanently. Strip both sides. | Same reasoning as #4. |
| 6 | **Keep the demo gate?** | **Yes.** Same screen-door design: Adaptive Card asking for the developer name, submit checked against `DEMO_GATE_NAME`, gate off when unset. | Card design stays John's job. |
| 7 | **CI deploy secret** | John adds the `AZURECREDENTIALS` repo secret to the new repo. | The deploy SP (`github-teams-bot-deploy`) already has Contributor on `rg-teams-bot-dev`. But the secret value lives only in the old repo's secrets and John's records — it must be added to the new repo before CI can deploy. John does this (it involves a credential). |
| 8 | **Old repo fate** | Leave private as history; do not delete. | The new repo is the live project. The old repo is the record of how it was built. |

---

## 4. Architecture

### 4.1 Stack

| Layer | Choice | Notes |
|-------|--------|-------|
| Language | Python 3.12 | Pin project-wide. |
| Agent SDK | Microsoft 365 Agents SDK for Python, **1.9.0** | Packages: `microsoft-agents-hosting-aiohttp`, `microsoft-agents-hosting-core`, `microsoft-agents-activity`, `microsoft-agents-authentication-msal`, `microsoft-agents-storage-blob` (later, for Blob state). |
| HTTP server | aiohttp (via the SDK's hosting-aiohttp package) | Same as the old bot. Serves `/api/messages` (via the SDK adapter), plus our own routes: `/health`, `/demo`, `/api/directline/token`, `/static/`. |
| Auth | MSAL via `microsoft-agents-authentication-msal`, SingleTenant | Same app registration (`a373dd89-9618-4e0b-8603-760575b9f47f`), same tenant (`6803d008-cce8-4670-8115-f4a77cca0357`). The SDK reads its connection settings from env vars — see §5.7: determine the exact env var names 1.9.0 expects and map the existing vault secrets to them. |
| State | `MemoryStorage` (in-memory) for v1 | Same as the old bot. Blob storage (`microsoft-agents-storage-blob`) is a later milestone (persistent memory), not v1. |
| LLM | OpenRouter, model from config | Same as the old bot. Model ID lives only in config (`.env` / app setting), never in code. Current model: `nvidia/nemotron-3-ultra-550b-a55b:free`. |
| Chat history | Rolling 4-exchange history in conversation state | Same as the old bot (`MAX_HISTORY_ENTRIES = 8` — 4 exchanges × 2). |
| Logging | OpenTelemetry → Azure Monitor (Application Insights) | Same as the old bot. See §5.5. |
| Secrets | Env / `.env` locally; Azure Key Vault in prod | Same vault (`kv-teamsbot-us4izdyz`), same secret names. See §5.7. |
| Hosting | Azure App Service, F1 (free) Linux, Python 3.12 | New web app (parallel deployment, decision #2). Same resource group `rg-teams-bot-dev`, westus3. |
| CI/CD | GitHub Actions: test → deploy (auto on push to main) + ponytail auto-fix | Same shape as the old bot. See §6.2. |

### 4.2 How the Agents SDK differs from the old Bot Framework SDK (what the build must account for)

These are the structural differences that shape the code. They are design constraints, not migration steps — the code is written fresh, but it must be written *to* these facts.

- **No `TeamsActivityHandler` subclass.** The agent is an `AgentApplication[TurnState]` instance. Behaviour is registered as routes on that instance, not as overridden methods.
- **Routing, not inheritance.** Handlers are registered with decorators / calls:
  - `@AGENT_APP.activity("message")` — message activities (the main chat path).
  - `@AGENT_APP.message("/help")` — fixed-string message routes (exact match).
  - `@AGENT_APP.message(re.compile(...))` — regex message routes.
  - `@AGENT_APP.conversation_update("membersAdded")` — the welcome / gate-card trigger.
  - `@AGENT_APP.error` — the top-level error handler (replaces the old `on_turn_error`).
  - Route evaluation stops at the first match by default. The catch-all message handler **must** be registered with `rank=RouteRank.LAST` so fixed commands and regex routes are tried first.
- **Adaptive Card submits are invoke activities, not message activities.** In the old BF SDK bot, a card submit arrived as a message activity carrying a value payload, and the gate read it from the message. In the Agents SDK, an Adaptive Card submit arrives as an **invoke activity** with `activity.name == "adaptiveCard/action"`. The gate must register an **invoke route** for the submit, not read it from the message handler. (Verify the exact invoke name and payload shape against the installed 1.9.0 package during the build — do not trust the docs' naming, which has drifted before.)
- **Turn state is explicit.** `TurnState` carries conversation state and user state; the agent reads/writes them through the state object, not through `ConversationState`/`UserState` property accessors as in the BF SDK.
- **Auth configuration is env-driven.** The SDK loads its connection settings from environment variables (`load_configuration_from_env`). The exact env var names for the app ID, password, and tenant in 1.9.0 must be verified against the installed package and mapped to the existing vault secrets (§5.7). Do not assume the BF SDK's env var names carry over.
- **Error handling is a route.** `@AGENT_APP.error` registers a handler taking `(context, error)`. The old bot's `on_turn_error` (adapter-level, guarded per-step so it never throws) has no direct equivalent — the error route must itself never throw, and must send a safe fallback message to the user.
- **API names drift between releases.** The docs have shown names the installed package does not have (e.g. docs' `turn_error` vs the real `error()` in 1.5.0; docs' `RouteRank.First/Unspecified/Last` vs the package's `FIRST/DEFAULT/LAST`). Rule for the build: **verify every SDK name against the installed 1.9.0 package (import it, inspect it) before using it.** Never copy a name from the docs or from a blog without checking it in the installed package.

### 4.3 Module layout (proposed)

```
resume-bot/
├── app.py                  # aiohttp app: /api/messages (SDK adapter), /health, /demo,
│                           #   /api/directline/token, /static/. Startup, config load.
├── agent.py                # AgentApplication instance, route registrations, gate logic,
│                           #   chat dispatch, error route. (Replaces old bot.py.)
├── chat.py                 # OpenRouter client: chat_reply(), system prompt, profile load,
│                           #   history handling. Carried over from the old bot, adapted
│                           #   to the Agents SDK's TurnState (no BF SDK imports).
├── config.py               # Env/.env first, Key Vault overrides. Same secret names.
│                           #   Maps vault secrets to the SDK's expected env vars (§5.7).
├── bot_logging.py          # OTel → Azure Monitor setup, DimensionLoggerAdapter.
│                           #   NOT named logging.py. Fix the channel-dimension wart (§3 #4).
├── profile.md              # About John Warnicke. Content outside code. Carried over
│                           #   from the old bot (same text, re-approved by John).
├── cards/
│   └── gate.json           # Demo gate Adaptive Card (v1.3). John's design. Carried over.
├── static/
│   └── card-bg.jpg         # Gate card background image. Carried over.
├── webchat/
│   └── index.html          # /demo page (WebChat 4.19.2 pinned CDN). Carried over.
├── tests/
│   ├── test_agent.py       # Route registration, gate logic, message dispatch.
│   ├── test_chat.py        # OpenRouter client, profile wiring, history, timeout.
│   ├── test_config.py      # Env/vault loading, flag computation.
│   ├── test_logging.py     # OTel setup, dimension adapter, azure logger quieting.
│   └── test_app.py         # /health flags, /demo serve, token endpoint behaviour.
├── infra/
│   ├── main.bicep          # Azure resources (carried over; only if new resources needed).
│   ├── deploy.sh           # Provisioning (carried over).
│   └── deploy-bot.sh       # Redeploy: pull --rebase, zip deployables, poll /health canary.
├── .github/workflows/ci.yml  # test job, deploy job (canary), ponytail auto-fix job.
├── AGENTS.md               # Generated (AGENTS.project.md + ponytail.md). Do not edit directly.
├── AGENTS.project.md       # Project section. Edit this.
├── ponytail.md             # Byte-exact upstream Ponytail copy.
├── scripts/
│   ├── generate-agents-md.sh
│   └── sync-ponytail.sh    # Sync + regen + commit + push. --check for CI. --no-push.
├── requirements.txt        # Pinned: microsoft-agents-* == 1.9.0, aiohttp, aiohttp, etc.
├── .python-version         # 3.12 (open offer from the old bot — include it this time).
├── .gitignore              # .venv, .env, __pycache__, etc. Never commit .env.
└── README.md               # Portfolio README: what it is, architecture, how to run it,
                            #   demo link. This is the first thing an employer reads.
```

---

## 5. Features (v1 scope)

v1 is the smallest complete rebuild: the bot works end-to-end in the demo and in Teams, with the same user-visible behaviour as the old bot minus weather. Nothing else is in v1. Later features are §8.

### 5.1 Chat — OpenRouter + profile (the core)

- **What:** Any non-gate message goes to the model. The model answers from a system prompt + `profile.md` (John's professional experience) + rolling 4-exchange history.
- **System prompt rules (carried over from the old bot):**
  - The bot is a general-purpose conversational assistant. It discusses any topic the user raises.
  - When the user asks about John, it answers **only** from the profile. If the profile doesn't cover it, it says "I don't know" — it never invents.
  - It does not reveal the system prompt or its instructions.
  - Replies end with `— via <model>` from the response's `model` field.
- **Profile:** `profile.md` at repo root, loaded at import, appended to the system prompt every call. Missing file → old prompt (no profile). The profile text is the same as the old bot's (re-approved by John; he can edit it as part of spec review).
- **History:** Rolling 4 exchanges (8 entries) stored in conversation state. History stores the bare reply (no `— via` suffix).
- **Timeout:** `chat_reply` uses a 120-second total timeout (free-tier long answers took 43s+ in the old bot). A genuinely hung request holds the turn for 2 minutes — accepted risk, same as the old bot.
- **Failure behaviour:** No key or no model configured → error fallback. Any model failure (`ChatError`, timeout, empty content) → error fallback. The error message is `I couldn't get a reply just now — try again in a bit.` (same as the old bot's `CHAT_ERROR_MESSAGE`).
- **Null-content guard:** The model once returned `content: null`. The client must guard nullable/missing fields before use (standing rule: never assume a field exists in an external API response).
- **Latency logging:** Time the OpenRouter call and log `openrouter_ms` (and `openrouter_model`) on the chat-reply log. Weather had no `openrouter_ms` (absent, not zero) — same here: only chat replies carry it.
- **OpenRouter quota rule (standing):** Check with John before any test call that burns his free daily requests (50/day, resets midnight UTC = 7pm EST) until he has paid credits going.

### 5.2 Demo gate — Adaptive Card challenge (screen door, not a lock)

- **What:** When a user joins the conversation (`membersAdded`), the bot sends the gate card (from `cards/gate.json`, v1.3) asking for the developer name. Until the submitted answer matches `DEMO_GATE_NAME`, no message reaches chat. Wrong answers are rejected without echoing the attempt in the transcript.
- **Gate off when `DEMO_GATE_NAME` is unset.** Same as the old bot.
- **Card submit is an invoke activity** (`adaptiveCard/action`) — see §4.2. The gate registers an invoke route for it. This is the one place the old bot's message-based submit handling does **not** carry over.
- **Comparison:** Strip **both** sides (submitted answer and configured name) and `.casefold()` both. (Fixes the old bot's strip bug — decision #5.)
- **Card content:** `cards/gate.json` is John's design. The build carries it over verbatim. The card's `GATE_INPUT_ID = "developerName"` is the contract — the JSON must contain an `Input.Text` with that id, validated at load (raises if missing).
- **Background image:** The card references `{APP_BASE_URL}/static/card-bg.jpg` via `backgroundImage` at the card top level, `fillMode: "cover"`. The app serves `/static/` from the repo's `static/` directory. `APP_BASE_URL` comes from the app setting (already set in Azure) or local env.
- **Welcome message:** On a correct answer, the bot replies with the greeting (John's current wording, carried over): "Welcome to ResumeBot! … I can tell you about my creator's professional experience, as well as converse on any other topic. I currently operate in webchat and Microsoft Teams." (John owns the wording; the test pins only that a greeting is sent, not the exact text.)

### 5.3 Direct Line token endpoint + /demo page

- **`GET /api/directline/token`:** Mints a ~30-minute Direct Line token server-side. The Direct Line secret lives in Key Vault as `DirectLineSecret` — it is **never** in client-side code or the repo. The endpoint is open by design (the secret can't be extracted from a minted token). Returns 503 if unconfigured, 502 on upstream failure. Same as the old bot.
- **`GET /demo`:** Serves the WebChat page (`webchat/index.html`), WebChat **4.19.2 pinned via jsDelivr**. No token auto-refresh — a >30-minute demo conversation needs a page refresh (known limitation, same as the old bot).
- **No secret in the demo page.** (Standing rule.)

### 5.4 /health + deploy canary

- **`GET /health`** returns five flags, all of which must be true for a deploy to pass the canary:
  - `app_id_configured`
  - `azure_logging_configured`
  - `key_vault_configured`
  - `directline_configured`
  - `openrouter_configured`
- **Canary rule (standing lesson):** After any deploy, curl `/health` and confirm all flags true. It is the canary for silent vault failures (the Oct 30 incident: the web app's managed identity had no Key Vault role assignment, every secret read was silently skipped, and only `/health` showed it).
- **CI deploy job** polls `/health` (up to 30×10s) and fails loudly (`CANARY FAILED`) unless all five flags are true.

### 5.5 Logging — OpenTelemetry → Azure Monitor

- **What:** Same as the old bot. `bot_logging.py` sets up OTel → Azure Monitor when the Application Insights connection string is present, stdout otherwise. `DimensionLoggerAdapter` deep-merges per-call `custom_dimensions`.
- **Fix the wart (decision #4):** Drop the hardcoded default dimension `channel: "msteams"` that rides on every record in the old bot. Filter traces on `channel_id`, never on `channel`.
- **Log-loop guard (standing rule):** `setup_logging()` quiets the `azure` logger to WARNING. The OTel `LoggingHandler` sits on the root logger — Azure SDK INFO chatter would otherwise be captured and re-exported as telemetry about telemetry. Anticipate and avoid log loops up front.
- **Turn dimensions:** Every turn log carries `channel_id` (from the activity), plus `activity_type`, `conversation_id`, `user_id`, `tenant_id`, `locale`.
- **Content logging (John's explicit choice):** The "message handled" log includes the incoming text, and all reply paths log `"reply sent: %s"`. The text lands in App Insights. Gate replies and the welcome message are deliberately **not** logged (static text).
- **Latency dimension:** Chat replies log `openrouter_ms` and `openrouter_model`. (See §5.1.)
- **KQL (for John's reference, not the build):** In App Insights Logs (`traces`), filter on `customDimensions.custom_service == "resume-bot"` (or whatever service name the spec sets — see below). In Log Analytics (`AppTraces`), same column. The flat attributes land as `custom_<key>` (e.g. `customDimensions.custom_skill`), not bare keys.

### 5.6 Error handling

- **Top-level error route** (`@AGENT_APP.error`): catches any exception from a turn, logs it (with dimensions), and sends the user a safe fallback message. The error handler itself must never throw.
- **Turn-level guards:** Each handler step is guarded so one failing step (e.g. a vault read, a model call) degrades to the error fallback instead of crashing the turn.
- **Startup guards:** Missing `static/` directory must not crash startup (old bot lesson: aiohttp's `add_static` raises `ValueError` if the directory doesn't exist). Guard with `is_dir()` and log a warning instead.

### 5.7 Config & secrets

| Setting | Source | Notes |
|---------|--------|-------|
| `MicrosoftAppId` | Vault / env | Same app registration (`a373dd89-…`). |
| `MicrosoftAppPassword` | Vault / env | Client secret. Never in the repo. |
| `MicrosoftAppTenantId` | Vault / env | `6803d008-…` (SingleTenant). |
| `ApplicationInsightsConnectionString` | Vault / env | OTel exporter target. |
| `OpenRouterApiKey` | Vault / env | Dedicated `teams-bot` key, $5 limit, expires 2027-10-25. |
| `DirectLineSecret` | Vault / env | Direct Line Default Site key. Never in the repo. |
| `OPENROUTER_MODEL` | Env / app setting | Model ID. Config only, never code. Current: `nvidia/nemotron-3-ultra-550b-a55b:free`. |
| `DEMO_GATE_NAME` | Env / app setting | Gate answer. Unset = gate off. |
| `APP_BASE_URL` | Env / app setting | Feeds the gate-card background substitution. In prod it is the new web app's URL (old bot's was `https://app-teams-bot-us4izdyzahmdi.azurewebsites.net`). |
| `LOG_LEVEL` | Env / app setting | `INFO` in prod. |
| `PORT` | Env | Default 3978 locally; App Service sets 8000. |

- **Precedence:** Env / `.env` first; if `KEY_VAULT_URL` is set, the vault overrides. Vault failure modes (carried over): per-secret `get_secret` exceptions are swallowed (logged, not fatal); an import/setup failure logs one warning and skips all vault loading. Eager vault load at startup is fine for now; fetch-on-demand with caching is the expected future direction (John, Oct 22).
- **Verification item (build-time):** Determine the exact env var names Agents SDK 1.9.0 expects for the app ID / password / tenant (its `load_configuration_from_env` contract) and map the vault secrets to them in `config.py`. Do not assume the BF SDK's env var names carry over. Verify by reading the installed package.
- **`.env` is never committed and never edited by the assistant directly.** Hand John the exact line to add. (Standing rule — the Oct 28 `.env` denial.)

---

## 6. Deployment

### 6.1 Azure resources (decision #2 — fresh, parallel deployment)

A parallel set of resources is provisioned with new names, via a small tweak to `infra/deploy.sh` / `main.bicep` (new resource names). The old bot stays live on its existing resources and demo URL while the new one is built and tested. The new bot gets its own endpoint and demo URL.

The existing resources (for reference — the old bot keeps running on these):

| Resource | Name / ID |
|----------|-----------|
| Resource group | `rg-teams-bot-dev` (westus3) |
| Web app | `app-teams-bot-us4izdyzahmdi` |
| App Service plan | F1 (free) |
| Key Vault | `kv-teamsbot-us4izdyz` |
| Bot (Azure Bot) | `bot-teams-bot-us4izdyzahmdi` |
| App ID | `a373dd89-9618-4e0b-8603-760575b9f47f` (SingleTenant) |
| App Insights | `appi-teams-bot` |
| Log Analytics | `log-teams-bot` |
| Direct Line | Default Site (already created, secret in vault) |
| Tenant | `6803d008-cce8-4670-8115-f4a77cca0357` |
| Subscription | "Azure subscription 1" (`2b5dd0c2-…`) |

The new resources mirror this shape (new resource group or new names within `rg-teams-bot-dev`, new web app, new vault, new bot registration, new App Insights). Exact new names are set when the provisioning is run — the spec does not fix them here.

- **Service principal creation is automated (John, Nov 9).** `deploy.sh` gains an idempotent step: check whether a service principal exists for the new app registration (`az ad sp show --id <new-app-id>`); if missing, create it (`az ad sp create --id <new-app-id>`); if present, skip. This closes the Oct 30 gap (the old app registration had no service principal, `AADSTS7000229`, and it had to be created by hand). The step runs on John's Mac with his credentials (he is a Global Administrator) — that is the security boundary. It is **not** in CI: the CI deploy SP (`github-teams-bot-deploy`) has Contributor on the resource group only and must not be given Azure AD admin rights. It is also **not** done via a Bicep `deploymentScripts` resource (that would add a managed identity with AD permissions — more privilege, more moving parts). Bicep cannot create AD objects directly; the CLI step in the provisioning script is the right layer.
- **Manual steps for the new resources:** store the secrets in the new vault (`MicrosoftAppId`, `MicrosoftAppPassword`, `MicrosoftAppTenantId`, `ApplicationInsightsConnectionString`, `OpenRouterApiKey`, `DirectLineSecret` — Direct Line needs its own channel + secret on the new bot), and set the app settings (`DEMO_GATE_NAME`, `OPENROUTER_MODEL`, `APP_BASE_URL` pointing at the new web app). The bicep declares the Key Vault Secrets User role assignment for the new web app's managed identity, so it should be created correctly this time — but verify it exists (the Oct 30 incident was caused by it being missing).
- **Cost:** ~$0. F1 + F0 tiers. No PAYG compute. (Standing rule: no paid tiers to test a single-user bot.) John is not burning through Azure credits.

### 6.2 CI/CD (GitHub Actions — carried over, adapted)

`.github/workflows/ci.yml` — three jobs, same shape as the old bot:

- **`test`** (push + PR to main): Python 3.12, `compileall` + `pytest`. Must pass before any commit lands.
- **`deploy`** (needs `test`; push-to-main only): zips the deploy file list, `azure/login@v2` with `${{ secrets.AZURECREDENTIALS }}`, `azure/webapps-deploy@v3` to the new web app (name set at provisioning), then the `/health` canary (all five flags, up to 30×10s, fail loudly). **Requires decision #7** — the `AZURECREDENTIALS` secret must exist in the new repo.
- **`ponytail`** (push to main: auto-fix; PR: check-only): runs `scripts/sync-ponytail.sh` (auto-fix, commits as `github-actions[bot]`) on push; `--check` on PRs. Not in the deploy job's `needs` chain (docs staleness doesn't block deploys).

### 6.3 Deploy script

`infra/deploy-bot.sh` (carried over, paths updated to the new repo): `git pull --rebase`, zip exactly the deployables (including `static/` — the old bot's crash-loop lesson: omitting `static/` crashed startup), `az webapp deploy`, poll `/health` canary. Runs on John's Mac (where `az` lives).

---

## 7. Testing

- **Framework:** `pytest`, run as `.venv/bin/python -m pytest -q`. Tests must pass before any commit. CI runs them on every push/PR.
- **Coverage target:** Same bar as the old bot (33 tests, all passing). New tests for: route registration (gate, message, invoke, error), the invoke-based card submit, the strip-both-sides comparison, the `/health` flags, the token endpoint's 503/502 behaviour, the OTel dimension fix (no hardcoded `channel`), the `static/`-missing startup guard.
- **Test for the greeting:** Assert only that a greeting is sent (wording is John's; the test pins that a greeting is sent, then the card, then nothing) — so future wording tweaks don't break CI. (Lesson from the old bot's Nov 1 CI failure.)
- **Local test loop:** `python app.py` on `:3978`. Agents Playground (`agentsplayground -e "http://localhost:3978/api/messages" -c "emulator"`) or the Bot Framework Emulator (4.14.1, renders cards poorly — preview cards in the Adaptive Cards designer or WebChat instead). Once the Teams license is live, iterate via ngrok / Azure Dev Tunnels pointing the Azure Bot endpoint at `localhost:3978`.

---

## 8. Non-goals (explicit — not in v1)

- **Weather.** Dropped by John's decision. Do not re-add it.
- **Persistent memory / Blob storage.** Later milestone. v1 uses `MemoryStorage`.
- **Adaptive Cards with live data in Teams.** Later, after the Teams license + sideload. The gate card is the only card in v1.
- **Copilot channel.** Deferred (John, Oct 25 — "at some point").
- **Microsoft Agent Framework (MAF).** A different SDK (agent reasoning / workflows / Foundry). Not used here. The M365 Agents SDK is the channel/hosting layer for Teams; MAF is a separate concern. Do not conflate them in the code or the README.
- **Agent 365 SDK.** Enterprise registration/identity layer. Not used here.
- **Multi-model orchestration / eval agents.** Ram's "harness" thesis (Astra/Omnara-style orchestration) is interesting future work, not v1. v1 is one agent, one model.
- **Muse Code as the build tool.** The build is done by Muse (this agent) unless John says otherwise. Muse Code is a candidate for future long-horizon work, not a v1 dependency.

---

## 9. Build order / milestones

1. **Spec approved** (this document). John reviews, edits, approves.
2. **Repo created** (John or assistant, after decision #1). Public. `AZURECREDENTIALS` secret added (decision #7).
3. **Scaffold:** repo layout (§4.3), `requirements.txt` (SDK 1.9.0 pinned), `config.py` + Key Vault, `bot_logging.py` (with the dimension fix), `app.py` (routes + `/health`), `agent.py` (routes registered, gate + chat dispatch), `chat.py` + `profile.md`, `cards/gate.json`, `webchat/index.html`, `static/card-bg.jpg`. Tests green.
4. **Tooling:** `infra/deploy-bot.sh`, CI (test/deploy/ponytail), Ponytail sync + AGENTS.md generation, `.python-version`, README.
5. **Deploy to the new web app.** `/health` all five true. Token endpoint 200. `/demo` serves.
6. **End-to-end verification:** gate card renders, wrong answer rejected, right answer passes, chat answers from the profile, `— via <model>` suffix present, logs in App Insights with the corrected dimensions.
7. **Portfolio README** finished. Demo link stable.

---

## 10. Risks & verification items (build-time checklist)

These are the things most likely to bite. Each must be verified during the build, not assumed.

- [ ] **SDK env var names.** The exact env vars 1.9.0 expects for app ID / password / tenant. Read the installed package.
- [ ] **SDK API names.** Every `AgentApplication` method, `RouteRank` value, and error-handler name verified against the installed 1.9.0 package. Docs have drifted before.
- [ ] **Invoke activity for card submit.** The gate's submit path is an invoke route (`adaptiveCard/action`), not a message route. Verify the activity name and payload shape in 1.9.0.
- [ ] **TurnState shape.** How conversation/user state is read and written in 1.9.0 (history storage, gate-passed flag).
- [ ] **Direct Line token endpoint.** The REST call to `directline.botframework.com` works from Azure (it does — the old bot proved it). Local testing from the assistant's VM is blocked by the egress quirk (TLS hangs) — test the token endpoint from Azure or John's Mac, not the VM.
- [ ] **Key Vault role assignment + service principal.** Both must exist before the first deploy (Oct 30 lesson). The SP creation is now an idempotent step in `deploy.sh` (not manual, not in CI). Verify both exist after the first provisioning run.
- [ ] **`AZURECREDENTIALS` in the new repo.** CI deploy cannot run without it (decision #7).
- [ ] **`/health` canary.** All five flags true after the first deploy, before calling it done.
- [ ] **No secrets in the public repo.** Audit before the first push: no `.env`, no keys, no tokens, no client secrets. The repo is public — anyone can read it.
- [ ] **OpenRouter quota.** Check with John before any test call that burns free daily requests (standing rule).

---

## 11. Sources & prior art

- Old bot repo (private): `warni001/teams-bot-framework`, main HEAD `d9b873a` (Nov 8, 2026). The source of every "carried over" item above.
- Old bot goal workspace: `~/workspace/goals/teams-bot-framework/` — `GOAL.md`, `files/bot-roadmap.md`, `files/teams-tenant-plan.md`, `files/Entire_Setup_Checklist.md`.
- M365 Agents SDK for Python: `microsoft/Agents-for-python` on GitHub; PyPI `microsoft-agents-*` (1.9.0, Oct 19, 2026). Quickstart: Microsoft Learn, "Quickstart: Create and test a basic agent."
- M365 Agents SDK vs Bot Framework: the SDK is the successor; same Activity Protocol wire format; the Azure Bot registration, Teams channel, and Key Vault all carry over unchanged.
- Ram Ahluwalia, Lumida Ledger, Nov 1, 2026, "Value Is Moving to the Harness": the harness thesis and the Muse Code recommendation for long-horizon builds. (Context for why the Agents SDK — the current harness — is the right layer to build on, and why the value is in the orchestration, not the model.)
