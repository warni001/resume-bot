"""
app.py — Application wiring + HTTP layer.

Wiring only: config -> logging -> storage -> adapter/authorization ->
agent app -> aiohttp app. All conversation logic lives in agent.py.

Endpoints
---------
POST /api/messages   Agents SDK activities (the messaging endpoint you
                     register on the Azure Bot resource)
GET  /health         Liveness probe + deploy canary (five config flags)
GET  /api/directline/token
                     Mint a short-lived Direct Line token for the demo page.
                     503s when no Direct Line secret is configured.
GET  /demo           The WebChat demo page (no credentials in it — it fetches
                     a token from /api/directline/token on load)
GET  /static/        Static files (gate card background image)

State backend is chosen here only: Blob storage when a connection string
is configured, MemoryStorage otherwise (local dev). agent.py never
depends on the backend.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from aiohttp import ClientSession, web
from aiohttp.web import Request, Response, json_response, middleware
from microsoft_agents.activity import load_configuration_from_env
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.aiohttp import (
    CloudAdapter,
    jwt_authorization_decorator,
    start_agent_process,
)
from microsoft_agents.hosting.core import Authorization, MemoryStorage
from microsoft_agents.storage.blob import BlobStorage, BlobStorageConfig

from agent import create_agent_app
from bot_logging import get_logger, setup_logging
from config import BotConfig

# ----------------------------------------------------------------------
# Bootstrap
# ----------------------------------------------------------------------
CONFIG = BotConfig.from_env()

setup_logging(
    connection_string=CONFIG.appinsights_connection_string or None,
    service_name=CONFIG.service_name,
    level=getattr(logging, CONFIG.log_level, logging.INFO),
    default_dimensions={"environment": CONFIG.environment},
)
log = get_logger(__name__, component="app")

# State: Blob storage persists across restarts and instances; the
# container is created on first use. Without a connection string
# (local dev), MemoryStorage keeps the bot runnable with no Azure.
if CONFIG.blob_storage_connection_string:
    STORAGE = BlobStorage(
        BlobStorageConfig(
            container_name="botstate",
            connection_string=CONFIG.blob_storage_connection_string,
        )
    )
else:
    log.warning(
        "no Blob storage connection string; using MemoryStorage "
        "(state will not persist)"
    )
    STORAGE = MemoryStorage()

# The SDK reads its connection settings from env vars; config.py has
# already populated os.environ from Key Vault where applicable. The
# config dict is UNPACKED into both constructors: installed 1.9.0 reads
# CONNECTIONS/CONNECTIONSMAP/AGENTAPPLICATION from kwargs, and the
# samples' `config=` form is silently ignored (verified against the
# installed package — the samples drift from the release).
AGENTS_SDK_CONFIG = load_configuration_from_env(os.environ)
CONNECTION_MANAGER = MsalConnectionManager(**AGENTS_SDK_CONFIG)
ADAPTER = CloudAdapter(connection_manager=CONNECTION_MANAGER)
AUTHORIZATION = Authorization(STORAGE, CONNECTION_MANAGER, **AGENTS_SDK_CONFIG)

AGENT_APP = create_agent_app(
    STORAGE,
    AUTHORIZATION,
    openrouter_api_key=CONFIG.openrouter_api_key,
    openrouter_model=CONFIG.openrouter_model,
    demo_gate_name=CONFIG.demo_gate_name,
    app_base_url=CONFIG.app_base_url,
)

# Demo page: read once at import; a missing file just 404s /demo.
try:
    _DEMO_PAGE_HTML = (
        Path(__file__).parent / "webchat" / "index.html"
    ).read_text(encoding="utf-8")
except OSError:
    _DEMO_PAGE_HTML = None


# ----------------------------------------------------------------------
# HTTP layer
# ----------------------------------------------------------------------
@middleware
async def error_middleware(request: Request, handler):
    """Outermost HTTP guard: log and 500, never drop the connection."""
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - this IS the top level
        log.error("unhandled http error: %s", exc, exc_info=exc)
        return json_response({"error": "internal server error"}, status=500)


@jwt_authorization_decorator
async def messages(req: Request) -> Response:
    """Channel-service activities. JWT-protected: without the SDK's
    authorization decorator the request carries no claims identity, the
    turn runs anonymous, and outbound sends go out unauthenticated
    (Direct Line 401s them). Only this route is protected; /health,
    /demo, /api/directline/token and /static/ stay open."""
    response = await start_agent_process(req, AGENT_APP, ADAPTER)
    return response if response is not None else Response(status=201)


async def directline_token(req: Request) -> Response:
    """Mint a short-lived Direct Line token for the demo page.

    The secret never leaves the server: the browser gets a token good
    for ~30 minutes, scoped to chatting with this bot only.
    """
    secret = CONFIG.directline_secret
    if not secret:
        return json_response({"error": "direct line not configured"}, status=503)
    try:
        async with ClientSession() as session:
            async with session.post(
                "https://directline.botframework.com/v3/directline/tokens/generate",
                headers={"Authorization": f"Bearer {secret}"},
            ) as resp:
                if resp.status != 200:
                    log.warning(
                        "direct line token request failed: HTTP %s", resp.status
                    )
                    return json_response(
                        {"error": "direct line token request failed"}, status=502
                    )
                data = await resp.json()
    except Exception as exc:  # noqa: BLE001 - network failure to Direct Line
        log.warning("direct line token request failed: %s", exc)
        return json_response({"error": "direct line token request failed"}, status=502)
    # Never assume fields exist in an external API response.
    token = data.get("token") if isinstance(data, dict) else None
    if not token:
        return json_response({"error": "direct line token request failed"}, status=502)
    expires_in = data.get("expires_in") if isinstance(data, dict) else None
    return json_response({"token": token, "expires_in": expires_in or 1800})


async def demo(req: Request) -> Response:
    if _DEMO_PAGE_HTML is None:
        return Response(status=404, text="demo page not found")
    return Response(text=_DEMO_PAGE_HTML, content_type="text/html")


async def health(req: Request) -> Response:
    return json_response(
        {
            "status": "ok",
            "service": CONFIG.service_name,
            "environment": CONFIG.environment,
            "app_id_configured": bool(CONFIG.app_id),
            "azure_logging_configured": bool(
                CONFIG.appinsights_connection_string
            ),
            "key_vault_configured": bool(CONFIG.key_vault_url),
            "directline_configured": bool(CONFIG.directline_secret),
            "openrouter_configured": bool(
                CONFIG.openrouter_api_key and CONFIG.openrouter_model
            ),
        }
    )


def create_app() -> web.Application:
    app = web.Application(middlewares=[error_middleware])
    # The JWT decorator reads the agent auth configuration from here to
    # validate inbound channel-service tokens.
    app["agent_configuration"] = (
        CONNECTION_MANAGER.get_default_connection_configuration()
    )
    app.router.add_post("/api/messages", messages)
    app.router.add_get("/health", health)
    app.router.add_get("/api/directline/token", directline_token)
    app.router.add_get("/demo", demo)
    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.router.add_static("/static/", str(static_dir))
    else:
        # Missing static/ must never crash the app (a deploy without it
        # took the site down, Oct 31 2026) — card images just won't load.
        log.warning("static/ directory missing; /static/ not served")
    return app


APP = create_app()

if __name__ == "__main__":
    try:
        log.info(
            "starting %s on %s:%s (env=%s)",
            CONFIG.service_name,
            CONFIG.host,
            CONFIG.port,
            CONFIG.environment,
        )
        web.run_app(APP, host=CONFIG.host, port=CONFIG.port)
    except Exception as exc:  # noqa: BLE001 - last-ditch startup guard
        log.critical("fatal startup error: %s", exc, exc_info=exc)
        sys.exit(1)
