"""Tests for agent.py: gate logic, routes, message dispatch, error route.

Behaviour is driven through AgentApplication.on_turn with a capturing
fake adapter — the same shape as the old bot's TestAdapter tests, minus
the Bot Framework SDK.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from microsoft_agents.activity import (
    Activity,
    ChannelAccount,
    ConversationAccount,
    ResourceResponse,
)
from microsoft_agents.hosting.core import MemoryStorage, TurnContext
from microsoft_agents.hosting.core.app.oauth import Authorization

import agent as agent_module
from agent import (
    CHAT_ERROR_MESSAGE,
    GATE_INPUT_ID,
    create_agent_app,
    gate_answer_matches,
    gate_card,
)
from chat import ChatError

GATE_NAME = "John Warnicke"


class CapturingAdapter:
    """Stands in for CloudAdapter; records every sent activity."""

    def __init__(self):
        self.sent = []

    async def send_activities(self, context, activities):
        self.sent.extend(activities)
        return [ResourceResponse(id=str(i)) for i, _ in enumerate(activities)]

    async def update_activity(self, context, activity):
        return ResourceResponse(id=activity.id or "0")

    async def delete_activity(self, context, reference):
        return None


def make_app(**kwargs):
    storage = MemoryStorage()
    auth = Authorization(storage, MagicMock())
    kwargs.setdefault("demo_gate_name", GATE_NAME)
    return create_agent_app(storage, auth, **kwargs)


def _activity(**kwargs):
    base = {
        "channel_id": "webchat",
        "service_url": "https://test.directline.botframework.com",
        "from_property": ChannelAccount(id="u1", name="Tester"),
        "conversation": ConversationAccount(id="c1"),
        "recipient": ChannelAccount(id="bot"),
    }
    base.update(kwargs)
    return Activity(**base)


def _turn(adapter, activity):
    return TurnContext(adapter, activity)


def _texts(adapter):
    return [(a.text or "") for a in adapter.sent if a.type == "message"]


def _attachments(adapter):
    out = []
    for a in adapter.sent:
        out.extend(getattr(a, "attachments", None) or [])
    return out


# ----------------------------------------------------------------------
# Gate comparison (spec decision #5: strip BOTH sides)
# ----------------------------------------------------------------------

def test_gate_answer_matches_exact():
    assert gate_answer_matches("John Warnicke", GATE_NAME)


def test_gate_answer_matches_case_insensitive():
    assert gate_answer_matches("john warnicke", GATE_NAME)
    assert gate_answer_matches("JOHN WARNICKE", GATE_NAME)


def test_gate_answer_matches_strips_submitted_side():
    assert gate_answer_matches("  John Warnicke  ", GATE_NAME)


def test_gate_answer_matches_strips_configured_side():
    # The old bug: a trailing space in DEMO_GATE_NAME locked the gate.
    assert gate_answer_matches("John Warnicke", "  John Warnicke  ")


def test_gate_answer_mismatch():
    assert not gate_answer_matches("John Wick", GATE_NAME)
    assert not gate_answer_matches("", GATE_NAME)


# ----------------------------------------------------------------------
# Gate card loading
# ----------------------------------------------------------------------

def test_gate_card_loads_with_developer_name_input():
    card = gate_card()
    body = card["body"]
    assert any(
        el.get("type") == "Input.Text" and el.get("id") == GATE_INPUT_ID
        for el in body
    )


def test_gate_card_substitutes_app_base_url():
    card = gate_card("https://example.azurewebsites.net")
    url = card["backgroundImage"]["url"]
    assert url.startswith("https://example.azurewebsites.net/static/")
    assert "{APP_BASE_URL}" not in url


def test_gate_card_returns_fresh_copy_each_call():
    first = gate_card()
    first["body"].append({"type": "TextBlock", "text": "mutated"})
    assert all(
        el.get("text") != "mutated" for el in gate_card()["body"]
    )


def test_gate_card_raises_without_developer_name_input(monkeypatch):
    monkeypatch.setattr(
        agent_module, "_GATE_CARD", {"body": [{"type": "TextBlock", "text": "x"}]}
    )
    with pytest.raises(RuntimeError, match=GATE_INPUT_ID):
        gate_card()


# ----------------------------------------------------------------------
# Route registration
# ----------------------------------------------------------------------

def test_routes_registered():
    app = make_app()
    # message catch-all (gate submits + chat) + membersAdded.
    assert len(list(app._route_list)) >= 2


# ----------------------------------------------------------------------
# Members added: greeting + gate card
# ----------------------------------------------------------------------

async def test_members_added_sends_greeting_and_gate_card():
    adapter = CapturingAdapter()
    app = make_app()
    activity = _activity(
        type="conversationUpdate",
        members_added=[ChannelAccount(id="u1", name="Tester")],
    )
    await app.on_turn(_turn(adapter, activity))
    texts = _texts(adapter)
    # A greeting is sent; the test pins that, not John's exact wording.
    assert any("Welcome" in t for t in texts)
    assert _attachments(adapter), "gate card should be sent"


async def test_members_added_skips_gate_when_gate_off():
    adapter = CapturingAdapter()
    app = make_app(demo_gate_name="")
    activity = _activity(
        type="conversationUpdate",
        members_added=[ChannelAccount(id="u1", name="Tester")],
    )
    await app.on_turn(_turn(adapter, activity))
    assert any("Welcome" in t for t in _texts(adapter))
    assert not _attachments(adapter)


async def test_members_added_skips_bot_itself():
    adapter = CapturingAdapter()
    app = make_app()
    activity = _activity(
        type="conversationUpdate",
        members_added=[ChannelAccount(id="bot", name="ResumeBot")],
    )
    await app.on_turn(_turn(adapter, activity))
    assert not adapter.sent


# ----------------------------------------------------------------------
# Gate submit (Action.Submit message carrying the inputs in value)
# ----------------------------------------------------------------------

def _gate_submit_activity(answer, email="tester@example.com"):
    return _activity(
        type="message",
        text="",
        value={GATE_INPUT_ID: answer, "email": email},
    )


async def test_gate_submit_correct_answer_passes_and_confirms():
    adapter = CapturingAdapter()
    app = make_app()
    await app.on_turn(_turn(adapter, _gate_submit_activity("John Warnicke")))
    assert any("You're in" in t for t in _texts(adapter))


async def test_gate_submit_forgiving_comparison():
    adapter = CapturingAdapter()
    app = make_app()
    await app.on_turn(
        _turn(adapter, _gate_submit_activity("  john warnicke "))
    )
    assert any("You're in" in t for t in _texts(adapter))


async def test_gate_submit_wrong_answer_reprompts_without_echo():
    adapter = CapturingAdapter()
    app = make_app()
    await app.on_turn(_turn(adapter, _gate_submit_activity("John Wick")))
    texts = _texts(adapter)
    assert any("Not quite" in t for t in texts)
    assert not any("John Wick" in t for t in texts)


async def test_gate_submit_wrong_answer_keeps_gate_closed(monkeypatch):
    adapter = CapturingAdapter()
    fake_chat = AsyncMock(return_value=("reply", "model"))
    monkeypatch.setattr(agent_module, "openrouter_chat", fake_chat)
    app = make_app(openrouter_api_key="k", openrouter_model="m")
    await app.on_turn(_turn(adapter, _gate_submit_activity("John Wick")))
    await app.on_turn(_turn(adapter, _activity(type="message", text="hello")))
    fake_chat.assert_not_called()
    assert _attachments(adapter), "gate card re-sent while unauthorized"


# ----------------------------------------------------------------------
# Message dispatch + chat
# ----------------------------------------------------------------------

async def test_message_while_unauthorized_sends_gate_card(monkeypatch):
    adapter = CapturingAdapter()
    fake_chat = AsyncMock(return_value=("reply", "model"))
    monkeypatch.setattr(agent_module, "openrouter_chat", fake_chat)
    app = make_app(openrouter_api_key="k", openrouter_model="m")
    await app.on_turn(_turn(adapter, _activity(type="message", text="hello")))
    fake_chat.assert_not_called()
    assert _attachments(adapter)


async def test_chat_reply_sent_with_model_suffix_after_gate(monkeypatch):
    adapter = CapturingAdapter()

    async def fake_chat(api_key, model, history, user_text):
        return "Hello back.", "test-model:free"

    monkeypatch.setattr(agent_module, "openrouter_chat", fake_chat)
    app = make_app(openrouter_api_key="k", openrouter_model="test-model:free")
    await app.on_turn(_turn(adapter, _gate_submit_activity(GATE_NAME)))
    adapter.sent.clear()
    await app.on_turn(_turn(adapter, _activity(type="message", text="hi")))
    assert _texts(adapter) == ["Hello back.\n\n— via test-model:free"]


async def test_chat_history_persists_across_turns_without_cap(monkeypatch):
    seen_histories = []

    async def fake_chat(api_key, model, history, user_text):
        seen_histories.append(list(history))
        return f"echo: {user_text}", "m"

    monkeypatch.setattr(agent_module, "openrouter_chat", fake_chat)
    app = make_app(openrouter_api_key="k", openrouter_model="m")
    adapter = CapturingAdapter()
    await app.on_turn(_turn(adapter, _gate_submit_activity(GATE_NAME)))
    # Five exchanges: history must hold all ten entries (no 8-entry cap).
    for i in range(5):
        await app.on_turn(_turn(adapter, _activity(type="message", text=f"q{i}")))
    assert len(seen_histories[4]) == 8
    assert seen_histories[1] == [
        {"role": "user", "content": "q0"},
        {"role": "assistant", "content": "echo: q0"},
    ]
    # History stores the bare reply — the — via suffix never pollutes it.
    assert all("— via" not in e["content"] for e in seen_histories[4])


async def test_chat_failure_falls_back_to_error_message(monkeypatch):
    async def failing_chat(api_key, model, history, user_text):
        raise ChatError("openrouter HTTP 429: rate limited")

    monkeypatch.setattr(agent_module, "openrouter_chat", failing_chat)
    adapter = CapturingAdapter()
    app = make_app(openrouter_api_key="k", openrouter_model="m")
    await app.on_turn(_turn(adapter, _gate_submit_activity(GATE_NAME)))
    adapter.sent.clear()
    await app.on_turn(_turn(adapter, _activity(type="message", text="hi")))
    assert _texts(adapter) == [CHAT_ERROR_MESSAGE]


async def test_no_api_key_keeps_error_message(monkeypatch):
    fake_chat = AsyncMock(return_value=("reply", "m"))
    monkeypatch.setattr(agent_module, "openrouter_chat", fake_chat)
    adapter = CapturingAdapter()
    app = make_app(demo_gate_name="", openrouter_api_key="", openrouter_model="m")
    await app.on_turn(_turn(adapter, _activity(type="message", text="hi")))
    fake_chat.assert_not_called()
    assert _texts(adapter) == [CHAT_ERROR_MESSAGE]


async def test_api_key_without_model_keeps_error_message(monkeypatch):
    fake_chat = AsyncMock(return_value=("reply", "m"))
    monkeypatch.setattr(agent_module, "openrouter_chat", fake_chat)
    adapter = CapturingAdapter()
    app = make_app(demo_gate_name="", openrouter_api_key="k", openrouter_model="")
    await app.on_turn(_turn(adapter, _activity(type="message", text="hi")))
    fake_chat.assert_not_called()
    assert _texts(adapter) == [CHAT_ERROR_MESSAGE]


async def test_value_without_text_stays_silent_after_unlock(monkeypatch):
    fake_chat = AsyncMock(return_value=("reply", "m"))
    monkeypatch.setattr(agent_module, "openrouter_chat", fake_chat)
    adapter = CapturingAdapter()
    app = make_app(openrouter_api_key="k", openrouter_model="m")
    await app.on_turn(_turn(adapter, _gate_submit_activity(GATE_NAME)))
    adapter.sent.clear()
    await app.on_turn(
        _turn(adapter, _activity(type="message", text="", value={"x": 1}))
    )
    assert not adapter.sent
    fake_chat.assert_not_called()


# ----------------------------------------------------------------------
# Error route
# ----------------------------------------------------------------------

async def test_error_route_sends_apology_and_never_throws(monkeypatch):
    # A missing gate card file makes the message handler raise; the
    # error route must catch it, apologize, and not raise itself.
    monkeypatch.setattr(agent_module, "_GATE_CARD", None)
    adapter = CapturingAdapter()
    app = make_app()
    await app.on_turn(_turn(adapter, _activity(type="message", text="hi")))
    assert any("something went wrong" in t for t in _texts(adapter))
