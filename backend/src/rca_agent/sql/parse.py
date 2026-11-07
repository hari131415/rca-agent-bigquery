"""SQL safety checks, parsed with sqlglot, not regex.

The agent's SQL is parsed into an AST and the tree is walked. A
comment, CTE, union, or second statement is a structural fact of
the parse, not a string to pattern-match — that is what makes the
bypass tests meaningful: `SeLeCt`, `/* DROP */`, a CTE hiding a
disallowed table, and a UNION smuggling one in are all caught by
the same walk.

Checks, in order (the first failure is the verdict):
1. The SQL must parse as BigQuery SQL. A parse error is a refusal
   (fail-closed): an unparsable query never runs.
2. Exactly one statement. `;` plus anything else is refused.
3. The statement must be a SELECT (or a UNION of SELECTs). DDL,
   DML, scripting (DECLARE, BEGIN...END, CALL, SET), CREATE TEMP
   FUNCTION, EXPORT DATA, and anything else are refused by kind.
4. Every table reference must be `dataset.table`, in the allowed
   dataset, on the allow-list, and not a wildcard, system view, or
   table function. CTE names are collected first so a CTE is not
   mistaken for a real table; the tables inside the CTE are still
   checked.
5. No FOR SYSTEM_TIME AS OF (time travel) and no cross-project
   references.
6. No ML/AI functions and no remote or user-defined functions:
   their cost is not visible in dry-run bytes (a model call or an
   external call does not show up in bytes scanned), so the byte
   caps cannot bound them. BigQuery's only dotted built-ins are
   ML./AI., so any dotted function call is refused; a bare
   user-defined function cannot resolve anyway, because CREATE
   FUNCTION is refused and Terraform creates no UDFs — and if one
   did, the dry run would fail on the unknown name.

LIMIT is enforced on the surviving query: a missing LIMIT is
injected, a LIMIT above the cap is lowered. A LIMIT that is not a
non-negative integer is refused outright. LIMIT bounds result
size only — cost is the dry-run byte cap in guardrails.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

SYSTEM_TABLES = frozenset({"__TABLES__", "__TABLES_SUMMARY__"})

# BigQuery ML/AI functions, as sqlglot parses them. sqlglot gives
# these dedicated classes (Predict for ML.PREDICT, GenerateBool for
# AI.GENERATE_BOOL, ...), so the check is by class, not by name —
# the rendered SQL drops the ML./AI. prefix. GENERATE_SERIES and
# friends are deliberately NOT here: they are cheap built-ins.
ML_AI_FUNCTION_CLASSES = frozenset(
    {
        "Predict",
        "MLForecast",
        "MLTranslate",
        "AIGenerate",
        "AIForecast",
        "GenerateBool",
        "GenerateDouble",
        "GenerateInt",
        "GenerateTable",
        "GenerateText",
        "GenerateEmbedding",
    }
)


def _is_ml_ai_function(node: exp.Func) -> bool:
    """True for a BigQuery ML/AI function node.

    Class-name prefix covers future ML*/AI* classes sqlglot adds;
    the explicit set covers the ones that do not follow the
    convention (Predict, Generate*).
    """
    name = type(node).__name__
    return name.startswith(("ML", "AI")) or name in ML_AI_FUNCTION_CLASSES


@dataclass(frozen=True)
class SqlCheck:
    """Result of the parse-time checks, before any dry run."""

    allowed: bool
    rule: str
    reason: str
    rewritten_sql: str | None = None
    limit_applied: int | None = None
    limit_injected: bool = False


def _refuse(rule: str, reason: str) -> SqlCheck:
    return SqlCheck(allowed=False, rule=rule, reason=reason)


def _statement_kind_name(statement: exp.Expr | exp.Expression) -> str:
    kind = type(statement).__name__
    if kind == "Command":
        return "scripting statement (CALL, BEGIN, or another unsupported command)"
    return kind


def _current_limit(tree: exp.Expression) -> int | None:
    """The top-level LIMIT as an integer, or None when there is none.

    Raises ValueError when a LIMIT exists but is not a non-negative
    integer (negative, fractional, or a string): a nonsense LIMIT
    is a refusal, not something to silently replace.
    """
    # sqlglot 30 stores the count in Limit.expression, not Limit.this.
    limit = tree.args.get("limit")
    if limit is None:
        return None
    count = limit.expression if isinstance(limit, exp.Limit) else limit
    if count is None:
        count = limit.this if isinstance(limit, exp.Limit) else None
    try:
        value = int(str(count))
    except (TypeError, ValueError):
        raise ValueError(f"LIMIT is not an integer: {count}") from None
    if value < 0:
        raise ValueError(f"LIMIT is negative: {value}")
    return value


def _apply_limit(tree: exp.Expression, max_rows: int) -> tuple[str, int, bool]:
    """Cap or inject the top-level LIMIT; returns (sql, limit, injected)."""
    current = _current_limit(tree)
    if current is not None and current <= max_rows:
        return tree.sql(dialect="bigquery"), current, False
    tree.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
    return tree.sql(dialect="bigquery"), max_rows, current is None


def check_sql(
    sql: str,
    *,
    dataset: str,
    allowed_tables: frozenset[str],
    max_rows: int,
) -> SqlCheck:
    """Parse and check one SQL string; never executes anything."""
    try:
        statements = sqlglot.parse(sql, read="bigquery")
    except (sqlglot.ParseError, sqlglot.TokenError) as exc:
        return _refuse("parse-error", f"could not parse as BigQuery SQL: {exc}")
    if not statements or any(statement is None for statement in statements):
        return _refuse("parse-error", "could not parse as BigQuery SQL: empty statement")
    if len(statements) > 1:
        return _refuse(
            "multi-statement",
            f"multiple statements are not allowed (found {len(statements)})",
        )
    tree = statements[0]
    if tree is None:
        return _refuse("parse-error", "could not parse as BigQuery SQL: empty statement")
    if not isinstance(tree, (exp.Select, exp.Union)):
        return _refuse(
            "not-select",
            f"only SELECT queries are allowed, got {_statement_kind_name(tree)}",
        )

    cte_names = {cte.alias_or_name.upper() for cte in tree.find_all(exp.CTE) if cte.alias_or_name}
    # Function policy runs before the table walk: sqlglot parses an
    # AI function's MODEL argument as a table reference, so a query
    # like AI.GENERATE_BOOL('...') would otherwise be misreported
    # as an unqualified table instead of a disallowed function.
    for func in tree.find_all(exp.Func):
        if _is_ml_ai_function(func):
            return _refuse(
                "disallowed-function",
                f"ML/AI functions are not allowed: their cost is not visible "
                f"in dry-run bytes: {func.sql(dialect='bigquery')}",
            )
    for dot in tree.find_all(exp.Dot):
        if isinstance(dot.expression, exp.Func):
            return _refuse(
                "disallowed-function",
                f"remote or user-defined functions are not allowed: they call "
                f"outside BigQuery and their cost is not visible in dry-run "
                f"bytes: {dot.sql(dialect='bigquery')}",
            )
    for table in tree.find_all(exp.Table):
        # Table functions (EXTERNAL_QUERY, ...) have no table name, so
        # this check must run before the empty-name skip below.
        if isinstance(table.this, exp.Func):
            return _refuse(
                "table-function",
                f"table functions are not allowed: {table.sql(dialect='bigquery')}",
            )
        name = table.name
        if not name:
            continue
        if name.upper() in cte_names:
            continue
        if "version" in table.args:
            return _refuse(
                "system-time",
                "FOR SYSTEM_TIME AS OF (time travel) is not allowed",
            )
        if "*" in name:
            return _refuse(
                "wildcard-table",
                f"wildcard tables are not allowed: {table.sql(dialect='bigquery')}",
            )
        upper_name = name.upper()
        if (
            upper_name in SYSTEM_TABLES
            or upper_name.startswith("__")
            or upper_name.startswith("INFORMATION_SCHEMA")
            or table.db.upper() == "INFORMATION_SCHEMA"
        ):
            return _refuse(
                "system-table",
                f"system views are not allowed: {table.sql(dialect='bigquery')}",
            )
        if table.catalog:
            return _refuse(
                "cross-project",
                f"cross-project references are not allowed: {table.sql(dialect='bigquery')}",
            )
        if not table.db:
            return _refuse(
                "unqualified-table",
                f"tables must be qualified as dataset.table: {name}",
            )
        if table.db.lower() != dataset.lower():
            return _refuse(
                "cross-dataset",
                f"table is in dataset '{table.db}', only '{dataset}' is allowed: "
                f"{table.sql(dialect='bigquery')}",
            )
        if name.lower() not in allowed_tables:
            return _refuse(
                "disallowed-table",
                f"table '{dataset}.{name}' is not on the allow-list",
            )

    try:
        rewritten, limit, injected = _apply_limit(tree, max_rows)
    except ValueError as exc:
        return _refuse("invalid-limit", str(exc))
    return SqlCheck(
        allowed=True,
        rule="allow",
        reason="allowed",
        rewritten_sql=rewritten,
        limit_applied=limit,
        limit_injected=injected,
    )
