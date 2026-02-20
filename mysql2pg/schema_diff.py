"""
Schema diff: compare MySQL vs PostgreSQL column types.
"""

import mysql.connector
from mysql.connector import Error as MySQLError
from rich.table import Table
from rich import box

from mysql2pg import console
from mysql2pg.config import MySQLConfig, PGConfig
from mysql2pg.validation import discover_pg_schema, get_pg_column_types


def get_mysql_schema(config: MySQLConfig) -> list[dict]:
    """Get column-level schema info from MySQL."""
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
            f"Cannot connect to MySQL for schema capture: {e}"
        )

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT table_name, column_name, column_type, is_nullable, column_default, column_key
            FROM information_schema.columns
            WHERE table_schema = %s
            ORDER BY table_name, ordinal_position
        """, (config.database,))
        columns = []
        for row in cursor.fetchall():
            columns.append({
                "table": row[0],
                "column": row[1],
                "type": row[2],
                "nullable": row[3],
                "default": row[4],
                "key": row[5],
            })
        return columns
    except MySQLError as e:
        raise RuntimeError(
            f"MySQL schema query failed: {e}"
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


def schema_diff_report(mysql_cfg: MySQLConfig, pg_cfg: PGConfig, mysql_database: str, verbose: bool = False) -> dict:
    """Compare MySQL vs PostgreSQL schemas and highlight type conversions."""
    if verbose:
        console.print("\n  [bold]Schema Diff Report[/bold]")
        console.print("  [dim]Comparing MySQL source types → PostgreSQL target types[/dim]\n")

    report = {
        "diffs": [],
        "identical": 0,
        "conversions": 0,
        "missing": 0,
        "total_columns": 0
    }

    # Get MySQL schema
    try:
        mysql_cols = get_mysql_schema(mysql_cfg)
    except RuntimeError as e:
        if verbose: console.print(f"  [red]✗ Cannot read MySQL schema:[/red] {e}")
        return report

    # Get PG schema
    try:
        target_schema = discover_pg_schema(pg_cfg, mysql_database)
        pg_cols = get_pg_column_types(pg_cfg, schema=target_schema)
    except RuntimeError as e:
        if verbose: console.print(f"  [red]✗ Cannot read PostgreSQL schema:[/red] {e}")
        return report

    # Build lookup: table.column -> type info
    mysql_lookup = {f"{col['table'].lower()}.{col['column'].lower()}": col for col in mysql_cols}
    pg_lookup = {f"{col['table'].lower()}.{col['column'].lower()}": col for col in pg_cols}

    # Build diff table
    diff_table = Table(
        box=box.ROUNDED,
        show_lines=False,
        pad_edge=True,
    )
    diff_table.add_column("Table.Column", style="cyan", min_width=25)
    diff_table.add_column("MySQL Type", style="yellow")
    diff_table.add_column("PG Type", style="green")
    diff_table.add_column("Status", justify="center")

    conversions = 0
    missing = 0
    identical = 0

    all_keys = sorted(set(list(mysql_lookup.keys()) + list(pg_lookup.keys())))
    report["total_columns"] = len(all_keys)

    for key in all_keys:
        m = mysql_lookup.get(key)
        p = pg_lookup.get(key)

        if m and p:
            mysql_type = m["type"]
            pg_type = p["udt_name"]

            mysql_base = mysql_type.split("(")[0].lower().strip()
            pg_base = pg_type.lower().strip()

            equiv_map = {
                "int": {"int4", "int8", "integer"},
                "bigint": {"int8", "bigint", "numeric"},
                "smallint": {"int2", "smallint"},
                "tinyint": {"bool", "boolean"},
                "varchar": {"varchar", "text"},
                "char": {"bpchar", "char"},
                "text": {"text"},
                "longtext": {"text"},
                "mediumtext": {"text"},
                "datetime": {"timestamp"},
                "timestamp": {"timestamp", "timestamptz"},
                "date": {"date"},
                "time": {"time", "timetz"},
                "decimal": {"numeric"},
                "float": {"float4", "float8", "real"},
                "double": {"float8", "double precision"},
                "blob": {"bytea"},
                "longblob": {"bytea"},
                "mediumblob": {"bytea"},
                "bit": {"bool", "bit"},
                "enum": {"text", "varchar"},
                "json": {"json", "jsonb"},
                "boolean": {"bool"},
            }

            is_equivalent = pg_base in equiv_map.get(mysql_base, set())
            is_same = mysql_base == pg_base

            if is_same:
                identical += 1
                report["diffs"].append({"key": key, "mysql": mysql_type, "pg": pg_type, "status": "identical"})
            elif is_equivalent:
                conversions += 1
                diff_table.add_row(key, mysql_type, pg_type, "[green]✓ converted[/green]")
                report["diffs"].append({"key": key, "mysql": mysql_type, "pg": pg_type, "status": "converted"})
            else:
                conversions += 1
                diff_table.add_row(key, mysql_type, pg_type, "[yellow]⚡ mapped[/yellow]")
                report["diffs"].append({"key": key, "mysql": mysql_type, "pg": pg_type, "status": "mapped"})
        elif m:
            missing += 1
            diff_table.add_row(key, m["type"], "—", "[red]✗ missing[/red]")
            report["diffs"].append({"key": key, "mysql": m["type"], "pg": "—", "status": "missing"})

    report["identical"] = identical
    report["conversions"] = conversions
    report["missing"] = missing

    if verbose and (conversions > 0 or missing > 0):
        console.print(diff_table)

    summary_color = "green" if missing == 0 else "red"
    console.print(f"  [{summary_color}]Schema diff:[/] {identical} identical, {conversions} conversions, {missing} missing")
    
    return report
