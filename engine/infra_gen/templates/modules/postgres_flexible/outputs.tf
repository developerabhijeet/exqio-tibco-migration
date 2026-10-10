output "id" {
  description = "Server resource id."
  value       = azurerm_postgresql_flexible_server.this.id
}

output "fqdn" {
  description = "Server host name."
  value       = azurerm_postgresql_flexible_server.this.fqdn
}

output "database_names" {
  description = "Names of the created databases."
  value       = [for d in azurerm_postgresql_flexible_server_database.this : d.name]
}
