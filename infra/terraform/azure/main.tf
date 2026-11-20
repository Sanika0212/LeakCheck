# Azure side of LeakCheck: the invoicing agent and the reference-data pipeline.
#
#   Kafka (Confluent) -> Function App (Python): LangChain agent on Azure OpenAI, approval page
#   Data Factory: public reference data (BLS wages and prices) -> Blob Storage -> warehouse
#   Key Vault holds the agent's secrets; the Function App reads them with its managed identity.

data "azurerm_client_config" "me" {}

locals {
  name = "leakcheck"
}

resource "azurerm_resource_group" "rg" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = { project = local.name }
}

resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

resource "azurerm_storage_account" "sa" {
  name                            = "${local.name}${random_string.suffix.result}"
  resource_group_name             = azurerm_resource_group.rg.name
  location                        = azurerm_resource_group.rg.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
}

resource "azurerm_storage_container" "reference" {
  name                  = "reference"
  storage_account_id    = azurerm_storage_account.sa.id
  container_access_type = "private"
}

resource "azurerm_key_vault" "kv" {
  name                       = "kv-${local.name}-${random_string.suffix.result}"
  resource_group_name        = azurerm_resource_group.rg.name
  location                   = azurerm_resource_group.rg.location
  tenant_id                  = data.azurerm_client_config.me.tenant_id
  sku_name                   = "standard"
  purge_protection_enabled   = false
  soft_delete_retention_days = 7
  rbac_authorization_enabled = true
}

resource "azurerm_log_analytics_workspace" "logs" {
  name                = "log-${local.name}"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
}

resource "azurerm_application_insights" "ai" {
  name                = "appi-${local.name}"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  workspace_id        = azurerm_log_analytics_workspace.logs.id
  application_type    = "web"
}

# ------------------------------------------------------------------ Azure OpenAI

resource "azurerm_cognitive_account" "openai" {
  name                  = "oai-${local.name}-${random_string.suffix.result}"
  resource_group_name   = azurerm_resource_group.rg.name
  location              = azurerm_resource_group.rg.location
  kind                  = "OpenAI"
  sku_name              = "S0"
  custom_subdomain_name = "oai-${local.name}-${random_string.suffix.result}"
}

resource "azurerm_cognitive_deployment" "chat" {
  name                 = var.openai_model
  cognitive_account_id = azurerm_cognitive_account.openai.id
  model {
    format  = "OpenAI"
    name    = var.openai_model
    version = var.openai_model_version
  }
  sku {
    name     = "GlobalStandard"
    capacity = 10
  }
}

# ------------------------------------------------------------------ the agent

resource "azurerm_service_plan" "plan" {
  name                = "asp-${local.name}"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  os_type             = "Linux"
  sku_name            = "Y1" # consumption: pay per execution
}

resource "azurerm_linux_function_app" "agent" {
  name                       = "func-${local.name}-${random_string.suffix.result}"
  resource_group_name        = azurerm_resource_group.rg.name
  location                   = azurerm_resource_group.rg.location
  service_plan_id            = azurerm_service_plan.plan.id
  storage_account_name       = azurerm_storage_account.sa.name
  storage_account_access_key = azurerm_storage_account.sa.primary_access_key
  https_only                 = true
  identity { type = "SystemAssigned" }
  site_config {
    application_stack { python_version = "3.12" }
    application_insights_connection_string = azurerm_application_insights.ai.connection_string
  }
  app_settings = {
    LC_LLM_PROVIDER         = "azure-openai"
    AZURE_OPENAI_ENDPOINT   = azurerm_cognitive_account.openai.endpoint
    AZURE_OPENAI_DEPLOYMENT = azurerm_cognitive_deployment.chat.name
    LC_KEY_VAULT_URL        = azurerm_key_vault.kv.vault_uri
  }
}

# Least privilege: the agent may read secrets and call the model, nothing more.
resource "azurerm_role_assignment" "agent_secrets" {
  scope                = azurerm_key_vault.kv.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_linux_function_app.agent.identity[0].principal_id
}

resource "azurerm_role_assignment" "agent_openai" {
  scope                = azurerm_cognitive_account.openai.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_linux_function_app.agent.identity[0].principal_id
}

# ------------------------------------------------------------------ reference data

resource "azurerm_data_factory" "adf" {
  name                = "adf-${local.name}-${random_string.suffix.result}"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  identity { type = "SystemAssigned" }
}

resource "azurerm_role_assignment" "adf_blob" {
  scope                = azurerm_storage_account.sa.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_data_factory.adf.identity[0].principal_id
}

resource "azurerm_data_factory_linked_service_azure_blob_storage" "blob" {
  name                 = "reference_blob"
  data_factory_id      = azurerm_data_factory.adf.id
  service_endpoint     = azurerm_storage_account.sa.primary_blob_endpoint
  use_managed_identity = true
}

resource "azurerm_data_factory_linked_service_web" "bls" {
  name                = "bls_api"
  data_factory_id     = azurerm_data_factory.adf.id
  authentication_type = "Anonymous"
  url                 = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
}

resource "azurerm_data_factory_dataset_http" "bls_series" {
  name                = "bls_series"
  data_factory_id     = azurerm_data_factory.adf.id
  linked_service_name = azurerm_data_factory_linked_service_web.bls.name
  relative_url        = "OEUN000000000000047214103"
  request_method      = "GET"
}

resource "azurerm_data_factory_dataset_json" "reference_json" {
  name                = "reference_json"
  data_factory_id     = azurerm_data_factory.adf.id
  linked_service_name = azurerm_data_factory_linked_service_azure_blob_storage.blob.name
  azure_blob_storage_location {
    container = azurerm_storage_container.reference.name
    path      = "bls"
    filename  = "painter_wage.json"
  }
  encoding = "UTF-8"
}

resource "azurerm_data_factory_pipeline" "reference" {
  name            = "refresh_reference_data"
  data_factory_id = azurerm_data_factory.adf.id
  activities_json = jsonencode([{
    name    = "copy_bls_wage"
    type    = "Copy"
    inputs  = [{ referenceName = azurerm_data_factory_dataset_http.bls_series.name, type = "DatasetReference" }]
    outputs = [{ referenceName = azurerm_data_factory_dataset_json.reference_json.name, type = "DatasetReference" }]
    typeProperties = {
      source = { type = "HttpSource" }
      sink   = { type = "JsonSink" }
    }
  }])
}

resource "azurerm_data_factory_trigger_schedule" "monthly" {
  name            = "monthly"
  data_factory_id = azurerm_data_factory.adf.id
  pipeline_name   = azurerm_data_factory_pipeline.reference.name
  frequency       = "Month"
  interval        = 1
}
