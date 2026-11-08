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

**Cost estimate for the live eval (measured 2026-11-08, hard cap
$4.00 per run):** $0.06–0.18 per question — thinking tokens
dominate (a 12-character JSON reply used 152 output tokens).
Test set (17 questions) ~$1–3, all 34 ~$2–6. The eval enforces
a hard cap in code (`--max-cost-usd 4.00` for the rerun): it stops
cleanly when the cumulative model cost reaches it, grades the
questions already run, and lists the rest as skipped. BigQuery
for the eval is $0 (a few MB per query; the first 1 TB/month is
free, dry runs are free). Cloud Run is $0 while idle. **Spent so
far: about $5 of trial credit (first run ~$0.90, completed run
$1.71, plus smoke tests). Rerun: ~$1–3, capped at $4.00.**

## What changed since the live eval (2026-11-08)

The first live eval ran 8 of 17 test questions before a 429
stopped it: 7 of 8 verdicts were None/None, only T04 reached
`analyze`. The read-only boundary is proven (a SELECT returned
15,814 rows as the runner; CREATE TABLE was denied with 403).
The run exposed bugs only a real model could reveal, and they
are all fixed in this bundle:

1. **Token counting was wrong.** `vertex.py` read
   `usage.response_token_count`, which does not exist; the real
   field is `candidates_token_count` (thinking tokens are billed
   as output). The test passed because the fake used the same
   wrong name — the test is now a contract test built from a
   real response shape.
2. **The plan prompt had no shape.** It said `"steps": [...]`;
   the model returned bare strings and the graph crashed on
   `step.get`. The prompt now specifies the object shape, and
   the graph normalizes strings to `{id, description}` (same
   for hypotheses).
3. **`BigQueryTraceStore` had no `list_traces`**, so the eval
   crashed — and the $3 cap depends on traces. It is
   implemented properly now (parameterized query).
4. **The model is global-only.** `gemini-3.8-flash` returned 404
   on us-central1; it works on the `global` location. There is
   now a `VERTEX_LOCATION` setting (default `global`) for the
   eval, the live app, and Terraform, and cost math uses the
   global prices ($0.75 input / $3.75 output per 1M).
5. **The SQL strategy was wrong.** The model wrote regex
   text-mining queries over `cdescr` instead of rate
   comparisons. The SQL prompt now directs rate-based analysis
   (complaints per 1,000 vehicles built, joined to builds, compared
   across entities and years) with one worked example query.
6. **The schema document invented columns.** It listed
   `plant_name`, `supplier_name`, `component`, `build_month` —
   none of which exist. `SCHEMA_DOC` now lists every table's
   exact columns and join keys, and a test checks it against the
   real DDL.
7. **Self-correction got no error and looped.** The prompt now
   carries the exact refusal (guardrail rule + reason, or the
   BigQuery error text), and if the model returns SQL identical
   to the failed attempt, the graph stops retrying — for guardrail
   refusals. An execution error may be transient (a dropped
   connection), so the same SQL is a legitimate retry there,
   bounded by the attempt cap.
8. **No more dead ends.** When the query cap or retries run out,
   the graph goes to `analyze` with whatever evidence was
   collected; with none, it returns an explicit verdict
   `{"cause_type": "insufficient_evidence"}`. The eval reports
   abstentions separately from wrong answers.
9. **429 handling.** The Vertex client retries 429/503 with
   exponential backoff and jitter; the eval waits a few seconds
   between questions (`--delay-seconds 2`) and catches
   per-question exceptions, records them with the cost already
   spent, and continues — it never crashes the run.
10. **Real cost.** See the estimate above. A thinking budget is
    available as `VERTEX_THINKING_BUDGET` (off by default; a low
    budget can hurt the SQL the model writes).
11. **Raw strings are fine.** The `'\\s'` in the traces looked
    like the guardrail mangling `r'...'` patterns. It is correct
    BigQuery escaping — the literal's value is still `\s` — and
    the rewritten SQL returns the same rows as the original.
    Regression tests prove it end to end.
12. **Runbook fixes (this file):** Live 1 creates the venv and
    seeds the warehouse; deploys use the image digest, not
    `:latest`; the impersonation grant is in Terraform; the eval
    runs unbuffered under nohup; ADC is revoked before teardown;
    the scaling drift is ignored in Terraform.

