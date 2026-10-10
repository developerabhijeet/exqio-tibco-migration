variable "log_analytics_name" {
  description = "Log Analytics workspace name."
  type        = string
}

variable "app_insights_name" {
  description = "Application Insights component name."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group that holds the monitoring resources."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "retention_in_days" {
  description = "Log retention in days (30 to 730)."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default     = {}
}
