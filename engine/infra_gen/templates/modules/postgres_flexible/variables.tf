variable "name" {
  description = "Server name (3 to 63 characters, globally unique)."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group that holds the server."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "postgres_version" {
  description = "PostgreSQL major version."
  type        = string
  default     = "16"
}

variable "sku_name" {
  description = "Compute SKU, for example B_Standard_B1ms or GP_Standard_D2s_v3."
  type        = string
  default     = "B_Standard_B1ms"
}

variable "storage_mb" {
  description = "Storage size in MB."
  type        = number
  default     = 32768
}

variable "backup_retention_days" {
  description = "Backup retention in days (7 to 35)."
  type        = number
  default     = 7
}

variable "administrator_login" {
  description = "Administrator login name."
  type        = string
  default     = "pgadmin"
}

variable "administrator_password" {
  description = "Administrator password; supply it through the PG_ADMIN_PASSWORD environment variable."
  type        = string
  sensitive   = true
  default     = ""

  validation {
    condition     = length(var.administrator_password) >= 12
    error_message = "Set PG_ADMIN_PASSWORD to a password of at least 12 characters."
  }
}

variable "database_names" {
  description = "Databases to create, from the JDBC resources of the migrated application."
  type        = list(string)
  default     = []
}

variable "delegated_subnet_id" {
  description = "Subnet for VNet integration. Leave null for public access with a firewall rule."
  type        = string
  default     = null
}

variable "private_dns_zone_id" {
  description = "Private DNS zone for VNet integration (required with delegated_subnet_id)."
  type        = string
  default     = null
}

variable "allow_azure_services" {
  description = "With public access, allow connections from Azure services."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default     = {}
}
