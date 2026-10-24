# helm_values is a values file for deploy/helm/exiqo: its keys are the chart's values keys, so the
# mapping is mechanical and nothing is retyped:
#
#   terraform output -json helm_values > values.azure.json
#   helm upgrade --install exiqo deploy/helm/exiqo -n exiqo -f values.gmf.yaml -f values.azure.json
#
# Only what this configuration created appears (a disabled switch leaves its keys to
# values.gmf.yaml). Helm merges maps key by key, so values.gmf.yaml keeps everything else
# (keyVault.secrets.appSecret, deploy.environments, ...). The other outputs are not values: they
# feed role assignments and the release pipelines.

locals {
  helm_values_all = {
    image = var.enable_acr ? {
      registry = azurerm_container_registry.this[0].login_server
    } : null

    keyVault = var.enable_key_vault ? { for k, v in {
      name = azurerm_key_vault.this[0].name
      secrets = var.enable_postgres ? { for k2, v2 in {
        databaseUrl     = var.secret_names.database_url
        testDatabaseUrl = local.write_test_database ? var.secret_names.test_database_url : null
      } : k2 => v2 if v2 != null } : null
    } : k => v if v != null } : null

    identity = var.enable_identities ? {
      platformClientId = azurerm_user_assigned_identity.platform[0].client_id
      deployIdentities = { for env, id in azurerm_user_assigned_identity.deploy : env => id.client_id }
    } : null

    postgres = var.enable_postgres ? {
      host  = azurerm_postgresql_flexible_server.this[0].fqdn
      cidrs = [var.postgres_delegated_subnet_cidr]
    } : null

    data = var.enable_data_share ? {
      size = "${azurerm_storage_share.data[0].quota}Gi"
      storageClass = {
        storageAccount = azurerm_storage_account.data[0].name
        resourceGroup  = local.rg
        shareName      = azurerm_storage_share.data[0].name
      }
    } : null

    # neo4jBackup.enabled stays false until sub-project 2 (values.yaml); these name its target.
    neo4jBackup = var.enable_backup_storage ? {
      storageAccount = azurerm_storage_account.backup[0].name
      container      = azurerm_storage_container.neo4j_backups[0].name
    } : null
  }
}

output "helm_values" {
  description = "Values for deploy/helm/exiqo (keys are the chart's values paths); pass with -f after values.gmf.yaml."
  value       = { for k, v in local.helm_values_all : k => v if v != null }
}

output "platform_identity_principal_id" {
  description = "exiqo-platform's object id (for role assignments GMF adds, e.g. on Azure OpenAI)."
  value       = one(azurerm_user_assigned_identity.platform[*].principal_id)
}

output "deploy_identity_principal_ids" {
  description = "Deploy environment -> exiqo-deploy-<env> object id. GMF grants each RBAC on its target subscription (README.md)."
  value       = { for env, id in azurerm_user_assigned_identity.deploy : env => id.principal_id }
}

output "cosign_key" {
  description = "COSIGN_KEY for the release pipelines' sign stage (cosign's Azure Key Vault reference)."
  value = var.enable_key_vault && var.enable_signing_key ? format(
    "azurekms://%s.vault.azure.net/%s", azurerm_key_vault.this[0].name, azurerm_key_vault_key.cosign[0].name,
  ) : null
}

output "cosign_public_key_pem" {
  description = "The signing key's public half: commit it (COSIGN_PUBLIC_KEY_FILE) so the deploy stage verifies without Key Vault access."
  value       = one(azurerm_key_vault_key.cosign[*].public_key_pem)
}

output "cosign_public_key_sha256" {
  description = "sha256 of cosign_public_key_pem as written by terraform output -raw: the deploy environment's COSIGN_PUBLIC_KEY_SHA256, which pins the committed key."
  value       = var.enable_key_vault && var.enable_signing_key ? sha256(azurerm_key_vault_key.cosign[0].public_key_pem) : null
}

output "egress_fqdns" {
  description = "Hosts created here that the platform pods reach: add them to egress.fqdns (a list, so Helm replaces it rather than merging) and to GMF's firewall."
  value = compact([
    var.enable_backup_storage ? "${azurerm_storage_account.backup[0].name}.blob.core.windows.net" : "",
  ])
}
