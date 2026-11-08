# AI Root Cause Analysis Agent on BigQuery — Plan (Phase 1)

Third portfolio project. A quality engineer asks a question in plain
language ("brake complaints spiked for one model year, why?") and the
agent plans the investigation, writes and runs safe BigQuery SQL,
fixes its own query errors, remembers past investigations, and
answers with findings, ranked hypotheses, and the exact SQL and rows
behind each claim. No claim without evidence.

Stack: Python 3.12, FastAPI, Pydantic v2, LangGraph, sqlglot,
BigQuery + Firestore on GCP, DuckDB + a deterministic mock LLM for
offline mode. MIT license. Commit author: HariKiran Reddy Angala,
182449131+hari131415@users.noreply.github.com. The user pushes to
GitHub himself; phases are gated and each ends with a report in the
standing format (deferrals first, proof output, demo transcript,
interview walkthrough).

## Folder structure

One line per module. Files that do not exist yet are the plan, not a
promise — each phase creates only what it needs.

```
rca-agent-bigquery/
  README.md                  # at-a-glance summary, architecture, quickstart, guardrails, eval, limits
  PLAN.md                    # this file
  LICENSE                    # MIT
  .env.example               # empty values only, never real keys
  .gitignore
  backend/
    pyproject.toml           # deps, ruff, mypy, pytest config
    src/rca_agent/
      __init__.py
      config.py              # Settings (env vars); model ID + prices with source URLs and dates
      main.py                # FastAPI app factory, startup recovery, router wiring
      api/
        __init__.py
        investigations.py    # start (202), poll status, results with evidence, list past
        health.py            # liveness/readiness, no secrets in output
      graph/
        __init__.py
        state.py             # InvestigationState: question, plan, hypotheses, queries, evidence, status
        nodes.py             # one function per step: plan, schema, SQL, guardrail, dry-run, execute,
                             # self-correct, analyze, conclude
        graph.py             # LangGraph StateGraph wiring, attempt/recursion limits
      llm/
        __init__.py
        base.py              # LLM protocol: complete() returning text + token usage
        vertex.py            # Gemini on Vertex AI (google-cloud-aiplatform)
        mock.py              # deterministic mock LLM: scripted replies for offline/tests/eval baseline
        embeddings.py        # Vertex embeddings for optional semantic recall (off by default)
      sql/
        __init__.py
        parse.py             # sqlglot parsing: single SELECT, allow-listed tables, LIMIT enforcement
        guardrails.py        # dry-run caps, per-investigation + daily byte budgets, refusal records
        dialect.py           # BigQuery <-> DuckDB differences; transpilation via sqlglot
        warehouse.py         # Warehouse protocol: dry_run, execute (shared by BigQuery and DuckDB)
        bigquery.py          # BigQuery implementation: dry run, jobs, bytes processed
        duckdb.py            # DuckDB implementation: offline stand-in, row-based caps
      memory/
        __init__.py
        store.py             # investigation record CRUD (protocol, not an implementation)
        firestore.py         # Firestore implementation (live)
        local.py             # local JSON/SQLite implementation (offline/tests)
        recall.py            # optional semantic recall over past investigations
      tracing/
        __init__.py
        traces.py            # per-step traces: prompt, SQL, dry-run bytes, rows, tokens, cost, latency
      services/
        __init__.py
        runner.py            # async investigation execution and status transitions
        recovery.py          # startup recovery: stuck "running" investigations -> failed, resumable
        budgets.py           # daily byte-budget accounting
    tests/
      conftest.py
      test_guardrails.py     # every guardrail + bypass attempts (comments, CTEs, unions, multi-stmt)
      test_sql_parse.py      # SELECT-only, single statement, allow-list, LIMIT injection
      test_graph_mock.py     # full graph on the mock LLM, incl. self-correction recovery
      test_injection.py      # planted injection string in a complaint; agent must not follow it
      test_api.py            # start/poll/results/list, 202 + polling, error shapes
      test_memory.py         # store + recall (local implementation)
      test_tracing.py        # traces recorded, no secrets in trace rows
      test_recovery.py       # stuck investigations recovered on startup
      test_dialect.py        # BigQuery SQL runs on DuckDB via transpilation
  data/
    schemas/
      bigquery.sql           # dataset/table DDL; source of truth for Terraform + loaders
      duckdb.sql             # same schema in DuckDB dialect
      README.md              # schema docs, synthetic labels, planted-correlation index
    loaders/
      nhtsa.py               # download + parse ODI flat files (complaints, recalls)
      synthetic.py           # plant/production data generator, fixed seed
      load_bigquery.py       # load into BigQuery (idempotent, records row counts)
      load_duckdb.py         # load sample into DuckDB
    samples/
      complaints_sample.csv  # small sample for the offline warehouse; regenerable, never hand-edited
  eval/
    benchmark/
      dev.json               # dev questions + expected answers
      test.json              # held-out questions + expected answers (fixed-seed split)
      adversarial.json       # unsafe/expensive SQL attempts for guardrail block rate
      README.md              # how questions were built, ground-truth rules
    harness/
      run.py                 # run the eval over a set, record per-question results
      metrics.py             # correctness, first-pass SQL success, recovery, block rate,
                             # hallucinated-evidence rate, false-discovery rate on negative
                             # controls, cost/latency per investigation
      report.py              # markdown report; held-out set only
  infra/
    terraform/
      main.tf                # dataset, tables, SAs, Firestore, Cloud Run, Secret Manager
      variables.tf           # project, region, caps; no secrets
      outputs.tf             # dataset, service URL, SA emails
      versions.tf            # Terraform + provider pins (pinned in Phase 7)
      README.md              # apply/destroy, what the user must do first
  scripts/
    seed_offline.py          # build the DuckDB sample warehouse from the loaders
    verify_model.py          # Phase 8: check model ID + prices against Google's pages
  .github/workflows/
    ci.yml                   # offline only: ruff, mypy, pytest (no cloud credentials)
    terraform.yml            # terraform fmt + validate (no apply, no credentials)
```

