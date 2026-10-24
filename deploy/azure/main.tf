# Azure prerequisites for the exiqo Helm chart (deploy/helm/exiqo): registry, Key Vault, the
# Workload Identity identities, Postgres with pgvector, the DATA_DIR share and the Neo4j backup
# container. Optional: GMF can provision any of these its own way (each is behind an enable_*
# variable). outputs.tf maps what is created onto the chart's values keys.
#
# Not here, by design: the AKS cluster and its network (inputs), and RBAC on the Deploy step's
# target subscriptions (GMF decides what each exiqo-deploy-<env> identity may change; the
# principal ids are outputs, README.md shows example assignments).

data "azurerm_client_config" "current" {}

data "azurerm_resource_group" "this" {
  name = var.resource_group_name
}

locals {
  location = coalesce(var.location, data.azurerm_resource_group.this.location)
  rg       = data.azurerm_resource_group.this.name
  tags     = merge({ application = "exiqo-migrate" }, var.tags)

  # Service accounts of the chart that use exiqo-platform (templates/serviceaccounts.yaml,
  # migrate-job.yaml, neo4j-backup-cronjob.yaml; the Neo4j pod reads its auth through the CSI
  # driver). Deploy identities trust exiqo-worker only: Terraform in the worker exchanges the
  # worker's token for whichever identity ARM_CLIENT_ID names.
  platform_service_accounts = ["exiqo-api", "exiqo-worker", "exiqo-migrate", "exiqo-neo4j", "exiqo-neo4j-backup"]
  deploy_environments       = var.enable_identities ? toset(var.deploy_environments) : toset([])

  create_postgres_dns_zone = var.enable_postgres && var.postgres_private_dns_zone_id == ""
  write_test_database      = var.enable_postgres && var.write_test_database_url
}

# ------------------------------------------------------------------------------------- registry

resource "azurerm_container_registry" "this" {
  count = var.enable_acr ? 1 : 0

  name                          = var.acr_name
  resource_group_name           = local.rg
  location                      = local.location
  sku                           = "Premium"
  admin_enabled                 = false
  anonymous_pull_enabled        = false
  public_network_access_enabled = var.public_network_access_enabled
  network_rule_bypass_option    = "AzureServices"
  zone_redundancy_enabled       = var.acr_zone_redundancy_enabled
  # No retention policy for untagged manifests: the chart pulls by digest, and the pipelines also
  # tag each release so a policy GMF adds later cannot delete a running image.
  tags = local.tags

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = can(regex("^[a-zA-Z0-9]{5,50}$", var.acr_name))
      error_message = "enable_acr needs acr_name (5-50 alphanumerics)."
    }
  }
}

# ------------------------------------------------------------------------------------- Key Vault

resource "azurerm_key_vault" "this" {
  count = var.enable_key_vault ? 1 : 0

  name                          = var.key_vault_name
  resource_group_name           = local.rg
  location                      = local.location
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = var.key_vault_sku
  rbac_authorization_enabled    = true
  purge_protection_enabled      = true
  soft_delete_retention_days    = 90
  public_network_access_enabled = var.public_network_access_enabled

  network_acls {
    default_action = "Deny"
    bypass         = "AzureServices"
  }

  tags = local.tags

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = can(regex("^[a-zA-Z][a-zA-Z0-9-]{1,22}[a-zA-Z0-9]$", var.key_vault_name))
      error_message = "enable_key_vault needs key_vault_name (3-24: a letter first, letters, digits, hyphens)."
    }
  }
}

