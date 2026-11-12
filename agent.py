"""
agent.py — The resume bot: AgentApplication, routes, gate, chat dispatch.

No `TeamsActivityHandler` subclass: behaviour is registered as routes on
an `AgentApplication[TurnState]` instance, built by `create_agent_app`.
The storage backend is chosen by the caller (app.py) and passed in —
this module never depends on the backend. State saves at the end of the
turn (the framework handles it); handlers only read/write TurnState.

Routes, in evaluation order (first match wins):
  conversation_update("membersAdded") — welcome + gate card.
  adaptive_card.action_execute("gate_submit") — gate card submits.
  activity("message", rank=RouteRank.LAST) — the catch-all chat path.
  error — the top-level error handler; never throws.
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any

from microsoft_agents.activity import (
    AdaptiveCardInvokeResponse,
    Attachment,
    ContentTypes,
)
from microsoft_agents.hosting.core import (
    AgentApplication,
    MessageFactory,
    RouteRank,
    TurnContext,
    TurnState,
)

from bot_logging import get_logger
from chat import ChatError
from chat import chat_reply as openrouter_chat

log = get_logger(__name__, component="agent")

CHAT_ERROR_MESSAGE = "I couldn't get a reply just now — try again in a bit."

# Field id of the answer input on the gate card. This is the contract
# between cards/gate.json and the gate check: the JSON must contain an
# Input.Text with this id, or the gate cannot read the submitted answer.
GATE_INPUT_ID = "developerName"

# Field id of the email input on the gate card. The email is the user
# identity key in WebChat/DirectLine (no stable user id there); in Teams
# the identity key is from.id. The id is the card designer's call.
GATE_EMAIL_ID = "email"

# Verb on the gate card's Action.Execute, routing the invoke to the
# gate handler below.
GATE_VERB = "gate_submit"

# Greeting sent on joining and on passing the gate (John's wording,
# carried over from the old bot).
GREETING = (
    "Welcome to ResumeBot!<br>"
    "I can tell you about my creator's professional experience,<br> "
    "as well as converse on any other topic.<br><br> "
    "I currently operate in webchat and Microsoft Teams."
)

# Presentation lives in cards/gate.json, not in code: the card is static,
# so there is nothing for code to generate. Loaded once at import; a
# missing or broken file only surfaces when the gate is actually used,
# so a bot running with the gate off is unaffected.
try:
    _GATE_CARD = json.loads(
        (Path(__file__).parent / "cards" / "gate.json").read_text(
            encoding="utf-8"
        )
    )
except (OSError, ValueError):
    _GATE_CARD = None


def gate_answer_matches(answer: str, gate_name: str) -> bool:
    """Forgiving gate comparison: strip BOTH sides, casefold both.

    The old bot stripped only the submitted answer — a trailing space in
    the configured name would lock the gate permanently. Both sides
    strip here.
    """
    return answer.strip().casefold() == gate_name.strip().casefold()


def gate_card(app_base_url: str = "") -> dict:
    """Challenge card for the public demo gate, from cards/gate.json.

    Returns a fresh copy each call so no sender can mutate the shared
    card. The card never carries the expected answer — the bot checks
    the submitted value server-side against config.

    Any {APP_BASE_URL} placeholder in string values (e.g. a hosted
    backgroundImage URL) is replaced with app_base_url, so one card
    JSON works in every environment.
    """
    if _GATE_CARD is None:
        raise RuntimeError(
            "gate card file missing or unreadable: cards/gate.json"
        )
    card = copy.deepcopy(_GATE_CARD)
    if app_base_url:
        card = _substitute_base_url(card, app_base_url)
    body = card.get("body") if isinstance(card, dict) else None
    if not (
        isinstance(body, list)
        and any(
            isinstance(el, dict)
            and el.get("type") == "Input.Text"
            and el.get("id") == GATE_INPUT_ID
            for el in body
        )
    ):
        raise RuntimeError(
            f"gate card has no Input.Text with id {GATE_INPUT_ID!r}: "
            "cards/gate.json"
        )
    return card


def _substitute_base_url(obj, base_url: str):
    """Replace {APP_BASE_URL} in every string in the card JSON."""
    if isinstance(obj, str):
        return obj.replace("{APP_BASE_URL}", base_url)
    if isinstance(obj, dict):
        return {k: _substitute_base_url(v, base_url) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute_base_url(v, base_url) for v in obj]
    return obj


def _turn_dimensions(context: TurnContext) -> dict[str, Any]:
    activity = context.activity
    tenant_id = ""
    channel_data = getattr(activity, "channel_data", None)
    if isinstance(channel_data, dict):
        tenant = channel_data.get("tenant") or {}
        tenant_id = tenant.get("id", "") if isinstance(tenant, dict) else ""
    from_property = getattr(activity, "from_property", None)
    conversation = getattr(activity, "conversation", None)
    return {
        "activity_type": getattr(activity, "type", "") or "",
        "channel_id": getattr(activity, "channel_id", "") or "",
        "conversation_id": getattr(conversation, "id", "") or "",
        "user_id": getattr(from_property, "id", "") or "",
        "tenant_id": tenant_id,
        "locale": getattr(activity, "locale", "") or "",
    }


def create_agent_app(
    storage,
    authorization,
    *,
    openrouter_api_key: str = "",
    openrouter_model: str = "",
    demo_gate_name: str = "",
    app_base_url: str = "",
) -> AgentApplication[TurnState]:
    """Build the AgentApplication with all routes registered.

    `storage` is the state backend (chosen by the caller); `authorization`
    is the SDK Authorization manager. Empty `demo_gate_name` disables the
    gate entirely. `demo_gate_name` is stripped so a whitespace-only value
    can't make empty answers pass; the comparison strips both sides.
    """
    gate_name = demo_gate_name.strip()
    base_url = app_base_url.rstrip("/")

    app: AgentApplication[TurnState] = AgentApplication[TurnState](
        storage=storage,
        authorization=authorization,
    )

    def _gate_attachment() -> Attachment:
        return Attachment(
            content_type="application/vnd.microsoft.card.adaptive",
            content=gate_card(base_url),
        )

    @app.conversation_update("membersAdded")
    async def _on_members_added(
        context: TurnContext, _state: TurnState
    ) -> None:
        dims = _turn_dimensions(context)
        members = getattr(context.activity, "members_added", None) or []
        recipient = getattr(context.activity, "recipient", None)
        recipient_id = getattr(recipient, "id", None)
        for member in members:
            # Don't greet the bot itself (its id == recipient id).
            if getattr(member, "id", None) == recipient_id:
                continue
            log.info(
                "member added",
                extra={
                    "custom_dimensions": {
                        **dims,
                        "added_member_id": getattr(member, "id", "") or "",
                    }
                },
            )
            await context.send_activity(MessageFactory.text(GREETING))
            if gate_name:
                await context.send_activity(
                    MessageFactory.attachment(_gate_attachment())
                )

    @app.adaptive_card.action_execute(GATE_VERB)
    async def _on_gate_submit(
        context: TurnContext, state: TurnState, data: object
    ) -> AdaptiveCardInvokeResponse:
        """Gate card submit (Action.Execute invoke).

        The comparison is forgiving (trimmed, case-insensitive on both
        sides): this is a screen door against drive-bys, not
        authentication. A wrong answer never echoes in the transcript.
        """
        dims = _turn_dimensions(context)
        values = data if isinstance(data, dict) else {}
        raw_answer = values.get(GATE_INPUT_ID)
        answer = raw_answer if isinstance(raw_answer, str) else ""
        raw_email = values.get(GATE_EMAIL_ID)
        email = raw_email if isinstance(raw_email, str) else ""
        if gate_name and gate_answer_matches(answer, gate_name):
            state.conversation.set_value("demo_authorized", True)
            if email.strip():
                state.user.set_value("email", email.strip())
            log.info(
                "gate passed",
                extra={
                    "custom_dimensions": {
                        **dims,
                        "identity_email": email.strip(),
                    }
                },
            )
            await context.send_activity(MessageFactory.text(GREETING))
            return AdaptiveCardInvokeResponse(status_code=200)
        await context.send_activity(
            MessageFactory.text("Not quite — try again.")
        )
        # Re-show the card in place so the visitor can retry.
        return AdaptiveCardInvokeResponse(
            status_code=200,
            type=ContentTypes.adaptive_card,
            value=gate_card(base_url),
        )

    @app.activity("message", rank=RouteRank.LAST)
    async def _on_message(context: TurnContext, state: TurnState) -> None:
        dims = _turn_dimensions(context)
        text = getattr(context.activity, "text", "") or ""

        # Demo gate runs before any skill: until the visitor passes the
        # challenge card, nothing they type reaches chat.
        authorized = bool(
            state.conversation.get_value("demo_authorized", lambda: False)
        )
        if gate_name and not authorized:
            await context.send_activity(
                MessageFactory.attachment(_gate_attachment())
            )
            return

        # A card submitted after unlocking carries a value but no text;
        # there is nothing to route, so stay silent instead of emitting
        # an empty error message.
        if getattr(context.activity, "value", None) is not None and not text:
            return

        turn_count = state.conversation.get_value("turn_count", lambda: 0)
        turn_count = turn_count + 1 if isinstance(turn_count, int) else 1
        state.conversation.set_value("turn_count", turn_count)
        state.conversation.set_value("last_text", text)
        from_property = getattr(context.activity, "from_property", None)
        if from_property is not None:
            state.user.set_value(
                "name", getattr(from_property, "name", "") or ""
            )
        log.info(
            "message handled: %s",
            text,
            extra={
                "custom_dimensions": {**dims, "turn_count": turn_count}
            },
        )

        if openrouter_api_key and openrouter_model:
            reply = await _handle_chat(context, state, text, dims)
            if reply is not None:
                await context.send_activity(MessageFactory.text(reply))
                log.info(
                    "reply sent: %s",
                    reply,
                    extra={"custom_dimensions": dims},
                )
                return

        await context.send_activity(MessageFactory.text(CHAT_ERROR_MESSAGE))
        log.info(
            "reply sent: %s",
            CHAT_ERROR_MESSAGE,
            extra={"custom_dimensions": dims},
        )

    async def _handle_chat(
        context: TurnContext,
        state: TurnState,
        text: str,
        dims: dict[str, Any],
    ) -> str | None:
        """Chat fallback. Returns the reply, or None to use the error msg.

        History is the full conversation in conversation state — no
        artificial cap, limited only by conversation duration. A failed
        model call degrades to the error message instead of surfacing
        provider errors.
        """
        history = state.conversation.get_value("chat_history", lambda: [])
        if not isinstance(history, list):
            history = []
        start = time.monotonic()
        try:
            reply, used_model = await openrouter_chat(
                openrouter_api_key,
                openrouter_model,
                history,
                text,
            )
        except ChatError as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            log.warning(
                "chat fallback failed: %s",
                exc,
                extra={
                    "custom_dimensions": {**dims, "openrouter_ms": elapsed_ms}
                },
            )
            return None
        elapsed_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "chat reply received",
            extra={
                "custom_dimensions": {
                    **dims,
                    "openrouter_ms": elapsed_ms,
                    "openrouter_model": used_model,
                }
            },
        )
        history = history + [
            {"role": "user", "content": text},
            {"role": "assistant", "content": reply},
        ]
        state.conversation.set_value("chat_history", history)
        # History keeps the bare reply; only the sent message names the
        # model, so the footer never pollutes later turns' context.
        return f"{reply}\n\n— via {used_model}"

    @app.error
    async def _on_error(context: TurnContext, error: Exception) -> None:
        # This handler must never throw: if the channel itself is what's
        # failing, an exception here would mask the original error and
        # turn a handled turn into a 500. Guard every step.
        try:
            log.error(
                "unhandled turn error: %s",
                error,
                exc_info=error,
                extra={"custom_dimensions": _turn_dimensions(context)},
            )
        except Exception:  # noqa: BLE001 - the error handler never throws
            pass
        try:
            await context.send_activity(
                MessageFactory.text("Sorry — something went wrong on my end.")
            )
        except Exception as send_exc:  # noqa: BLE001
            log.warning("error route: apology send failed: %s", send_exc)

    return app