## Phase map

1. **Plan (this phase).** Structure, data plan, GCP resources. Stop for
   go-ahead. No code.
2. **Data.** NHTSA download + schema, synthetic plant data with planted
   correlations, DuckDB offline warehouse, loaders. Exit: sample loads
   in both warehouses with matching schemas; download date and license
   recorded.
3. **Guardrails.** SQL parsing, allow-lists, dry-run caps, budgets,
   full tests including bypass attempts. BigQuery-specific bypass
   routes refused with reasons (scripting, CREATE TEMP FUNCTION
   incl. JS UDFs, EXPORT DATA, CALL, DML/DDL, wildcard tables,
   INFORMATION_SCHEMA/system views, cross-project/cross-dataset,
   FOR SYSTEM_TIME AS OF, EXTERNAL_QUERY; comment/case tricks,
   CTEs/subqueries/UNIONs hiding disallowed tables). LIMIT
   documented as result-size only — the dry-run byte cap is the
   cost control, tested with a LIMIT-ed query over the cap.
   Verdict is a structured object recorded with every attempt.
   Additions (2026-11-07): the read-only, dataset-scoped service
   account is the real security boundary and the sqlglot checks are
   a first-line filter (defense in depth, `docs/guardrails.md`);
   unparsable SQL is refused `parse-error`, fail closed (including
   a tokenizer error that first escaped the catch); the executed SQL
   is always the serialized, checked AST after LIMIT rewriting,
   never the original string (tested); ML/AI and remote/user-defined
   functions are refused — their cost is not visible in dry-run
   bytes; byte units are decimal GB everywhere. Exit: every
   guardrail test green, refusals recorded with reasons.
4. **Agent.** LangGraph flow with the mock LLM, self-correction,
   evidence-backed answers (claims whose evidence is synthetic are
   labeled "(synthetic)" in the answer), prompt-injection test. Exit:
   full investigation runs offline end to end; injection test green.
   **Done 2026-11-07:** `llm/` (protocol + deterministic mock) and
   `graph/` (state, nodes, LangGraph wiring, max 3 corrections, fail
   node on exhaustion). The answer builder quotes exact SQL + rows,
   labels synthetic evidence, and never quotes free-text complaint
   content. The dry-run step lives inside the guardrail node
   (`Guardrails.check` already couples parse, dry run, and caps).
   The Vertex LLM is Phase 8 (model ID unverified until then).
   **Multi-step (2026-11-07, before Phase 5):** the plan produces
   up to N steps (configurable, default 4), each with its own query,
   guardrail verdict, and evidence; the analyze step can request
   one follow-up query when the evidence is insufficient; all steps
   share the per-investigation query cap (a spent budget fails the
   investigation instead of burning corrections). A deterministic
   claim validator (`graph/validate.py`) checks every number a
   hypothesis or finding cites against the evidence rows (row
   values and column sums, rounding tolerance); unmatched claims
   are marked "(unverified)" and counted per investigation.
   Self-correction also covers execution errors, within the same
   attempt limit. **On the injection test:** it proves the plumbing
   only — evidence delimiters, the SQL prompt never seeing
   complaint text, the answer builder never quoting free text.
   Resistance of a real model is measured in Phase 8, not asserted
   here (see README Limitations).
