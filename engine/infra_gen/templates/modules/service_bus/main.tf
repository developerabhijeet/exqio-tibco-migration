terraform {
  required_version = ">= 1.6.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.69"
    }
  }
}

locals {
  queues = { for q in var.queues : q.name => q }
  topics = { for t in var.topics : t.name => t }
  # Subscription entities are keyed "<topic>/<subscription>" so two topics can share a name.
  subscriptions = { for s in var.subscriptions : "${s.topic}/${s.name}" => s }
}

resource "azurerm_servicebus_namespace" "this" {
  name                          = var.name
  resource_group_name           = var.resource_group_name
  location                      = var.location
  sku                           = var.sku
  capacity                      = var.sku == "Premium" ? var.capacity : 0
  minimum_tls_version           = "1.2"
  local_auth_enabled            = var.local_auth_enabled
  public_network_access_enabled = true
  tags                          = var.tags
}

resource "azurerm_servicebus_queue" "this" {
  for_each = local.queues

  name                                 = each.value.name
  namespace_id                         = azurerm_servicebus_namespace.this.id
  max_delivery_count                   = each.value.max_delivery_count
  lock_duration                        = each.value.lock_duration
  default_message_ttl                  = each.value.default_message_ttl
  dead_lettering_on_message_expiration = each.value.dead_lettering_on_message_expiration
  requires_duplicate_detection         = each.value.requires_duplicate_detection
}

resource "azurerm_servicebus_topic" "this" {
  for_each = local.topics

  name                         = each.value.name
  namespace_id                 = azurerm_servicebus_namespace.this.id
  default_message_ttl          = each.value.default_message_ttl
  requires_duplicate_detection = each.value.requires_duplicate_detection
  support_ordering             = each.value.support_ordering

  lifecycle {
    precondition {
      condition     = var.sku != "Basic"
      error_message = "Topics need the Standard or Premium Service Bus SKU."
    }
  }
}

resource "azurerm_servicebus_subscription" "this" {
  for_each = local.subscriptions

  name                                 = each.value.name
  topic_id                             = azurerm_servicebus_topic.this[each.value.topic].id
  max_delivery_count                   = each.value.max_delivery_count
  lock_duration                        = each.value.lock_duration
  default_message_ttl                  = each.value.default_message_ttl
  dead_lettering_on_message_expiration = each.value.dead_lettering_on_message_expiration
}
