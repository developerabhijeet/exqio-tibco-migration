# The outputs -> chart values mapping (outputs.tf), run against mocked providers: no Azure
# credentials, nothing created. make azure-validate runs it (terraform test); make chart-check
# renders tests/helm_values.expected.json with values.gmf.example.yaml against the chart's schema.

mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      tenant_id = "3b2f8a64-1c2d-4e5f-9a8b-7c6d5e4f3a2b"
      object_id = "4c5d6e7f-8091-4a2b-b3c4-d5e6f7a8b9c0"
    }
  }
  mock_data "azurerm_resource_group" {
    defaults = {
      name     = "rg-gmf-exiqo-prod"
      location = "eastus2"
    }
  }
  mock_resource "azurerm_container_registry" {
    defaults = {
      id           = "/subscriptions/2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f/resourceGroups/rg-gmf-exiqo-prod/providers/Microsoft.ContainerRegistry/registries/gmfacr"
      login_server = "gmfacr.azurecr.io"
    }
  }
  mock_resource "azurerm_key_vault" {
    defaults = {
      id = "/subscriptions/2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f/resourceGroups/rg-gmf-exiqo-prod/providers/Microsoft.KeyVault/vaults/kv-gmf-exiqo-prod"
    }
  }
  mock_resource "azurerm_key_vault_key" {
    defaults = {
      resource_versionless_id = "/subscriptions/2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f/resourceGroups/rg-gmf-exiqo-prod/providers/Microsoft.KeyVault/vaults/kv-gmf-exiqo-prod/keys/exiqo-cosign"
      public_key_pem          = "-----BEGIN PUBLIC KEY-----\nmock\n-----END PUBLIC KEY-----\n"
    }
  }
  mock_resource "azurerm_storage_account" {
    defaults = {
      id = "/subscriptions/2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f/resourceGroups/rg-gmf-exiqo-prod/providers/Microsoft.Storage/storageAccounts/stmock"
    }
  }
  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f/resourceGroups/rg-gmf-exiqo-prod/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mock"
      client_id    = "6f1c2d3e-4b5a-4c6d-8e7f-9a0b1c2d3e4f"
      principal_id = "0d9e8f7a-6b5c-4d3e-a2f1-0e9d8c7b6a50"
    }
  }
  mock_resource "azurerm_postgresql_flexible_server" {
    defaults = {
      id   = "/subscriptions/2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f/resourceGroups/rg-gmf-exiqo-prod/providers/Microsoft.DBforPostgreSQL/flexibleServers/gmf-exiqo-pg"
      fqdn = "gmf-exiqo-pg.postgres.database.azure.com"
    }
  }
  mock_resource "azurerm_private_dns_zone" {
    defaults = {
      id = "/subscriptions/2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f/resourceGroups/rg-gmf-exiqo-prod/providers/Microsoft.Network/privateDnsZones/gmf-exiqo-pg.private.postgres.database.azure.com"
    }
  }
}

mock_provider "random" {}
mock_provider "time" {}

variables {
  resource_group_name            = "rg-gmf-exiqo-prod"
  acr_name                       = "gmfacr"
  key_vault_name                 = "kv-gmf-exiqo-prod"
  postgres_server_name           = "gmf-exiqo-pg"
  data_storage_account_name      = "stgmfexiqodata"
  backup_storage_account_name    = "stgmfexiqobackup"
  aks_oidc_issuer_url            = "https://eastus2.oic.prod-aks.azure.com/tenant/cluster/"
  deploy_environments            = ["dev", "prod"]
  postgres_delegated_subnet_id   = "/subscriptions/2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f/resourceGroups/rg-gmf-network/providers/Microsoft.Network/virtualNetworks/vnet-gmf-aks/subnets/snet-postgres"
  postgres_delegated_subnet_cidr = "10.20.30.0/28"
  postgres_vnet_id               = "/subscriptions/2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f/resourceGroups/rg-gmf-network/providers/Microsoft.Network/virtualNetworks/vnet-gmf-aks"
}

