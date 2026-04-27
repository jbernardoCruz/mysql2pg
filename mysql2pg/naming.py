"""
Naming conventions: camelCase/PascalCase → snake_case conversion
and SQL rename generation for Prisma compatibility.
"""

import re

import mysql.connector
from mysql.connector import Error as MySQLError

from mysql2pg import console
from mysql2pg.config import MySQLConfig


# ═════════════════════════════════════════════════════════════
# Core conversion functions
# ═════════════════════════════════════════════════════════════

def camel_to_snake(name: str) -> str:
    """Convert camelCase or PascalCase to snake_case.

    Examples:
        firstName     → first_name
        UserAccount   → user_account
        HTMLParser    → html_parser
        already_snake → already_snake
        useraccounts  → useraccounts  (no uppercase = no change)
        getHTTPSUrl   → get_https_url
    """
    if not name or "_" in name:
        # Already has underscores — likely already snake_case
        return name.lower()

    # Insert underscore before uppercase letters that follow lowercase/digits
    # e.g. firstName → first_Name → first_name
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)

    # Handle consecutive uppercase (acronyms): HTMLParser → HTML_Parser → html_parser
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)

    return s.lower()


def needs_snake_conversion(name: str) -> bool:
    """Check if a name needs camelCase → snake_case conversion.

    Returns True if the name contains uppercase letters (meaning
    the original MySQL name was camelCase/PascalCase and pgloader's
    `downcase identifiers` just lowercased it without adding underscores).
    """
    # If the original name has uppercase, it needs conversion
    return bool(re.search(r"[A-Z]", name))


def is_valid_snake_case(name: str) -> bool:
    """Check if a name is already valid snake_case."""
    return bool(re.match(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$", name))


# ═════════════════════════════════════════════════════════════
# MySQL schema introspection for original names
# ═════════════════════════════════════════════════════════════

def get_mysql_original_names(config: MySQLConfig) -> dict:
    """Get original table and column names from MySQL.

    Returns:
        {
            "tables": {"OriginalTableName": ["OrigCol1", "OrigCol2", ...]},
            "table_map": {"originaltablename": "OriginalTableName"},
        }
    """
    try:
        conn = mysql.connector.connect(
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            database=config.database,
            connect_timeout=10,
        )
    except MySQLError as e:
        raise RuntimeError(
            f"Cannot connect to MySQL for name introspection: {e}"
        )

    try:
        cursor = conn.cursor()

        # Get all tables
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s AND table_type = 'BASE TABLE'",
            (config.database,),
        )
        tables = [row[0] for row in cursor.fetchall()]

        # Build structure with original names
        result = {"tables": {}, "table_map": {}}

        for table in tables:
            result["table_map"][table.lower()] = table

            # Get columns for this table
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s "
                "ORDER BY ordinal_position",
                (config.database, table),
            )
            columns = [row[0] for row in cursor.fetchall()]
            result["tables"][table] = columns

        return result
    except MySQLError as e:
        raise RuntimeError(
            f"MySQL query failed during name introspection: {e}"
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════
# SQL generation for renames
# ═════════════════════════════════════════════════════════════

def generate_rename_sql(mysql_cfg: MySQLConfig, schema: str = "legacy") -> tuple[list[str], dict]:
    """Generate ALTER TABLE/COLUMN RENAME statements for snake_case conversion.

    Uses the original MySQL names as source of truth, applies camel_to_snake(),
    and generates SQL to rename from pgloader's lowercased names to proper snake_case.

    Returns:
        (sql_statements, report)
        where report = {
            "tables_renamed": [{"from": old, "to": new}, ...],
            "columns_renamed": [{"table": tbl, "from": old, "to": new}, ...],
        }
    """
    original = get_mysql_original_names(mysql_cfg)

    sql_statements = []
    report = {
        "tables_renamed": [],
        "columns_renamed": [],
    }

    # We process column renames FIRST (before table rename),
    # because the ALTER TABLE references the current (old) table name.
    # Then we rename the tables.

    column_renames = []  # (pg_table_name, pg_col_name, new_col_name)
    table_renames = []   # (pg_table_name, new_table_name)

    for orig_table, orig_columns in original["tables"].items():
        pg_table_name = orig_table.lower()  # What pgloader created
        snake_table_name = camel_to_snake(orig_table)

        # Column renames (done before table rename)
        for orig_col in orig_columns:
            pg_col_name = orig_col.lower()  # What pgloader created
            snake_col_name = camel_to_snake(orig_col)

            if pg_col_name != snake_col_name:
                column_renames.append((pg_table_name, pg_col_name, snake_col_name))
                report["columns_renamed"].append({
                    "table": snake_table_name,
                    "from": pg_col_name,
                    "to": snake_col_name,
                })

        # Table rename
        if pg_table_name != snake_table_name:
            table_renames.append((pg_table_name, snake_table_name))
            report["tables_renamed"].append({
                "from": pg_table_name,
                "to": snake_table_name,
            })

    # Generate SQL: columns first, then tables
    for pg_table, pg_col, new_col in column_renames:
        sql_statements.append(
            f'ALTER TABLE "{schema}"."{pg_table}" '
            f'RENAME COLUMN "{pg_col}" TO "{new_col}";'
        )

    for pg_table, new_table in table_renames:
        sql_statements.append(
            f'ALTER TABLE "{schema}"."{pg_table}" '
            f'RENAME TO "{new_table}";'
        )

    return sql_statements, report
