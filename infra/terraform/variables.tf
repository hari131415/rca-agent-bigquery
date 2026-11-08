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
  description = "Container image for the Cloud Run service. Use the image DIGEST (us-central1-docker.pkg.dev/<project>/rca-agent/rca-agent@sha256:<digest>), not :latest: the tag does not change when a new image is pushed, so Terraform sees no diff and never redeploys. See docs/phase8-runbook.md (Live 3-4)."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "run_invoker_email" {
  description = "Email allowed to invoke the Cloud Run service (roles/run.invoker). Empty means no invoker is granted — the service is IAM-protected and nobody can call it until an invoker is added."
  type        = string
  default     = ""
}

variable "vertex_model_id" {
  description = "Vertex AI model id for the agent. Verified 2026-11-07 against Google's pages: gemini-3.8-flash (GA, released 2026-09-02). Prices on the global endpoint: $0.75 input / $3.75 output per 1M tokens."
  type        = string
  default     = "gemini-3.8-flash"
}

variable "vertex_location" {
  description = "Vertex AI location for the model. gemini-3.8-flash is available ONLY on the global endpoint for this project (the us-central1 endpoint returned 404 in the first live run, 2026-11-08). Do not set this to a region."
  type        = string
  default     = "global"
}

variable "operator_email" {
  description = "Email of the person who runs the live eval in Cloud Shell. They are granted roles/iam.serviceAccountTokenCreator on the runner service account, so they can impersonate it for the eval (see docs/phase8-runbook.md, Live 5). The project Owner role does NOT include impersonation — without this grant, ADC impersonation fails. Empty means no grant."
  type        = string
  default     = ""
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

variable "deletion_protection" {
  description = "When true (the default), the BigQuery tables and the Cloud Run service cannot be deleted, and the Firestore database is kept on destroy (deletion_policy ABANDON). Set it to false in terraform.tfvars and apply before running terraform destroy, so the teardown actually removes everything. See docs/phase8-runbook.md (Teardown)."
  type        = bool
  default     = true
}
