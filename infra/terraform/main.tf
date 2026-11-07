# RCA agent infrastructure. Flat layout, no modules (a deliberate
# choice for a demo: the whole deployment is one file you can read
# top to bottom). Nothing here creates a GCP project, a billing
# account, or any secret — the user creates the project, enables
# billing, and enables the APIs first (see README.md).
#
# The in-app guardrails (sqlglot parsing, dry-run caps, the daily
# byte ledger) are the first line of defence; the service account
# below is the real security boundary (see docs/guardrails.md):
# it can read only the rca_agent dataset and run only jobs, so even
# a query that slipped past the parser could not write, delete, or
# reach another dataset.

locals {
  apis = [
    "bigquery.googleapis.com",
    "firestore.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# --- BigQuery ---

resource "google_bigquery_dataset" "rca_agent" {
  project     = var.project_id
  dataset_id  = var.dataset_id
  location    = "US"
  description = "RCA agent warehouse: NHTSA complaints and recalls, synthetic plant data, and traces."

  depends_on = [google_project_service.apis]
}

resource "google_bigquery_dataset" "rca_ops" {
  project     = var.project_id
  dataset_id  = var.traces_dataset_id
  location    = "US"
  description = "RCA agent operations data: the traces table only. Separate from the warehouse so the runner can write traces without any write access to the queried data."

  depends_on = [google_project_service.apis]
}

resource "google_bigquery_table" "complaints" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.rca_agent.dataset_id
  table_id   = "complaints"

  # Partitioned by the date the complaint was received (ldate) and
  # clustered by make/model_year — the filters nearly every
  # investigation uses. Matches data/schemas/bigquery.sql.
  time_partitioning {
    type  = "DAY"
    field = "ldate"
  }
  clustering = ["make", "model_year"]

  schema = jsonencode([
    { name = "cmplid", type = "STRING" },
    { name = "odino", type = "STRING" },
    { name = "mfr_name", type = "STRING" },
    { name = "make", type = "STRING" },
    { name = "model", type = "STRING" },
    { name = "model_year", type = "INT64" },
    { name = "crash", type = "BOOL" },
    { name = "faildate", type = "DATE" },
    { name = "fire", type = "BOOL" },
    { name = "injured", type = "INT64" },
    { name = "deaths", type = "INT64" },
    { name = "compdesc", type = "STRING" },
    { name = "state", type = "STRING" },
    { name = "datea", type = "DATE" },
    { name = "ldate", type = "DATE" },
    { name = "miles", type = "INT64" },
    { name = "occurrences", type = "INT64" },
    { name = "cdescr", type = "STRING" },
    { name = "cmpl_type", type = "STRING" },
    { name = "prod_type", type = "STRING" },
    { name = "medical_attn", type = "BOOL" },
    { name = "vehicles_towed_yn", type = "BOOL" },
    { name = "state_of_incident", type = "STRING" },
    { name = "is_synthetic", type = "BOOL", mode = "REQUIRED" },
    { name = "plant_id", type = "STRING" },
    { name = "line_id", type = "STRING" },
    { name = "supplier_id", type = "STRING" },
  ])
}

resource "google_bigquery_table" "recalls" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.rca_agent.dataset_id
  table_id   = "recalls"

  schema = jsonencode([
    { name = "record_id", type = "INT64" },
    { name = "campno", type = "STRING" },
    { name = "make", type = "STRING" },
    { name = "model", type = "STRING" },
    { name = "model_year", type = "INT64" },
    { name = "mfgcampno", type = "STRING" },
    { name = "compname", type = "STRING" },
    { name = "mfgname", type = "STRING" },
    { name = "bgman", type = "DATE" },
    { name = "endman", type = "DATE" },
    { name = "rcltypecd", type = "STRING" },
    { name = "potaff", type = "INT64" },
    { name = "odate", type = "DATE" },
    { name = "influenced_by", type = "STRING" },
    { name = "mfgtxt", type = "STRING" },
    { name = "rcdate", type = "DATE" },
    { name = "datea", type = "DATE" },
    { name = "rpno", type = "STRING" },
    { name = "fmvss", type = "STRING" },
    { name = "desc_defect", type = "STRING" },
    { name = "conequence_defect", type = "STRING" },
    { name = "corrective_action", type = "STRING" },
    { name = "notes", type = "STRING" },
    { name = "rcl_cmpt_id", type = "STRING" },
    { name = "mfr_comp_name", type = "STRING" },
    { name = "mfr_comp_desc", type = "STRING" },
    { name = "mfr_comp_ptno", type = "STRING" },
    { name = "do_not_drive", type = "STRING" },
    { name = "park_outside", type = "STRING" },
  ])
}

