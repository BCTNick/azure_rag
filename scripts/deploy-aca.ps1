$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$envFile = Join-Path $repoRoot ".env"

if (-not (Test-Path $envFile)) {
  throw ".env not found at $envFile"
}

function Get-EnvMap {
  param([string]$Path)

  $map = @{}
  foreach ($line in Get-Content -Path $Path) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) {
      continue
    }

    $idx = $trimmed.IndexOf("=")
    if ($idx -lt 1) {
      continue
    }

    $key = $trimmed.Substring(0, $idx).Trim()
    $value = $trimmed.Substring($idx + 1).Trim()

    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
      $value = $value.Substring(1, $value.Length - 2)
    }

    $map[$key] = $value
  }

  return $map
}

function Get-RequiredEnvValue {
  param(
    [hashtable]$Map,
    [string]$Key
  )

  if (-not $Map.ContainsKey($Key) -or [string]::IsNullOrWhiteSpace($Map[$Key])) {
    throw "Missing required variable in .env: $Key"
  }

  return $Map[$Key]
}

function Invoke-Arm {
  param(
    [string]$Method,
    [string]$Uri,
    [hashtable]$Headers,
    $Body = $null,
    [int]$TimeoutSec = 300
  )

  if ($null -eq $Body) {
    return Invoke-RestMethod -Method $Method -Uri $Uri -Headers $Headers -TimeoutSec $TimeoutSec
  }

  $json = $Body | ConvertTo-Json -Depth 25
  return Invoke-RestMethod -Method $Method -Uri $Uri -Headers $Headers -Body $json -ContentType "application/json" -TimeoutSec $TimeoutSec
}

$envMap = Get-EnvMap -Path $envFile

$SubscriptionId = Get-RequiredEnvValue $envMap "AZURE_SUBSCRIPTION_ID"
$TenantId = Get-RequiredEnvValue $envMap "AZURE_TENANT_ID"
$ClientId = Get-RequiredEnvValue $envMap "AZURE_CLIENT_ID"
$ClientSecret = Get-RequiredEnvValue $envMap "AZURE_CLIENT_SECRET"

$ResourceGroup = Get-RequiredEnvValue $envMap "AZURE_RESOURCE_GROUP"
$Location = Get-RequiredEnvValue $envMap "AZURE_LOCATION"
$AcrName = Get-RequiredEnvValue $envMap "AZURE_ACR_NAME"
$WorkspaceName = Get-RequiredEnvValue $envMap "ACA_LOG_ANALYTICS_WORKSPACE"
$ContainerAppsEnv = Get-RequiredEnvValue $envMap "ACA_ENV_NAME"
$ContainerAppName = Get-RequiredEnvValue $envMap "ACA_APP_NAME"
$ImageTag = if ($envMap.ContainsKey("AZURE_IMAGE_TAG") -and $envMap["AZURE_IMAGE_TAG"]) { $envMap["AZURE_IMAGE_TAG"] } else { "v1" }

$LocalStorage = if ($envMap.ContainsKey("LOCAL_STORAGE") -and $envMap["LOCAL_STORAGE"]) { $envMap["LOCAL_STORAGE"] } else { "/app/input_data/local_storage" }

$AzureStorageAccountName = Get-RequiredEnvValue $envMap "AZURE_STORAGE_ACCOUNT_NAME"
$AzureContainerName = Get-RequiredEnvValue $envMap "AZURE_CONTAINER_NAME"
$AzureContainerSasToken = Get-RequiredEnvValue $envMap "AZURE_CONTAINER_SAS_TOKEN"
$AzureContainerSasUrl = Get-RequiredEnvValue $envMap "AZURE_CONTAINER_SAS_URL"

