<#
.SYNOPSIS
  Deploys ComplyAgent to Azure: a registry, the image (built in Azure), PostgreSQL, the web app
  and the scheduled jobs (main.bicep). Run from the repository root after `az login`.

.EXAMPLE
  ./deploy/azure/deploy.ps1 -ResourceGroup complyagent-rg -Location westeurope `
      -AzureAiEndpoint https://my-ai.openai.azure.com/ -AdminEmails you@example.com

  It asks for the secrets (PostgreSQL password, Azure AI key, admin API token), so they never
  appear in the shell history. Deploying again updates everything in place.
#>
param(
  [Parameter(Mandatory)] [string] $ResourceGroup,
  [string] $Location = "westeurope",
  [string] $Name = "complyagent",
  [Parameter(Mandatory)] [string] $AzureAiEndpoint,
  [string] $AzureAiDeployment = "gpt-4.1-mini",
  [string] $AdminEmails = "",
  [string] $Tag = (Get-Date -Format "yyyyMMddHHmm")
)
$ErrorActionPreference = "Stop"

function Read-Secret([string] $Prompt) {
  $secure = Read-Host -AsSecureString $Prompt
  [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
}

az account show --query name -o tsv | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Sign in first: az login" }

$PostgresPassword = Read-Secret "PostgreSQL administrator password (12+ characters, letters and digits)"
$AzureAiKey = Read-Secret "Azure AI API key"
$AdminToken = Read-Secret "Admin API token (16+ characters)"

Write-Host "Resource group $ResourceGroup in $Location"
az group create --name $ResourceGroup --location $Location -o none

$Registry = ($Name -replace "[^a-zA-Z0-9]", "") + "acr" + (Get-Random -Maximum 99999)
$existing = az acr list --resource-group $ResourceGroup --query "[0].name" -o tsv
if ($existing) { $Registry = $existing } else {
  Write-Host "Container registry $Registry"
  az acr create --resource-group $ResourceGroup --name $Registry --sku Basic --admin-enabled true -o none
}

$Image = "$Registry.azurecr.io/complyagent:$Tag"
Write-Host "Building $Image in Azure (no local Docker needed)"
az acr build --registry $Registry --image "complyagent:$Tag" --file Dockerfile .

Write-Host "Deploying main.bicep"
$out = az deployment group create --resource-group $ResourceGroup `
  --template-file deploy/azure/main.bicep `
  --parameters name=$Name image=$Image registryName=$Registry `
    postgresPassword=$PostgresPassword azureAiApiKey=$AzureAiKey azureAiEndpoint=$AzureAiEndpoint `
    azureAiDeployment=$AzureAiDeployment adminApiToken=$AdminToken adminEmails=$AdminEmails `
  --query properties.outputs -o json | ConvertFrom-Json

Write-Host ""
Write-Host "ComplyAgent is at $($out.url.value)"
Write-Host "Next, load the data and the policies (once), with the admin API token or from a shell:"
Write-Host "  az containerapp exec -g $ResourceGroup -n $Name-web --command 'python -m app.manage import-customers'"
Write-Host "  az containerapp exec -g $ResourceGroup -n $Name-web --command 'python -m app.manage score-customers'"
Write-Host "  az containerapp exec -g $ResourceGroup -n $Name-web --command 'python -m app.manage add-documents /app/sample_policies/customer_contact_policy.md /app/sample_policies/escalation_policy.pdf /app/sample_policies/hardship_assistance_policy.txt /app/sample_policies/messaging_policy.docx'"
Write-Host "Sign up with an address in -AdminEmails to become an administrator."