resource "google_bigquery_table" "plants" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.rca_agent.dataset_id
  table_id   = "plants"

  schema = jsonencode([
    { name = "plant_id", type = "STRING", mode = "REQUIRED" },
    { name = "name", type = "STRING" },
    { name = "state", type = "STRING" },
  ])
}

resource "google_bigquery_table" "lines" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.rca_agent.dataset_id
  table_id   = "lines"

  schema = jsonencode([
    { name = "line_id", type = "STRING", mode = "REQUIRED" },
    { name = "plant_id", type = "STRING", mode = "REQUIRED" },
    { name = "product", type = "STRING" },
  ])
}

resource "google_bigquery_table" "suppliers" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.rca_agent.dataset_id
  table_id   = "suppliers"

  schema = jsonencode([
    { name = "supplier_id", type = "STRING", mode = "REQUIRED" },
    { name = "name", type = "STRING" },
    { name = "part", type = "STRING" },
  ])
}

resource "google_bigquery_table" "builds" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.rca_agent.dataset_id
  table_id   = "builds"

  schema = jsonencode([
    { name = "build_id", type = "STRING", mode = "REQUIRED" },
    { name = "make", type = "STRING" },
    { name = "model", type = "STRING" },
    { name = "model_year", type = "INT64" },
    { name = "plant_id", type = "STRING" },
    { name = "line_id", type = "STRING" },
    { name = "build_date", type = "DATE" },
    { name = "supplier_brake", type = "STRING" },
    { name = "supplier_steering", type = "STRING" },
    { name = "supplier_airbag", type = "STRING" },
    { name = "vehicles_built", type = "INT64" },
  ])
}

resource "google_bigquery_table" "traces" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.rca_ops.dataset_id
  table_id   = "traces"

  # The traces table lives in rca_ops, not in the warehouse dataset:
  # the runner writes traces here and only reads rca_agent (see the
  # IAM section below). Columns match services/traces.py
  # TRACE_COLUMNS. created_at is a
  # TIMESTAMP; the trace store writes an RFC 3339 string, which
  # BigQuery accepts for a TIMESTAMP field.
  schema = jsonencode([
    { name = "investigation_id", type = "STRING" },
    { name = "step", type = "STRING" },
    { name = "prompt", type = "STRING" },
    { name = "sql", type = "STRING" },
    { name = "dry_run_bytes", type = "INT64" },
    { name = "result_rows", type = "INT64" },
    { name = "tokens_in", type = "INT64" },
    { name = "tokens_out", type = "INT64" },
    { name = "cost_usd", type = "FLOAT64" },
    { name = "latency_ms", type = "FLOAT64" },
    { name = "created_at", type = "TIMESTAMP" },
  ])
}

# --- Service accounts ---

resource "google_service_account" "runner" {
  project      = var.project_id
  account_id   = "rca-agent-runner"
  display_name = "RCA agent runner (Cloud Run service identity)"
}

# The runner can run BigQuery jobs and read only this dataset. It
# cannot write, delete, or create tables, and it cannot read any
# other dataset — that is the real security boundary.
resource "google_project_iam_member" "runner_bigquery_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_bigquery_dataset_iam_member" "runner_dataset_viewer" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.rca_agent.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_bigquery_dataset_iam_member" "runner_ops_editor" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.rca_ops.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_project_iam_member" "runner_firestore_user" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.runner.email}"
}

# The runner calls Vertex AI (Gemini) to plan, write SQL, and
# analyze. roles/aiplatform.user is the least-privilege role for
# invoking a Vertex AI model: it allows prediction calls and nothing
# else — it cannot create or delete endpoints, models, or datasets.
# Vertex AI roles are project-scoped (there is no dataset-level
# scoping for a model), so this grant is on the project.
resource "google_project_iam_member" "runner_aiplatform_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_service_account" "deployer" {
  project      = var.project_id
  account_id   = "rca-agent-deployer"
  display_name = "RCA agent deployer (applies Terraform, deploys Cloud Run)"
}

resource "google_project_iam_member" "deployer_run_admin" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

