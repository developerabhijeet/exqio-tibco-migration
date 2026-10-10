terraform {
  required_version = ">= 1.6.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.69"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.12"
    }
  }
}

data "azurerm_client_config" "current" {}

resource "azurerm_key_vault" "this" {
  name                          = var.name
  resource_group_name           = var.resource_group_name
  location                      = var.location
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = var.sku_name
  rbac_authorization_enabled    = true
  purge_protection_enabled      = var.purge_protection_enabled
  soft_delete_retention_days    = var.soft_delete_retention_days
  public_network_access_enabled = var.public_network_access_enabled
  tags                          = var.tags
}

# The principal that runs Terraform needs data-plane rights to create the secret placeholders.
resource "azurerm_role_assignment" "deployer" {
  count = var.grant_deployer_secrets_officer ? 1 : 0

  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

# Azure RBAC takes a while to propagate; creating secrets right after the role assignment can fail with 403.
resource "time_sleep" "rbac_propagation" {
  count = var.grant_deployer_secrets_officer ? 1 : 0

  create_duration = var.rbac_propagation_wait

  depends_on = [azurerm_role_assignment.deployer]
}

# Secret names come from the design record's secret configuration keys. Values are placeholders:
# operators set the real values out of band, and Terraform never overwrites them afterwards.
resource "azurerm_key_vault_secret" "this" {
  for_each = toset(var.secret_names)

  name         = each.value
  value        = var.placeholder_value
  key_vault_id = azurerm_key_vault.this.id
  content_type = "text/plain"
  tags         = var.tags

  depends_on = [time_sleep.rbac_propagation]

  lifecycle {
    ignore_changes = [value]
  }
}
