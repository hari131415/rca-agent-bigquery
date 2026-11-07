# Phase 8 runbook — live steps (run in Google Cloud Shell)

Steps 0–6 are **done** (2026-11-07, in Cloud Shell): the bundle was
cloned, the state bucket created, the APIs enabled, Terraform
applied, the image built with Cloud Build and deployed, the data
loaded (17,247 rows; complaints 7,108 real + 8,706 synthetic), and
the service answered `/health` with `{"status":"ok"}`. The smoke
test passed: status done, Riverside 4.14 vs 1.22, verdict
supplier/acme-brakes. Cloud Run URL:
`https://rca-agent-hao5ceoedq-uc.a.run.app`.

This runbook covers the live steps only: the fixes found while
running steps 0–6, the live wiring, and the live eval. The
Terraform state is in the bucket
(`gs://rca-agent-portfolio-tfstate`), so the new bundle below
replaces the code only — the state is untouched.

Project: `rca-agent-portfolio` (project number 958337524782).
Billing: free trial ($300 credit), budget alert already set at $10.
Invoker: <your-email>.

**Cost estimate for the live eval (approved, hard cap $3.00):**
test set (17 questions) ~$0.30–0.60, all 34 ~$0.60–1.25, 2x safety
~$1.25–2.50. The eval enforces a hard cap of $3.00 total in code
(`--max-cost-usd 3.00`): it stops cleanly when the cumulative
model cost reaches it, grades the questions already run, and lists
the rest as skipped. BigQuery for the eval is $0 (a few MB per
query; the first 1 TB/month is free, dry runs are free). Cloud Run
is $0 while idle. **Total for all of Phase 8: about $1–3.**

## What changed since steps 0–6

These are the fixes found while running steps 0–6, all in this
bundle. The repo and this runbook were updated for each one.

1. **Terraform is not preinstalled in Cloud Shell.** Live 2 starts
   with `terraform version`; if it is not found, install 1.13.3
   into `~/bin` (the commands are there).
2. **Two more APIs are needed.** `terraform plan` failed without
   `cloudresourcemanager.googleapis.com` and
   `iam.googleapis.com`. They are now in the Terraform API list
   (and in the `gcloud services enable` command, if you ever
   re-run it).
3. **The Cloud Build writer member was wrong.**
   `service-<n>@cloudbuild.gserviceaccount.com` does not exist as
   a member you can grant to. The build runs as the Compute
   Engine default service account,
   `<project-number>-compute@developer.gserviceaccount.com`
   (958337524782-compute@developer.gserviceaccount.com here).
   Check it with `gcloud builds get-default-service-account
   --project=rca-agent-portfolio`. The Terraform grant now uses
   that account.
4. **`gcloud builds submit` has no `-f` flag.** The build is now
   defined in `cloudbuild.yaml` and run with `gcloud builds
   submit --config cloudbuild.yaml .`. A `.gcloudignore` controls
   what is uploaded: it excludes `.git/`, `.venv/`,
   `.terraform/`, and `*.tfvars`, but re-includes
   `data/offline.duckdb` (gitignored, yet the Dockerfile copies
   it into the image).
5. **Port mismatch.** The app listens on 8000, but Cloud Run
   defaulted to 8080. The Terraform now sets `container_port =
   8000`, and `serve.py` reads Cloud Run's `PORT` env var first,
   then `RCA_PORT`, then 8000.
6. **Scaling drift.** The `scaling` block with
   `min_instance_count = 0` showed drift on every plan (the API
   returns null for it, so Terraform read 0 -> null). The
   `min_instance_count` line is removed (it defaults to 0);
   `max_instance_count = 1` stays. Plans are clean now.
7. **Teardown would have failed.** The BigQuery tables and the
   Cloud Run service have `deletion_protection = true`, and the
   Firestore database had `deletion_policy = ABANDON` (destroy
   would not delete it). There is now a `deletion_protection`
   variable (default true): set it to false in `terraform.tfvars`
   and apply before `terraform destroy`, and the teardown
   removes everything. The teardown below also deletes the Cloud
   Build bucket `gs://rca-agent-portfolio_cloudbuild` (created by
   Cloud Build, not managed by Terraform).

**New bug found in the smoke test, fixed in this bundle.** The
verdict said supplier/acme-brakes, but the only supporting query
grouped by plant, so no supporting row contained the supplier.
There is now a verdict-grounding check
(`graph/validate.py:check_verdict_grounding`): the verdict entity
must appear in a supporting query's result rows for the matching
dimension (plant_id, supplier_id, line_id, campno, or make+model
for volume). Otherwise the verdict is marked "(unverified)" in
the answer and counted in the eval (`unverified_verdicts`).
Mock baseline with the check: test set 82.3% (14/17, same as
before) with **4 unverified verdicts (T01, T03, T05, T06 — all
P1)**; dev set 100% with 0 unverified verdicts. The check is the
point: T01's verdict is right but ungrounded, and the eval now
says so instead of silently passing it.

