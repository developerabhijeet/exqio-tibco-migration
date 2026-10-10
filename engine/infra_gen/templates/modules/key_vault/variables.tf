variable "name" {
  description = "Key Vault name (3 to 24 characters, globally unique)."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group that holds the vault."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "sku_name" {
  description = "Vault SKU: standard or premium."
  type        = string
  default     = "standard"
}

variable "purge_protection_enabled" {
  description = "Enable purge protection (turn on for production)."
  type        = bool
  default     = false
}

variable "soft_delete_retention_days" {
  description = "Soft delete retention in days (7 to 90)."
  type        = number
  default     = 7
}

variable "public_network_access_enabled" {
  description = "Allow public network access to the vault data plane."
  type        = bool
  default     = true
}

variable "grant_deployer_secrets_officer" {
  description = "Give the identity that runs Terraform the Key Vault Secrets Officer role on this vault."
  type        = bool
  default     = true
}

variable "rbac_propagation_wait" {
  description = "Wait after the deployer role assignment before secrets are created (RBAC propagation)."
  type        = string
  default     = "60s"
}

variable "secret_names" {
  description = "Secret names (letters, digits and dashes only). Two dashes stand for the colon of a .NET configuration key."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for n in var.secret_names : can(regex("^[A-Za-z0-9-]{1,127}$", n))])
    error_message = "Key Vault secret names may contain only letters, digits and dashes (1 to 127 characters)."
  }
}

variable "placeholder_value" {
  description = "Initial value for new secrets. Replace it out of band; changes to the value are ignored."
  type        = string
  default     = "REPLACE_ME"
  sensitive   = true
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default     = {}
}