$AzureSearchEndpoint = Get-RequiredEnvValue $envMap "AZURE_SEARCH_ENDPOINT"
$AzureSearchAdminKey = Get-RequiredEnvValue $envMap "AZURE_SEARCH_ADMIN_KEY"
$AzureSearchIndexName = Get-RequiredEnvValue $envMap "AZURE_SEARCH_INDEX_NAME"
$AzureSearchDatasourceName = Get-RequiredEnvValue $envMap "AZURE_SEARCH_DATASOURCE_NAME"
$AzureSearchSkillsetName = Get-RequiredEnvValue $envMap "AZURE_SEARCH_SKILLSET_NAME"
$AzureSearchIndexerName = Get-RequiredEnvValue $envMap "AZURE_SEARCH_INDEXER_NAME"
$AzureSearchKnowledgeSourceName = Get-RequiredEnvValue $envMap "AZURE_SEARCH_KNOWLEDGE_SOURCE_NAME"
$AzureSearchKnowledgeBaseName = Get-RequiredEnvValue $envMap "AZURE_SEARCH_KNOWLEDGE_BASE_NAME"

$AzureOpenAiEndpoint = Get-RequiredEnvValue $envMap "AZURE_OPENAI_ENDPOINT"
$AzureOpenAiEmbeddingDeployment = Get-RequiredEnvValue $envMap "AZURE_OPENAI_EMBEDDING_DEPLOYMENT"
$AzureOpenAiEmbeddingModel = Get-RequiredEnvValue $envMap "AZURE_OPENAI_EMBEDDING_MODEL"
$AzureOpenAiChatDeployment = Get-RequiredEnvValue $envMap "AZURE_OPENAI_CHAT_DEPLOYMENT"
$AzureOpenAiApiKey = Get-RequiredEnvValue $envMap "AZURE_OPENAI_API_KEY"

$tokenResp = Invoke-RestMethod -Method Post -Uri "https://login.microsoftonline.com/$TenantId/oauth2/v2.0/token" -Body @{
  client_id = $ClientId
  client_secret = $ClientSecret
  scope = "https://management.azure.com/.default"
  grant_type = "client_credentials"
} -ContentType "application/x-www-form-urlencoded"

$accessToken = $tokenResp.access_token
if (-not $accessToken) {
  throw "Failed to acquire Azure ARM access token."
}

$headers = @{ Authorization = "Bearer $accessToken" }
$armBase = "https://management.azure.com"

$rgUri = "$armBase/subscriptions/$SubscriptionId/resourcegroups/${ResourceGroup}?api-version=2023-07-01"
Invoke-Arm -Method Put -Uri $rgUri -Headers $headers -Body @{ location = $Location } | Out-Null

$providers = @("Microsoft.OperationalInsights", "Microsoft.App", "Microsoft.ContainerRegistry")
foreach ($provider in $providers) {
  $registerUri = "$armBase/subscriptions/$SubscriptionId/providers/$provider/register?api-version=2021-04-01"
  Invoke-Arm -Method Post -Uri $registerUri -Headers $headers | Out-Null
}

$acrResourceId = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.ContainerRegistry/registries/$AcrName"
$acrUri = "$armBase${acrResourceId}?api-version=2023-07-01"
$acrBody = @{
  location = $Location
  sku = @{ name = "Basic" }
  properties = @{ adminUserEnabled = $true }
}
Invoke-Arm -Method Put -Uri $acrUri -Headers $headers -Body $acrBody | Out-Null

$acr = Invoke-Arm -Method Get -Uri $acrUri -Headers $headers
$acrServer = $acr.properties.loginServer

$acrCredUri = "$armBase$acrResourceId/listCredentials?api-version=2023-07-01"
$acrCreds = Invoke-Arm -Method Post -Uri $acrCredUri -Headers $headers -Body @{}
$acrUser = $acrCreds.username
$acrPass = $acrCreds.passwords[0].value

$image = "$acrServer/legal-rag-api:$ImageTag"

Push-Location $repoRoot
try {
  docker login $acrServer -u $acrUser -p $acrPass
  docker build -t $image .
  docker push $image
}
finally {
  Pop-Location
}

$workspaceResourceId = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.OperationalInsights/workspaces/$WorkspaceName"
$workspaceUri = "$armBase${workspaceResourceId}?api-version=2022-10-01"
$workspaceBody = @{ location = $Location; properties = @{} }
Invoke-Arm -Method Put -Uri $workspaceUri -Headers $headers -Body $workspaceBody | Out-Null

