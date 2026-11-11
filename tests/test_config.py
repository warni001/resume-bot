import os
import sys
import types

from config import BotConfig

SDK_ENV = {
    "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID": "app-id",
    "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET": "secret",
    "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID": "tenant",
}


def test_defaults():
    cfg = BotConfig()
    assert cfg.service_name == "resume-bot"
    assert cfg.port == 3978
    assert cfg.app_id == ""
    assert cfg.openrouter_model == ""
    assert cfg.openrouter_api_key == ""
    assert cfg.demo_gate_name == ""


def test_from_env_reads_sdk_auth_vars(monkeypatch):
    for key, value in SDK_ENV.items():
        monkeypatch.setenv(key, value)
    cfg = BotConfig.from_env(load_vault=False)
    assert cfg.app_id == "app-id"
    assert cfg.app_password == "secret"
    assert cfg.app_tenant_id == "tenant"


def test_from_env_reads_app_settings(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL", "some/model:free")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("DEMO_GATE_NAME", "John Warnicke")
    monkeypatch.setenv("APP_BASE_URL", "https://example.azurewebsites.net")
    monkeypatch.setenv("PORT", "8000")
    cfg = BotConfig.from_env(load_vault=False)
    assert cfg.openrouter_model == "some/model:free"
    assert cfg.openrouter_api_key == "sk-or-test"
    assert cfg.demo_gate_name == "John Warnicke"
    assert cfg.app_base_url == "https://example.azurewebsites.net"
    assert cfg.port == 8000


def test_from_env_defaults_sdk_env_vars_present(monkeypatch):
    # The SDK connection manager needs the SERVICE_CONNECTION entry to
    # exist (even empty) or the app can't boot to report missing config.
    for key in SDK_ENV:
        monkeypatch.delenv(key, raising=False)
    BotConfig.from_env(load_vault=False)
    for key in SDK_ENV:
        assert key in os.environ
        assert os.environ[key] == ""


def _install_fake_vault(monkeypatch, secrets):
    """Fake azure.identity / azure.keyvault.secrets in sys.modules."""

    class _FakeSecret:
        def __init__(self, value):
            self.value = value

    class _FakeClient:
        def __init__(self, vault_url, credential):
            self.vault_url = vault_url

        def get_secret(self, name):
            if name not in secrets:
                raise Exception(f"secret {name} not found")
            return _FakeSecret(secrets[name])

    identity = types.ModuleType("azure.identity")
    identity.DefaultAzureCredential = lambda: object()
    kv_secrets = types.ModuleType("azure.keyvault.secrets")
    kv_secrets.SecretClient = _FakeClient
    for name, module in [
        ("azure", types.ModuleType("azure")),
        ("azure.identity", identity),
        ("azure.keyvault", types.ModuleType("azure.keyvault")),
        ("azure.keyvault.secrets", kv_secrets),
    ]:
        monkeypatch.setitem(sys.modules, name, module)


def test_from_env_vault_overrides_env(monkeypatch):
    _install_fake_vault(
        monkeypatch,
        {
            "CONNECTIONS-SERVICE-CONNECTION-SETTINGS-CLIENTID": "vault-app-id",
            "OpenRouterApiKey": "vault-key",
            "BlobStorageConnectionString": "vault-blob-conn",
        },
    )
    monkeypatch.setenv("KEY_VAULT_URL", "https://vault.vault.azure.net/")
    monkeypatch.setenv(
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID", "env-app-id"
    )
    cfg = BotConfig.from_env(load_vault=True)
    assert cfg.app_id == "vault-app-id"
    assert cfg.openrouter_api_key == "vault-key"
    assert cfg.blob_storage_connection_string == "vault-blob-conn"
    # Vault values also land in os.environ for the SDK's env loader.
    assert (
        os.environ["CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID"]
        == "vault-app-id"
    )
    assert os.environ["OPENROUTER_API_KEY"] == "vault-key"


def test_from_env_vault_absent_secret_keeps_env_value(monkeypatch):
    _install_fake_vault(monkeypatch, {})
    monkeypatch.setenv("KEY_VAULT_URL", "https://vault.vault.azure.net/")
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    cfg = BotConfig.from_env(load_vault=True)
    assert cfg.openrouter_api_key == "env-key"


def test_from_env_vault_failure_does_not_raise(monkeypatch):
    # A vault outage must not take the bot down at import time: an
    # import/setup failure logs one warning and skips vault loading.
    # An azure.identity module with no DefaultAzureCredential stands in
    # for a broken vault setup.
    monkeypatch.setitem(sys.modules, "azure", types.ModuleType("azure"))
    monkeypatch.setitem(
        sys.modules, "azure.identity", types.ModuleType("azure.identity")
    )
    monkeypatch.setitem(
        sys.modules, "azure.keyvault", types.ModuleType("azure.keyvault")
    )
    monkeypatch.setitem(
        sys.modules,
        "azure.keyvault.secrets",
        types.ModuleType("azure.keyvault.secrets"),
    )
    monkeypatch.setenv("KEY_VAULT_URL", "https://vault.vault.azure.net/")
    cfg = BotConfig.from_env(load_vault=True)  # must not raise
    assert cfg.key_vault_url == "https://vault.vault.azure.net/"
    assert cfg.app_id == ""
