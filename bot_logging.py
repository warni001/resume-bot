"""
bot_logging.py — Azure logging via OpenTelemetry, with custom dimensions.

Why not `logging.py`? A module with that name shadows the stdlib `logging`
package for every import in the process (Agents SDK, Azure SDKs included)
and breaks them. Same handler, safe name.

Usage
-----
    from bot_logging import setup_logging, get_logger

    setup_logging(connection_string=..., service_name="resume-bot")
    log = get_logger(__name__, bot="resume", env="dev")
    log.info("turn handled", extra={"custom_dimensions": {"turn_count": 3}})

Every record is merged with the default dimensions given to setup_logging()
and with per-logger dimensions from get_logger(), then per-call
extra={"custom_dimensions": {...}} wins on key collisions.

Destination
-----------
* If an Application Insights connection string is supplied (env
  APPLICATIONINSIGHTS_CONNECTION_STRING, or the Key Vault secret
  ApplicationInsightsConnectionString), records are exported to Azure
  Monitor / Application Insights via the OpenTelemetry exporter, and
  `custom_dimensions` lands in App Insights as customDimensions.
* Otherwise records go to stdout only — local dev / Agents Playground.

No hardcoded `channel` dimension: filter traces on `channel_id` only.
(The old bot rode a hardcoded `channel: "msteams"` on every record.)
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Mapping, Optional

_DEFAULT_DIMENSIONS: dict[str, Any] = {}
_CONFIGURED = False


class CustomDimensionsFilter(logging.Filter):
    """Merge default dimensions into every record's `custom_dimensions`."""

    def __init__(self, defaults: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__()
        self.defaults = dict(defaults or {})

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - stdlib API
        merged: dict[str, Any] = dict(self.defaults)
        existing = getattr(record, "custom_dimensions", None)
        if isinstance(existing, Mapping):
            merged.update(existing)
        record.custom_dimensions = merged
        # Also expose dimensions as flat attributes so the Azure Monitor
        # OTel exporter picks them up even on versions that don't map
        # a `custom_dimensions` dict natively.
        for key, value in merged.items():
            setattr(record, f"custom_{key}", value)
        return True


def setup_logging(
    connection_string: Optional[str] = None,
    *,
    service_name: str = "resume-bot",
    level: int = logging.INFO,
    default_dimensions: Optional[Mapping[str, Any]] = None,
) -> logging.Logger:
    """Configure the root logger once. Idempotent after the first call."""
    global _CONFIGURED, _DEFAULT_DIMENSIONS
    root = logging.getLogger()
    if _CONFIGURED:
        return root

    _DEFAULT_DIMENSIONS = {
        "service": service_name,
        **dict(default_dimensions or {}),
    }
    dim_filter = CustomDimensionsFilter(_DEFAULT_DIMENSIONS)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s "
        "custom_dimensions=%(custom_dimensions)s"
    )

    # Always log to stdout (App Service log stream / container logs / local).
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.addFilter(dim_filter)
    root.addHandler(console)

    if connection_string:
        try:
            from azure.monitor.opentelemetry.exporter import (
                AzureMonitorLogExporter,
            )
            from opentelemetry._logs import set_logger_provider
            from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
            from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
            from opentelemetry.sdk.resources import Resource

            resource = Resource.create({"service.name": service_name})
            provider = LoggerProvider(resource=resource)
            provider.add_log_record_processor(
                BatchLogRecordProcessor(
                    AzureMonitorLogExporter(connection_string=connection_string)
                )
            )
            set_logger_provider(provider)

            otel_handler = LoggingHandler(level=level, logger_provider=provider)
            # NOTE: LoggingHandler formats its own record; the filter is what
            # carries custom_dimensions into the exported attributes.
            otel_handler.addFilter(dim_filter)
            root.addHandler(otel_handler)
        except Exception as exc:  # Azure logging must never kill the bot.
            root.warning(
                "Azure Monitor log exporter failed to initialise (%s); "
                "continuing with stdout logging only.",
                exc,
            )

    root.setLevel(level)
    _CONFIGURED = True
    # The Azure SDKs log every telemetry export at INFO (request/response
    # lines via http_logging_policy, "Transmission succeeded", ...). Those
    # records reach the root OTel handler and get re-exported as telemetry
    # about telemetry — a self-sustaining noise loop. Keep them quiet.
    logging.getLogger("azure").setLevel(logging.WARNING)
    return root


class DimensionLoggerAdapter(logging.LoggerAdapter):
    """LoggerAdapter that deep-merges `custom_dimensions` instead of
    letting the adapter-level dict overwrite the per-call dict (the
    stdlib adapter merges extra= shallowly, keyed on 'custom_dimensions'
    itself, which would silently drop per-call dimensions)."""

    def process(self, msg, kwargs):
        call_extra = dict(kwargs.get("extra") or {})
        call_dims = dict(call_extra.get("custom_dimensions") or {})
        merged = {**(self.extra.get("custom_dimensions") or {}), **call_dims}
        call_extra["custom_dimensions"] = merged
        kwargs["extra"] = call_extra
        return msg, kwargs


def get_logger(name: str, **dimensions: Any) -> DimensionLoggerAdapter:
    """Return a logger that attaches `dimensions` to every record it emits."""
    merged = {**_DEFAULT_DIMENSIONS, **dimensions}
    return DimensionLoggerAdapter(
        logging.getLogger(name), {"custom_dimensions": merged}
    )
