// Resume Bot — Azure free-tier infrastructure.
//
// Deploys into an existing resource group (see deploy.sh):
//   App Service plan (F1, Linux) + Web App (Python 3.14, system-assigned
//   managed identity) running `python app.py`
//   Azure Bot (F0, SingleTenant — Azure Bot Service has deprecated new
//   MultiTenant bots) with the Teams channel, messaging
//   endpoint pointed at the web app
//   Key Vault (RBAC) holding the bot + App Insights + Blob storage secrets;
//   the web app's managed identity gets Key Vault Secrets User
//   Storage account (Standard_LRS, Hot) for Blob state
//   Log Analytics workspace + Application Insights (workspace-based)
//
// The Microsoft App ID / password come from an app registration created by
// deploy.sh (Bicep cannot create Entra app registrations). The password is
// a @secure() parameter: it is passed to the deployment and stored only in
// Key Vault; it is never written to disk here or echoed by the script.
//
// Vault secret names map to the Agents SDK env var names by convention
// (dashes replace underscores): CONNECTIONS-SERVICE-CONNECTION-SETTINGS-*.
// The Blob storage connection string's exact env var name is TBD — verify
// against the installed Agents SDK 1.9.0 package during the build (spec
// §5.7). It is stored here as BlobStorageConnectionString.

@description('Base name for resources; a unique suffix is appended where global uniqueness is required.')
param baseName string = 'resume-bot'

@description('Azure region for regional resources (bot and vault are global).')
param location string = resourceGroup().location

@description('Microsoft App ID (app registration client ID) created by deploy.sh.')
param microsoftAppId string

@description('Microsoft App password (client secret) for the app registration. Stored only in Key Vault.')
@secure()
param microsoftAppPassword string

@description('Tenant ID of the directory that owns the app registration (SingleTenant bots). Passed by deploy.sh from az account show.')
param microsoftAppTenantId string

var suffix = uniqueString(resourceGroup().id)
var planName = 'plan-${baseName}'
var webAppName = 'app-${baseName}-${suffix}'
var botName = 'bot-${baseName}-${suffix}'
var vaultName = 'kv-${replace(baseName, '-', '')}-${take(suffix, 8)}'
var storageName = 'st${replace(baseName, '-', '')}${take(suffix, 8)}'
var logAnalyticsName = 'log-${baseName}'
var appInsightsName = 'appi-${baseName}'
var messagingEndpoint = 'https://${webAppName}.azurewebsites.net/api/messages'

// Key Vault Secrets User built-in role.
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  sku: {
    name: 'F1'
    tier: 'Free'
  }
  kind: 'linux'
  properties: {
    reserved: true
  }
}

resource webApp 'Microsoft.Web/sites@2023-12-01' = {
  name: webAppName
  location: location
  kind: 'app,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.14'
      appCommandLine: 'python app.py'
      alwaysOn: false // not available on F1
      ftpsState: 'FtpsOnly'
      appSettings: [
        {
          name: 'KEY_VAULT_URL'
          value: vault.properties.vaultUri
        }
        {
          name: 'LOG_LEVEL'
          value: 'INFO'
        }
        {
          name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
          value: 'true'
        }
        {
          name: 'APP_BASE_URL'
          value: 'https://${webAppName}.azurewebsites.net'
        }
      ]
    }
  }
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: vaultName
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enabledForDeployment: false
    enabledForTemplateDeployment: false
    enabledForDiskEncryption: false
  }
}

resource secretClientId 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'CONNECTIONS-SERVICE-CONNECTION-SETTINGS-CLIENTID'
  properties: {
    value: microsoftAppId
  }
}

resource secretClientSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'CONNECTIONS-SERVICE-CONNECTION-SETTINGS-CLIENTSECRET'
  properties: {
    value: microsoftAppPassword
  }
}

resource secretTenantId 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'CONNECTIONS-SERVICE-CONNECTION-SETTINGS-TENANTID'
  properties: {
    value: microsoftAppTenantId
  }
}

resource secretAppInsights 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'ApplicationInsightsConnectionString'
  properties: {
    value: appInsights.properties.ConnectionString
  }
}

resource secretBlobStorage 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'BlobStorageConnectionString'
  properties: {
    value: 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'
  }
}

// The web app reads the vault at startup via DefaultAzureCredential
// (its managed identity); config.py expects exactly these secret names.
resource vaultAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, webApp.id, keyVaultSecretsUserRoleId)
  scope: vault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalId: webApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

resource bot 'Microsoft.BotService/botServices@2022-09-15' = {
  name: botName
  location: 'global'
  kind: 'azurebot'
  sku: {
    name: 'F0'
  }
  properties: {
    displayName: baseName
    description: 'Resume bot (resume-bot).'
    endpoint: messagingEndpoint
    msaAppId: microsoftAppId
    msaAppType: 'SingleTenant'
    msaAppTenantId: microsoftAppTenantId
  }
}

resource teamsChannel 'Microsoft.BotService/botServices/channels@2022-09-15' = {
  parent: bot
  name: 'MsTeamsChannel'
  location: 'global'
  properties: {
    channelName: 'MsTeamsChannel'
    properties: {
      isEnabled: true
    }
  }
}

output webAppName string = webApp.name
output messagingEndpoint string = messagingEndpoint
output keyVaultUrl string = vault.properties.vaultUri
output botName string = bot.name
output microsoftAppId string = microsoftAppId
output appInsightsName string = appInsights.name
output storageAccountName string = storage.name
