variable "name" {
  description = "User-assigned identity name."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group that holds the identity."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "container_registry_id" {
  description = "Container registry id: the identity gets AcrPull here."
  type        = string
}

variable "key_vault_id" {
  description = "Key Vault id: the identity gets Key Vault Secrets User here."
  type        = string
}

variable "service_bus_namespace_id" {
  description = "Service Bus namespace id: the identity gets Azure Service Bus Data Owner here."
  type        = string
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default     = {}
}
