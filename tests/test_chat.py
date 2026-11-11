import aiohttp
import pytest

import chat
from chat import ChatError, chat_reply
from config import BotConfig


# Model choice is configuration (.env), not code — no model id is
# hardcoded anywhere. A fresh checkout has no model until one is set.
def test_no_model_configured_by_default():
    assert BotConfig().openrouter_model == ""


def test_profile_md_is_wired_into_system_prompt():
    # profile.md ships with the repo; the model must see it on every
    # call, so it ends up in the system prompt at import.
    assert chat._PROFILE, "profile.md should load"
    assert chat._PROFILE in chat.SYSTEM_PROMPT
    assert "John Warnicke" in chat.SYSTEM_PROMPT


def test_system_prompt_has_no_weather_line():
    # Weather was dropped in the rebuild; the prompt must not mention it.
    assert "weather" not in chat.SYSTEM_PROMPT.lower()


class _FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def json(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """Stands in for aiohttp.ClientSession; records the request."""

    def __init__(self, response, calls):
        self._response = response
        self._calls = calls

    def post(self, url, headers=None, json=None):
        self._calls.append({"url": url, "headers": headers, "json": json})
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _patch_session(monkeypatch, status, body):
    calls = []
    monkeypatch.setattr(
        aiohttp,
        "ClientSession",
        lambda *a, **k: _FakeSession(_FakeResponse(status, body), calls),
    )
    return calls


async def test_chat_reply_success_posts_openai_compatible_payload(monkeypatch):
    calls = _patch_session(
        monkeypatch,
        200,
        {
            "model": "cohere/north-mini-code:free",
            "choices": [{"message": {"role": "assistant", "content": "  Hi there. "}}],
        },
    )
    reply, used_model = await chat_reply(
        "sk-or-test",
        "openrouter/free",
        [{"role": "user", "content": "earlier"}],
        "hello",
    )
    assert reply == "Hi there."
    assert used_model == "cohere/north-mini-code:free"
    assert len(calls) == 1
    payload = calls[0]["json"]
    assert calls[0]["url"] == chat.CHAT_URL
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-or-test"
    assert payload["model"] == "openrouter/free"
    assert "max_tokens" not in payload
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1] == {"role": "user", "content": "earlier"}
    assert payload["messages"][2] == {"role": "user", "content": "hello"}


async def test_chat_reply_falls_back_to_requested_model(monkeypatch):
    _patch_session(
        monkeypatch,
        200,
        {"choices": [{"message": {"role": "assistant", "content": "hi"}}]},
    )
    _, used_model = await chat_reply(
        "sk-or-test", "openrouter/free", [], "hello"
    )
    assert used_model == "openrouter/free"


async def test_chat_reply_non_200_raises_chat_error(monkeypatch):
    _patch_session(monkeypatch, 429, {"error": {"message": "rate limited"}})
    with pytest.raises(ChatError, match="429.*rate limited"):
        await chat_reply("sk-or-test", "openrouter/free", [], "hello")


async def test_chat_reply_malformed_response_raises_chat_error(monkeypatch):
    _patch_session(monkeypatch, 200, {"choices": []})
    with pytest.raises(ChatError):
        await chat_reply("sk-or-test", "openrouter/free", [], "hello")


async def test_chat_reply_null_content_raises_chat_error(monkeypatch):
    # Reasoning models can spend the tokens on reasoning and return a
    # null content. That must degrade to the error message, not crash.
    _patch_session(
        monkeypatch,
        200,
        {"choices": [{"message": {"role": "assistant", "content": None}}]},
    )
    with pytest.raises(ChatError, match="no text content"):
        await chat_reply("sk-or-test", "openrouter/free", [], "hello")


async def test_chat_reply_network_error_raises_chat_error(monkeypatch):
    class _FailingSession(_FakeSession):
        def post(self, url, headers=None, json=None):
            raise aiohttp.ClientError("boom")

    monkeypatch.setattr(
        aiohttp, "ClientSession", lambda *a, **k: _FailingSession(None, [])
    )
    with pytest.raises(ChatError, match="request failed"):
        await chat_reply("sk-or-test", "openrouter/free", [], "hello")