**Live wiring (in this bundle).** `llm/vertex.py` (Gemini on
Vertex AI via the google-genai SDK, model `gemini-3.8-flash`,
prices $0.825 input / $4.125 output per 1M in us-central1;
thinking tokens billed as output), `create_live_app` (BigQuery
warehouse, Vertex LLM, Firestore store for investigations and
the daily byte ledger, BigQuery traces in `rca_ops`), and the
eval live mode (`--llm vertex --warehouse bigquery`). The
container entrypoint (`serve.py`) picks the wiring from the env
vars: with `GCP_PROJECT` and `VERTEX_MODEL_ID` set it serves
the live app, without them the offline app. One wiring bug was
found and fixed: the Terraform sets the byte caps in GB
(`RCA_MAX_BYTES_PER_QUERY_GB`), but `config.py` only read the
byte vars — it now reads the GB vars (they win when both are
set).

**Model (verified 2026-11-07 against Google's own pages):**
`gemini-3.8-flash` — Generally Available, released 2026-09-02,
retirement 2027-09-02 or later, 1,048,576-token context,
structured output supported. Sources:
- https://cloud.google.com/vertex-ai/generative-ai/docs/models
- https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-8-flash
- https://cloud.google.com/vertex-ai/generative-ai/pricing

## Live 1 — Upload the new bundle and replace the repo

In Cloud Shell, use the Upload menu (the three-dot menu, top
right) to upload the new `rca-agent-bigquery.bundle`, then:

```bash
cd ~
git clone rca-agent-bigquery.bundle rca-agent-bigquery-new
cp rca-agent-bigquery/infra/terraform/terraform.tfvars rca-agent-bigquery-new/infra/terraform/terraform.tfvars
mv rca-agent-bigquery rca-agent-bigquery-old
mv rca-agent-bigquery-new rca-agent-bigquery
cd rca-agent-bigquery
git log --oneline
```

The `cp` carries your `terraform.tfvars` (project id and email)
into the new repo — it is gitignored and was never in the bundle.
The old repo is kept as `rca-agent-bigquery-old` until the live
steps are done. Expected: a list of commits ending in the live
wiring and fix commits. **Record:** the `git log` output, or
"cloned OK" if it matches. Cost: $0.

## Live 2 — Re-apply Terraform (picks up the fixes)

Terraform is not preinstalled in Cloud Shell. Check first:

```bash
terraform version
```

Expected: `Terraform v1.13.3`. If it is not found, install it:

```bash
cd /tmp
curl -fsSL -o terraform.zip https://releases.hashicorp.com/terraform/1.13.3/terraform_1.13.3_linux_amd64.zip
unzip -o terraform.zip terraform
mkdir -p ~/bin
mv terraform ~/bin/
export PATH=~/bin:$PATH
terraform version
```

Expected: `Terraform v1.13.3`. (Add `export PATH=~/bin:$PATH`
to `~/.bashrc` if you want it to survive a new shell.)

Then re-apply, from the repo root:

```bash
cd ~/rca-agent-bigquery/infra/terraform
terraform init
terraform plan
terraform apply
```

Expected: `plan` shows the fixes — the two new APIs, the Cloud
Build writer member changing to the `-compute` account, the
`container_port = 8000` on Cloud Run, the `scaling` block losing
`min_instance_count`, and `deletion_protection` on the tables and
the service. `apply` asks "Do you want to perform these actions?"
— type `yes`. **Record:** the `apply` output.

Then confirm the plan is clean (no drift):

```bash
terraform plan
```

Expected: `No changes. Your infrastructure matches the
configuration.` If it shows a diff, record it and stop — the
scaling fix (item 6 above) should have removed the recurring
diff. Cost: $0.

## Live 3 — Rebuild the image (live wiring)

Back at the repo root. The build uses `cloudbuild.yaml` now
(`gcloud builds submit` has no `-f` flag):

```bash
cd ~/rca-agent-bigquery
gcloud builds submit --config cloudbuild.yaml .
```

Run from the repo root (the `.` is the build context, which the
Dockerfile needs — it copies `backend/` and
`data/offline.duckdb`). The `.gcloudignore` uploads
`data/offline.duckdb` (the Dockerfile copies it into the image)
and excludes `.git/`, `.venv/`, `.terraform/`, and `*.tfvars`.

Expected: build logs, ending with `PUSH` and `DONE`, plus the
image digest (a `sha256:...` line). **Record:** the last ~10
lines (the pushed image name and digest). Cost: $0 (Cloud Build
free tier is 120 build-minutes/day; this build takes ~2–5
minutes).

## Live 4 — Redeploy and smoke-test the live wiring

The `container_image` in `terraform.tfvars` already points at
the `latest` image, so re-applying deploys the rebuilt image:

```bash
cd infra/terraform
terraform apply
```

Expected: `apply` updates the Cloud Run service (a new revision
with the rebuilt image). Type `yes` at the prompt. **Record:**
the `apply` output and the Cloud Run URL. Cost: $0 while idle.

Verify the service answers (it is IAM-protected, so the request
needs your identity token):

```bash
URL=$(terraform output -raw cloud_run_url)
curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" "$URL/health"
```

Expected: `{"status":"ok"}`. **Record:** the curl output.

Smoke-test one investigation. This now runs the **live**
wiring (the image has `GCP_PROJECT` and `VERTEX_MODEL_ID` set,
so the service calls the Vertex model — a few cents, inside the
$3.00 cap):

```bash
ID=$(curl -s -X POST -H "Authorization: Bearer $(gcloud auth print-identity-token)" -H "Content-Type: application/json" -d '{"question":"Why did brake complaints spike for the 2021 F-150?"}' "$URL/investigations" | python3 -c "import sys,json; print(json.load(sys.stdin)['investigation_id'])")
sleep 30
curl -s -H "Authorization: Bearer $(gcloud auth print-identity-token)" "$URL/investigations/$ID" | python3 -m json.tool | head -40
```

Expected: the investigation comes back `"status": "done"` and
the answer mentions Riverside. **Record:** the answer excerpt
and the verdict. Cost: ~$0.02–0.04 (one live investigation).

## Live 5 — Run the eval as the runner service account

The live eval must run with the **runner** service account's
permissions, not your owner account, so the read-only IAM
boundary is actually exercised: the runner can read only the
`rca_agent` dataset and write only traces to `rca_ops` and the
ledger to Firestore. If the eval ran as your owner account, a
query that slipped past the guardrails could write anywhere and
the test would prove nothing.

Set up Application Default Credentials to impersonate the
runner:

```bash
gcloud auth application-default login --impersonate-service-account=rca-agent-runner@rca-agent-portfolio.iam.gserviceaccount.com
```

No IAM grant is needed for this: you are the project owner, and
the Owner role includes `iam.serviceAccounts.impersonate` on
every service account in the project. (If you ever run this as a
non-owner, that person needs `roles/iam.serviceAccountTokenCreator`
on the runner service account.)

Verify the impersonation before the eval:

```bash
gcloud auth application-default print-access-token | cut -d. -f2 | base64 -d 2>/dev/null | python3 -m json.tool | grep '"email"'
```

Expected: `"email":
"rca-agent-runner@rca-agent-portfolio.iam.gserviceaccount.com"`.
If it shows your own email instead, the impersonation is not
active — stop and record the output. The eval harness also
prints `running as: ...` at startup in live mode, which is a
second check.

## Live 6 — Run the live eval (test set first), then STOP

Install the live dependencies and run the **test set only**:

```bash
cd ~/rca-agent-bigquery
backend/.venv/bin/pip install -e "backend[dev,gcp]"
backend/.venv/bin/python eval/run.py --set test --llm vertex --warehouse bigquery --project rca-agent-portfolio --max-cost-usd 3.00 --out eval/results
```

Expected: the first line is `running as:
rca-agent-runner@rca-agent-portfolio.iam.gserviceaccount.com`
(the impersonation from Live 5). Then one line per question
(`OK`/`MISS` with the expected and actual verdict and the cost
so far), and the metrics table at the end. It writes
`eval/results/2026-11-07-test.json` and
`eval/results/2026-11-07-test.md`. Expected total cost:
~$0.30–0.60 for the 17 questions. If the cost reaches $3.00,
the eval stops cleanly, grades the questions already run, and
lists the rest as skipped — that is the cap working, not a
failure.

**STOP. Do not run the dev set yet.** Record the results file
and the report, and send them back. The dev set runs only after
the test-set results are reviewed. **Record:** the full eval
output, the results file, and the report. Cost: ~$0.30–0.60
(expected), hard-capped at $3.00.

## Teardown (after the eval)

When the eval is done and you want nothing left billing:

```bash
cd infra/terraform
cat >> terraform.tfvars <<'EOF'
deletion_protection = false
EOF
terraform apply
terraform destroy
```

The `deletion_protection = false` line turns off the protection
on the BigQuery tables and the Cloud Run service and switches
the Firestore database to `deletion_policy = DELETE` — without
it, `destroy` fails on the protected resources and leaves the
Firestore database behind. `apply` updates the protection flags
(type `yes`), then `destroy` lists everything it will remove and
asks for confirmation (type `yes`). It deletes the datasets (the
warehouse and the traces), the service accounts, the Firestore
database, the Cloud Run service, and the Artifact Registry
repository (and its images). **Record:** the destroy output.

If the Firestore database is still there after destroy (it
should not be), delete it directly:

```bash
gcloud firestore databases delete --database=rca-agent --project=rca-agent-portfolio
```

The Cloud Build bucket is not managed by Terraform (Cloud Build
created it), so `destroy` does not delete it. Delete it
separately:

```bash
gsutil rm -r gs://rca-agent-portfolio_cloudbuild
```

The GCS state bucket is not managed by Terraform either (you
created it with `gsutil` in step 1), so `destroy` does not
delete it. Delete it separately, only after `destroy` has
finished and you no longer need the state:

```bash
gsutil rm -r gs://rca-agent-portfolio-tfstate
```

Do not delete the state bucket before `destroy` — Terraform
needs the state file to know what to remove. After teardown, the
only thing left is the $300 free-trial credit minus the ~$1–3
spent on the eval.
