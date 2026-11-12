#!/usr/bin/env bash
# Redeploy the bot code to the Azure web app.
# Run from the repo root on a machine with the Azure CLI logged in:
#
#   ./infra/deploy-bot.sh
#
# Pulls latest main, zips the deployable files, zip-deploys, then polls
# /health until the app is back and all config flags are true — the
# canary for silent Key Vault failures (Oct 30 2026).
set -euo pipefail

cd "$(dirname "$0")/.."

RESOURCE_GROUP="rg-resume-bot"
WEBAPP_NAME="app-resume-bot-jyq4f2yizrb3e"
HEALTH_URL="https://${WEBAPP_NAME}.azurewebsites.net/health"
ZIP_PATH="/tmp/bot-deploy.zip"

git pull --rebase

# App settings from the local .env (never committed): DEMO_GATE_NAME and
# OPENROUTER_MODEL are applied on every deploy so the Azure config tracks
# the local source of truth. Absent values warn and skip — the canary
# reports a missing model, and a missing gate name just means no gate
# card. APP_BASE_URL is declared in main.bicep, not set here.
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi
SETTINGS=()
if [ -n "${OPENROUTER_MODEL:-}" ]; then
    SETTINGS+=("OPENROUTER_MODEL=${OPENROUTER_MODEL}")
else
    echo "warning: OPENROUTER_MODEL not set; skipping (canary will report it)" >&2
fi
if [ -n "${DEMO_GATE_NAME:-}" ]; then
    SETTINGS+=("DEMO_GATE_NAME=${DEMO_GATE_NAME}")
else
    echo "warning: DEMO_GATE_NAME not set; skipping (gate will be off)" >&2
fi
if [ "${#SETTINGS[@]}" -gt 0 ]; then
    az webapp config appsettings set \
        -g "$RESOURCE_GROUP" \
        -n "$WEBAPP_NAME" \
        --settings "${SETTINGS[@]}" \
        -o none
    echo "Applied app settings from .env."
fi

rm -f "$ZIP_PATH"
zip -r "$ZIP_PATH" \
    app.py agent.py bot_logging.py chat.py config.py \
    requirements.txt cards webchat profile.md static \
    -x '*/__pycache__/*'

az webapp deploy \
    -g "$RESOURCE_GROUP" \
    -n "$WEBAPP_NAME" \
    --src-path "$ZIP_PATH" \
    --type zip

echo "Deploy submitted — waiting for the app to restart..."
for _ in $(seq 1 30); do
    if curl -sf --max-time 10 "$HEALTH_URL" >/dev/null 2>&1; then
        break
    fi
    sleep 10
done

echo "Health check:"
HEALTH_JSON=$(curl -s --max-time 15 "$HEALTH_URL" || true)
if [ -z "$HEALTH_JSON" ]; then
    echo "CANARY FAILED — /health did not respond after deploy." >&2
    exit 1
fi
echo "$HEALTH_JSON" | python3 -m json.tool
echo "$HEALTH_JSON" | python3 -c "
import json, sys
h = json.load(sys.stdin)
flags = ['app_id_configured', 'azure_logging_configured',
         'key_vault_configured', 'directline_configured',
         'openrouter_configured']
bad = [f for f in flags if not h.get(f)]
if bad:
    sys.exit('CANARY FAILED — these flags are false: ' + ', '.join(bad))
print('Canary green — all config flags true.')
"