5. **Memory, API, tracing, startup recovery.** FastAPI service,
   Firestore/local store, traces to BigQuery (local file offline),
   async 202 + polling, recovery on startup. Exit: API exercised
   offline; stuck investigations recovered.
   **Done 2026-11-07:** FastAPI (`POST /investigations` 202,
   `GET /investigations/{id}`, `GET /investigations`, `GET /health`)
   over one stateless graph; the graph streams its state after each
   node and every intermediate state is saved, so polling shows
   progress. Stores: MemoryStore (tests), FileStore (offline
   default, one JSON file per investigation + a budgets file),
   FirestoreStore (live, lazy import, emulator honored). The daily
   byte ledger persists through the Store (StoreLedger), so a
   restart does not reset the day's spend. Traces
   (`services/traces.py`): prompt, SQL, dry-run bytes, result rows,
   tokens, cost, latency per step — DuckDBTraceStore offline (its
   own file, so re-seeding never wipes it), BigQueryTraceStore live
   (lazy import, tested against a fake client; first real BigQuery
   write is Phase 8). Startup recovery marks "running"
   investigations "failed" ("interrupted by server restart").
   Semantic recall is a stub interface only (NoopRecall; real
   embeddings in Phase 8). The plan prompt takes similar past
   investigations when recall returns any.
6. **Eval harness and benchmark.** 30–40 questions (user reviews
   questions and answers), split by planted pattern, not by question
   (see `docs/data-plan.md` for the assignment), negative controls
   with a false-discovery-rate metric, real-recall questions verified
   against the recall records and using only real rows, mock
   baseline, held-out report. Exit: report on the held-out set with
   the mock baseline.
   **Done 2026-11-07 (benchmark is a DRAFT for review, not frozen):**
   the agent's answer carries a machine-readable verdict
   (cause_type plant/supplier/line/volume/recall/none, entity,
   supporting_query_ids), stored in the state and graded exactly
   with no LLM judging. `eval/benchmark.json` (34 questions, 17
   dev / 17 test) + a rendered `eval/benchmark/README.md`; the
   harness `eval/run.py` runs the mock over a copy of the warehouse
   and writes JSON + Markdown reports to `eval/results/`. Held-out
   test set (mock baseline): verdict accuracy 88.2% (15/17; the
   misses are the line question and the plant-vs-supplier question,
   which the scripted mock cannot answer), FDR 0.0%, first-pass
   SQL 100%, unverified claims 0, guardrail block rate 100% (33/33
   adversarial), latency median 283 ms / p95 512 ms, cost $0.00.
   Two bugs the eval caught and fixed: the claim validator read a
   campaign number ("26V104000") and a model name ("F-150") as
   claimed numbers — the number regex now requires a number not to
   touch a letter, digit, or hyphen — and the mock's "within 10%"
   was a made-up number. The Ram (24V653000) and Takata
   (13E017000) campaigns are not in the benchmark: they are real
   recalls but not in the warehouse (scoped to F-150 + Pilot),
   so no question about them is answerable from the agent's data;
   the dev recall questions use the Pilot campaigns in the
   warehouse instead.
   **Frozen 2026-11-07 at version 1.0** (user's review changes):
   a new cause_type `fleet_wide` for a real rise not concentrated
   in any plant, line, or supplier — P3 (T07–T11) now expects
   fleet_wide, the negative controls keep `none`; T06's expected
   verdict is plant/riverside (a location question, like T05);
   T04 replaced with "Did brake complaint rates rise at Riverside
   for the 2019 F-150?" → none (verified: the 2019 Riverside brake
   rate is 1.15 per 1,000, the lowest of the four plants). The
   harness now reports metrics per pattern, and eval/README.md
   notes that the test set has ~5 independent patterns (so overall
   accuracy is coarse) and that the recall questions are mostly
   lookups. Re-run baseline on v1.0: test set 82.3% (14/17; the
   misses are T03 line, T05 plant, T06 plant — the mock's fixed
   supplier verdict), non-none 78.6%, FDR 0.0%, first-pass SQL
   100%, unverified claims 0, guardrail block 100%, latency median
   270 ms / p95 500 ms. Per pattern (test): P1 50.0% (3/6), P3
   100%, P5 100%, negative controls 100%, recall lookups 100%.
   Dev 100.0% (17/17); all 91.2% (31/34).
