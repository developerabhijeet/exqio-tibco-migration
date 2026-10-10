variable "name" {
  description = "Application name used for the deployment, service and labels."
  type        = string
}

variable "cluster_name" {
  description = "Existing AKS cluster (OIDC issuer and workload identity enabled)."
  type        = string
}

variable "cluster_resource_group_name" {
  description = "Resource group of the AKS cluster."
  type        = string
}

variable "identity_id" {
  description = "User-assigned identity id federated with the Kubernetes service account."
  type        = string
}

variable "identity_client_id" {
  description = "Client id of the user-assigned identity."
  type        = string
}

variable "container_registry_id" {
  description = "Registry id: the cluster's kubelet identity gets AcrPull here. Null skips the assignment."
  type        = string
  default     = null
}

variable "namespace" {
  description = "Kubernetes namespace for the workload."
  type        = string
}

variable "service_account_name" {
  description = "Kubernetes service account bound to the identity."
  type        = string
}

variable "image" {
  description = "Full image reference, for example myacr.azurecr.io/gmf-credit:1.0.0."
  type        = string
}

variable "replicas" {
  description = "Replica count."
  type        = number
  default     = 2
}

variable "ingress_enabled" {
  description = "Expose the service: true when the application has REST, HTTP or SOAP starters."
  type        = bool
  default     = true
}

variable "external_ingress" {
  description = "Expose the service through a public load balancer (use an ingress controller in production)."
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

variable "cpu_request" {
  description = "CPU request per pod."
  type        = string
  default     = "250m"
}

variable "memory_request" {
  description = "Memory request per pod."
  type        = string
  default     = "512Mi"
}

variable "memory_limit" {
  description = "Memory limit per pod."
  type        = string
  default     = "1Gi"
}

variable "env_vars" {
  description = "Plain environment variables, from the non-secret configuration keys."
  type        = map(string)
  default     = {}
}
