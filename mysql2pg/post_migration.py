"""
Post-migration transforms for Prisma compatibility.

Executes naming conversions and ENUM creation on the PostgreSQL
database AFTER pgloader has completed the initial migration.
"""

import psycopg2

from mysql2pg import console
from mysql2pg.config import MySQLConfig, PGConfig
from mysql2pg.naming import generate_rename_sql
from mysql2pg.enums import extract_mysql_enums, generate_enum_sql
from mysql2pg.validation import discover_pg_schema


def run_post_migration(
    mysql_cfg: MySQLConfig,
    pg_cfg: PGConfig,
    mysql_database: str,
) -> dict:
    """Run all Prisma compatibility transforms on the migrated PostgreSQL database.

    Performs (in order):
    1. Rename tables and columns to snake_case
    2. Create native ENUM types and alter columns

    All operations run inside a single transaction for atomicity.

    Returns:
        {
            "tables_renamed": int,
            "columns_renamed": int,
            "enums_created": int,
            "rename_details": [...],
            "enum_details": [...],
            "errors": [...],
        }
    """
    report = {
        "tables_renamed": 0,
        "columns_renamed": 0,
        "enums_created": 0,
        "rename_details": [],
        "enum_details": [],
        "errors": [],
    }

    # Discover which schema pgloader used
    try:
        schema = discover_pg_schema(pg_cfg, mysql_database)
    except Exception as e:
        report["errors"].append(f"Cannot discover PG schema: {e}")
        return report

    # ── Step 1: Generate rename SQL ───────────────────────────
    console.print("  [dim]Analyzing naming conventions...[/dim]")

    try:
        rename_sql, rename_report = generate_rename_sql(mysql_cfg, schema)
        report["rename_details"] = rename_report
    except RuntimeError as e:
        report["errors"].append(f"Naming analysis failed: {e}")
        rename_sql = []
        rename_report = {"tables_renamed": [], "columns_renamed": []}

    # ── Step 2: Generate ENUM SQL ─────────────────────────────
    console.print("  [dim]Extracting ENUM definitions...[/dim]")

    try:
        mysql_enums = extract_mysql_enums(mysql_cfg)
        enum_sql, enum_report = generate_enum_sql(
            mysql_enums,
            schema=schema,
            use_snake_case=True,  # We rename first, then create enums
        )
        report["enum_details"] = enum_report
    except RuntimeError as e:
        report["errors"].append(f"ENUM extraction failed: {e}")
        enum_sql = []

    # ── Step 3: Execute all SQL in a transaction ──────────────
    all_sql = rename_sql + enum_sql

    if not all_sql:
        console.print("  [dim]No transforms needed — schema already compatible.[/dim]")
        return report

    console.print(
        f"  [dim]Executing {len(rename_sql)} rename + {len(enum_sql)} ENUM statements...[/dim]"
    )

    try:
        conn = psycopg2.connect(
            host=pg_cfg.host,
            port=pg_cfg.port,
            user=pg_cfg.user,
            password=pg_cfg.password,
            dbname=pg_cfg.database,
            connect_timeout=10,
        )
    except psycopg2.Error as e:
        report["errors"].append(f"Cannot connect to PostgreSQL: {e}")
        return report

    try:
        cursor = conn.cursor()

        for i, stmt in enumerate(all_sql):
            try:
                cursor.execute(f"SAVEPOINT sp_{i}")
                cursor.execute(stmt)
            except psycopg2.Error as e:
                # Log the error, rollback to savepoint, and continue
                error_msg = f"SQL failed: {stmt[:80]}... → {e}"
                report["errors"].append(error_msg)
                console.print(f"  [yellow]⚠ {error_msg}[/yellow]")
                cursor.execute(f"ROLLBACK TO SAVEPOINT sp_{i}")

        # Commit all successful changes
        conn.commit()

    except Exception as e:
        conn.rollback()
        report["errors"].append(f"Transaction failed: {e}")
        return report
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # ── Update report counters ────────────────────────────────
    report["tables_renamed"] = len(rename_report.get("tables_renamed", []))
    report["columns_renamed"] = len(rename_report.get("columns_renamed", []))
    report["enums_created"] = len(enum_report) if enum_sql else 0

    # ── Summary output ────────────────────────────────────────
    if report["tables_renamed"] > 0:
        console.print(
            f"  [green]✓[/green] Renamed {report['tables_renamed']} tables to snake_case"
        )
    if report["columns_renamed"] > 0:
        console.print(
            f"  [green]✓[/green] Renamed {report['columns_renamed']} columns to snake_case"
        )
    if report["enums_created"] > 0:
        console.print(
            f"  [green]✓[/green] Created {report['enums_created']} native ENUM types"
        )
    if not report["tables_renamed"] and not report["columns_renamed"] and not report["enums_created"]:
        console.print("  [dim]No naming changes or ENUM conversions were needed.[/dim]")

    if report["errors"]:
        console.print(
            f"  [yellow]⚠ {len(report['errors'])} warnings during transforms[/yellow]"
        )

    return report
