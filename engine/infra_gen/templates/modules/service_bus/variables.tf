variable "name" {
  description = "Service Bus namespace name (6 to 50 characters, globally unique)."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group that holds the namespace."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "sku" {
  description = "Namespace SKU. Topics and subscriptions need Standard or Premium."
  type        = string
  default     = "Standard"

  validation {
    condition     = contains(["Basic", "Standard", "Premium"], var.sku)
    error_message = "sku must be Basic, Standard or Premium."
  }
}

variable "capacity" {
  description = "Messaging units, used only for the Premium SKU."
  type        = number
  default     = 1
}

variable "local_auth_enabled" {
  description = "Allow shared access keys. Off by default: applications use managed identity."
  type        = bool
  default     = false
}

variable "queues" {
  description = "Queues, from the design record's queue messaging entities."
  type = list(object({
    name                                 = string
    max_delivery_count                   = optional(number, 10)
    lock_duration                        = optional(string, "PT1M")
    default_message_ttl                  = optional(string, "P14D")
    dead_lettering_on_message_expiration = optional(bool, true)
    requires_duplicate_detection         = optional(bool, false)
  }))
  default = []
}

variable "topics" {
  description = "Topics, from the design record's topic messaging entities."
  type = list(object({
    name                         = string
    default_message_ttl          = optional(string, "P14D")
    requires_duplicate_detection = optional(bool, false)
    support_ordering             = optional(bool, false)
  }))
  default = []
}

variable "subscriptions" {
  description = "Subscriptions, one per consuming process."
  type = list(object({
    name                                 = string
    topic                                = string
    max_delivery_count                   = optional(number, 10)
    lock_duration                        = optional(string, "PT1M")
    default_message_ttl                  = optional(string, "P14D")
    dead_lettering_on_message_expiration = optional(bool, true)
  }))
  default = []
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default     = {}
}
