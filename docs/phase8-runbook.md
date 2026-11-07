# Phase 8 runbook — run in Google Cloud Shell

Run everything in Cloud Shell (the browser terminal). It is already
authenticated as you, and it has `terraform`, `gcloud`, and `python3`.
Do not install anything locally. Each step is one copy-paste block,
with the expected output and what to record. Stop after
each step if anything looks different from the expected output.

Project: `rca-agent-portfolio` (project number 958337524782).
Billing: free trial ($300 credit), budget alert already set at $10.
Invoker: <your-email>.

**Total expected cost for all of Phase 8 (infra + build + load +
deploy + live eval): about $1–3, well under the $10 budget alert.**
Every step below lists its own expected cost. Nothing in steps 0–6
bills more than a few cents; the only real spend is the live eval
(step 7), which does not run until you approve the estimate.

## Step 0 — Upload the bundle and clone it

In Cloud Shell, use the Upload menu (the three-dot menu, top right)
to upload `rca-agent-bigquery.bundle`, then:

```bash
git clone rca-agent-bigquery.bundle rca-agent-bigquery
cd rca-agent-bigquery
git log --oneline
```

Expected: a list of commits ending in a `fix:` commit for the
Terraform fixes. **Record:** the `git log` output, or just
"cloned OK" if it matches. Cost: $0.

## Step 1 — Create the Terraform state bucket

Terraform state lives in a GCS bucket, not in Cloud Shell. Cloud
Shell's home directory persists, but it is a 5 GB disk on a VM that
can be recycled, and the state file is the only record of what
Terraform created — losing it means Terraform can no longer manage
(or destroy) the resources. The bucket holds only the state file (a
few KB).

```bash
gsutil mb -p rca-agent-portfolio -l US gs://rca-agent-portfolio-tfstate
```

Expected: `Creating gs://rca-agent-portfolio-tfstate/...`. If the
name is taken (bucket names are globally unique), pick another name
and update it in `infra/terraform/backend.tf` before the next step.
**Record:** the output. Cost: ~$0.00 (a few KB at $0.02/GB/month).

## Step 2 — Enable the APIs

```bash
gcloud services enable bigquery.googleapis.com firestore.googleapis.com run.googleapis.com secretmanager.googleapis.com aiplatform.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com --project=rca-agent-portfolio
```

Expected: no output on success (or a short confirmation). **Send
back:** "APIs enabled", or any error. Cost: $0.

## Step 3 — Terraform apply (create the infrastructure)

```bash
cat > infra/terraform/terraform.tfvars <<'EOF'
project_id        = "rca-agent-portfolio"
run_invoker_email = "<your-email>"
EOF
cd infra/terraform
terraform init
terraform plan
terraform apply
```

`terraform.tfvars` holds your project id and email. It is
gitignored — it stays in Cloud Shell and is never committed.

Expected: `init` downloads the Google provider and configures the
GCS backend (it will say the backend was initialized); `plan` shows
the resources to create (datasets, tables, service accounts,
Firestore, Cloud Run, Artifact Registry, the APIs); `apply` asks
"Do you want to perform these actions?" — type `yes`. It then
creates everything and prints an `Outputs:` block (dataset ids,
Cloud Run URL, the runner and deployer service account emails, the
Artifact Registry repository, the Firestore database). **Record:**
the full `terraform apply` output, especially the `Outputs:` block.
Cost: $0 (no billable resource while idle — the datasets and tables
are empty, Firestore is unused, Cloud Run is at min 0 with the
placeholder image, the Artifact Registry repo is empty).

## Step 4 — Build the warehouse, then build and push the image

Back at the repo root. The project needs Python 3.12 (the package
declares `requires-python >=3.12`, so the install below fails on an
older Python). Check first:

```bash
python3 --version
```

Expected: `Python 3.12.x`. If it shows a different version, check
for a 3.12 binary:

```bash
python3.12 --version
```

If that prints `Python 3.12.x`, use `python3.12` in place of
`python3` in the commands below. If neither is 3.12, install it
(Cloud Shell allows `sudo apt`):

```bash
sudo apt-get update && sudo apt-get install -y python3.12 python3.12-venv
```

then use `python3.12`. If the install fails (no 3.12 package for
the Cloud Shell image), stop and record the `python3 --version`
output — the venv step below will not work on an older Python.

The Dockerfile copies the offline warehouse into the image, so
build it first from the committed sample CSVs (no download):

```bash
cd ~/rca-agent-bigquery
python3 -m venv backend/.venv
backend/.venv/bin/pip install -e "backend[dev]"
backend/.venv/bin/python scripts/seed_offline.py --from-sample
```

Expected: the pattern checks print `PASS` and the last line reads
`offline warehouse ready: .../data/offline.duckdb`. **Record:**
the seed output. Cost: $0.

Then build and push with Cloud Build (no local Docker needed).
The Cloud Build service account's permission to push to the
Artifact Registry repository is granted by Terraform (applied in
step 3) — on a new project it cannot push without that grant, and
the build would fail on the push step.

```bash
gcloud builds submit --tag us-central1-docker.pkg.dev/rca-agent-portfolio/rca-agent/rca-agent:latest -f backend/Dockerfile .
```

Run from the repo root (the `.` at the end is the build context).
Expected: build logs, ending with `PUSH` and `DONE`, plus the image
digest (a `sha256:...` line). **Record:** the last ~10 lines
(the pushed image name and digest). Cost: $0 (Cloud Build free tier
is 120 build-minutes/day; this build takes ~2–5 minutes). Storing
the image costs ~$0.02–0.05/month for a ~200–500 MB image.

## Step 5 — Load the data into BigQuery

```bash
backend/.venv/bin/pip install -e "backend[dev,gcp]"
backend/.venv/bin/python scripts/load_to_bigquery.py --project rca-agent-portfolio
```

