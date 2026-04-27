"""
MySQL ENUM extraction and PostgreSQL native ENUM creation.

Converts pgloader's ENUM→TEXT migration into proper native
PostgreSQL ENUM types for Prisma compatibility.
"""

import re

import mysql.connector
from mysql.connector import Error as MySQLError

from mysql2pg import console
from mysql2pg.config import MySQLConfig
from mysql2pg.naming import camel_to_snake


# ═════════════════════════════════════════════════════════════
# MySQL ENUM extraction
# ═════════════════════════════════════════════════════════════

def extract_mysql_enums(config: MySQLConfig) -> list[dict]:
    """Extract all ENUM columns and their allowed values from MySQL.

    Returns a list of dicts:
        [
            {
                "table": "users",          # original MySQL table name
                "column": "status",        # original MySQL column name
                "values": ["active", "inactive"],
                "is_nullable": True/False,
                "default": "active" or None,
            },
            ...
        ]
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
            f"Cannot connect to MySQL for ENUM extraction: {e}"
        )

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT table_name, column_name, column_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = %s AND data_type = 'enum'
            ORDER BY table_name, ordinal_position
        """, (config.database,))

        enums = []
        for row in cursor.fetchall():
            table_name = row[0]
            column_name = row[1]
            column_type = row[2]  # e.g. "enum('active','inactive')"
            is_nullable = row[3] == "YES"
            default_val = row[4]

            # Parse ENUM values from column_type
            values = _parse_enum_values(column_type)

            if values:
                enums.append({
                    "table": table_name,
                    "column": column_name,
                    "values": values,
                    "is_nullable": is_nullable,
                    "default": default_val,
                })

        return enums
    except MySQLError as e:
        raise RuntimeError(
            f"MySQL query failed during ENUM extraction: {e}"
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _parse_enum_values(column_type: str) -> list[str]:
    """Parse ENUM values from MySQL column_type string.

    Example: "enum('active','inactive','pending')" → ["active", "inactive", "pending"]
    """
    match = re.match(r"enum\((.+)\)", column_type, re.IGNORECASE)
    if not match:
        return []

    # Extract quoted values
    raw = match.group(1)
    values = re.findall(r"'([^']*)'", raw)
    return values


# ═════════════════════════════════════════════════════════════
# PostgreSQL ENUM SQL generation
# ═════════════════════════════════════════════════════════════

def generate_enum_type_name(table_name: str, column_name: str) -> str:
    """Generate a PostgreSQL ENUM type name using {table}_{column} convention.

    Both table and column are converted to snake_case.
    Example: ("UserAccounts", "accountStatus") → "user_accounts_account_status"
    """
    snake_table = camel_to_snake(table_name)
    snake_col = camel_to_snake(column_name)
    return f"{snake_table}_{snake_col}"


def generate_enum_sql(
    mysql_enums: list[dict],
    schema: str = "legacy",
    use_snake_case: bool = True,
) -> tuple[list[str], list[dict]]:
    """Generate SQL to create native PostgreSQL ENUMs and alter columns.

    This runs AFTER pgloader has migrated data (ENUM→TEXT), so:
    1. Create the ENUM type
    2. Alter the column from TEXT to the new ENUM type

    Args:
        mysql_enums: Output from extract_mysql_enums()
        schema: PostgreSQL schema name
        use_snake_case: If True, use snake_case names for enum types and
                        reference snake_case table/column names (assumes
                        rename has already happened or will happen)

    Returns:
        (sql_statements, report)
        where report = [{"type_name": ..., "table": ..., "column": ..., "values": [...]}, ...]
    """
    sql_statements = []
    report = []

    for enum_info in mysql_enums:
        orig_table = enum_info["table"]
        orig_column = enum_info["column"]
        values = enum_info["values"]

        # Generate the ENUM type name
        type_name = generate_enum_type_name(orig_table, orig_column)

        # Determine the actual PG table/column names
        # (after pgloader downcase + optional snake_case rename)
        if use_snake_case:
            pg_table = camel_to_snake(orig_table)
            pg_column = camel_to_snake(orig_column)
        else:
            pg_table = orig_table.lower()
            pg_column = orig_column.lower()

        # 1. Create the ENUM type
        quoted_values = ", ".join(f"'{v}'" for v in values)
        sql_statements.append(
            f'CREATE TYPE "{schema}"."{type_name}" '
            f"AS ENUM ({quoted_values});"
        )

        # 2. Alter the column from TEXT to the ENUM type
        sql_statements.append(
            f'ALTER TABLE "{schema}"."{pg_table}" '
            f'ALTER COLUMN "{pg_column}" '
            f'TYPE "{schema}"."{type_name}" '
            f'USING "{pg_column}"::"{schema}"."{type_name}";'
        )

        report.append({
            "type_name": type_name,
            "table": pg_table,
            "column": pg_column,
            "values": values,
        })

    return sql_statements, report
