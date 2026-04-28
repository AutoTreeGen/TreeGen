output "imports_queue_name" {
  description = "Fully-qualified name of the imports queue."
  value       = google_cloud_tasks_queue.queue["imports"].name
}

output "hypotheses_queue_name" {
  description = "Fully-qualified name of the hypotheses queue."
  value       = google_cloud_tasks_queue.queue["hypotheses"].name
}

output "notifications_queue_name" {
  description = "Fully-qualified name of the notifications queue."
  value       = google_cloud_tasks_queue.queue["notifications"].name
}

output "queue_ids" {
  description = "Map of queue short name → fully-qualified resource name."
  value       = { for k, q in google_cloud_tasks_queue.queue : k => q.id }
}