# The deployer may act as the runner service account — and only
# that account — when deploying the Cloud Run service.
resource "google_service_account_iam_member" "deployer_act_as_runner" {
  service_account_id = google_service_account.runner.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_project_iam_member" "deployer_artifact_registry_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

# --- Artifact Registry ---

resource "google_artifact_registry_repository" "rca_agent" {
  project       = var.project_id
  location      = var.region
  repository_id = var.artifact_repository_id
  format        = "DOCKER"
  description   = "Container images for the RCA agent Cloud Run service."

  depends_on = [google_project_service.apis]
}

# Cloud Run pulls the image as its service agent
# (service-<project-number>@serverless-robot-prod.iam.gserviceaccount.com),
# not as the runner service account, so the service agent needs read
# access to the repository. The deployer pushes (it already has
# artifactregistry.writer); the runner never touches the registry.
data "google_project" "current" {
  project_id = var.project_id
}

resource "google_artifact_registry_repository_iam_member" "cloud_run_reader" {
  project    = var.project_id
  location   = google_artifact_registry_repository.rca_agent.location
  repository = google_artifact_registry_repository.rca_agent.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:service-${data.google_project.current.number}@serverless-robot-prod.iam.gserviceaccount.com"
}

# Cloud Build pushes the image to this repository, and on a new
# project its service account cannot do that by default — the build
# fails with a permission error on the push. Grant it
# artifactregistry.writer on this repository only (not project-wide,
# and not the deployer's broader writer role). The deployer still
# pushes with its own writer grant; this one is only for Cloud Build.
resource "google_artifact_registry_repository_iam_member" "cloud_build_writer" {
  project    = var.project_id
  location   = google_artifact_registry_repository.rca_agent.location
  repository = google_artifact_registry_repository.rca_agent.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:service-${data.google_project.current.number}@cloudbuild.gserviceaccount.com"
}

# --- Firestore ---

resource "google_firestore_database" "rca_agent" {
  project                     = var.project_id
  name                        = var.firestore_database
  location_id                 = var.region
  type                        = "FIRESTORE_NATIVE"
  concurrency_mode            = "OPTIMISTIC"
  app_engine_integration_mode = "DISABLED"

  depends_on = [google_project_service.apis]
}

# The collections (investigations, budgets) are not created here:
# Firestore creates a collection on its first write, and the app
# writes both. Creating empty collections in Terraform would be
# dead configuration.

# --- Cloud Run ---

resource "google_cloud_run_v2_service" "rca_agent" {
  project  = var.project_id
  name     = "rca-agent"
  location = var.region

  template {
    service_account = google_service_account.runner.email

    # One request at a time per instance: the background investigation
    # queue is per-instance, so concurrency above 1 would let a
    # second request start while the first investigation is still
    # running in the background.
    max_instance_request_concurrency = 1

    containers {
      image = var.container_image

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        # CPU always allocated (instance-based billing). Investigations
        # run as FastAPI background tasks after the 202 response, and
        # under the default request-based billing Cloud Run throttles
        # CPU to near zero after the response, so the background work
        # would stall. See docs/gcp-resources.md for the trade-off:
        # this bills for every second the instance exists, which is
        # why min instances is 0 and max instances is 1.
        cpu_idle = false
      }

      env {
        name  = "GCP_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GCP_REGION"
        value = var.region
      }
      env {
        name  = "BIGQUERY_DATASET"
        value = var.dataset_id
      }
      env {
        name  = "FIRESTORE_DATABASE"
        value = var.firestore_database
      }
      env {
        name  = "VERTEX_MODEL_ID"
        value = var.vertex_model_id
      }
      env {
        name  = "RCA_MAX_BYTES_PER_QUERY_GB"
        value = tostring(var.max_bytes_per_query_gb)
      }
      env {
        name  = "RCA_MAX_BYTES_PER_DAY_GB"
        value = tostring(var.max_bytes_per_day_gb)
      }
    }

    scaling {
      # 0 keeps the cost at zero when idle; 1 caps the cost and keeps
      # the per-instance state (the query counts, the background
      # queue) consistent. The residual risk is scale-to-zero killing
      # a running investigation — the startup recovery marks it
      # failed instead of stuck. See docs/gcp-resources.md.
      min_instance_count = 0
      max_instance_count = 1
    }

    # Short: no request waits on an investigation. The API returns
    # 202 and the client polls.
    timeout = "60s"
  }

  depends_on = [google_project_service.apis]
}

# IAM-protected by default: only the invoker named below can call
# the service. Opening it publicly is a deliberate one-line change
# (grant roles/run.invoker to allUsers) — do not do that silently.
resource "google_cloud_run_v2_service_iam_member" "invoker" {
  count    = var.run_invoker_email != "" ? 1 : 0
  project  = var.project_id
  location = google_cloud_run_v2_service.rca_agent.location
  name     = google_cloud_run_v2_service.rca_agent.name
  role     = "roles/run.invoker"
  member   = "user:${var.run_invoker_email}"
}

# --- Secret Manager ---
#
# No secret resources are created: Vertex AI, BigQuery, and
# Firestore all use Application Default Credentials / workload
# identity, so there is nothing to store. The secretmanager API is
# enabled above in case a non-ADC secret is ever needed; if one is,
# add a google_secret_manager_secret here and grant the runner
# roles/secretmanager.secretAccessor on that secret only — never
# project-wide, and never with a value in Terraform.
