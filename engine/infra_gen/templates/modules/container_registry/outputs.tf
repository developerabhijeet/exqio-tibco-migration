output "id" {
  description = "Registry resource id (scope for the AcrPull role)."
  value       = azurerm_container_registry.this.id
}

output "name" {
  description = "Registry name."
  value       = azurerm_container_registry.this.name
}

output "login_server" {
  description = "Registry login server, for example myacr.azurecr.io."
  value       = azurerm_container_registry.this.login_server
}
