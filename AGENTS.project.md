# Project: Resume Bot — read this section first

Resume chatbot on the Microsoft 365 Agents SDK for Python (1.9.0), Python
3.14, targeting Azure free tier (Azure Bot F0, App Service F1, Key Vault,
Blob storage state, Application Insights via OpenTelemetry). Public repo —
the portfolio piece. The Ponytail rules below govern coding style; this
section governs this repo. The spec is `docs/resume-bot-agents-sdk-spec.md`.

## Pattern rule (standing)

When proposing a solution, think (and check where appropriate) what everyone
else does first — follow the established convention / sensible default rather
than inventing a novel approach. Use good patterns, avoid anti-patterns, and
be aware of the patterns in common use. Only create a new pattern for a
*reason* — and "feels nicer" can be a reason, but only after doing the work
of learning the prevailing patterns. The failure is inventing from ignorance
rather than from knowledge. (John, Nov 9 — from the Key Vault
underscore-to-hyphen naming discussion.)

## Commands

- Setup: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- Test: `.venv/bin/python -m pytest -q` (must pass before any commit)
- Run: `.venv/bin/python app.py` → `POST /api/messages`, `GET /health` on :3978
- CI: `.github/workflows/ci.yml` runs compileall + pytest on push/PR to `main`. Never merge on red.
- Ponytail: `ponytail.md` is the exact upstream copy (DietrichGebert/ponytail); `scripts/sync-ponytail.sh` updates it, rebuilds this file, and commits + pushes the result. CI auto-fixes it on push to main and only checks on PRs. Do not edit `AGENTS.md` directly.

## Boundaries

- Never name a module `logging.py` — it shadows the stdlib and breaks the
  SDKs. Logging lives in `bot_logging.py`; dimensions go through
  `custom_dimensions` (deep-merge adapter — stdlib LoggerAdapter drops them).
- Secrets only via environment or Key Vault (`config.py`). New bot-specific
  vault; secret names map to the Agents SDK env var names by convention
  (dashes replace underscores): `CONNECTIONS-SERVICE-CONNECTION-SETTINGS-CLIENTID`,
  `...-CLIENTSECRET`, `...-TENANTID`, plus `ApplicationInsightsConnectionString`,
  `OpenRouterApiKey`, `DirectLineSecret`, and the Blob storage connection
  string. Never commit a `.env`. Never edit `.env` directly — hand John the
  exact line to add.
- Config lives in config, not code. No model IDs, secrets, or
  environment-specific values hardcoded. Model ID only in `OPENROUTER_MODEL`.
- State: Blob storage (`microsoft-agents-storage-blob`). Identity key is the
  gate-collected email (WebChat/DirectLine) or `from.id` (Teams/Entra ID).
  History persists, no artificial cap, conversation scope. `agent.py` must
  never depend on the backend; state saves in the turn.
- The agent is an `AgentApplication[TurnState]` instance. Behaviour is
  registered as routes, not overridden methods. The catch-all message handler
  is registered with `rank=RouteRank.LAST`. The error route
  (`@AGENT_APP.error`) must never throw — guard every step in it.
- Gate card uses `Action.Execute` — the submit arrives as an invoke activity
  (`adaptiveCard/action`), not a message. Strip both sides and `.casefold()`
  both when comparing the submitted answer to `DEMO_GATE_NAME`.
- SDK API names drift between releases. Verify every SDK name against the
  installed 1.9.0 package (import it, inspect it) before using it. Never copy
  a name from the docs or a blog without checking it in the installed package.
- Never assume a field exists in an external API response: guard
  nullable/missing fields before use (OpenRouter returned null content
  and `.strip()` on it crashed a turn, Oct 25 2026 — assume makes an
  ass of u and me, especially in Python).
- Log-loop guard: `setup_logging()` quiets the `azure` logger to WARNING.
  No hardcoded `channel` dimension — filter traces on `channel_id` only.
- OpenRouter quota: check with John before any test call that burns his free
  daily requests (50/day, resets midnight UTC = 7pm EST) until he has paid
  credits going.
- Git: commits and pushes are 1:1 (one commit, one push, immediately).
  Never commit `.venv/` or any vendored dependency — source + manifests only.
- Parallel agents on this repo each get their own `git worktree` + branch
  with an explicitly owned file surface. No two agents share a checkout.
  Every agent working in this repo reads this file before writing code.
- Card design iteration: preview card JSON in the Adaptive Cards
  designer (adaptivecards.io/designer) or WebChat, never the Bot Framework
  Emulator (4.14.1 renders cards poorly). Card design is John's job.
- No weather feature. Dropped by John's decision — do not re-add it.
- Do not conflate with the Microsoft Agent Framework (MAF) or the Agent 365
  SDK. Both are non-goals; the M365 Agents SDK is the channel/hosting layer.
