import json
from unittest.mock import AsyncMock, MagicMock, patch

import app
from app import create_app, demo, directline_token, health, speech_token


def fake_session_with(status, payload=None, text=None):
    """An aiohttp ClientSession whose post() yields a fake response.

    JSON bodies via `payload`, plain-text bodies (the STS token
    endpoint) via `text`.
    """
    fake_resp = AsyncMock()
    fake_resp.status = status
    fake_resp.json = AsyncMock(return_value=payload)
    fake_resp.text = AsyncMock(return_value=text)
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


async def test_speech_token_unconfigured_without_key_503():
    with (
        patch.object(app.CONFIG, "speech_key", ""),
        patch.object(app.CONFIG, "speech_region", "westus3"),
    ):
        resp = await speech_token(None)
    assert resp.status == 503


async def test_speech_token_unconfigured_without_region_503():
    with (
        patch.object(app.CONFIG, "speech_key", "key"),
        patch.object(app.CONFIG, "speech_region", ""),
    ):
        resp = await speech_token(None)
    assert resp.status == 503


async def test_speech_token_success():
    session = fake_session_with(200, text="tok")
    with (
        patch.object(app.CONFIG, "speech_key", "speechkey123"),
        patch.object(app.CONFIG, "speech_region", "westus3"),
        patch("app.ClientSession", return_value=session),
    ):
        resp = await speech_token(None)
    assert resp.status == 200
    body = json.loads(resp.text)
    assert body["token"] == "tok"
    assert body["region"] == "westus3"
    assert body["expires_in"] == 600
    # The key is sent to STS, never back to the caller.
    assert "speechkey123" not in resp.text
    # The STS contract: region-scoped issueToken URL, key header.
    url = session.post.call_args.args[0]
    assert (
        url
        == "https://westus3.api.cognitive.microsoft.com/sts/v1.0/issueToken"
    )
    headers = session.post.call_args.kwargs["headers"]
    assert headers == {"Ocp-Apim-Subscription-Key": "speechkey123"}


async def test_speech_token_upstream_failure_502():
    session = fake_session_with(401, text="")
    with (
        patch.object(app.CONFIG, "speech_key", "key"),
        patch.object(app.CONFIG, "speech_region", "westus3"),
        patch("app.ClientSession", return_value=session),
    ):
        resp = await speech_token(None)
    assert resp.status == 502


async def test_speech_token_empty_token_502():
    session = fake_session_with(200, text="  \n")
    with (
        patch.object(app.CONFIG, "speech_key", "key"),
        patch.object(app.CONFIG, "speech_region", "westus3"),
        patch("app.ClientSession", return_value=session),
    ):
        resp = await speech_token(None)
    assert resp.status == 502


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
    assert "/api/speech/token" in paths
    assert "/demo" in paths
    # static/ ships with the repo, so /static/ is served.
    assert "/static" in paths


def test_create_app_sets_agent_configuration():
    # The JWT decorator reads this to validate inbound tokens; without
    # it, /api/messages 500s on every channel-service call.
    application = create_app()
    assert application["agent_configuration"] is (
        app.CONNECTION_MANAGER.get_default_connection_configuration()
    )


async def test_messages_without_auth_header_401():
    # No Authorization header and anonymous auth off: the JWT
    # decorator rejects before the turn runs. (A stub request is
    # enough — the decorator only touches .headers and .app.)
    from types import SimpleNamespace

    from app import messages

    stub = SimpleNamespace(
        headers={},
        app={
            "agent_configuration": (
                app.CONNECTION_MANAGER.get_default_connection_configuration()
            )
        },
    )
    resp = await messages(stub)
    assert resp.status == 401
