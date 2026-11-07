variable "project_id" {
  description = "The GCP project to deploy into. The user creates the project and enables billing; Terraform never creates a project."
  type        = string
}

variable "region" {
  description = "Region for BigQuery, Firestore, and Cloud Run."
  type        = string
  default     = "us-central1"
}

variable "dataset_id" {
  description = "BigQuery dataset for the warehouse and traces."
  type        = string
  default     = "rca_agent"
}

variable "traces_dataset_id" {
  description = "BigQuery dataset for the traces table. Separate from the warehouse dataset: the runner can write traces here but only read the warehouse (see main.tf)."
  type        = string
  default     = "rca_ops"
}

variable "artifact_repository_id" {
  description = "Artifact Registry repository for the container image."
  type        = string
  default     = "rca-agent"
}

variable "firestore_database" {
  description = "Firestore database id (Native mode)."
  type        = string
  default     = "rca-agent"
}

variable "container_image" {
  description = "Container image for the Cloud Run service. Placeholder until Phase 8 builds the real image from backend/."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "run_invoker_email" {
  description = "Email allowed to invoke the Cloud Run service (roles/run.invoker). Empty means no invoker is granted — the service is IAM-protected and nobody can call it until an invoker is added."
  type        = string
  default     = ""
}

variable "vertex_model_id" {
  description = "Vertex AI model id for the agent. Verified 2026-11-07 against Google's pages: gemini-3.8-flash (GA, released 2026-09-02). Prices in us-central1: $0.825 input / $4.125 output per 1M tokens through 2026-12-31, then $1.65 / $8.25 from 2027-01-01."
  type        = string
  default     = "gemini-3.8-flash"
}

variable "max_bytes_per_query_gb" {
  description = "Per-query dry-run byte cap, in GB. Mirrors the in-app guardrail default (config.py)."
  type        = number
  default     = 1
}

variable "max_bytes_per_day_gb" {
  description = "Daily byte cap, in GB. Mirrors the in-app guardrail default (config.py)."
  type        = number
  default     = 10
}