run "everything_enabled_maps_onto_the_values_keys" {
  command = apply

  assert {
    # The same file is rendered with the chart (make chart-check), so its keys are proven values keys.
    condition     = output.helm_values == jsondecode(file("tests/helm_values.expected.json"))
    error_message = "helm_values does not match the chart's values keys: ${jsonencode(output.helm_values)}"
  }

  assert {
    condition     = output.cosign_public_key_sha256 == sha256(output.cosign_public_key_pem)
    error_message = "cosign_public_key_sha256 pins cosign_public_key_pem."
  }

  assert {
    condition     = startswith(output.cosign_public_key_pem, "-----BEGIN PUBLIC KEY-----")
    error_message = "cosign_public_key_pem is the key's PEM."
  }

  assert {
    condition     = output.cosign_key == "azurekms://kv-gmf-exiqo-prod.vault.azure.net/exiqo-cosign"
    error_message = "cosign_key is ${output.cosign_key}"
  }

  assert {
    condition     = startswith(azurerm_key_vault_secret.database_url[0].value, "postgresql+asyncpg://exiqoadmin:") && endswith(azurerm_key_vault_secret.database_url[0].value, "@gmf-exiqo-pg.postgres.database.azure.com:5432/exiqo?ssl=require")
    error_message = "DATABASE_URL is not the asyncpg URL the app expects."
  }

  assert {
    condition     = endswith(azurerm_key_vault_secret.test_database_url[0].value, ":5432/exiqo_test?ssl=require")
    error_message = "TEST_DATABASE_URL does not name exiqo_test."
  }

  assert {
    condition     = azurerm_postgresql_flexible_server_configuration.extensions[0].value == "VECTOR" && azurerm_postgresql_flexible_server.this[0].version == "16"
    error_message = "Postgres 16 with the vector extension allowlisted."
  }

  assert {
    condition = toset(concat(
      azurerm_federated_identity_credential.platform_api[*].subject,
      azurerm_federated_identity_credential.platform_worker[*].subject,
      azurerm_federated_identity_credential.platform_migrate[*].subject,
      azurerm_federated_identity_credential.platform_neo4j[*].subject,
      azurerm_federated_identity_credential.platform_neo4j_backup[*].subject,
      )) == toset([
      "system:serviceaccount:exiqo:exiqo-api", "system:serviceaccount:exiqo:exiqo-worker",
      "system:serviceaccount:exiqo:exiqo-migrate", "system:serviceaccount:exiqo:exiqo-neo4j",
      "system:serviceaccount:exiqo:exiqo-neo4j-backup",
    ])
    error_message = "exiqo-platform must trust each of the chart's service accounts that reads Key Vault."
  }

  assert {
    condition     = alltrue([for c in azurerm_federated_identity_credential.deploy : c.subject == "system:serviceaccount:exiqo:exiqo-worker"]) && length(azurerm_federated_identity_credential.deploy) == 2
    error_message = "Each deploy identity trusts exiqo-worker."
  }

  assert {
    condition     = !azurerm_container_registry.this[0].admin_enabled && azurerm_key_vault.this[0].rbac_authorization_enabled && azurerm_key_vault.this[0].purge_protection_enabled
    error_message = "ACR admin user off; Key Vault in RBAC mode with purge protection."
  }

  assert {
    condition     = length(azurerm_private_endpoint.this) == 0
    error_message = "No private endpoints without private_endpoint_subnet_id."
  }
}

run "disabled_switches_leave_their_keys_to_values_gmf" {
  state_key = "disabled_switches_leave_their_keys_to_values_gmf"
  command   = apply

  variables {
    enable_acr              = false
    enable_postgres         = false
    enable_data_share       = false
    enable_backup_storage   = false
    write_test_database_url = false
  }

  assert {
    condition     = keys(output.helm_values) == ["identity", "keyVault"]
    error_message = "Only created resources appear: ${jsonencode(output.helm_values)}"
  }

  assert {
    condition     = output.helm_values.keyVault == { name = "kv-gmf-exiqo-prod" }
    error_message = "Without Postgres, keyVault.secrets is left to values.gmf.yaml."
  }
}

run "private_endpoints_follow_the_switches" {
  state_key = "private_endpoints_follow_the_switches"
  command   = plan

  variables {
    enable_acr                 = false
    private_endpoint_subnet_id = "/subscriptions/2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f/resourceGroups/rg-gmf-network/providers/Microsoft.Network/virtualNetworks/vnet-gmf-aks/subnets/snet-pe"
  }

  assert {
    condition     = toset(keys(azurerm_private_endpoint.this)) == toset(["vault", "file", "blob"])
    error_message = "One private endpoint per enabled resource."
  }
}