## What changed since the completed live run (2026-11-08)

The completed run finished all 17 test questions on a VM: 52.9%
(9/17) under v1.0, 64.7% (11/17) under v1.1 (see
`eval/README.md`). It exposed a second round of failure modes,
all fixed in this bundle:

1. **Over-attribution (P3).** The model named a supplier, a
   plant, and a volume effect for an airbag rise that is flat
   across plants (1.80–1.83), lines (1.77–1.86), and years. A
   deterministic uniformity check now overrides a plant/line/
   supplier verdict to fleet_wide when the highest rate is under
   twice the lowest (threshold 2.0, between the uniform 1.02–
   1.05x and the localized 3.6–4.3x). The eval also reports a
   **false-attribution rate** (live 4/8, 50.0%) next to FDR, and
   a **strict accuracy** that counts ungrounded verdicts as
   abstentions.
2. **Confound (T01).** The model said plant/riverside; the cause
   is supplier/acme-brakes. The agent now requests a follow-up
   query stratifying one dimension within the other before
   deciding, and the follow-up is no longer blocked by the
   4-step cap. The schema document now states that synthetic
   rows for brakes, steering, and airbags carry `supplier_id`.
3. **Unverified claims (35.3%).** The validator only accepted
   row values and column sums, so a ratio, a difference, or a
   share was flagged even when computed from the rows. Those
   derivations now verify when they come from one query's rows
   within one column.
4. **Recall ambiguity (T15, T17).** 22V193000 is a second real
   Ford recall for the same component covering the 2021 F-150,
   so those questions have two valid answers. Benchmark v1.1
   defines the expected verdict for T14/T15/T17 as the accepted
   set {26V104000, 22V193000}; v1.0 is frozen and stays the CI
   baseline. The rerun uses v1.1.
5. **Eval identity.** On a VM, `google.auth` reports "default".
   The eval now resolves that to the real service account email
   via the GCE metadata server.
6. **Investigation ids are unique per run**
   (`eval-<id>-<timestamp>`). Reused ids made `list_traces` mix
   costs across runs.
7. **Run the eval on a VM, not in Cloud Shell.** Cloud Shell
   kills long jobs when the browser idles or disconnects — that
   happened three times. The completed run took about 51 minutes
   of question time; the VM path is Live 6 below. The VM runs as
   the runner natively, so no impersonation is needed there.

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
   `max_instance_count = 1` stays. **Update 2026-11-08: the
   drift was still there after the removal**, so the service now
   has `lifecycle { ignore_changes = [scaling] }` — Terraform
   no longer manages the scaling block at all. Plans are clean
   now.
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
global endpoint, prices $0.75 input / $3.75 output per 1M;
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

**Model (verified 2026-11-07 against Google's own pages,
corrected 2026-11-08 by the live run):**
`gemini-3.8-flash` — Generally Available, released 2026-09-02,
retirement 2027-09-02 or later, 1,048,576-token context,
structured output supported. Available ONLY on the `global`
location (us-central1 returned 404); global prices $0.75 input /
$3.75 output per 1M through 2026-12-31, then $1.50 / $7.50.
Sources:
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

The new clone has no virtualenv and no warehouse yet, so the
build (Live 3) and the eval (Live 6) would both fail. Create
them now. Check the Python version first — the project needs
3.12:

```bash
cd ~/rca-agent-bigquery
python3 --version
```

Expected: `Python 3.12.x`. If Cloud Shell has a different
version, use `python3.12` for the commands below (or install
3.12 first — `sudo apt-get install -y python3.12
python3.12-venv`).

```bash
python3 -m venv backend/.venv
backend/.venv/bin/pip install -e "backend[dev,gcp]"
backend/.venv/bin/python scripts/seed_offline.py --from-sample
```

Expected: pip installs cleanly, and the seed prints the
warehouse counts (15,814 complaints, 841 recalls, 576 build
batches) in about a second — `--from-sample` rebuilds the
warehouse from the committed sample CSVs, no download. This
also creates `data/offline.duckdb`, which the Dockerfile copies
into the image in Live 3. **Record:** the seed output. Cost: $0.

