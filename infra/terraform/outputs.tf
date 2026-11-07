output "dataset_id" {
  description = "BigQuery dataset for the warehouse (read-only for the runner)."
  value       = google_bigquery_dataset.rca_agent.dataset_id
}

output "traces_dataset_id" {
  description = "BigQuery dataset for the traces table (separate from the warehouse)."
  value       = google_bigquery_dataset.rca_ops.dataset_id
}

output "artifact_registry_repository" {
  description = "Artifact Registry repository for the container image."
  value       = google_artifact_registry_repository.rca_agent.name
}

output "cloud_run_url" {
  description = "URL of the Cloud Run service."
  value       = google_cloud_run_v2_service.rca_agent.uri
}

output "runner_service_account" {
  description = "Service account the Cloud Run service runs as."
  value       = google_service_account.runner.email
}

output "deployer_service_account" {
  description = "Service account that applies Terraform and deploys Cloud Run."
  value       = google_service_account.deployer.email
}

output "firestore_database" {
  description = "Firestore database id."
  value       = google_firestore_database.rca_agent.name
}