run "postgres_without_key_vault_is_refused" {
  state_key = "postgres_without_key_vault_is_refused"
  command   = plan

  variables {
    enable_key_vault = false
  }

  expect_failures = [azurerm_postgresql_flexible_server.this]
}

# One identity per release stage: build/scan cannot sign, the signer cannot deploy, the deployer
# cannot sign; no AKS role unless GMF opts in.
run "release_identities_are_split" {
  state_key = "release_identities_are_split"
  command   = plan

  variables {
    build_principal_id    = "4c5d6e7f-8091-4a2b-b3c4-d5e6f7a8b9c0"
    signer_principal_id   = "5d6e7f80-91a2-4b3c-84d5-e6f7a8b9c0d1"
    deployer_principal_id = "6e7f8091-a2b3-4c4d-95e6-f7a8b9c0d1e2"
    aks_cluster_id        = "/subscriptions/2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f/resourceGroups/rg-gmf-aks/providers/Microsoft.ContainerService/managedClusters/aks-gmf-prod"
  }

  assert {
    condition = (
      azurerm_role_assignment.build_acr_push[0].role_definition_name == "AcrPush" &&
      azurerm_role_assignment.build_acr_push[0].principal_id == var.build_principal_id &&
      azurerm_role_assignment.signer_acr_push[0].principal_id == var.signer_principal_id &&
      azurerm_role_assignment.signer_key[0].role_definition_name == "Key Vault Crypto User" &&
      azurerm_role_assignment.signer_key[0].principal_id == var.signer_principal_id &&
      azurerm_role_assignment.deployer_acr_pull[0].role_definition_name == "AcrPull" &&
      azurerm_role_assignment.deployer_acr_pull[0].principal_id == var.deployer_principal_id
    )
    error_message = "Build: AcrPush; signer: AcrPush + Crypto User on the key; deployer: AcrPull."
  }

  assert {
    condition     = length(azurerm_role_assignment.deployer_aks) == 0 && length(azurerm_role_assignment.deployer_aks_cluster_user) == 0
    error_message = "No AKS role for the deployer unless deployer_aks_role opts in."
  }
}

run "deployer_aks_role_is_opt_in" {
  state_key = "deployer_aks_role_is_opt_in"
  command   = plan

  variables {
    deployer_principal_id   = "6e7f8091-a2b3-4c4d-95e6-f7a8b9c0d1e2"
    aks_cluster_id          = "/subscriptions/2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f/resourceGroups/rg-gmf-aks/providers/Microsoft.ContainerService/managedClusters/aks-gmf-prod"
    deployer_aks_role       = "Azure Kubernetes Service RBAC Admin"
    deployer_aks_role_scope = "namespace"
  }

  assert {
    condition     = azurerm_role_assignment.deployer_aks[0].scope == "${var.aks_cluster_id}/namespaces/exiqo"
    error_message = "A namespace-scoped deployer role is scoped to the release namespace."
  }

  assert {
    condition = (
      azurerm_role_assignment.deployer_aks_cluster_user[0].role_definition_name == "Azure Kubernetes Service Cluster User Role" &&
      azurerm_role_assignment.deployer_aks_cluster_user[0].scope == var.aks_cluster_id &&
      azurerm_role_assignment.deployer_aks_cluster_user[0].principal_id == var.deployer_principal_id
    )
    error_message = "az aks get-credentials needs Cluster User Role at cluster scope, even with a namespace-scoped role."
  }
}

run "deployer_cluster_scope_also_gets_cluster_user" {
  state_key = "deployer_cluster_scope_also_gets_cluster_user"
  command   = plan

  variables {
    deployer_principal_id = "6e7f8091-a2b3-4c4d-95e6-f7a8b9c0d1e2"
    aks_cluster_id        = "/subscriptions/2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f/resourceGroups/rg-gmf-aks/providers/Microsoft.ContainerService/managedClusters/aks-gmf-prod"
    deployer_aks_role     = "Azure Kubernetes Service RBAC Cluster Admin"
  }

  assert {
    condition     = azurerm_role_assignment.deployer_aks[0].scope == var.aks_cluster_id && azurerm_role_assignment.deployer_aks_cluster_user[0].scope == var.aks_cluster_id
    error_message = "Cluster scope: the role and Cluster User Role both on the cluster."
  }
}
