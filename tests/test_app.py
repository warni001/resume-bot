import json
from unittest.mock import AsyncMock, MagicMock, patch

import app
from app import create_app, demo, directline_token, health


def fake_session_with(status, payload):
    """An aiohttp ClientSession whose post() yields a fake response."""
    fake_resp = AsyncMock()
    fake_resp.status = status
    fake_resp.json = AsyncMock(return_value=payload)
    response_cm = MagicMock()
    response_cm.__aenter__ = AsyncMock(return_value=fake_resp)
    response_cm.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.post = MagicMock(return_value=response_cm)
    return session


async def test_directline_token_unconfigured_503():
    with patch.object(app.CONFIG, "directline_secret", ""):
        resp = await directline_token(None)
    assert resp.status == 503


async def test_directline_token_success():
    session = fake_session_with(200, {"token": "tok", "expires_in": 1800})
    with (
        patch.object(app.CONFIG, "directline_secret", "secret"),
        patch("app.ClientSession", return_value=session),
    ):
        resp = await directline_token(None)
    assert resp.status == 200
    body = json.loads(resp.text)
    assert body["token"] == "tok"
    assert body["expires_in"] == 1800
    # The secret is sent to Direct Line, never back to the caller.
    assert "secret" not in resp.text


async def test_directline_token_upstream_failure_502():
    session = fake_session_with(401, {})
    with (
        patch.object(app.CONFIG, "directline_secret", "secret"),
        patch("app.ClientSession", return_value=session),
    ):
        resp = await directline_token(None)
    assert resp.status == 502


async def test_directline_token_missing_token_field_502():
    session = fake_session_with(200, {})
    with (
        patch.object(app.CONFIG, "directline_secret", "secret"),
        patch("app.ClientSession", return_value=session),
    ):
        resp = await directline_token(None)
    assert resp.status == 502


async def test_directline_token_defaults_expires_in():
    session = fake_session_with(200, {"token": "tok"})
    with (
        patch.object(app.CONFIG, "directline_secret", "secret"),
        patch("app.ClientSession", return_value=session),
    ):
        resp = await directline_token(None)
    assert json.loads(resp.text)["expires_in"] == 1800


async def test_health_reports_all_five_flags():
    with (
        patch.object(app.CONFIG, "app_id", "id"),
        patch.object(
            app.CONFIG,
            "appinsights_connection_string",
            "InstrumentationKey=abc",
        ),
        patch.object(app.CONFIG, "key_vault_url", "https://vault/"),
        patch.object(app.CONFIG, "directline_secret", "secret"),
        patch.object(app.CONFIG, "openrouter_api_key", "key"),
        patch.object(app.CONFIG, "openrouter_model", "model"),
    ):
        resp = await health(None)
    body = json.loads(resp.text)
    assert body["status"] == "ok"
    assert body["app_id_configured"] is True
    assert body["azure_logging_configured"] is True
    assert body["key_vault_configured"] is True
    assert body["directline_configured"] is True
    assert body["openrouter_configured"] is True


async def test_health_flags_false_when_unconfigured():
    with (
        patch.object(app.CONFIG, "app_id", ""),
        patch.object(app.CONFIG, "appinsights_connection_string", ""),
        patch.object(app.CONFIG, "key_vault_url", ""),
        patch.object(app.CONFIG, "directline_secret", ""),
        patch.object(app.CONFIG, "openrouter_api_key", ""),
        patch.object(app.CONFIG, "openrouter_model", ""),
    ):
        resp = await health(None)
    body = json.loads(resp.text)
    assert body["app_id_configured"] is False
    assert body["azure_logging_configured"] is False
    assert body["key_vault_configured"] is False
    assert body["directline_configured"] is False
    assert body["openrouter_configured"] is False


async def test_health_openrouter_needs_key_and_model():
    with (
        patch.object(app.CONFIG, "openrouter_api_key", "key"),
        patch.object(app.CONFIG, "openrouter_model", ""),
    ):
        resp = await health(None)
    assert json.loads(resp.text)["openrouter_configured"] is False


async def test_demo_serves_page():
    with patch.object(app, "_DEMO_PAGE_HTML", "<html>demo</html>"):
        resp = await demo(None)
    assert resp.status == 200
    assert resp.text == "<html>demo</html>"


async def test_demo_missing_page_404():
    with patch.object(app, "_DEMO_PAGE_HTML", None):
        resp = await demo(None)
    assert resp.status == 404


def test_create_app_registers_routes():
    application = create_app()
    paths = {r.resource.canonical for r in application.router.routes()}
    assert "/api/messages" in paths
    assert "/health" in paths
    assert "/api/directline/token" in paths
    assert "/demo" in paths
    # static/ ships with the repo, so /static/ is served.
    assert "/static" in paths
