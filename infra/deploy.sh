#!/usr/bin/env bash
# Deploy the Resume Bot Azure resources (free tier) with Bicep.
#
# Prereqs:
#   - Azure CLI installed and signed in:  az login
#     (use the account that owns the subscription; you must be a Global
#     Administrator — the two service-principal steps below need it)
#   - Bicep: the script installs it via `az bicep install` if missing.
#   - GitHub CLI (`gh`), authenticated, to set the AZURE_DEPLOY_CREDENTIALS
#     secret automatically. Optional — without it, the script prints the
#     credential JSON for manual copy.
#   - Run on John's Mac (where `az` lives), not on the assistant VM.
#
# What it does:
#   1. Ensures the Microsoft app registration (SingleTenant — Azure Bot
#      Service no longer accepts new MultiTenant bots) exists, reusing
#      one with the same display name if present, + a fresh client
#      secret. The secret is held in a shell variable only, passed to
#      the deployment as a secure parameter, and stored in Key Vault by
#      the template. It is never printed or written to disk.
#   2. Ensures a service principal exists for the app registration
#      (idempotent — creates it only if missing). This closes the Oct 30
#      gap, when the old bot's app registration had no service principal
#      and auth failed with AADSTS7000229 until one was created by hand.
#   3. Creates the resource group (before the deploy SP step — the
#      Contributor role assignment needs the resource group to exist).
#   4. Ensures the deploy service principal `github-resume-bot-deploy`
#      exists with Contributor on the resource group (idempotent). The
#      credential JSON is set as the AZURE_DEPLOY_CREDENTIALS secret in
#      the resume-bot repo via `gh secret set` (requires gh CLI,
#      authenticated). If gh is missing, the JSON is printed for manual
#      copy. It is shown only once. Do not store it anywhere else (not
#      in the Key Vault — the bot's managed identity could read it and
#      gain a deployment credential).
#   5. Deploys infra/main.bicep (App Service F1, Azure Bot F0 + Teams
#      channel, Key Vault, Storage account for Blob state, Log Analytics
#      + App Insights).
#   6. Ensures the Direct Line channel exists on the new bot and stores
#      its secret in the Key Vault as DirectLineSecret. The secret is
#      held in a shell variable only and never printed.
#
# Re-running is safe: the deployment is incremental. It WILL mint a
# fresh app password each run and update the Key Vault secret to match.
set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-resume-bot}"
LOCATION="${LOCATION:-westus3}"
BASE_NAME="${BASE_NAME:-resume-bot}"
APP_DISPLAY_NAME="${APP_DISPLAY_NAME:-resume-bot}"
DEPLOY_SP_NAME="${DEPLOY_SP_NAME:-github-resume-bot-deploy}"
GITHUB_REPO="${GITHUB_REPO:-warni001/resume-bot}"

cd "$(dirname "$0")"

command -v az >/dev/null || { echo "Azure CLI not found. Install: brew install azure-cli"; exit 1; }
az account show >/dev/null 2>&1 || { echo "Not signed in. Run: az login"; exit 1; }
az bicep version >/dev/null 2>&1 || az bicep install

echo "Subscription: $(az account show --query '{name:name, id:id}' -o tsv)"

SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
TENANT_ID="$(az account show --query tenantId -o tsv)"
echo "Tenant: $TENANT_ID"

echo "Ensuring app registration '$APP_DISPLAY_NAME' (SingleTenant)..."
APP_ID="$(az ad app list --display-name "$APP_DISPLAY_NAME" --query "[0].appId" -o tsv)"
if [ -n "$APP_ID" ]; then
  echo "App registration '$APP_DISPLAY_NAME' already exists ($APP_ID); reusing it."
else
  APP_ID="$(az ad app create \
    --display-name "$APP_DISPLAY_NAME" \
    --sign-in-audience AzureADMyOrg \
    --query appId -o tsv)"
  echo "Created app registration '$APP_DISPLAY_NAME' ($APP_ID)."
fi

echo "Creating client secret for app $APP_ID ..."
APP_PASSWORD="$(az ad app credential reset \
  --id "$APP_ID" --append --display-name 'resume-bot' \
  --query password -o tsv)"
[ -n "$APP_PASSWORD" ] || { echo "Failed to create client secret."; exit 1; }

echo "Ensuring service principal for app registration $APP_ID ..."
if az ad sp show --id "$APP_ID" >/dev/null 2>&1; then
  echo "Service principal already exists for $APP_ID; skipping."
else
  az ad sp create --id "$APP_ID" -o none
  echo "Created service principal for $APP_ID."
fi

echo "Creating resource group $RESOURCE_GROUP in $LOCATION ..."
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" -o none

echo "Ensuring deploy service principal '$DEPLOY_SP_NAME' ..."
DEPLOY_SP_APP_ID="$(az ad sp list --display-name "$DEPLOY_SP_NAME" --query "[0].appId" -o tsv)"
RG_SCOPE="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP"
if [ -n "$DEPLOY_SP_APP_ID" ]; then
  echo "Deploy SP '$DEPLOY_SP_NAME' already exists (appId $DEPLOY_SP_APP_ID); resetting its credential..."
  DEPLOY_SP_PASSWORD="$(az ad sp credential reset --id "$DEPLOY_SP_APP_ID" --append --query password -o tsv)"
  [ -n "$DEPLOY_SP_PASSWORD" ] || { echo "Failed to reset deploy SP credential."; exit 1; }
  CREDENTIAL_JSON="$(python3 -c 'import json,sys; print(json.dumps({"clientId": sys.argv[1], "clientSecret": sys.argv[2], "subscriptionId": sys.argv[3], "tenantId": sys.argv[4], "activeDirectoryEndpointUrl": "https://login.microsoftonline.com/", "resourceManagerEndpointUrl": "https://management.azure.com/", "activeDirectoryGraphResourceId": "https://graph.windows.net/", "sqlManagementEndpointUrl": "https://management.core.windows.net:8443/", "galleryEndpointUrl": "https://gallery.azure.com/", "managementEndpointUrl": "https://management.core.windows.net/"}, indent=2))' "$DEPLOY_SP_APP_ID" "$DEPLOY_SP_PASSWORD" "$SUBSCRIPTION_ID" "$TENANT_ID")"
  unset DEPLOY_SP_PASSWORD