$workspaceKeysUri = "$armBase$workspaceResourceId/sharedKeys?api-version=2022-10-01"
$workspaceKeys = Invoke-Arm -Method Post -Uri $workspaceKeysUri -Headers $headers -Body @{}
$workspaceCustomerId = (Invoke-Arm -Method Get -Uri $workspaceUri -Headers $headers).properties.customerId
$workspaceSharedKey = $workspaceKeys.primarySharedKey

$envUri = "$armBase/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.App/managedEnvironments/${ContainerAppsEnv}?api-version=2024-03-01"
$envBody = @{
  location = $Location
  properties = @{
    appLogsConfiguration = @{
      destination = "log-analytics"
      logAnalyticsConfiguration = @{
        customerId = $workspaceCustomerId
        sharedKey = $workspaceSharedKey
      }
    }
  }
}
Invoke-Arm -Method Put -Uri $envUri -Headers $headers -Body $envBody | Out-Null

$appUri = "$armBase/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.App/containerApps/${ContainerAppName}?api-version=2024-03-01"
$appBody = @{
  location = $Location
  properties = @{
    managedEnvironmentId = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.App/managedEnvironments/$ContainerAppsEnv"
    configuration = @{
      ingress = @{
        external = $true
        targetPort = 8000
      }
      registries = @(
        @{
          server = $acrServer
          username = $acrUser
          passwordSecretRef = "acr-password"
        }
      )
      secrets = @(
        @{ name = "acr-password"; value = $acrPass }
      )
    }
    template = @{
      containers = @(
        @{
          name = "legal-rag-api"
          image = $image
          env = @(
            @{ name = "LOCAL_STORAGE"; value = "$LocalStorage" }
            @{ name = "AZURE_STORAGE_ACCOUNT_NAME"; value = "$AzureStorageAccountName" }
            @{ name = "AZURE_CONTAINER_NAME"; value = "$AzureContainerName" }
            @{ name = "AZURE_CONTAINER_SAS_TOKEN"; value = "$AzureContainerSasToken" }
            @{ name = "AZURE_CONTAINER_SAS_URL"; value = "$AzureContainerSasUrl" }
            @{ name = "AZURE_SEARCH_ENDPOINT"; value = "$AzureSearchEndpoint" }
            @{ name = "AZURE_SEARCH_ADMIN_KEY"; value = "$AzureSearchAdminKey" }
            @{ name = "AZURE_SEARCH_INDEX_NAME"; value = "$AzureSearchIndexName" }
            @{ name = "AZURE_SEARCH_DATASOURCE_NAME"; value = "$AzureSearchDatasourceName" }
            @{ name = "AZURE_SEARCH_SKILLSET_NAME"; value = "$AzureSearchSkillsetName" }
            @{ name = "AZURE_SEARCH_INDEXER_NAME"; value = "$AzureSearchIndexerName" }
            @{ name = "AZURE_SEARCH_KNOWLEDGE_SOURCE_NAME"; value = "$AzureSearchKnowledgeSourceName" }
            @{ name = "AZURE_SEARCH_KNOWLEDGE_BASE_NAME"; value = "$AzureSearchKnowledgeBaseName" }
            @{ name = "AZURE_OPENAI_ENDPOINT"; value = "$AzureOpenAiEndpoint" }
            @{ name = "AZURE_OPENAI_EMBEDDING_DEPLOYMENT"; value = "$AzureOpenAiEmbeddingDeployment" }
            @{ name = "AZURE_OPENAI_EMBEDDING_MODEL"; value = "$AzureOpenAiEmbeddingModel" }
            @{ name = "AZURE_OPENAI_CHAT_DEPLOYMENT"; value = "$AzureOpenAiChatDeployment" }
            @{ name = "AZURE_OPENAI_API_KEY"; value = "$AzureOpenAiApiKey" }
          )
        }
      )
      scale = @{
        minReplicas = 1
        maxReplicas = 3
      }
    }
  }
}

$app = Invoke-Arm -Method Put -Uri $appUri -Headers $headers -Body $appBody

Write-Host "Container App URL:"
Write-Host $app.properties.configuration.ingress.fqdn