Then set the impersonation grant (needed in Live 5) before the
Terraform apply in Live 2. Add your operator email to
`terraform.tfvars` — the same email as `run_invoker_email`:

```bash
cat >> infra/terraform/terraform.tfvars <<'EOF'
operator_email = "<your-email>"
EOF
```

Keep the aligned `=` spacing the file already uses (the values
line up in columns). If you leave `operator_email` empty,
Terraform creates no grant and the Live 5 impersonation fails
with a permission error.

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
the service. If you set `operator_email` in Live 1, the plan
also shows the new `google_service_account_iam_member` granting
`roles/iam.serviceAccountTokenCreator` on the runner to your
email. `apply` asks "Do you want to perform these actions?"
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
lines (the pushed image name and digest) — the digest is what
Live 4 deploys, so copy it exactly. If you lose it, this lists
the digest for each tag:

```bash
gcloud artifacts docker images list us-central1-docker.pkg.dev/rca-agent-portfolio/rca-agent/rca-agent --include-tags
```

Cost: $0 (Cloud Build free tier is 120 build-minutes/day; this build takes ~2–5
minutes).

## Live 4 — Redeploy and smoke-test the live wiring

`:latest` never changes, so Terraform would not redeploy on a
re-apply — deploy by image **digest**. Set `container_image`
in `terraform.tfvars` to the digest form (keep the aligned `=`
spacing the file already uses):

Edit `terraform.tfvars` so the line reads (with the digest from
Live 3, keeping the aligned `=` spacing the file already uses):

```
container_image = "us-central1-docker.pkg.dev/rca-agent-portfolio/rca-agent/rca-agent@sha256:<digest>"
```

Then re-apply:

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
and the verdict. Cost: ~$0.06–0.18 (one live investigation —
thinking tokens dominate, so it is more than the $0.02–0.04
first estimated).

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

This needs an explicit grant: **Owner does NOT include
impersonation.** Terraform grants
`roles/iam.serviceAccountTokenCreator` on the runner to the
`operator_email` you set in `terraform.tfvars` in Live 1 (the
apply in Live 2 created it). If the login fails with a
permission error, check that `operator_email` is set in
`terraform.tfvars` and that Live 2 applied after it was added.

Verify the impersonation before the eval:

```bash
backend/.venv/bin/python -c "import google.auth; creds, _ = google.auth.default(); print(getattr(creds, 'service_account_email', creds))"
```

Expected: `rca-agent-runner@rca-agent-portfolio.iam.gserviceaccount.com`.
The eval harness also prints `running as: ...` at startup in
live mode, which is a second check. Do NOT use the old
JWT-decode check (`gcloud auth application-default
print-access-token | cut -d. -f2 | base64 -d ...`): Google
access tokens are opaque, not JWTs, so that command prints
nothing useful.

Note: **the impersonation is lost when Cloud Shell restarts.**
If your shell restarts (or the ADC file is gone), redo this
step before running the eval.

## Live 6 — Run the live eval on a VM (test set first), then STOP

**Use a VM, not Cloud Shell.** Cloud Shell kills long jobs when
the browser idles or disconnects — that happened three times
during the live runs. The completed 2026-11-08 run used a VM
and finished (about 51 minutes of question time). The VM runs
as the runner service account natively, so Live 5's
impersonation is **not** needed on the VM: the eval's `running
as:` line resolves the identity through the GCE metadata server.

### Create the VM (from Cloud Shell)

```bash
gcloud services enable compute.googleapis.com --project=rca-agent-portfolio
gcloud compute instances create rca-eval-vm \
  --project=rca-agent-portfolio \
  --zone=us-central1-a \
  --machine-type=e2-small \
  --service-account=rca-agent-runner@rca-agent-portfolio.iam.gserviceaccount.com \
  --scopes=cloud-platform \
  --image-family=debian-12 \
  --image-project=debian-cloud