# The identity running Terraform writes the secrets (and creates the signing key). With public
# access off it must also reach the vault's private endpoint (run from GMF's network).
resource "azurerm_role_assignment" "runner_secrets_officer" {
  count = var.enable_key_vault && var.grant_runner_key_vault_access ? 1 : 0

  scope                = azurerm_key_vault.this[0].id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "runner_crypto_officer" {
  count = var.enable_key_vault && var.enable_signing_key && var.grant_runner_key_vault_access ? 1 : 0

  scope                = azurerm_key_vault.this[0].id
  role_definition_name = "Key Vault Crypto Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

# Role assignments take a minute or two to apply to the data plane.
resource "time_sleep" "runner_rbac" {
  count = var.enable_key_vault && var.grant_runner_key_vault_access ? 1 : 0

  create_duration = "90s"
  depends_on      = [azurerm_role_assignment.runner_secrets_officer, azurerm_role_assignment.runner_crypto_officer]
}

# cosign signs with this key in place (azurekms://<vault>.vault.azure.net/<key>): the private key
# never leaves the vault. P-256 is what cosign's Azure KMS signer supports.
resource "azurerm_key_vault_key" "cosign" {
  count = var.enable_key_vault && var.enable_signing_key ? 1 : 0

  name         = var.signing_key_name
  key_vault_id = azurerm_key_vault.this[0].id
  key_type     = var.key_vault_sku == "premium" ? "EC-HSM" : "EC"
  curve        = "P-256"
  key_opts     = ["sign", "verify"]
  tags         = local.tags

  depends_on = [time_sleep.runner_rbac]

  lifecycle {
    prevent_destroy = true
  }
}

# ------------------------------------------------------------------------------------- identities

resource "azurerm_user_assigned_identity" "platform" {
  count = var.enable_identities ? 1 : 0

  name                = "exiqo-platform"
  resource_group_name = local.rg
  location            = local.location
  tags                = local.tags

  lifecycle {
    precondition {
      condition     = var.aks_oidc_issuer_url != ""
      error_message = "enable_identities needs aks_oidc_issuer_url (the federated credentials' issuer)."
    }
  }
}

resource "azurerm_federated_identity_credential" "platform" {
  for_each = var.enable_identities ? toset(local.platform_service_accounts) : toset([])

  name                      = "${var.namespace}-${each.key}"
  user_assigned_identity_id = azurerm_user_assigned_identity.platform[0].id
  audience                  = ["api://AzureADTokenExchange"]
  issuer                    = var.aks_oidc_issuer_url
  subject                   = "system:serviceaccount:${var.namespace}:${each.key}"
}

resource "azurerm_user_assigned_identity" "deploy" {
  for_each = local.deploy_environments

  name                = "exiqo-deploy-${each.key}"
  resource_group_name = local.rg
  location            = local.location
  tags                = local.tags
}

resource "azurerm_federated_identity_credential" "deploy" {
  for_each = local.deploy_environments

  name                      = "${var.namespace}-exiqo-worker"
  user_assigned_identity_id = azurerm_user_assigned_identity.deploy[each.key].id
  audience                  = ["api://AzureADTokenExchange"]
  issuer                    = var.aks_oidc_issuer_url
  subject                   = "system:serviceaccount:${var.namespace}:exiqo-worker"
}

# The CSI driver reads the chart's secrets as exiqo-platform.
resource "azurerm_role_assignment" "platform_secrets_user" {
  count = var.enable_identities && var.enable_key_vault ? 1 : 0

  scope                = azurerm_key_vault.this[0].id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.platform[0].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "platform_openai_user" {
  count = var.enable_identities && var.azure_openai_account_id != "" ? 1 : 0

  scope                = var.azure_openai_account_id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.platform[0].principal_id
  principal_type       = "ServicePrincipal"
}

# The Neo4j backup uploads as exiqo-platform (identity.backupClientId empty), to this container only.
resource "azurerm_role_assignment" "platform_backup_writer" {
  count = var.enable_identities && var.enable_backup_storage ? 1 : 0

  scope                = "${azurerm_storage_account.backup[0].id}/blobServices/default/containers/${azurerm_storage_container.neo4j_backups[0].name}"
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.platform[0].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "kubelet_acr_pull" {
  count = var.enable_acr && var.aks_kubelet_identity_object_id != "" ? 1 : 0

  scope                = azurerm_container_registry.this[0].id
  role_definition_name = "AcrPull"
  principal_id         = var.aks_kubelet_identity_object_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "cluster_data_account" {
  count = var.enable_data_share && var.aks_cluster_identity_principal_id != "" ? 1 : 0

  scope                = azurerm_storage_account.data[0].id
  role_definition_name = "Storage Account Contributor"
  principal_id         = var.aks_cluster_identity_principal_id
  principal_type       = "ServicePrincipal"
}

# Release pipelines, one identity per stage (variables.tf). Build/scan pushes candidates only; it
# cannot sign. The signer can sign and push tags but cannot deploy. The deployer can pull and
# deploy but cannot sign: it verifies with the committed public key (cosign_public_key_pem).
resource "azurerm_role_assignment" "build_acr_push" {
  count = var.enable_acr && var.build_principal_id != "" ? 1 : 0

  scope                = azurerm_container_registry.this[0].id
  role_definition_name = "AcrPush"
  principal_id         = var.build_principal_id
}

resource "azurerm_role_assignment" "signer_acr_push" {
  count = var.enable_acr && var.signer_principal_id != "" ? 1 : 0

  scope                = azurerm_container_registry.this[0].id
  role_definition_name = "AcrPush"
  principal_id         = var.signer_principal_id
}

resource "azurerm_role_assignment" "signer_key" {
  count = var.enable_key_vault && var.enable_signing_key && var.signer_principal_id != "" ? 1 : 0

  scope                = azurerm_key_vault_key.cosign[0].resource_versionless_id
  role_definition_name = "Key Vault Crypto User"
  principal_id         = var.signer_principal_id
}

resource "azurerm_role_assignment" "deployer_acr_pull" {
  count = var.enable_acr && var.deployer_principal_id != "" ? 1 : 0

  scope                = azurerm_container_registry.this[0].id
  role_definition_name = "AcrPull"
  principal_id         = var.deployer_principal_id
}

# Opt-in only: what the deployer may do in the cluster is GMF's decision.
resource "azurerm_role_assignment" "deployer_aks" {
  count = var.deployer_principal_id != "" && var.deployer_aks_role != "" ? 1 : 0

  scope                = var.deployer_aks_role_scope == "cluster" ? var.aks_cluster_id : "${var.aks_cluster_id}/namespaces/${var.namespace}"
  role_definition_name = var.deployer_aks_role
  principal_id         = var.deployer_principal_id

  lifecycle {
    precondition {
      condition     = var.aks_cluster_id != ""
      error_message = "deployer_aks_role needs aks_cluster_id."
    }
  }
}

# ------------------------------------------------------------------------------------- Postgres

resource "azurerm_private_dns_zone" "postgres" {
  count = local.create_postgres_dns_zone ? 1 : 0

  name                = "${var.postgres_server_name}.private.postgres.database.azure.com"
  resource_group_name = local.rg
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "postgres" {
  count = local.create_postgres_dns_zone ? 1 : 0

  name                  = "${var.postgres_server_name}-link"
  resource_group_name   = local.rg
  private_dns_zone_name = azurerm_private_dns_zone.postgres[0].name
  virtual_network_id    = var.postgres_vnet_id
  registration_enabled  = false
  tags                  = local.tags

  lifecycle {
    precondition {
      condition     = var.postgres_vnet_id != ""
      error_message = "Without postgres_private_dns_zone_id, postgres_vnet_id is needed to link the zone this creates."
    }
  }
}

# URL-safe characters only: the password goes into DATABASE_URL as is.
resource "random_password" "postgres" {
  count = var.enable_postgres ? 1 : 0

  length           = 40
  special          = true
  override_special = "-_~."
  min_lower        = 2
  min_upper        = 2
  min_numeric      = 2
}

resource "azurerm_postgresql_flexible_server" "this" {
  count = var.enable_postgres ? 1 : 0

  name                          = var.postgres_server_name
  resource_group_name           = local.rg
  location                      = local.location
  version                       = "16"
  sku_name                      = var.postgres_sku_name
  storage_mb                    = var.postgres_storage_mb
  backup_retention_days         = var.postgres_backup_retention_days
  geo_redundant_backup_enabled  = var.postgres_geo_redundant_backup_enabled
  zone                          = var.postgres_zone
  delegated_subnet_id           = var.postgres_delegated_subnet_id
  private_dns_zone_id           = local.create_postgres_dns_zone ? azurerm_private_dns_zone.postgres[0].id : var.postgres_private_dns_zone_id
  public_network_access_enabled = false
  administrator_login           = var.postgres_admin_login
  administrator_password        = random_password.postgres[0].result

  # Password auth: the app's asyncpg URL carries the password. Entra-only auth is the documented
  # alternative (README.md) and needs a token-fetching connection path in the app.
  authentication {
    password_auth_enabled         = true
    active_directory_auth_enabled = false
  }

  dynamic "high_availability" {
    for_each = var.postgres_high_availability ? [1] : []
    content {
      mode = "ZoneRedundant"
    }
  }

  tags = local.tags

  lifecycle {
    prevent_destroy = true

    # Azure may move the primary to the standby's zone on failover.
    ignore_changes = [zone, high_availability[0].standby_availability_zone]

    precondition {
      condition     = can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.postgres_server_name))
      error_message = "enable_postgres needs postgres_server_name (3-63: lowercase letters, digits, hyphens)."
    }
    precondition {
      condition     = var.postgres_delegated_subnet_id != "" && var.postgres_delegated_subnet_cidr != ""
      error_message = "enable_postgres needs postgres_delegated_subnet_id and postgres_delegated_subnet_cidr (private access)."
    }
    precondition {
      condition     = var.enable_key_vault
      error_message = "enable_postgres writes DATABASE_URL to the Key Vault: it needs enable_key_vault."
    }
  }

  depends_on = [azurerm_private_dns_zone_virtual_network_link.postgres]
}

# pgvector: engine/knowledge/store.py runs CREATE EXTENSION vector, which Azure allows only for
# allowlisted extensions.
resource "azurerm_postgresql_flexible_server_configuration" "extensions" {
  count = var.enable_postgres ? 1 : 0

  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.this[0].id
  value     = "VECTOR"
}

resource "azurerm_postgresql_flexible_server_database" "exiqo" {
  count = var.enable_postgres ? 1 : 0

  name      = "exiqo"
  server_id = azurerm_postgresql_flexible_server.this[0].id
  charset   = "UTF8"
  collation = "en_US.utf8"

  lifecycle {
    prevent_destroy = true
  }
}

# The API's test suite refuses a database whose name does not end _test (api/tests/conftest.py).
# tflint-ignore: azurerm_resources_missing_prevent_destroy # test data only; write_test_database_url = false drops it
resource "azurerm_postgresql_flexible_server_database" "exiqo_test" {
  count = local.write_test_database ? 1 : 0

  name      = "exiqo_test"
  server_id = azurerm_postgresql_flexible_server.this[0].id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

locals {
  postgres_url_base = var.enable_postgres ? format(
    "postgresql+asyncpg://%s:%s@%s:5432",
    var.postgres_admin_login, random_password.postgres[0].result,
    azurerm_postgresql_flexible_server.this[0].fqdn,
  ) : ""
}

# tflint-ignore: azurerm_resources_missing_prevent_destroy # derived from the server; soft delete keeps old versions
resource "azurerm_key_vault_secret" "database_url" {
  count = var.enable_postgres && var.enable_key_vault ? 1 : 0

  name         = var.secret_names.database_url
  key_vault_id = azurerm_key_vault.this[0].id
  value        = "${local.postgres_url_base}/${azurerm_postgresql_flexible_server_database.exiqo[0].name}?ssl=require"
  content_type = "DATABASE_URL"
  tags         = local.tags

  depends_on = [time_sleep.runner_rbac, azurerm_postgresql_flexible_server_configuration.extensions]
}

# tflint-ignore: azurerm_resources_missing_prevent_destroy # derived from the server; soft delete keeps old versions
resource "azurerm_key_vault_secret" "test_database_url" {
  count = local.write_test_database && var.enable_key_vault ? 1 : 0

  name         = var.secret_names.test_database_url
  key_vault_id = azurerm_key_vault.this[0].id
  value        = "${local.postgres_url_base}/${azurerm_postgresql_flexible_server_database.exiqo_test[0].name}?ssl=require"
  content_type = "TEST_DATABASE_URL"
  tags         = local.tags

  depends_on = [time_sleep.runner_rbac, azurerm_postgresql_flexible_server_configuration.extensions]
}

# ------------------------------------------------------------------------------------- DATA_DIR share
# Premium Azure Files over SMB (the chart's StorageClass mounts it as UID 10001). FileStorage
# accounts hold file shares only, so the backup container has its own account below. The CSI
# driver mounts with the account key, so shared keys stay enabled on this account.

resource "azurerm_storage_account" "data" {
  count = var.enable_data_share ? 1 : 0

  name                            = var.data_storage_account_name
  resource_group_name             = local.rg
  location                        = local.location
  account_kind                    = "FileStorage"
  account_tier                    = "Premium"
  account_replication_type        = var.data_replication_type
  https_traffic_only_enabled      = true
  min_tls_version                 = "TLS1_2"
  public_network_access_enabled   = var.public_network_access_enabled
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = true
  tags                            = local.tags

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = can(regex("^[a-z0-9]{3,24}$", var.data_storage_account_name))
      error_message = "enable_data_share needs data_storage_account_name (3-24 lowercase alphanumerics)."
    }
  }
}

resource "azurerm_storage_share" "data" {
  count = var.enable_data_share ? 1 : 0

  name               = "exiqo-data"
  storage_account_id = azurerm_storage_account.data[0].id
  quota              = var.data_share_quota_gb
  enabled_protocol   = "SMB"
  access_tier        = "Premium"

  lifecycle {
    prevent_destroy = true
  }
}

# ------------------------------------------------------------------------------------- Neo4j backup

resource "azurerm_storage_account" "backup" {
  count = var.enable_backup_storage ? 1 : 0

  name                            = var.backup_storage_account_name
  resource_group_name             = local.rg
  location                        = local.location
  account_kind                    = "StorageV2"
  account_tier                    = "Standard"
  account_replication_type        = var.backup_replication_type
  https_traffic_only_enabled      = true
  min_tls_version                 = "TLS1_2"
  public_network_access_enabled   = var.public_network_access_enabled
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false # the backup job uploads with Entra ID (--auth-mode login)
  default_to_oauth_authentication = true

  blob_properties {
    versioning_enabled = true
    delete_retention_policy {
      days = 7
    }
    container_delete_retention_policy {
      days = 7
    }
  }

  tags = local.tags

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = can(regex("^[a-z0-9]{3,24}$", var.backup_storage_account_name))
      error_message = "enable_backup_storage needs backup_storage_account_name (3-24 lowercase alphanumerics)."
    }
  }
}

resource "azurerm_storage_container" "neo4j_backups" {
  count = var.enable_backup_storage ? 1 : 0

  name                  = "neo4j-backups"
  storage_account_id    = azurerm_storage_account.backup[0].id
  container_access_type = "private"

  lifecycle {
    prevent_destroy = true
  }
}

# Retention of the dumps (the chart's backup job never deletes).
resource "azurerm_storage_management_policy" "backup" {
  count = var.enable_backup_storage ? 1 : 0

  storage_account_id = azurerm_storage_account.backup[0].id

  rule {
    name    = "neo4j-backup-retention"
    enabled = true
    filters {
      blob_types   = ["blockBlob"]
      prefix_match = ["${azurerm_storage_container.neo4j_backups[0].name}/"]
    }
    actions {
      base_blob {
        delete_after_days_since_creation_greater_than = var.backup_retention_days
      }
      version {
        delete_after_days_since_creation = var.backup_retention_days
      }
    }
  }
}

# ------------------------------------------------------------------------------------- private endpoints

locals {
  # Subresource -> target. Keys come from the switches (known at plan), ids may be unknown.
  private_endpoint_targets = {
    vault    = { enabled = var.enable_key_vault, name = var.key_vault_name, id = one(azurerm_key_vault.this[*].id) }
    registry = { enabled = var.enable_acr, name = var.acr_name, id = one(azurerm_container_registry.this[*].id) }
    file     = { enabled = var.enable_data_share, name = var.data_storage_account_name, id = one(azurerm_storage_account.data[*].id) }
    blob     = { enabled = var.enable_backup_storage, name = var.backup_storage_account_name, id = one(azurerm_storage_account.backup[*].id) }
  }
  private_endpoints = {
    for k, t in local.private_endpoint_targets : k => t if t.enabled && var.private_endpoint_subnet_id != ""
  }
}

resource "azurerm_private_endpoint" "this" {
  for_each = local.private_endpoints

  name                = "pe-${each.value.name}-${each.key}"
  resource_group_name = local.rg
  location            = local.location
  subnet_id           = var.private_endpoint_subnet_id

  private_service_connection {
    name                           = "${each.value.name}-${each.key}"
    private_connection_resource_id = each.value.id
    subresource_names              = [each.key]
    is_manual_connection           = false
  }

  dynamic "private_dns_zone_group" {
    for_each = lookup(var.private_dns_zone_ids, each.key, "") == "" ? [] : [var.private_dns_zone_ids[each.key]]
    content {
      name                 = "default"
      private_dns_zone_ids = [private_dns_zone_group.value]
    }
  }

  tags = local.tags
}
