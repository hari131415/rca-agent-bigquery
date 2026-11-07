# Guardrails (Phase 3)

The agent's SQL passes through code before it touches a warehouse.
The model is asked for SQL; the code decides whether it runs.
`Guardrails.check` (or `execute`, which checks then runs) is the
single gate, and every attempt — allowed or refused — is recorded
as a structured `Verdict` (see "The verdict" below).

## What is checked, in order

1. **Parse (sql/parse.py).** The SQL must parse as BigQuery SQL
   with sqlglot. A parse error is a refusal (fail-closed).
   Exactly one statement. The statement must be a SELECT or a
   UNION of SELECTs — DDL, DML, scripting (DECLARE, BEGIN...END,
   CALL, SET), CREATE TEMP FUNCTION (including JavaScript UDFs),
   and EXPORT DATA are all refused by statement kind.
2. **Tables.** Every table reference in the tree — including ones
   hidden in CTEs, subqueries, and UNION branches — must be
   `dataset.table`, in the allowed dataset (`rca_agent`), on the
   allow-list (complaints, recalls, plants, lines, suppliers,
   builds), and not a wildcard (`dataset.*`, `table_*`), a system
   view (INFORMATION_SCHEMA, `__TABLES__`), a cross-project or
   cross-dataset reference, or a table function (EXTERNAL_QUERY).
   FOR SYSTEM_TIME AS OF is refused. CTE names are collected first
   so a CTE is not mistaken for a real table.
3. **Query cap.** An investigation may make at most
   `max_queries_per_investigation` attempts (default 20),
   including refused ones — the self-correct loop must not spin
   forever on bad SQL.
4. **Dry run.** The query must plan on the warehouse. Live, the
   dry run also reports the bytes the query would scan.
5. **Byte caps.** The dry-run bytes must fit the per-query cap
   (default 1 GB) and the day's remaining byte budget (default
   10 GB/day).

## LIMIT protects result size, not cost

This is the point most often misunderstood, so it is stated
plainly: **a LIMIT does not make a query cheap.**

BigQuery bills bytes scanned, not rows returned. `SELECT * FROM
rca_agent.complaints LIMIT 10` still scans every byte of every
column the query touches, because the engine must read the table
to find the first 10 rows. The LIMIT bounds the rows that come
back; it says nothing about the bytes read to produce them.

The real cost control is the **dry-run byte cap**: before any
query runs, a BigQuery dry run reports `total_bytes_processed`,
and the guardrail refuses the query if that number is over the
per-query cap — LIMIT or no LIMIT. There is a test for exactly
this: a `LIMIT 10` query whose dry run reports 2 GB is refused
with rule `byte-cap`. The daily byte cap is the second control:
bytes are reserved at dry-run time, so two queries cannot
double-spend the same budget.

Offline (DuckDB) there is no byte billing, so the dry run is
EXPLAIN — it proves the query plans — and reports no byte count.
The byte caps do not apply offline; the row cap (LIMIT) does, in
both modes. The verdict records `bytes_scanned: null` offline so
the difference is visible, not hidden.

## Defense in depth: what the real boundary is

The sqlglot checks above are a **first-line filter, not the
security boundary**. They exist to catch mistakes early, give the
agent a clear reason it can self-correct from, and enforce the
cost caps before a job starts. They must never be the only
control, because a parser and an engine can interpret the same
SQL differently: sqlglot may accept something BigQuery reads
another way, or fail to parse a construct BigQuery accepts.

The real boundary is the **read-only, dataset-scoped service
account** the queries run as (see `docs/gcp-resources.md`): the
runner service account holds `bigquery.jobUser` plus
`bigquery.dataViewer` on the `rca_agent` dataset only — no DDL or
DML rights, no access to any other dataset or project, no keys
(the runner uses workload identity). Even if a query slipped past
the parser, the account itself cannot write, delete, create, or
read outside its dataset. The two layers defend different
failures: the parser stops the agent from *trying* something
unsafe or expensive, the account stops it from *succeeding*.

## The verdict

Every `check` returns a `Verdict` and appends it to
`Guardrails.attempts`, in order:

- `allowed`: whether the query may run.
- `rule`: a stable id for the decision (`allow`, `parse-error`,
  `multi-statement`, `not-select`, `wildcard-table`,
  `system-table`, `cross-project`, `cross-dataset`,
  `unqualified-table`, `disallowed-table`, `system-time`,
  `table-function`, `query-cap`, `dry-run`, `byte-cap`,
  `daily-byte-cap`).
- `reason`: human-readable, naming the construct or the numbers
  (e.g. "dry run would scan 2,000,000,000 bytes, over the
  1,000,000,000 per-query cap; LIMIT bounds rows returned, not
  bytes scanned").
- `sql`: the SQL as submitted; `rewritten_sql`: after LIMIT
  injection/capping (only when allowed).
- `bytes_scanned`: dry-run bytes, or null offline.
- `limit_applied`: the LIMIT that will run.
- `investigation_id`, `attempt`: per-investigation attempt number.

Phase 5 persists these records to the BigQuery traces table; until
then the in-memory `attempts` list is the record.

## Defaults

All caps live in `GuardrailSettings` (config.py) and can be
overridden with environment variables (`RCA_MAX_RESULT_ROWS`,
`RCA_MAX_BYTES_PER_QUERY`, `RCA_MAX_QUERIES_PER_INVESTIGATION`,
`RCA_MAX_BYTES_PER_DAY`, `RCA_DATASET`, `RCA_ALLOWED_TABLES`).
Defaults: 1,000 rows, 1 GB per query, 20 queries per
investigation, 10 GB per day. They are generous for this
warehouse (16k rows) and will be revisited against real BigQuery
dry-run numbers in Phase 8.

## What is NOT verified here

- The BigQuery warehouse (`sql/bigquery.py`) is written but has
  never run — no GCP project or credential until Phase 8. Its dry
  run is a real dry-run job; the byte numbers it reports are
  untested until then.
- The daily byte ledger is in memory; a server restart resets it.
  Phase 5 persists it to Firestore.
- The guardrails check SQL, not the question: a query can be
  allowed and still answer the wrong question. That is the eval's
  job (Phase 6).
