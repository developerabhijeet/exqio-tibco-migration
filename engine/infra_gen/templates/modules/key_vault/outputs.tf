output "id" {
  description = "Key Vault resource id (scope for the Key Vault Secrets User role)."
  value       = azurerm_key_vault.this.id
}

output "name" {
  description = "Key Vault name."
  value       = azurerm_key_vault.this.name
}

output "vault_uri" {
  description = "Key Vault data-plane URI."
  value       = azurerm_key_vault.this.vault_uri
}

output "secret_ids" {
  description = "Versionless secret ids by secret name."
  value       = { for k, v in azurerm_key_vault_secret.this : k => v.versionless_id }
}