```

`--scopes=cloud-platform` gives the VM's service account its
full Cloud API scope; the account itself is the runner, so its
permissions are still the runner's (read-only on `rca_agent`,
traces to `rca_ops`, ledger to Firestore).

### Copy the repo and install (from Cloud Shell, then on the VM)

```bash
cd ~/rca-agent-bigquery
tar --exclude=.venv --exclude=.git --exclude=__pycache__ -czf /tmp/rca-agent-bigquery.tgz .
gcloud compute scp /tmp/rca-agent-bigquery.tgz rca-eval-vm:~/rca-agent-bigquery.tgz --project=rca-agent-portfolio --zone=us-central1-a
gcloud compute ssh rca-eval-vm --project=rca-agent-portfolio --zone=us-central1-a
```

On the VM:

```bash
mkdir -p ~/rca-agent-bigquery && tar -xzf ~/rca-agent-bigquery.tgz -C ~/rca-agent-bigquery && cd ~/rca-agent-bigquery
sudo apt-get update && sudo apt-get install -y python3.12-venv
python3 -m venv backend/.venv
backend/.venv/bin/pip install -e "backend[dev,gcp]"
backend/.venv/bin/python scripts/seed_offline.py --from-sample
```

Expected: the seed prints the pattern checks and `offline
warehouse ready` in about a second. (The live eval queries
BigQuery, not the offline warehouse; the seed just verifies the
repo is intact on the VM.)

### Run the eval (on the VM)

Run the **test set only**, under benchmark v1.1, unbuffered and
in the background, so a dropped connection cannot kill it and
the output is written as it happens:

```bash
cd ~/rca-agent-bigquery
nohup backend/.venv/bin/python -u eval/run.py --set test --llm vertex --warehouse bigquery --project rca-agent-portfolio --benchmark eval/benchmark-v1.1.json --max-cost-usd 4.00 --delay-seconds 2 --out eval/results > eval-live-test.log 2>&1 &
```

Then follow the log:

```bash
tail -f eval-live-test.log
```

`--delay-seconds 2` waits two seconds between questions, so
the run is less likely to hit the 429 that stopped the first
run. Expected: the first line is `running as:
rca-agent-runner@rca-agent-portfolio.iam.gserviceaccount.com`
(resolved via the metadata server — no impersonation on the
VM). Then one line per question (`OK`/`MISS`/`ABSTAIN`/`ERROR`
with the expected and actual verdict and the cost so far), and
the metrics table at the end. It writes
`eval/results/<today>-test.json` and
`eval/results/<today>-test.md`. Expected total cost: ~$1–3 for
the 17 questions (the completed run cost $1.71) — the run may
hit the $4.00 cap, which is the cap working, not a failure. If
the cost reaches $4.00, the eval stops cleanly, grades the
questions already run, and lists the rest as skipped.

### Copy the results back (from Cloud Shell)

```bash
mkdir -p ~/rca-agent-bigquery/eval/results-live-vm/
gcloud compute scp --recurse rca-eval-vm:~/rca-agent-bigquery/eval/results/ ~/rca-agent-bigquery/eval/results-live-vm/ --project=rca-agent-portfolio --zone=us-central1-a
gcloud compute scp rca-eval-vm:~/rca-agent-bigquery/eval-live-test.log ~/rca-agent-bigquery/eval-live-test.log --project=rca-agent-portfolio --zone=us-central1-a
```

**STOP. Do not run the dev set yet.** Record the results file
and the report, and send them back. The dev set runs only after
the test-set results are reviewed. **Record:** the full eval
output, the results file, and the report. Cost: ~$1–3
(expected), hard-capped at $4.00.

Cloud Shell + nohup is still a fallback for short runs, but it
has killed this eval three times — use the VM for the rerun.

## Teardown (after the eval)

When the eval is done and you want nothing left billing:

**Revoke the ADC impersonation first.** The shell is still
impersonating the runner service account, and the runner cannot
destroy infrastructure — Terraform would run with the runner's
permissions and fail partway through:

```bash
gcloud auth application-default revoke
```

Then turn off deletion protection and destroy:

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
only thing left is the $300 free-trial credit minus the ~$5
spent on the eval runs.

If the eval ran on a VM (Live 6), delete it too — it is not
managed by Terraform, and an e2-small keeps billing while it
exists:

```bash
gcloud compute instances delete rca-eval-vm --project=rca-agent-portfolio --zone=us-central1-a
```
