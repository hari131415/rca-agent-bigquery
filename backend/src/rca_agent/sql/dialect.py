"""The BigQuery <-> DuckDB dialect boundary, in one module.

The agent only ever writes BigQuery SQL. Offline execution
transpiles it to DuckDB with sqlglot; agent logic never branches
on the warehouse. Known differences (kept here, not scattered):

- Backticks for identifiers work in both; sqlglot normalizes them.
- The BigQuery dataset qualifier (`rca_agent.complaints`) has no
  counterpart in the offline DuckDB file, whose tables are
  unqualified; the transpiler strips the qualifier.
- SAFE_DIVIDE exists in BigQuery only; sqlglot rewrites it to a
  CASE in DuckDB.
- DATE_TRUNC takes (date, part) in BigQuery and (part, date) in
  DuckDB; sqlglot swaps the arguments. It returns a TIMESTAMP in
  DuckDB where BigQuery returns a DATE; values agree, types do not.
- EXTRACT / FORMAT_DATE / PARSE_DATE are covered by the transpiler.
- UNNEST and arrays are avoided: the schema is flat by design, so
  no generated SQL uses them (see docs/data-plan.md).
- A dry run in DuckDB is EXPLAIN (the query must plan); in
  BigQuery it is a real dry-run job reporting bytes scanned.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp


def to_duckdb(sql: str, dataset: str = "rca_agent") -> str:
    """Transpile one BigQuery SQL string to DuckDB SQL.

    The dataset qualifier is stripped: the offline DuckDB file
    holds the tables unqualified, while BigQuery needs them
    qualified. The strip is structural (AST table nodes), not a
    string replace, so a qualifier inside a string literal is
    untouched.
    """
    tree = sqlglot.parse_one(sql, read="bigquery")
    for table in tree.find_all(exp.Table):
        if table.db and table.db.lower() == dataset.lower():
            table.set("db", None)
    return tree.sql(dialect="duckdb")