7. **Terraform, CI, README.** Infra, both workflows, full README.
   Exit: CI green offline; `terraform validate` green. The CI
   backend job must run `mypy src ../data ../scripts tests` — the
   earlier command omitted `src/`, which let imports resolve
   silently to `Any` and hid 45 real type errors. Cloud Run:
   `cpu_idle = false` (CPU always allocated), max instances 1,
   min instances 0 — recorded 2026-11-07 in
   `docs/gcp-resources.md` with the reasoning (background
   investigations stall under request-based CPU throttling; the
   trade-off is per-second billing while an instance exists, and
   the residual risk is scale-to-zero killing a running
   investigation, which startup recovery makes visible).
   **Done 2026-11-07:** `infra/terraform/` (main.tf, variables.tf,
   outputs.tf, versions.tf — Terraform >= 1.9.0, google provider
   ~> 6.0 — plus a README): the BigQuery dataset and seven tables
   (schemas match data/schemas/bigquery.sql; complaints partitioned
   by ldate, clustered by make/model_year; the traces table is
   defined here, from services/traces.py), the runner SA
   (bigquery.jobUser + dataViewer on the dataset only +
   datastore.user; no secretAccessor — no secret exists) and the
   deployer SA (run.admin, iam.serviceAccountUser on the runner
   only, artifactregistry.writer), the Firestore Native database,
   and the Cloud Run service (cpu_idle = false, min 0, max 1,
   60s timeout, one request per instance, IAM-protected with a
   conditional run.invoker grant; the container image is a
   placeholder until Phase 8). No secrets, no budget alert (Phase 8
   backstop), no public access. `terraform fmt -check` and
   `terraform validate` green (Terraform 1.13.3). CI:
   `.github/workflows/ci.yml` (fresh venv, `pip install -e
   ".[dev]"`, seed `--from-sample`, ruff format/check, the
   corrected `mypy src ../data ../scripts tests`, pytest) and
   `terraform.yml` (fmt + init + validate, no apply, no
   credentials). Both workflows were proven in a clean-room copy
   (fresh venv, fresh checkout, no warehouse): ruff/mypy clean
   (50 files), pytest 162 passed, terraform validate green. The
   full README is written (summary, Mermaid architecture, quickstart
   with Windows notes, GCP setup, guardrails, eval results,
   data/PII, limitations).
   **Three fixes 2026-11-07 (before Phase 8):** (1) the runner SA
   gets `roles/aiplatform.user` (project-scoped — the least-privilege
   role for calling a Vertex AI model; it allows prediction calls and
   nothing else). (2) The traces table moved to a separate dataset
   `rca_ops`; the runner has `dataEditor` on `rca_ops` only and
   `dataViewer` on `rca_agent`. The SQL allow-list stays `rca_agent`
   only. Documented as two independent layers (the SQL guardrail +
   IAM) preventing any write to the queried data. (3) An Artifact
   Registry repository (`rca-agent`, DOCKER) with the Cloud Run
   service agent granted `artifactregistry.reader` on it (the
   deployer pushes, the runner never touches the registry), a
   `backend/Dockerfile` + a thin container entrypoint
   `backend/src/rca_agent/serve.py` (the offline API for now; Phase 8
   swaps the entrypoint to the live wiring), and documented image
   builds (Cloud Build via `gcloud builds submit`, or local docker
   build/push) in `infra/terraform/README.md`. Docker is not
   available in the sandbox, so the first real image build is
   Phase 8. `terraform fmt -check` and `terraform validate` re-run
   green after the fixes, and the clean-room CI re-run passes
   (ruff/mypy clean, 51 files; pytest 162 passed).
   **Phase 8 prep 2026-11-07:** the repo is git-initialized (8
   commits on main, HEAD 0d2d152, author HariKiran Reddy Angala) and
   a bundle is at ~/workspace/your_files/rca-agent-bigquery.bundle
   (1.4 MB, verified, test-cloned clean). The user runs Phase 8 in
   Google Cloud Shell (already authenticated; terraform, gcloud,
   python available; nothing installed locally). Project
   rca-agent-portfolio (number 958337524782), invoker
   <your-email>, free trial ($300 credit), budget
   alert $10. `terraform.tfvars` and all state files are gitignored;
   state lives in the GCS bucket `rca-agent-portfolio-tfstate`
   (declared in infra/terraform/backend.tf; created by the user
   before init). The Cloud Build API was added to the Terraform API
   list (the image build needs it). The Dockerfile now copies the
   offline warehouse into the image (4.2 MB) and sets the container
   env vars; `serve.py`'s repo-root computation was fixed (it was
   resolving to backend/ instead of the repo root). A load driver
   `scripts/load_to_bigquery.py` was added (reads the six tables
   from the offline DuckDB warehouse and full-refresh loads them
   into BigQuery; the tables already exist from Terraform; tested
   against a fake client). The runbook is docs/phase8-runbook.md:
   clone the bundle, create the state bucket, enable APIs, apply,
   build the warehouse + the image (Cloud Build), load the data,
   deploy, then STOP with the cost estimate. Gates after the prep:
   ruff/mypy clean (53 files), pytest 165 passed, terraform
   validate green, clean-room CI re-run green.
