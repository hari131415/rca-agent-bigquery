# GCP resources (Terraform scope)

This is the plan for `infra/terraform/` (Phase 7) and for the live
phase (Phase 8). Nothing here is created in Phase 1. The user creates
the GCP project and enables billing; the agent verifies, estimates,
and stops for approval before any spend.

## Project and region

- One GCP project, created by the user. Terraform takes the project
  ID as a variable; it never creates the project or the billing
  account.
- Region: `us-central1` for BigQuery, Firestore, and Cloud Run
  (single region keeps the demo simple; Vertex AI calls go to the
  same region). BigQuery dataset location: `US` (multi-region) unless
  Phase 7 finds a reason to pin it to `us-central1`.
- APIs the user enables before Phase 8 (documented in the Terraform
  README; Terraform does not enable APIs, so a missing API fails
  loudly at plan time instead of half-applying):
  `bigquery.googleapis.com`, `firestore.googleapis.com`,
  `run.googleapis.com`, `secretmanager.googleapis.com`,
  `aiplatform.googleapis.com`, `artifactregistry.googleapis.com`.

## BigQuery

- Dataset `rca_agent`, location `US`: the warehouse.
- Dataset `rca_ops`, location `US`: operations data — the `traces`
  table only. The traces table is deliberately NOT in the warehouse
  dataset (fixed in Phase 7, after the first Terraform draft).
- Tables in `rca_agent` (from `data/schemas/bigquery.sql`, the
  source of truth): `complaints`, `recalls`, `plants`, `lines`,
  `suppliers`, `builds`. The `traces` table lives in `rca_ops`
  (its columns match `services/traces.py`). No separate eval-results
  table; eval runs locally and writes a markdown report plus JSON
  under `eval/`.
- `complaints` partitioned by `received_date` (month) and clustered
  by `make, model_year` — decided finally in Phase 2 once the real
  columns and volumes are known. Partitioning is a cost control:
  investigations almost always filter by date and make.
