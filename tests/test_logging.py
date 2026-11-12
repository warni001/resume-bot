import logging

import bot_logging
from bot_logging import (
    CustomDimensionsFilter,
    DimensionLoggerAdapter,
    get_logger,
    setup_logging,
)


def test_filter_merges_defaults_and_call_dimensions():
    filt = CustomDimensionsFilter({"service": "resume-bot", "env": "dev"})
    record = logging.LogRecord(
        "test", logging.INFO, __file__, 1, "msg", None, None
    )
    record.custom_dimensions = {"env": "prod", "turn": 3}
    assert filt.filter(record) is True
    assert record.custom_dimensions == {
        "service": "resume-bot",
        "env": "prod",
        "turn": 3,
    }
    # Flat attributes for the OTel exporter.
    assert record.custom_env == "prod"
    assert record.custom_turn == 3


def test_filter_without_existing_dimensions():
    filt = CustomDimensionsFilter({"service": "resume-bot"})
    record = logging.LogRecord(
        "test", logging.INFO, __file__, 1, "msg", None, None
    )
    assert filt.filter(record) is True
    assert record.custom_dimensions == {"service": "resume-bot"}


def test_adapter_deep_merges_dimensions(caplog):
    logger = logging.getLogger("test-adapter-merge")
    adapter = DimensionLoggerAdapter(
        logger, {"custom_dimensions": {"component": "agent", "turn": 1}}
    )
    with caplog.at_level(logging.INFO, logger="test-adapter-merge"):
        adapter.info("hi", extra={"custom_dimensions": {"turn": 2}})
    record = caplog.records[0]
    # Per-call wins on collision; adapter-level keys survive. (The root
    # filter may also merge default dimensions into the record; the
    # deep-merge behaviour is what this pins, not the full dict.)
    assert record.custom_dimensions["component"] == "agent"
    assert record.custom_dimensions["turn"] == 2


def test_get_logger_attaches_dimensions():
    log = get_logger(__name__, component="chat")
    assert log.extra["custom_dimensions"]["component"] == "chat"


def test_setup_logging_has_no_hardcoded_channel_dimension(monkeypatch):
    # Spec decision #4: the old bot's hardcoded channel="msteams" default
    # dimension is gone; filter traces on channel_id only.
    monkeypatch.setattr(bot_logging, "_CONFIGURED", False)
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    try:
        setup_logging(service_name="resume-bot")
        assert "channel" not in bot_logging._DEFAULT_DIMENSIONS
        assert bot_logging._DEFAULT_DIMENSIONS["service"] == "resume-bot"
    finally:
        for h in root.handlers:
            if h not in handlers_before:
                root.removeHandler(h)
        monkeypatch.setattr(bot_logging, "_CONFIGURED", True)


def test_setup_logging_quiets_azure_logger(monkeypatch):
    # Log-loop guard: the OTel handler sits on the root logger, so Azure
    # SDK INFO chatter would be re-exported as telemetry about telemetry.
    monkeypatch.setattr(bot_logging, "_CONFIGURED", False)
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    azure_logger = logging.getLogger("azure")
    previous_level = azure_logger.level
    try:
        setup_logging(service_name="resume-bot")
        assert azure_logger.level == logging.WARNING
    finally:
        azure_logger.setLevel(previous_level)
        for h in root.handlers:
            if h not in handlers_before:
                root.removeHandler(h)
        monkeypatch.setattr(bot_logging, "_CONFIGURED", True)


def test_setup_logging_is_idempotent(monkeypatch):
    monkeypatch.setattr(bot_logging, "_CONFIGURED", False)
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    try:
        setup_logging(service_name="resume-bot")
        count_after_first = len(root.handlers)
        setup_logging(service_name="resume-bot")
        assert len(root.handlers) == count_after_first
    finally:
        for h in root.handlers:
            if h not in handlers_before:
                root.removeHandler(h)
        monkeypatch.setattr(bot_logging, "_CONFIGURED", True)