Expected, one line per table:

```
complaints: 15814 rows -> rca-agent-portfolio.rca_agent.complaints
recalls: 841 rows -> rca-agent-portfolio.rca_agent.recalls
plants: 4 rows -> rca-agent-portfolio.rca_agent.plants
lines: 8 rows -> rca-agent-portfolio.rca_agent.lines
suppliers: 4 rows -> rca-agent-portfolio.rca_agent.suppliers
builds: 576 rows -> rca-agent-portfolio.rca_agent.builds
total: 17247 rows
```

**Record:** the output. Cost: $0 (load jobs are free; the
warehouse is ~5 MB, within BigQuery's 10 GB/month free storage).

Verify the load:

```bash
bq query --project_id=rca-agent-portfolio --use_legacy_sql=false "SELECT COUNT(*) AS n FROM rca-agent-portfolio.rca_agent.complaints"
```

Expected: `n = 15814`. **Record:** the count. Cost: $0 (the
query scans ~4 MB; the first 1 TB/month is free).

## Step 6 — Deploy (point Cloud Run at the built image)

Add the image to `terraform.tfvars` and re-apply:

```bash
cat >> infra/terraform/terraform.tfvars <<'EOF'
container_image = "us-central1-docker.pkg.dev/rca-agent-portfolio/rca-agent/rca-agent:latest"
EOF
cd infra/terraform
terraform apply
```

Expected: `apply` updates the Cloud Run service (a new revision
with the image). Type `yes` at the prompt. **Record:** the
`apply` output and the Cloud Run URL. Cost: $0 while idle.

Verify the service answers (it is IAM-protected, so the request
needs your identity token):

```bash
URL=$(terraform output -raw cloud_run_url)
curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" "$URL/health"
```

Expected: `{"status":"ok"}`. **Record:** the curl output.
Cost: $0 (a few requests, within Cloud Run's free tier).

Smoke-test one investigation (the deployed image runs the offline
API — the DuckDB warehouse is inside the image — as a smoke test;
the live wiring is swapped in after the cost estimate is approved):

```bash
ID=$(curl -s -X POST -H "Authorization: Bearer $(gcloud auth print-identity-token)" -H "Content-Type: application/json" -d '{"question":"Why did brake complaints spike for the 2021 F-150?"}' "$URL/investigations" | python3 -c "import sys,json; print(json.load(sys.stdin)['investigation_id'])")
sleep 10
curl -s -H "Authorization: Bearer $(gcloud auth print-identity-token)" "$URL/investigations/$ID" | python3 -m json.tool | head -30
```

Expected: the investigation comes back `"status": "done"` and the
answer mentions Riverside. **Record:** the answer excerpt.
Cost: $0.

## Step 7 — Model verification and cost estimate (verified in advance)

Already done in advance (2026-11-07), so you have the numbers
before you run anything. You do not run a command here.

**Model id (verified against Google's own pages):**
`gemini-3.8-flash` — Generally Available, released 2026-09-02,
retirement 2027-09-02 or later, 1,048,576-token context, structured
output supported (the agent needs JSON). Sources:
- https://cloud.google.com/vertex-ai/generative-ai/docs/models
- https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-8-flash
- https://cloud.google.com/vertex-ai/generative-ai/pricing

**Prices (us-central1, the project's region):** $0.825 input /
$4.125 output per 1M tokens through 2026-12-31, then $1.65 / $8.25
from 2027-01-01. (The Global-endpoint prices are lower, $0.75 /
$3.75, but the project's region uses the non-global endpoint, so
the estimate uses the higher non-global prices.)

**Cost estimate for the live eval (the only real spend):** each
investigation makes ~4–6 model calls; the SQL and analyze prompts
are the big ones (~2,000–4,000 input tokens each, mostly the schema
and the evidence rows). Estimated per investigation: ~10,000–20,000
input tokens and ~2,000–5,000 output tokens (including thinking
tokens, which are billed as output).

- Test set (17 questions): ~170,000–340,000 input tokens
  ($0.14–0.28) + ~34,000–85,000 output tokens ($0.14–0.35)
  = **about $0.30–0.60**.
- All 34 questions (dev + test): **about $0.60–1.25**.
- With a 2x safety factor (longer prompts, retries, a follow-up or
  two): **about $1.25–2.50**.

BigQuery for the eval: $0 (the eval queries scan a few MB each;
the first 1 TB/month is free, and dry runs are free). Cloud Run:
$0 while idle. **Total for all of Phase 8: about $1–3.**

**STOP. The live eval does not run until you approve this
estimate.** After steps 0–6, record the outputs. The live wiring
(Vertex LLM, BigQuery warehouse, Firestore store, BigQuery traces
in `rca_ops`) is written next; then rebuild and redeploy the image
(repeat step 4 with the updated code), and the live eval runs on
the held-out test set — after the cost estimate is approved.

## Teardown (after the eval)

When the eval is done and you want nothing left billing:

```bash
cd infra/terraform
terraform destroy
```

Expected: `destroy` lists everything it will remove and asks for
confirmation — type `yes`. It deletes the datasets (the warehouse
and the traces), the service accounts, the Firestore database, the
Cloud Run service, and the Artifact Registry repository (and its
images). **Record:** the destroy output. Cost after: $0.

The GCS state bucket is not managed by Terraform (you created it
with `gsutil` in step 1), so `destroy` does not delete it. Delete
it separately, only after `destroy` has finished and you no longer
need the state:

```bash
gsutil rm -r gs://rca-agent-portfolio-tfstate
```

Do not delete the bucket before `destroy` — Terraform needs the
state file to know what to remove. After teardown, the only thing
left is the $300 free-trial credit minus the ~$1–3 spent on the
eval.