- The agent's service account gets `roles/bigquery.jobUser` (run
  jobs) and `roles/bigquery.dataViewer` on the `rca_agent` dataset
  only. It cannot create datasets, delete tables, or read any other
  dataset. The loader uses a separate, short-lived credential (the
  user's own, or a deploy service account) — loading is not the
  agent's job.
- The runner gets `roles/bigquery.dataEditor` on `rca_ops` only, so
  it can write traces. Two independent layers prevent any write to
  the queried data: (1) the SQL guardrail — the agent's queries are
  parsed and allow-listed to `rca_agent` tables only, and the
  traces table is not in the allow-list, so the parser refuses any
  query against `rca_ops` (or any other dataset) before it runs;
  (2) IAM — the runner has only `dataViewer` on `rca_agent`, so
  even a query that slipped past the parser could not write, delete,
  or create a table there. Either layer alone is enough; together
  they are defense in depth. The SQL allow-list stays `rca_agent`
  only.
- Cost notes for Phase 8 to verify: BigQuery on-demand queries bill
  per TB scanned; dry runs are free and return bytes processed, which
  is why the guardrail dry-runs everything first. The in-app caps
  (per-query bytes, per-investigation queries, daily bytes) are the
  primary cost control; a GCP budget alert is a backstop, not the
  mechanism.

## Firestore (Native mode)

- One database, `rca-agent`, in the same region.
- Collections: `investigations` (one document per investigation:
  question, plan, hypotheses, queries, results summary, conclusions,
  timestamps, status) and `budgets` (one document per day: bytes
  processed, queries run — the daily cap ledger).
- Access: the agent's service account gets `roles/datastore.user`.
  No public access. Offline mode uses the local JSON/SQLite store
  instead; nothing in the app branches on Firestore availability at
  import time.

## Service accounts (least privilege)

| Account | Purpose | Roles |
| --- | --- | --- |
| `rca-agent-runner` | The Cloud Run service identity | `bigquery.jobUser`, `bigquery.dataViewer` (dataset `rca_agent` only), `bigquery.dataEditor` (dataset `rca_ops` only, for traces), `datastore.user`, `aiplatform.user` (project-scoped: the least-privilege role for calling a Vertex AI model — it allows prediction calls and nothing else) |
| `rca-agent-deployer` | Used by the user/CI to deploy, not by the app | `run.admin`, `iam.serviceAccountUser` (for the runner SA only), `artifactregistry.writer` |

No service account gets project-wide Editor/Owner. No keys are
created for the runner: Cloud Run uses workload identity (the
service's attached service account). If a key is ever needed for
local testing, the user creates it and it is never committed.

## Cloud Run

- Service `rca-agent`, from a container built from `backend/`
  (Dockerfile in Phase 7; the repo already pins Python 3.12).
- Max instances 1 for the demo; request timeout short (60s)
  because long investigations run asynchronously — the API
  returns 202 and the client polls, so no request waits on the
  model. Min instances is not set in the Terraform: it defaults
  to 0, and setting it to 0 explicitly showed drift on every
  plan (the API returns null for it, so Terraform read 0 ->
  null). The port is set explicitly (`container_port = 8000`):
  the app listens on 8000 and Cloud Run defaults to 8080 when no
  port is set, so without it the service started but never
  answered.
- The image is built by Cloud Build, which runs as the Compute
  Engine default service account
  (`<project-number>-compute@developer.gserviceaccount.com`,
  check with `gcloud builds get-default-service-account`) — not
  as the Cloud Build service agent
  (`service-<project-number>@cloudbuild.gserviceaccount.com`).
  The Artifact Registry writer grant is on the Compute Engine
  default account.
- CPU always allocated (instance-based billing): `cpu_idle = false`
  in the Terraform `google_cloud_run_v2_service` resource (the
  equivalent of `--no-cpu-throttling`).

  Why: an investigation runs in a FastAPI background task *after*
  the 202 response is sent. Under the default request-based
  billing, Cloud Run allocates CPU only while a request is in
  flight and throttles it to near zero after the response, so the
  background investigation would stall (or run so slowly it looks
  stalled). With CPU always allocated, the instance keeps its CPU
  for its whole lifetime and the background work makes progress.

  The trade-off: instance-based billing charges for every second
  the instance exists, including idle seconds — it turns the
  service from pay-per-request into pay-while-up. That is why min
  instances is 0 (no idle cost when the service has scaled to
  zero) and max instances is 1 (one instance at a time, which caps
  the cost and keeps the per-instance state — the in-memory query
  counts and the single background queue — consistent; there is no
  concurrent-instance race to reason about).

  The residual risk: CPU always allocated does *not* keep an
  instance alive forever. An idle instance is still stopped (about
  15 minutes of idleness, SIGTERM with a 10-second grace), and a
  background investigation is not a request, so the platform can
  scale the instance down while an investigation is still running
  and kill it. That failure is visible, not silent: the startup
  recovery marks the investigation "failed" ("interrupted by
  server restart") the next time the service starts, and the
  client sees a final answer instead of a stuck "running". For a
  production service the right answer would be min instances 1, or
  a proper job queue (Cloud Tasks invoking a worker endpoint)
  instead of background tasks in the request container — both are
  out of scope for this demo, and the choice is recorded here so
  Phase 8 does not have to rediscover it.
- Environment: no secrets in env vars. Configuration (project,
  dataset, model ID, caps) comes from env vars that are not secret;
  anything secret comes from Secret Manager or workload identity.
- Auth posture: IAM-protected by default (`run.invoker` only for the
  user). Opening it publicly is a deliberate one-line Terraform
  change, decided finally in Phase 7.

## Secret Manager

- Entries are created by Terraform but left empty-valued; values are
  set by the user, never by the agent and never in code. Expected
  entries: none required for Vertex AI, BigQuery, or Firestore, all
  of which use Application Default Credentials / workload identity.
  The entries exist so that if a non-ADC secret is ever needed (e.g.,
  a third-party API key for a future source), there is exactly one
  place for it. Phase 7 documents this as "no secrets required" if
  that is still true then.

## Model and embeddings (verified in Phase 8)

- LLM: Gemini on Vertex AI, model `gemini-3.8-flash` (GA, released
  2026-09-02, retirement 2027-09-02 or later, 1,048,576-token
  context, structured output supported). The model is available
  ONLY on the `global` location — us-central1 returned 404 in the
  first live run (2026-11-08). The app, the eval, and Terraform
  all take `VERTEX_LOCATION` (default `global`). Global prices:
  $0.75 input / $3.75 output per 1M tokens through 2026-12-31,
  then $1.50 / $7.50. Sources: the Vertex AI model list and
  pricing pages, recorded in `backend/src/rca_agent/config.py`.
- Real cost, measured 2026-11-08: $0.06–0.18 per question —
  thinking tokens dominate (a 12-character JSON reply used 152
  output tokens). Test set (17) ≈ $1–3; all 34 ≈ $2–6. The eval
  enforces a hard cost cap (`--max-cost-usd`, default 3.00). A
  thinking budget is available as `VERTEX_THINKING_BUDGET`
  (verified against google-genai 3.0.0: `thinking_config` /
  `thinking_budget`); it is off by default, because a low budget
  can hurt the SQL the model writes.
- Embeddings (optional semantic recall, off by default): NOT
  verified — the embedding model was never checked against a
  Google source. Recall stays a stub until that happens.

## Cost controls (in-app first, GCP second)

1. Dry run before every query; refuse over the byte cap (free to
   check, so bad queries cost $0).
2. Per-investigation query cap and daily byte cap, enforced in code,
   ledgered in Firestore (`budgets` collection).
3. GCP budget alert (user sets the threshold in the console; the plan
   suggests $10 warning / $25 stop-review, final number in Phase 8
   with the estimate).
4. BigQuery custom quota is noted as an option and deliberately not
   set by default — it can break legitimate investigations in a way
   the in-app caps handle more gracefully.

## What Phase 8 needs from the user

1. A GCP project ID with billing enabled.
2. The six APIs above enabled.
3. Credentials for Terraform to apply (the user runs apply with
   their own credentials, or provides a short-lived deploy credential
   — their call in Phase 8).
4. Approval of the cost estimate before any spend: model ID + prices
   verified, BigQuery scan estimate for the full load and for a
   typical investigation, and the cap values that bound the total.
