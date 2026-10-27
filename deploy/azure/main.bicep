// ComplyAgent on Azure: Container Apps + Azure Database for PostgreSQL.
//
//   web              the app (API + React), 1-3 replicas behind HTTPS
//   follow-ups job   python -m app.manage run-follow-ups, every 15 minutes
//   mailbox job      python -m app.manage poll-email, every 5 minutes (when email is on)
//   PostgreSQL       Flexible Server (Burstable B1ms), database "complyagent"
//   Azure Files      the uploaded policy documents, shared by every replica
//
// The jobs replace the in-process schedulers, which would run once per replica. Follow-ups
// are claimed before they run and mailbox emails are de-duplicated, so overlapping runs are
// safe. Deploy with deploy.ps1.

@description('Name prefix for every resource, e.g. complyagent.')
param name string = 'complyagent'
param location string = resourceGroup().location
@description('The image in the registry, e.g. complyagentacr.azurecr.io/complyagent:1.')
param image string
@description('The registry the image is in (created by deploy.ps1).')
param registryName string

@secure()
param postgresPassword string
@secure()
param azureAiApiKey string
param azureAiEndpoint string
param azureAiDeployment string = 'gpt-4.1-mini'
@secure()
@description('At least 16 characters: the API token for scripts.')
param adminApiToken string
@description('Comma-separated emails that become administrators when they sign up.')
param adminEmails string = ''

@description('none: Send only records emails. graph: Microsoft 365 (fill the GRAPH_* parameters).')
@allowed(['none', 'graph'])
param emailProvider string = 'none'
param graphTenantId string = ''
param graphClientId string = ''
@secure()
param graphClientSecret string = ''
param graphMailbox string = ''

var postgresUser = 'complyagent'
var suffix = uniqueString(resourceGroup().id)
var databaseUrl = 'postgresql://${postgresUser}:${postgresPassword}@${postgres.properties.fullyQualifiedDomainName}:5432/complyagent?sslmode=require'

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${name}-logs'
  location: location
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2023-12-01-preview' = {
  name: '${name}-pg-${suffix}'
  location: location
  sku: { name: 'Standard_B1ms', tier: 'Burstable' }
  properties: {
    version: '16'
    administratorLogin: postgresUser
    administratorLoginPassword: postgresPassword
    storage: { storageSizeGB: 32 }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
  }
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-12-01-preview' = {
  parent: postgres
  name: 'complyagent'
}

// Lets Azure services (the container apps) reach the server. For a private network, replace
// this with a VNet-integrated environment and a private endpoint.
resource allowAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-12-01-preview' = {
  parent: postgres
  name: 'AllowAzureServices'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: take('${replace(name, '-', '')}st${suffix}', 24)
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: { minimumTlsVersion: 'TLS1_2', allowBlobPublicAccess: false }
}

resource share 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  name: '${storage.name}/default/uploads'
  properties: { shareQuota: 10 }
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${name}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource envStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: env
  name: 'uploads'
  properties: {
    azureFile: {
      accountName: storage.name
      accountKey: storage.listKeys().keys[0].value
      shareName: 'uploads'
      accessMode: 'ReadWrite'
    }
  }
}

var secrets = [
  { name: 'database-url', value: databaseUrl }
  { name: 'azure-ai-api-key', value: azureAiApiKey }
  { name: 'admin-api-token', value: adminApiToken }
  { name: 'graph-client-secret', value: empty(graphClientSecret) ? 'unused' : graphClientSecret }
  { name: 'registry-password', value: registry.listCredentials().passwords[0].value }
]

var environment = [
  { name: 'APP_ENV', value: 'production' }
  { name: 'DATABASE_URL', secretRef: 'database-url' }
  { name: 'LLM_PROVIDER', value: 'azure' }
  { name: 'AZURE_AI_ENDPOINT', value: azureAiEndpoint }
  { name: 'AZURE_AI_DEPLOYMENT', value: azureAiDeployment }
  { name: 'AZURE_AI_API_KEY', secretRef: 'azure-ai-api-key' }
  { name: 'ADMIN_API_TOKEN', secretRef: 'admin-api-token' }
  { name: 'ADMIN_EMAILS', value: adminEmails }
  { name: 'EMAIL_PROVIDER', value: emailProvider }
  { name: 'GRAPH_TENANT_ID', value: graphTenantId }
  { name: 'GRAPH_CLIENT_ID', value: graphClientId }
  { name: 'GRAPH_CLIENT_SECRET', secretRef: 'graph-client-secret' }
  { name: 'GRAPH_MAILBOX', value: graphMailbox }
  // The jobs below do this work: the web replicas must not also run it.
  { name: 'FOLLOW_UP_INTERVAL_MINUTES', value: '0' }
  { name: 'EMAIL_POLL_MINUTES', value: '0' }
]

var registries = [
  { server: registry.properties.loginServer, username: registry.listCredentials().username, passwordSecretRef: 'registry-password' }
]

var volumes = [{ name: 'uploads', storageType: 'AzureFile', storageName: envStorage.name }]
var mounts = [{ volumeName: 'uploads', mountPath: '/app/storage/uploads' }]

resource web 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${name}-web'
  location: location
  dependsOn: [database, allowAzure]
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: { external: true, targetPort: 8000, transport: 'auto', allowInsecure: false }
      secrets: secrets
      registries: registries
    }
    template: {
      containers: [
        {
          name: 'web'
          image: image
          resources: { cpu: json('1.0'), memory: '2Gi' }
          env: environment
          volumeMounts: mounts
          probes: [
            { type: 'Liveness', httpGet: { path: '/api/health', port: 8000 }, initialDelaySeconds: 20 }
          ]
        }
      ]
      volumes: volumes
      scale: { minReplicas: 1, maxReplicas: 3 }
    }
  }
}

resource followUps 'Microsoft.App/jobs@2024-03-01' = {
  name: '${name}-follow-ups'
  location: location
  dependsOn: [web]
  properties: {
    environmentId: env.id
    configuration: {
      triggerType: 'Schedule'
      scheduleTriggerConfig: { cronExpression: '*/15 * * * *', parallelism: 1, replicaCompletionCount: 1 }
      replicaTimeout: 1800
      replicaRetryLimit: 0
      secrets: secrets
      registries: registries
    }
    template: {
      containers: [
        {
          name: 'follow-ups'
          image: image
          command: ['python', '-m', 'app.manage', 'run-follow-ups']
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: environment
          volumeMounts: mounts
        }
      ]
      volumes: volumes
    }
  }
}

resource mailbox 'Microsoft.App/jobs@2024-03-01' = if (emailProvider == 'graph') {
  name: '${name}-mailbox'
  location: location
  dependsOn: [web]
  properties: {
    environmentId: env.id
    configuration: {
      triggerType: 'Schedule'
      scheduleTriggerConfig: { cronExpression: '*/5 * * * *', parallelism: 1, replicaCompletionCount: 1 }
      replicaTimeout: 600
      replicaRetryLimit: 0
      secrets: secrets
      registries: registries
    }
    template: {
      containers: [
        {
          name: 'mailbox'
          image: image
          command: ['python', '-m', 'app.manage', 'poll-email']
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: environment
          volumeMounts: mounts
        }
      ]
      volumes: volumes
    }
  }
}

output url string = 'https://${web.properties.configuration.ingress.fqdn}'
output postgresServer string = postgres.properties.fullyQualifiedDomainName
