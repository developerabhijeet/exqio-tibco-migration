terraform {
  required_version = ">= 1.6.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.38"
    }
  }
}

# The cluster belongs to the platform team: this module deploys onto an existing AKS cluster with
# the OIDC issuer and workload identity enabled, and binds the user-assigned identity to the app.
data "azurerm_kubernetes_cluster" "this" {
  name                = var.cluster_name
  resource_group_name = var.cluster_resource_group_name
}

provider "kubernetes" {
  host                   = data.azurerm_kubernetes_cluster.this.kube_config[0].host
  cluster_ca_certificate = base64decode(data.azurerm_kubernetes_cluster.this.kube_config[0].cluster_ca_certificate)

  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "kubelogin"
    # 6dae42f8-4368-4678-94ff-3960e28e3630 is the well-known AKS Microsoft Entra server application.
    args = ["get-token", "--login", "azurecli", "--server-id", "6dae42f8-4368-4678-94ff-3960e28e3630"]
  }
}

locals {
  labels = {
    "app.kubernetes.io/name"       = var.name
    "app.kubernetes.io/managed-by" = "terraform"
  }
}

resource "azurerm_federated_identity_credential" "this" {
  name                = "${var.name}-aks"
  resource_group_name = var.resource_group_name
  parent_id           = var.identity_id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = data.azurerm_kubernetes_cluster.this.oidc_issuer_url
  subject             = "system:serviceaccount:${var.namespace}:${var.service_account_name}"
}

resource "kubernetes_namespace_v1" "this" {
  metadata {
    name   = var.namespace
    labels = local.labels
  }
}

resource "kubernetes_service_account_v1" "this" {
  metadata {
    name      = var.service_account_name
    namespace = kubernetes_namespace_v1.this.metadata[0].name
    labels    = local.labels

    annotations = {
      "azure.workload.identity/client-id" = var.identity_client_id
    }
  }
}

resource "kubernetes_deployment_v1" "this" {
  metadata {
    name      = var.name
    namespace = kubernetes_namespace_v1.this.metadata[0].name
    labels    = local.labels
  }

  spec {
    replicas = var.replicas

    selector {
      match_labels = {
        "app.kubernetes.io/name" = var.name
      }
    }

    template {
      metadata {
        labels = merge(local.labels, { "azure.workload.identity/use" = "true" })
      }

      spec {
        service_account_name = kubernetes_service_account_v1.this.metadata[0].name

        security_context {
          run_as_non_root = true
        }

        container {
          name  = "api"
          image = var.image

          port {
            container_port = var.target_port
          }

          # Secrets are read from Key Vault by the application through workload identity.
          env {
            name  = "AZURE_CLIENT_ID"
            value = var.identity_client_id
          }

          dynamic "env" {
            for_each = var.env_vars

            content {
              name  = env.key
              value = env.value
            }
          }

          resources {
            requests = {
              cpu    = var.cpu_request
              memory = var.memory_request
            }
            limits = {
              memory = var.memory_limit
            }
          }

          liveness_probe {
            http_get {
              path = var.health_path
              port = var.target_port
            }
          }

          readiness_probe {
            http_get {
              path = var.health_path
              port = var.target_port
            }
          }

          security_context {
            allow_privilege_escalation = false
            read_only_root_filesystem  = true
          }
        }
      }
    }
  }
}

resource "kubernetes_service_v1" "this" {
  metadata {
    name      = var.name
    namespace = kubernetes_namespace_v1.this.metadata[0].name
    labels    = local.labels
  }

  spec {
    type = var.ingress_enabled && var.external_ingress ? "LoadBalancer" : "ClusterIP"

    selector = {
      "app.kubernetes.io/name" = var.name
    }

    port {
      name        = "http"
      port        = 80
      target_port = var.target_port
    }
  }
}
