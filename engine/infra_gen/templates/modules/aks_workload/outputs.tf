output "namespace" {
  description = "Namespace of the workload."
  value       = kubernetes_namespace_v1.this.metadata[0].name
}

output "service_name" {
  description = "Kubernetes service name."
  value       = kubernetes_service_v1.this.metadata[0].name
}

output "oidc_issuer_url" {
  description = "OIDC issuer used by the federated identity credential."
  value       = data.azurerm_kubernetes_cluster.this.oidc_issuer_url
}
