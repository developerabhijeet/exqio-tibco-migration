output "log_analytics_workspace_id" {
  description = "Log Analytics workspace resource id."
  value       = azurerm_log_analytics_workspace.this.id
}

output "app_insights_id" {
  description = "Application Insights resource id."
  value       = azurerm_application_insights.this.id
}

output "app_insights_connection_string" {
  description = "Application Insights connection string."
  value       = azurerm_application_insights.this.connection_string
  sensitive   = true
}
