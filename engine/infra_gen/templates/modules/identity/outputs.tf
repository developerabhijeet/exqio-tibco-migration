output "id" {
  description = "User-assigned identity resource id."
  value       = azurerm_user_assigned_identity.this.id
}

output "principal_id" {
  description = "Object id of the identity's service principal."
  value       = azurerm_user_assigned_identity.this.principal_id
}

output "client_id" {
  description = "Client id of the identity (AZURE_CLIENT_ID for DefaultAzureCredential)."
  value       = azurerm_user_assigned_identity.this.client_id
}

output "tenant_id" {
  description = "Tenant id of the identity."
  value       = azurerm_user_assigned_identity.this.tenant_id
}

output "role_assignment_ids" {
  description = "Role assignment ids by purpose; consumers depend on these so roles exist before use."
  value       = { for k, v in azurerm_role_assignment.this : k => v.id }
}
