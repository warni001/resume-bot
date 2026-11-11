"""
config.py — Bot configuration: environment first, Azure Key Vault on top.

Secrets hygiene (standing rule): nothing secret lives in code or in git.
Locally you set env vars / a .env file; in Azure the App Service gets a
managed identity and this module reads the vault directly when
KEY_VAULT_URL is set.

The Agents SDK reads its connection settings from environment variables
(`load_configuration_from_env`). The vault secret names are those env var
names with underscores replaced by dashes (vault names cannot contain
underscores); this module maps them back when loading from the vault:

    CONNECTIONS-SERVICE-CONNECTION-SETTINGS-CLIENTID   -> CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID
    CONNECTIONS-SERVICE-CONNECTION-SETTINGS-CLIENTSECRET -> CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET
    CONNECTIONS-SERVICE-CONNECTION-SETTINGS-TENANTID   -> CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID

Other vault secrets keep their names:

    ApplicationInsightsConnectionString  -> APPLICATIONINSIGHTS_CONNECTION_STRING
    OpenRouterApiKey                     -> OPENROUTER_API_KEY
    DirectLineSecret                     -> DIRECTLINE_SECRET
    BlobStorageConnectionString          -> BLOB_STORAGE_CONNECTION_STRING

The demo gate name (DEMO_GATE_NAME) is NOT a secret — it is the
developer's name, asked for on a challenge card — so it lives in env /
App Settings only, never in Key Vault. OPENROUTER_MODEL, DEMO_GATE_NAME,
and APP_BASE_URL are app settings, not vault secrets.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Vault secret name -> (BotConfig field, env var name the SDK/config reads).
_VAULT_SECRETS = {
    "CONNECTIONS-SERVICE-CONNECTION-SETTINGS-CLIENTID": (
        "app_id",
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID",
    ),
    "CONNECTIONS-SERVICE-CONNECTION-SETTINGS-CLIENTSECRET": (
        "app_password",
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET",
    ),
    "CONNECTIONS-SERVICE-CONNECTION-SETTINGS-TENANTID": (
        "app_tenant_id",
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID",
    ),
    "ApplicationInsightsConnectionString": (
        "appinsights_connection_string",
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
    ),
    "OpenRouterApiKey": ("openrouter_api_key", "OPENROUTER_API_KEY"),
    "DirectLineSecret": ("directline_secret", "DIRECTLINE_SECRET"),
    "BlobStorageConnectionString": (
        "blob_storage_connection_string",
        "BLOB_STORAGE_CONNECTION_STRING",
    ),
}


@dataclass
class BotConfig:
    # Azure Bot registration (SingleTenant)
    app_id: str = ""
    app_password: str = ""
    app_tenant_id: str = ""

    # Runtime
    port: int = 3978
    host: str = "0.0.0.0"
    service_name: str = "resume-bot"
    environment: str = "dev"
    log_level: str = "INFO"

    # Azure (optional locally, expected in Azure)
    key_vault_url: str = ""
    appinsights_connection_string: str = ""
    blob_storage_connection_string: str = ""

    # OpenRouter chat (optional; bot works without it, chat just stays off)
    openrouter_api_key: str = ""
    openrouter_model: str = ""  # OPENROUTER_MODEL, set in .env / app settings

    # Public WebChat demo (optional; off unless configured)
    demo_gate_name: str = ""  # DEMO_GATE_NAME; empty disables the gate
    directline_secret: str = ""
    app_base_url: str = ""  # APP_BASE_URL, e.g. https://<app>.azurewebsites.net

    @classmethod
    def from_env(cls, load_vault: bool = True) -> "BotConfig":
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass

        cfg = cls(
            app_id=os.getenv(
                "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID", ""
            ),
            app_password=os.getenv(
                "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET", ""
            ),
            app_tenant_id=os.getenv(
                "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID", ""
            ),
            port=int(os.getenv("PORT", os.getenv("Port", "3978"))),
            host=os.getenv("HOST", "0.0.0.0"),
            service_name=os.getenv("SERVICE_NAME", "resume-bot"),
            environment=os.getenv("APP_ENV", "dev"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            key_vault_url=os.getenv(
                "KEY_VAULT_URL", os.getenv("AZURE_KEYVAULT_URL", "")
            ),
            appinsights_connection_string=os.getenv(
                "APPLICATIONINSIGHTS_CONNECTION_STRING", ""
            ),
            blob_storage_connection_string=os.getenv(
                "BLOB_STORAGE_CONNECTION_STRING", ""
            ),
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
            openrouter_model=os.getenv("OPENROUTER_MODEL", ""),
            demo_gate_name=os.getenv("DEMO_GATE_NAME", ""),
            directline_secret=os.getenv("DIRECTLINE_SECRET", ""),
            app_base_url=os.getenv("APP_BASE_URL", ""),
        )
        if load_vault and cfg.key_vault_url:
            cfg._load_from_key_vault()
        # The SDK's connection manager requires a SERVICE_CONNECTION entry
        # to exist — load_configuration_from_env only creates one when the
        # env vars are present. Default them to "" so the app boots with no
        # credentials and /health can report what's missing, instead of
        # crashing at import before the canary can run.
        for _env_name in (
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID",
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET",
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID",
        ):
            os.environ.setdefault(_env_name, "")
        return cfg

    def _load_from_key_vault(self) -> None:
        """Override fields with Key Vault values, where the vault has them.

        Uses the hosting environment's managed identity /
        DefaultAzureCredential. Per-secret failures are swallowed (the
        secret is simply absent); an import/setup failure logs one
        warning and skips all vault loading. A vault outage must not
        take the bot down at import time. Vault values also populate
        os.environ so the SDK's load_configuration_from_env finds them.
        """
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient

            client = SecretClient(
                vault_url=self.key_vault_url,
                credential=DefaultAzureCredential(),
            )
            for secret_name, (field_name, env_name) in _VAULT_SECRETS.items():
                try:
                    secret = client.get_secret(secret_name)
                except Exception:
                    continue  # secret simply not present in this vault
                if secret.value:
                    setattr(self, field_name, secret.value)
                    os.environ[env_name] = secret.value
        except Exception as exc:
            log.warning("Key Vault load skipped/failed: %s", exc)
