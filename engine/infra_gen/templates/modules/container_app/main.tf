terraform {
  required_version = ">= 1.6.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

locals {
  # A custom scale rule name may contain only lowercase letters, digits and dashes.
  queue_rules = { for q in var.scale_queues : replace(lower(q.queue_name), "/[^a-z0-9]+/", "-") => q }
}

resource "azurerm_container_app_environment" "this" {
  name                       = var.environment_name
  resource_group_name        = var.resource_group_name
  location                   = var.location
  log_analytics_workspace_id = var.log_analytics_workspace_id
  tags                       = var.tags
}

resource "azurerm_container_app" "this" {
  name                         = var.name
  resource_group_name          = var.resource_group_name
  container_app_environment_id = azurerm_container_app_environment.this.id
  revision_mode                = "Single"
  tags                         = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [var.identity_id]
  }

  registry {
    server   = var.registry_login_server
    identity = var.identity_id
  }

  # Secrets are resolved from Key Vault by the user-assigned identity; no secret value is stored here.
  dynamic "secret" {
    for_each = var.secrets

    content {
      name                = secret.value.secret_name
      identity            = var.identity_id
      key_vault_secret_id = var.key_vault_secret_ids[secret.value.key_vault_secret_name]
    }
  }

  dynamic "secret" {
    for_each = var.app_insights_connection_string == "" ? [] : [1]

    content {
      name  = "appinsights-connection-string"
      value = var.app_insights_connection_string
    }
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = "api"
      image  = var.image
      cpu    = var.cpu
      memory = var.memory

      dynamic "env" {
        for_each = var.env_vars

        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = var.secrets

        content {
          name        = env.value.env_name
          secret_name = env.value.secret_name
        }
      }

      dynamic "env" {
        for_each = var.app_insights_connection_string == "" ? [] : [1]

        content {
          name        = "APPLICATIONINSIGHTS_CONNECTION_STRING"
          secret_name = "appinsights-connection-string"
        }
      }

      liveness_probe {
        transport = "HTTP"
        port      = var.target_port
        path      = var.health_path
      }

      readiness_probe {
        transport = "HTTP"
        port      = var.target_port
        path      = var.health_path
      }
    }

    dynamic "http_scale_rule" {
      for_each = var.ingress_enabled ? [1] : []

      content {
        name                = "http-concurrency"
        concurrent_requests = tostring(var.http_concurrent_requests)
      }
    }

    # Queue depth scaling for the JMS-receive consumers; the identity needs Service Bus Data Owner.
    dynamic "custom_scale_rule" {
      for_each = local.queue_rules

      content {
        name             = "sb-${custom_scale_rule.key}"
        custom_rule_type = "azure-servicebus"
        identity_id      = var.identity_id

        metadata = {
          namespace    = var.service_bus_namespace_name
          queueName    = custom_scale_rule.value.queue_name
          messageCount = tostring(custom_scale_rule.value.message_count)
        }
      }
    }
  }

  dynamic "ingress" {
    for_each = var.ingress_enabled ? [1] : []

    content {
      external_enabled = var.external_ingress
      target_port      = var.target_port
      transport        = "auto"

      traffic_weight {
        latest_revision = true
        percentage      = 100
      }
    }
  }
}