8. **Live GCP.** User sets up the project and credentials. Verify
   model IDs and prices against Google's pages, cost estimate, STOP
   for approval before any spend. Then load real data, deploy, run
   the eval on the held-out set. Exit: live eval report. Also:
   make the daily byte ledger increment a Firestore transaction
   (the read-then-write in `FirestoreStore.note_bytes` can let two
   concurrent investigations both pass the cap); test it against
   the emulator.
   **Verified 2026-11-07 against Google's own pages:** model id
   `gemini-3.8-flash` (GA, released 2026-09-02, retirement
   2027-09-02 or later, 1,048,576-token context, structured output
   supported). Prices in us-central1: $0.825 input / $4.125 output
   per 1M tokens through 2026-12-31, then $1.65 / $8.25 from
   2027-01-01. Sources: cloud.google.com/vertex-ai/generative-ai/
   docs/models, .../models/gemini/3-8-flash, .../generative-ai/
   pricing. The embedding model for the Phase 8 recall is NOT
   verified (the embeddings page is a console link that does not
   load without a login). Cost estimate for the live eval: test set
   (17 questions) about $0.30–0.60, all 34 about $0.60–1.25, with a
   2x safety factor about $1.25–2.50; BigQuery $0; total Phase 8
   about $1–3, well under the $10 budget alert. The user runs
   Phase 8 in Google Cloud Shell (project rca-agent-portfolio,
   invoker <your-email>, free trial $300 credit,
   budget alert $10); the runbook is docs/phase8-runbook.md and the
   bundle is ~/workspace/your_files/rca-agent-bigquery.bundle.
   **Steps 0–6 done 2026-11-07 in Cloud Shell:** Cloud Run URL
   https://rca-agent-hao5ceoedq-uc.a.run.app, /health ok, 17,247
   rows loaded (complaints 7,108 real + 8,706 synthetic), image
   built via Cloud Build and deployed, smoke test done (Riverside
   4.14 vs 1.22, verdict supplier/acme-brakes). **Seven fixes
   from the Cloud Shell run, all in the repo and runbook:**
   (1) Terraform is not preinstalled in Cloud Shell — install
   1.13.3 into ~/bin; (2) enable cloudresourcemanager + iam APIs
   (plan failed without them); (3) the Cloud Build writer grant
   goes to <number>-compute@developer.gserviceaccount.com (the
   build runs as the Compute Engine default SA, not the
   cloudbuild service agent); (4) `gcloud builds submit` has no
   -f flag — cloudbuild.yaml + .gcloudignore (uploads
   data/offline.duckdb, excludes tfvars/tfplan/.terraform/.venv/
   .git); (5) Cloud Run container_port = 8000, serve.py reads
   PORT first, then RCA_PORT, then 8000; (6) min_instance_count
   removed from the scaling block (0 -> null drift on every
   plan); (7) a deletion_protection variable (default true) for
   teardown, plus the Firestore delete and the Cloud Build
   bucket gs://rca-agent-portfolio_cloudbuild in the teardown
   sequence. **Verdict-grounding check (new bug from the smoke
   test):** the verdict said supplier/acme-brakes but the only
   supporting query grouped by plant — the entity must now
   appear in a supporting query's result rows for its dimension,
   or the verdict is marked "(unverified)" and counted in the
   eval. Mock baseline: test 82.3% with 4 unverified verdicts
   (T01, T03, T05, T06, all P1); dev 100% with 0. **Live wiring
   done:** llm/vertex.py (google-genai, vertexai=True), create_live_app
   (BigQuery warehouse, Vertex LLM, Firestore store + ledger,
   BigQuery traces in rca_ops), eval live mode (--llm vertex
   --warehouse bigquery, --max-cost-usd 3.00 hard cap, stops
   cleanly). **Cost estimate APPROVED with a $3.00 hard cap.**
   The live eval runs as the runner service account (ADC
   impersonation — the owner already has
   iam.serviceAccounts.impersonate, no extra grant), test set
   first, then STOP and report before the dev set.
   **Live eval, first run 2026-11-08 (partial):** 8 of 17 test
   questions ran before a 429 crashed the run; 7 of 8 verdicts
   were None/None; only T04 reached analyze. The read-only
   boundary is PROVEN: as rca-agent-runner, a SELECT returned
   15,814 rows and CREATE TABLE was denied with 403
   bigquery.tables.create. Spend ~$0.90. The model wrote regex
   text-mining queries over cdescr instead of rate comparisons,
   invented columns (plant_name, supplier_name, component, dtc),
   and self-corrected with the identical SQL three times; T01 had
   9 self-corrects and 19 guardrail checks with no analyze.
   **Corrections to the 2026-11-07 record:** (a) the model is
   available ONLY on the `global` location (us-central1 returned
   404); global prices are $0.75 input / $3.75 output per 1M, so
   the $0.825/$4.125 figures above do not apply. (b) Owner does
   NOT include impersonation — an explicit
   roles/iam.serviceAccountTokenCreator grant on the runner SA is
   needed (added to Terraform as an optional operator_email var).
   (c) Actual cost is $0.06–0.18 per question (thinking tokens
   dominate), so the test set is ~$1–3, not $0.30–0.60 — the
   $3.00 hard cap stands, per run. **Seventeen fixes from the
   live run, all in the repo with tests:** (1) vertex.py reads
   candidates_token_count (response_token_count does not exist),
   proven by a contract test built from a real response shape;
   (2) the plan prompt specifies the step/hypothesis object shape
   and the graph normalizes bare strings; (3) BigQueryTraceStore
   has a real list_traces (parameterized query); (4) VERTEX_LOCATION
   (default global) in config, serve.py, the eval, and Terraform;
   (5) the SQL prompt directs rate-based analysis with a worked
   example; text search of cdescr is not the primary method;
   (6) SCHEMA_DOC lists every table's exact columns and join
   keys, tested against the DDL; (7) self-correct gets the exact
   refusal and stops on identical SQL (refusals only — an
   execution error may be transient, so the same SQL is a
   legitimate retry there, bounded by the attempt cap);
   (8) cap/retry exhaustion goes to analyze, with an explicit
   insufficient_evidence verdict when there is no evidence, and
   the eval reports abstentions separately; (9) 429/503 retries
   with exponential backoff and jitter in the Vertex client, a
   per-question delay in the eval, and per-question exception
   handling that records errors and continues; (10) the cost
   estimate and README updated to the measured $0.06–0.18, with
   an optional VERTEX_THINKING_BUDGET (verified against
   google-genai 3.0.0); (11) raw strings (r'...') are NOT
   mishandled — the '\\s' in the traces is correct BigQuery
   escaping, proven end to end, with regression tests;
   (12–17) runbook: Live 1 creates the venv and seeds
   --from-sample; deploy by image digest; the impersonation
   grant; python -u + nohup for the eval; ADC revoke before
   teardown; lifecycle ignore_changes on the service scaling.
   Gates after the fixes (in-repo): ruff format/check clean,
   pytest 203 passed (was 185), mypy clean except 8 pre-existing
   google.cloud errors in the sandbox venv (verified pre-existing
   against the unmodified code — the google namespace resolves
   oddly under mypy here; the code itself is unchanged there),
   terraform validate green. Clean-room CI and the mock baseline
   rerun then ran before delivery (2026-11-08): clean-room 201
   passed + 2 skipped (importorskip), mypy fully clean there;
   mock baseline no regression (test 82.3%, dev 100%, all 91.2%).
   **Live eval, completed run (2026-11-08, GCE VM as
   rca-agent-runner, gemini-3.8-flash global, $4.00 cap,
   --delay-seconds 2):** all 17 test questions, cost $1.71
   ($0.1006/investigation), latency median 163 s / p95 353 s.
   Verdict accuracy 52.9% (9/17) under v1.0, 64.7% (11/17) under
   v1.1 (post-hoc regrade); strict accuracy 47.1% / 58.8%;
   false-attribution rate 4/8 (50.0%) while FDR was 0.0%;
   first-pass SQL 100%, 0 errors, 0 abstentions, unverified
   claims 35.3% (8), unverified verdicts 3. Per pattern: P1 5/6
   (beats the mock's 50%), P3 1/5, P5 0/2 (1/2 under v1.1),
   recall lookups 1/2 (2/2 under v1.1), negative controls 2/2.
   Failure modes and the fixes in this bundle: (a) P3
   over-attribution — a deterministic uniformity check (max/min
   rate ratio under 2.0) overrides a plant/line/supplier verdict
   to fleet_wide; (b) T01 confound — a follow-up query
   stratifies one dimension within the other before deciding,
   and the follow-up is no longer blocked by the 4-step cap;
   (c) strict accuracy counts ungrounded verdicts as
   abstentions; (d) the validator now accepts ratios,
   differences, and shares derived from one query's rows within
   one column (the answers were not in the run's files, so the
   specific unverified numbers could not be inspected — the
   diagnosis is from the validator's rules); (e) the analyze
   prompt lists all matching recalls — superseded after Run 2 by
   the one-recall rule (below); (f) benchmark v1.1 defines
   T14/T15/T17 as the accepted set {26V104000, 22V193000} —
   22V193000 is a real Ford recall for the same component
   covering the 2021 F-150 (recalls sample record 227416) —
   v1.0 stays frozen and is the CI baseline; (g) the eval
   resolves the "default" ADC identity via the GCE metadata
   server; (h) investigation ids are unique per run
   (eval-<id>-<timestamp>); (i) the runbook documents the VM
   path (Cloud Shell killed the job three times).
   **Live eval, Run 2 (2026-11-08, GCE VM as rca-agent-runner,
   gemini-3.8-flash global, $4.00 cap, --delay-seconds 2,
   benchmark v1.1, after the fixes):** all 17 test questions,
   cost $1.98 ($0.1162/investigation), latency median 173 s /
   p95 478 s. Verdict accuracy 88.2% (15/17), strict 88.2%;
   false-attribution rate 0/8 (was 4/8); first-pass SQL 100%, 0
   errors, 1 abstention, unverified claims 11.8% (5),
   unverified verdicts 1. Per pattern: P1 6/6, P3 4/5, P5 1/2,
   recall lookups 2/2, negative controls 2/2. Misses: T11
   abstained (fleet-wide), T14 listed five recalls in one
   verdict. **The 88.2% is an upper bound, not a held-out
   score:** the fixes were designed after seeing Run 1's test
   failures, so Run 2 re-measures the set they were tuned on.
   **Live eval, dev set (2026-11-08, same VM/model/cap, v1.1):**
   all 17 dev questions, cost $1.57 ($0.0922/investigation),
   latency median 163 s / p95 398 s. As graded 52.9% (9/17);
   P2 4/5 — the uniformity check did NOT override the real
   localized Lakeside spike, the key generalization risk —
   false attributions 0/2. Four of the five P4 misses are a
   format bug (volume/"HONDA PILOT" and "Honda Pilot" vs
   expected "honda-pilot", flagged ungrounded for the same
   reason); the normalized post-hoc regrade recovers them
   (76.5%, 13/17; P4 4/5), original untouched. Genuine misses:
   D05 (none for plant/lakeside), D09 (none for volume), D11
   (abstained on a negative control), D13 (two recalls in one
   verdict). Fixes in this bundle: (j) one canonical entity
   form per cause type (`canonicalize_entity`), used by the
   analyze prompt, the grounding check, and the grader, and
   applied by `--regrade` (which clears a stored ungrounded
   flag only where the canonical verdict entity agrees with the
   expected entity); (k) a recall verdict names exactly ONE
   campaign, the best match by component and dates, with the
   others listed in the answer text — a multi-campaign entity
   is ungrounded and matches nothing. No more live runs unless
   the user asks: the VM is deleted, and Phase 8 spend stands
   at about $8.50 of trial credit.
9. **Final polish, secrets/personal-data scan, commits, bundle.**
   ~8–12 small conventional commits, git bundle for the user to push
   from Windows.

## Design decisions (the ones that matter)

- **sqlglot, not regex, for SQL safety.** Guardrails parse SQL into an
  AST and walk it. A comment, CTE, union, or second statement is a
  structural fact of the parse, not a string to pattern-match. This is
  why bypass tests are written against the parser, not the prompt.
- **The agent only ever writes BigQuery SQL.** The dialect boundary is
  one module (`sql/dialect.py`): offline execution transpiles
  BigQuery SQL to DuckDB with sqlglot. Agent logic never branches on
  the warehouse. Differences are documented in `docs/data-plan.md`.
- **Guardrails are code, not prompts.** The model is asked for SQL;
  the code decides whether it runs. Dry run precedes every execution;
  bytes caps and query caps are enforced before the job starts; every
  refusal is recorded with its reason.
- **Warehouse is a protocol, not a client.** BigQuery and DuckDB both
  implement `dry_run` + `execute`. DuckDB has no byte billing, so its
  dry run estimates rows and the caps are row-based offline, byte-based
  live. Same guardrail code, different units — stated in one place.
- **The mock LLM is scripted and deterministic.** It returns canned
  plans and SQL keyed to the question pattern. The eval's mock baseline
  uses the same mock, so mock numbers are a floor, not a claim about
  the live model.
- **State is persisted after every graph step.** The investigation
  record (question, plan, hypotheses, queries, evidence, status) is
  written to Firestore (or the local store offline) as the graph
  advances. That is what makes polling, resume, recall, and startup
  recovery possible instead of bolted on.
- **No claim without evidence is enforced in the answer builder, not
  the prompt.** The conclude step can only cite rows the warehouse
  actually returned; the eval measures hallucinated-evidence rate by
  checking each claim's row references.
- **Complaint text is data, never instructions.** It reaches the model
  only inside delimited blocks as query results, and SQL is generated
  from the schema and the question, never from complaint text. A
  planted injection string in a complaint must not change the SQL, the
  plan, or the conclusions — tested in Phase 4.
- **Terraform stays flat.** One dataset, ~8 tables, two service
  accounts, Firestore, one Cloud Run service, Secret Manager. Modules
  would be over-engineering for this size.
- **CI never touches GCP.** Offline mode only: ruff, mypy, pytest,
  terraform fmt/validate. No cloud credentials in GitHub Actions.

## Decisions deliberately NOT made in Phase 1

- **Model ID and prices.** Not chosen. Third-party pages in Nov 2026
  point to `gemini-3.8-flash` at $0.75/$3.75 per 1M through Dec 31,
  2026, but that is not a Google source and the user requires
  verification against Google's own model list and pricing page. Phase
  8 verifies the exact ID and prices, records them in config with
  source URLs and dates, and stops for approval before any spend. No
  model ID is baked into any code before then.
- **Embedding model for optional recall.** Same treatment as the LLM:
  candidate noted in `docs/gcp-resources.md`, verified in Phase 8,
  off by default regardless.
- **Exact NHTSA columns.** The flat-file headers and row counts are
  confirmed from the actual download in Phase 2, not from memory.
  The data plan names the files and their documented shape; the schema
  files are written from the real headers.
- **Partitioning/clustering.** Complaints will likely be partitioned
  by received date and clustered by make/model year; decided in Phase
  2 once the real columns and volumes are known.
- **Cloud Run auth posture.** IAM-protected by default in the plan;
  the user can deliberately open it with a one-line Terraform change.
  Decided finally in Phase 7.
- **Terraform/provider versions.** Pinned in Phase 7 against what is
  current then, not guessed now.
