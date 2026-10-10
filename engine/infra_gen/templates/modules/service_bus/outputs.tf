output "namespace_id" {
  description = "Namespace resource id (scope for the Service Bus Data Owner role)."
  value       = azurerm_servicebus_namespace.this.id
}

output "namespace_name" {
  description = "Namespace name."
  value       = azurerm_servicebus_namespace.this.name
}

output "fqdn" {
  description = "Fully qualified namespace host name used by the Service Bus client."
  value       = "${azurerm_servicebus_namespace.this.name}.servicebus.windows.net"
}

output "queue_ids" {
  description = "Queue resource ids by queue name."
  value       = { for k, v in azurerm_servicebus_queue.this : k => v.id }
}

output "topic_ids" {
  description = "Topic resource ids by topic name."
  value       = { for k, v in azurerm_servicebus_topic.this : k => v.id }
}

output "subscription_ids" {
  description = "Subscription resource ids by <topic>/<subscription>."
  value       = { for k, v in azurerm_servicebus_subscription.this : k => v.id }
}
