output "id" {
  description = "Container app resource id."
  value       = azurerm_container_app.this.id
}

output "name" {
  description = "Container app name."
  value       = azurerm_container_app.this.name
}

output "environment_id" {
  description = "Container Apps environment id."
  value       = azurerm_container_app_environment.this.id
}

output "fqdn" {
  description = "Ingress host name, null when ingress is disabled."
  value       = try(azurerm_container_app.this.ingress[0].fqdn, null)
}

output "url" {
  description = "Public URL, null when ingress is disabled."
  value       = try("https://${azurerm_container_app.this.ingress[0].fqdn}", null)
}
