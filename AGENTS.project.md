# Project: Resume Bot — read this section first

Resume chatbot on the Microsoft 365 Agents SDK for Python (1.9.0), Python 3.14, Azure free tier. The Ponytail rules below govern coding style; this section governs this repo. Spec: `docs/resume-bot-agents-sdk-spec.md`.

## Pattern rule (standing)

Check what everyone else does first. Follow the established convention. Use good patterns, avoid anti-patterns. Only create a new pattern when the convention fails.

## Commands

- Setup: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- Test: `.venv/bin/python -m pytest -q` (must pass before any commit)
- Run: `.venv/bin/python app.py` → `POST /api/messages`, `GET /health` on :3978
- CI: `.github/workflows/ci.yml` runs compileall + pytest on push/PR to `main`. Never merge on red.
- Ponytail: `ponytail.md` is the exact upstream copy. `scripts/sync-ponytail.sh` updates it and rebuilds this file. Do not edit `AGENTS.md` directly.

## Boundaries

- Never name a module `logging.py` — it shadows the stdlib. Logging lives in `bot_logging.py`; dimensions go through `custom_dimensions`.
- Secrets only via environment or Key Vault (`config.py`). New bot-specific vault; secret names map to SDK env vars by convention (dashes replace underscores). Never commit a `.env`. Never edit `.env` directly — hand John the exact line to add.
- Config lives in config, not code. No model IDs, secrets, or environment-specific values hardcoded. Model ID only in `OPENROUTER_MODEL`.
- State: Blob storage. Identity key is the gate email (WebChat) or `from.id` (Teams). History persists, no cap, conversation scope. `agent.py` never depends on the backend; state saves in the turn.
- The agent is an `AgentApplication[TurnState]` instance. Behaviour is registered as routes, not overridden methods. Catch-all message handler uses `rank=RouteRank.LAST`. The error route (`@AGENT_APP.error`) must never throw.
- Gate card uses `Action.Submit` — the submit arrives as a message activity carrying the inputs in its `value` payload, and the gate check runs in the message handler. (WebChat 4.19.2 drops `Action.Execute`.) Strip both sides and `.casefold()` both when comparing the submitted answer to `DEMO_GATE_NAME`.
- SDK API names drift. Verify every name against the installed 1.9.0 package before using it. Never copy a name from the docs.
- Never assume a field exists in an external API response. Guard nullable/missing fields before use.
- Log-loop guard: `setup_logging()` quiets the `azure` logger to WARNING. No hardcoded `channel` dimension — filter traces on `channel_id` only.
- OpenRouter quota: check with John before any test call that burns his free daily requests until he has paid credits.
- Git: commits and pushes are 1:1, after tests validate. Failed tests don't push. Never commit `.venv/` or vendored dependencies — source + manifests only.
- Parallel agents each get their own `git worktree` + branch with an explicitly owned file surface. No two agents share a checkout. Every agent working in this repo reads this file before writing code.
- Card design: preview in the Adaptive Cards designer or WebChat, never the Emulator. Card design is John's job.
- No weather feature. Do not re-add it.
- Do not conflate with the Microsoft Agent Framework (MAF) or the Agent 365 SDK. Both are non-goals.
