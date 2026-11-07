# Terraform

Deploys the RCA agent's GCP resources: the BigQuery datasets and
tables, the runner and deployer service accounts, the Artifact
Registry repository, the Firestore database, and the Cloud Run
service. Flat layout, no modules — the whole deployment is `main.tf`,
`variables.tf`, `outputs.tf`, and `versions.tf`.

## Building and pushing the container image

The image is built from `backend/` (build context is the repo
root) and pushed to the Artifact Registry repository Terraform
creates (`<region>-docker.pkg.dev/<project>/rca-agent/rca-agent`).
The Dockerfile runs the API via `python -m rca_agent.serve`; the
entrypoint picks the wiring from the env vars (live when
GCP_PROJECT and VERTEX_MODEL_ID are set, offline otherwise). Docker
is not needed for the app, the tests, or CI; it is only needed to
build this image.

**Option A — Cloud Build (no local Docker):**

```
gcloud builds submit --config cloudbuild.yaml .
```

Run from the repo root. `gcloud builds submit` has no `-f` flag
for a Dockerfile, so the build is defined in `cloudbuild.yaml`
(the Dockerfile path and the image tag live there). Cloud Build
uses the repo as the build context, so the `backend/` paths in
the Dockerfile resolve. `.gcloudignore` controls what is
uploaded: it excludes `.git/`, `.venv/`, `.terraform/`, and
`*.tfvars`, but re-includes `data/offline.duckdb` (gitignored,
yet the Dockerfile copies it into the image).

**Option B — local docker build and push:**

```
docker build -f backend/Dockerfile -t <region>-docker.pkg.dev/<project>/rca-agent/rca-agent:latest .
docker push <region>-docker.pkg.dev/<project>/rca-agent/rca-agent:latest
```

Then set `container_image` in `terraform.tfvars` to the pushed
image (it defaults to the Cloud Run hello image) and apply:

```
container_image = "us-central1-docker.pkg.dev/<project>/rca-agent/rca-agent:latest"
```

The deployer service account pushes (it has
`artifactregistry.writer`); Cloud Run pulls as its own service
agent (Terraform grants it `artifactregistry.reader` on the
repository). The runner service account never touches the registry.

## What the user must do first

1. Create a GCP project and enable billing on it.
2. Enable the six APIs Terraform needs (or let Terraform enable
   them — `google_project_service` does that — but the project must
   exist first): BigQuery, Firestore, Cloud Run, Secret Manager,
   Vertex AI, Artifact Registry.
3. Authenticate Terraform with an account that can create service
   accounts and grant IAM roles in the project (usually the project
   owner, or the deployer service account once it exists).
4. Choose the values in `terraform.tfvars` (not committed — it holds
   your project id):
   ```
   project_id        = "your-project-id"
   run_invoker_email = "you@example.com"
   ```
   `vertex_model_id` stays empty until Phase 8 verifies the model id
   and prices against Google's pages. `container_image` is a
   placeholder (the Cloud Run hello image) until Phase 8 builds the
   real image from `backend/`.

## Apply / destroy

```
terraform init
terraform plan
terraform apply
```

`terraform destroy` removes everything Terraform created. It does
not disable the APIs (`disable_on_destroy = false`), and it does
not delete the project. Destroying the dataset deletes the
warehouse and the traces — there is no backup, so export anything
you want to keep first.

## What is deliberately not here

- **No secrets.** Vertex AI, BigQuery, and Firestore all use
  Application Default Credentials / workload identity. No secret
  resources are created and no secret value ever appears in
  Terraform.
- **No GCP budget alert.** The in-app caps (dry-run-first,
  per-query and daily byte caps) are the cost control; a GCP budget
  alert is a Phase 8 backstop.
- **No public access.** The Cloud Run service is IAM-protected:
  only `run_invoker_email` can call it. Opening it publicly is a
  deliberate one-line change (grant `roles/run.invoker` to
  `allUsers`) — do not do that silently.
- **No min instances.** The service scales to zero when idle, so
  there is no idle cost. The trade-off is that an idle instance is
  stopped and a background investigation can be killed; the startup
  recovery marks it failed instead of stuck. See
  `docs/gcp-resources.md` for the full reasoning.
