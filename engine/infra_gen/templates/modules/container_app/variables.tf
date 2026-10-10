variable "name" {
  description = "Container app name (lowercase letters, digits and dashes, at most 32 characters)."
  type        = string
}

variable "environment_name" {
  description = "Container Apps environment name."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group that holds the environment and the app."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "log_analytics_workspace_id" {
  description = "Log Analytics workspace that receives the environment logs."
  type        = string
}

variable "image" {
  description = "Full image reference, for example myacr.azurecr.io/gmf-credit:1.0.0."
  type        = string
}

variable "registry_login_server" {
  description = "Container registry login server the identity pulls from."
  type        = string
}

variable "identity_id" {
  description = "User-assigned identity id used for registry pull, Key Vault references and scaling."
  type        = string
}

variable "ingress_enabled" {
  description = "Expose the app: true when the application has REST, HTTP or SOAP starters."
  type        = bool
  default     = true
}

variable "external_ingress" {
  description = "Make the ingress reachable from the internet."
  type        = bool
  default     = true
}

variable "target_port" {
  description = "Container port (ASPNETCORE_URLS listens on 8080)."
  type        = number
  default     = 8080
}

variable "health_path" {
  description = "HTTP path used by the liveness and readiness probes."
  type        = string
  default     = "/health"
}

variable "cpu" {
  description = "vCPU per replica."
  type        = number
  default     = 0.5
}

variable "memory" {
  description = "Memory per replica, for example 1Gi."
  type        = string
  default     = "1Gi"
}

variable "min_replicas" {
  description = "Minimum replicas. Use at least 1 for services that consume messages."
  type        = number
  default     = 1
}

variable "max_replicas" {
  description = "Maximum replicas."
  type        = number
  default     = 3
}

variable "http_concurrent_requests" {
  description = "Concurrent requests per replica that trigger the HTTP scale rule."
  type        = number
  default     = 50
}

variable "env_vars" {
  description = "Plain environment variables, from the non-secret configuration keys."
  type        = map(string)
  default     = {}
}

variable "secrets" {
  description = "Environment variables backed by Key Vault secrets."
  type = list(object({
    env_name              = string
    secret_name           = string
    key_vault_secret_name = string
  }))
  default = []
}

variable "key_vault_secret_ids" {
  description = "Versionless Key Vault secret ids by secret name (output of the key_vault module)."
  type        = map(string)
  default     = {}
}

variable "app_insights_connection_string" {
  description = "Application Insights connection string; empty disables the setting."
  type        = string
  default     = ""
  sensitive   = true
}

variable "service_bus_namespace_name" {
  description = "Service Bus namespace name used by the queue-depth scale rules."
  type        = string
  default     = ""
}

variable "scale_queues" {
  description = "Queues whose depth scales the app out."
  type = list(object({
    queue_name    = string
    message_count = optional(number, 10)
  }))
  default = []
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default     = {}
}
