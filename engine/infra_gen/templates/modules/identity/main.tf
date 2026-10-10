terraform {
  required_version = ">= 1.6.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

resource "azurerm_user_assigned_identity" "this" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = var.tags
}

locals {
  # Static keys keep for_each stable even when a scope id is only known after apply.
  role_assignments = {
    acr_pull = {
      scope = var.container_registry_id
      role  = "AcrPull"
    }
    key_vault_secrets_user = {
      scope = var.key_vault_id
      role  = "Key Vault Secrets User"
    }
    service_bus_data_owner = {
      scope = var.service_bus_namespace_id
      role  = "Azure Service Bus Data Owner"
    }
  }
}

resource "azurerm_role_assignment" "this" {
  for_each = local.role_assignments

  scope                = each.value.scope
  role_definition_name = each.value.role
  principal_id         = azurerm_user_assigned_identity.this.principal_id
  principal_type       = "ServicePrincipal"
}
