#!/usr/bin/env bash
# Deploy the Resume Bot Azure resources (free tier) with Bicep.
#
# Prereqs:
#   - Azure CLI installed and signed in:  az login
#     (use the account that owns the subscription; you must be a Global
#     Administrator — the two service-principal steps below need it)
#   - Bicep: the script installs it via `az bicep install` if missing.
#   - Run on John's Mac (where `az` lives), not on the assistant VM.
#
# What it does:
#   1. Creates the Microsoft app registration (SingleTenant — Azure Bot
#      Service no longer accepts new MultiTenant bots) + a client secret.
#      The secret is held in a shell variable only, passed to the
#      deployment as a secure parameter, and stored in Key Vault by the
#      template. It is never printed or written to disk.
#   2. Ensures a service principal exists for the app registration
#      (idempotent — creates it only if missing). This closes the Oct 30
#      gap, when the old bot's app registration had no service principal
#      and auth failed with AADSTS7000229 until one was created by hand.
#   3. Ensures the deploy service principal `github-resume-bot-deploy`
#      exists with Contributor on the resource group (idempotent). On a
#      fresh creation it prints the credential JSON — save it as the
#      AZURE_DEPLOY_CREDENTIALS secret in the resume-bot repo. It is
#      shown only once. Do not store it anywhere else (not in the Key
#      Vault — the bot's managed identity could read it and gain a
#      deployment credential).
#   4. Creates the resource group.
#   5. Deploys infra/main.bicep (App Service F1, Azure Bot F0 + Teams
#      channel, Key Vault, Storage account for Blob state, Log Analytics
#      + App Insights).
#
# Re-running is safe: the deployment is incremental, and the two
# service-principal steps skip when the principals already exist. It WILL
# mint a fresh app password each run and update the Key Vault secret to
# match.
set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-resume-bot}"
LOCATION="${LOCATION:-westus3}"
BASE_NAME="${BASE_NAME:-resume-bot}"
APP_DISPLAY_NAME="${APP_DISPLAY_NAME:-resume-bot}"
DEPLOY_SP_NAME="${DEPLOY_SP_NAME:-github-resume-bot-deploy}"

cd "$(dirname "$0")"

command -v az >/dev/null || { echo "Azure CLI not found. Install: brew install azure-cli"; exit 1; }
az account show >/dev/null 2>&1 || { echo "Not signed in. Run: az login"; exit 1; }
az bicep version >/dev/null 2>&1 || az bicep install

echo "Subscription: $(az account show --query '{name:name, id:id}' -o tsv)"

SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
TENANT_ID="$(az account show --query tenantId -o tsv)"
echo "Tenant: $TENANT_ID"

echo "Creating app registration '$APP_DISPLAY_NAME' (SingleTenant)..."
APP_ID="$(az ad app create \
  --display-name "$APP_DISPLAY_NAME" \
  --sign-in-audience AzureADMyOrg \
  --query appId -o tsv)"

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

echo "Ensuring deploy service principal '$DEPLOY_SP_NAME' ..."
DEPLOY_SP_APP_ID="$(az ad sp list --display-name "$DEPLOY_SP_NAME" --query "[0].appId" -o tsv)"
RG_SCOPE="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP"
if [ -n "$DEPLOY_SP_APP_ID" ]; then
  echo "Deploy SP '$DEPLOY_SP_NAME' already exists (appId $DEPLOY_SP_APP_ID); skipping creation."
  echo "The AZURE_DEPLOY_CREDENTIALS secret is already in the repo from the first creation."
else
  echo "Creating deploy SP '$DEPLOY_SP_NAME' with Contributor on $RESOURCE_GROUP ..."
  az ad sp create-for-rbac --name "$DEPLOY_SP_NAME" --role Contributor --scopes "$RG_SCOPE" --sdk-auth
  echo
  echo "Save the JSON above as the AZURE_DEPLOY_CREDENTIALS secret in the resume-bot repo."
  echo "It is shown only once. Do not store it anywhere else."
fi

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

echo "Creating resource group $RESOURCE_GROUP in $LOCATION ..."
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" -o none

echo "Deploying infra/main.bicep ..."
RESULT="$(az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --template-file main.bicep \
  --parameters baseName="$BASE_NAME" microsoftAppId="$APP_ID" microsoftAppPassword="$APP_PASSWORD" microsoftAppTenantId="$TENANT_ID" \
  --query properties.outputs -o json)"
unset APP_PASSWORD

echo
echo "Done. Key outputs:"
echo "$RESULT" | python3 -c 'import json,sys; [print("  %s: %s" % (k, v["value"])) for k, v in json.load(sys.stdin).items()]'
echo
echo "Next steps:"
echo "  1. Store the remaining secrets in the new Key Vault (vault URL above):"
echo "       OpenRouterApiKey, DirectLineSecret (create a Direct Line channel on the"
echo "       new bot first, then store its key), and the app settings below."
echo "  2. Set the app settings on the new web app:"
echo "       OPENROUTER_MODEL, DEMO_GATE_NAME, APP_BASE_URL (the new web app URL)."
echo "  3. Add the AZURE_DEPLOY_CREDENTIALS secret to the resume-bot repo (step 3 above,"
echo "     if this was a fresh creation)."
echo "  4. Deploy the code: from the repo root, zip deploy (or wire CI/CD):"
echo "       az webapp deploy --resource-group $RESOURCE_GROUP --name <webAppName from outputs> --src-path <zip>"
echo "  5. Health check: https://<webAppName>.azurewebsites.net/health (all five flags true)"