else
  echo "Creating deploy SP '$DEPLOY_SP_NAME' with Contributor on $RESOURCE_GROUP ..."
  CREDENTIAL_JSON="$(az ad sp create-for-rbac --name "$DEPLOY_SP_NAME" --role Contributor --scopes "$RG_SCOPE" --sdk-auth)"
fi

# Set the AZURE_DEPLOY_CREDENTIALS secret in the repo via gh. Falls back
# to printing the JSON for manual copy if gh is missing or not authed.
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  echo "$CREDENTIAL_JSON" | gh secret set AZURE_DEPLOY_CREDENTIALS --repo "$GITHUB_REPO"
  echo "Set AZURE_DEPLOY_CREDENTIALS in $GITHUB_REPO."
else
  echo "gh CLI not found or not authenticated. Save the JSON below as the AZURE_DEPLOY_CREDENTIALS secret in $GITHUB_REPO:"
  echo "$CREDENTIAL_JSON"
  echo "It is shown only once. Do not store it anywhere else."
fi
unset CREDENTIAL_JSON

# Ensure the Contributor role assignment exists (idempotent — the
# create-for-rbac above sets it on a fresh creation; this covers the
# already-exists path and any drift).
if [ -n "$DEPLOY_SP_APP_ID" ]; then
  if [ -z "$(az role assignment list --assignee "$DEPLOY_SP_APP_ID" --role Contributor --scope "$RG_SCOPE" --query "[0].id" -o tsv)" ]; then
    az role assignment create --assignee "$DEPLOY_SP_APP_ID" --role Contributor --scope "$RG_SCOPE" -o none
    echo "Granted Contributor on $RESOURCE_GROUP to '$DEPLOY_SP_NAME'."
  else
    echo "Contributor on $RESOURCE_GROUP already assigned to '$DEPLOY_SP_NAME'."
  fi
fi

echo "Deploying infra/main.bicep ..."
RESULT="$(az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --template-file main.bicep \
  --parameters baseName="$BASE_NAME" microsoftAppId="$APP_ID" microsoftAppPassword="$APP_PASSWORD" microsoftAppTenantId="$TENANT_ID" \
  --query properties.outputs -o json)"
unset APP_PASSWORD

# Direct Line: ensure the channel exists on the new bot and store its
# secret in the Key Vault as DirectLineSecret. Bot name and vault name
# come from the bicep outputs above.
BOT_NAME="$(echo "$RESULT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["botName"]["value"])')"
KEY_VAULT_NAME="$(echo "$RESULT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["keyVaultUrl"]["value"].split("https://")[1].split(".vault.azure.net")[0])')"
echo "Ensuring Direct Line channel on bot $BOT_NAME ..."
if az bot directline show --resource-group "$RESOURCE_GROUP" --name "$BOT_NAME" >/dev/null 2>&1; then
  echo "Direct Line channel already exists on $BOT_NAME; getting its secret..."
  DL_SECRET="$(az bot directline show --resource-group "$RESOURCE_GROUP" --name "$BOT_NAME" --query properties.properties.sites[0].key -o tsv)"
else
  echo "Creating Direct Line channel on $BOT_NAME ..."
  DL_SECRET="$(az bot directline create --resource-group "$RESOURCE_GROUP" --name "$BOT_NAME" --query properties.properties.sites[0].key -o tsv)"
fi
[ -n "$DL_SECRET" ] || { echo "Failed to get Direct Line secret."; exit 1; }
az keyvault secret set --vault-name "$KEY_VAULT_NAME" --name DirectLineSecret --value "$DL_SECRET" -o none
unset DL_SECRET
echo "Stored DirectLineSecret in Key Vault $KEY_VAULT_NAME."

echo
echo "Done. Key outputs:"
echo "$RESULT" | python3 -c 'import json,sys; [print("  %s: %s" % (k, v["value"])) for k, v in json.load(sys.stdin).items()]'
echo
echo "Next steps:"
echo "  1. Store the OpenRouterApiKey secret in the new Key Vault (vault URL above)."
echo "     (DirectLineSecret is already stored by this script.)"
echo "  2. Set the app settings on the new web app:"
echo "       OPENROUTER_MODEL, DEMO_GATE_NAME, APP_BASE_URL (the new web app URL)."
echo "  3. AZURE_DEPLOY_CREDENTIALS is set in the resume-bot repo by this script"
echo "     (or printed above for manual copy if gh was unavailable)."
echo "  4. Deploy the code: from the repo root, zip deploy (or wire CI/CD):"
echo "       az webapp deploy --resource-group $RESOURCE_GROUP --name <webAppName from outputs> --src-path <zip>"
echo "  5. Health check: https://<webAppName>.azurewebsites.net/health (all five flags true)"
