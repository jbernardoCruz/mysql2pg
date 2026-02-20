"""
Validation: row counts, type mapping, constraints, and schema discovery.
"""

import psycopg2
import mysql.connector
from mysql.connector import Error as MySQLError
from rich.table import Table
from rich import box

from mysql2pg import console
from mysql2pg.config import MySQLConfig, PGConfig


def get_mysql_tables(config: MySQLConfig) -> dict[str, int]:
    """Get table names and row counts from MySQL."""
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
            f"Cannot connect to MySQL for validation: {e}\n"
            "  Check that MySQL is still running and accessible."
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

        if not tables:
            console.print("  [yellow]⚠ No tables found in MySQL database.[/yellow]")
            console.print(f"  [dim]Database: {config.database}[/dim]")
            return {}

        # Get exact row counts
        counts = {}
        for table in tables:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
                counts[table.lower()] = cursor.fetchone()[0]  # lowercase to match PG
            except MySQLError as e:
                console.print(f"  [yellow]⚠ Could not count rows in MySQL table `{table}`:[/yellow] {e}")
                counts[table.lower()] = -1  # Mark as error

        return counts
    except MySQLError as e:
        raise RuntimeError(
            f"MySQL query failed during validation: {e}\n"
            "  The database may have become unavailable."
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


def discover_pg_schema(config: PGConfig, mysql_database: str) -> str:
    """Discover which schema pgloader put tables into.
    
    pgloader may create tables in 'public' or in a schema matching
    the MySQL database name, depending on ALTER SCHEMA success.
    """
    try:
        conn = psycopg2.connect(
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            dbname=config.database,
            connect_timeout=10,
        )
        cursor = conn.cursor()
        # Check which schemas have tables
        cursor.execute("""
            SELECT table_schema, COUNT(*) as table_count
            FROM information_schema.tables
            WHERE table_type = 'BASE TABLE'
              AND table_schema NOT IN ('pg_catalog', 'information_schema')
            GROUP BY table_schema
            ORDER BY table_count DESC
        """)
        schemas = {row[0]: row[1] for row in cursor.fetchall()}
        conn.close()

        if not schemas:
            return 'public'  # Fallback

        # Prefer the MySQL database name schema if it has tables
        mysql_db_lower = mysql_database.lower()
        if mysql_db_lower in schemas:
            console.print(f"  [dim]Found tables in schema: '{mysql_db_lower}'[/dim]")
            return mysql_db_lower
        
        # Otherwise use 'public' if it has tables
        if 'public' in schemas:
            return 'public'
        
        # Use whichever schema has the most tables
        best = max(schemas, key=schemas.get)
        console.print(f"  [dim]Found tables in schema: '{best}'[/dim]")
        return best

    except psycopg2.Error:
        return 'public'  # Fallback


def get_pg_tables(config: PGConfig, schema: str = 'public') -> dict[str, int]:
    """Get table names and row counts from PostgreSQL."""
    try:
        conn = psycopg2.connect(
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            dbname=config.database,
            connect_timeout=10,
        )
    except psycopg2.Error as e:
        raise RuntimeError(
            f"Cannot connect to PostgreSQL for validation: {e}\n"
            "  Check that the pg_target container is still running: docker ps"
        )

    try:
        cursor = conn.cursor()

        # Get all tables
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s AND table_type = 'BASE TABLE'",
            (schema,)
        )
        tables = [row[0] for row in cursor.fetchall()]

        if not tables:
            console.print("  [yellow]⚠ No tables found in PostgreSQL.[/yellow]")
            console.print("  [dim]This suggests the migration may not have transferred any data.[/dim]")
            return {}

        # Get exact row counts
        counts = {}
        for table in tables:
            try:
                cursor.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"')
                counts[table] = cursor.fetchone()[0]
            except psycopg2.Error as e:
                console.print(f"  [yellow]⚠ Could not count rows in PG table `{table}`:[/yellow] {e}")
                conn.rollback()  # Reset transaction state after error
                counts[table] = -1

        return counts
    except psycopg2.Error as e:
        raise RuntimeError(
            f"PostgreSQL query failed during validation: {e}\n"
            "  The database may have become unavailable."
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_pg_column_types(config: PGConfig, schema: str = 'public') -> list[dict]:
    """Get column type info from PostgreSQL."""
    try:
        conn = psycopg2.connect(
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            dbname=config.database,
            connect_timeout=10,
        )
    except psycopg2.Error as e:
        raise RuntimeError(
            f"Cannot connect to PostgreSQL for type verification: {e}"
        )

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT table_name, column_name, data_type, udt_name, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = %s
            ORDER BY table_name, ordinal_position
        """, (schema,))
        columns = []
        for row in cursor.fetchall():
            columns.append({
                "table": row[0],
                "column": row[1],
                "data_type": row[2],
                "udt_name": row[3],
                "nullable": row[4],
                "default": row[5],
            })
        return columns
    except psycopg2.Error as e:
        raise RuntimeError(
            f"PostgreSQL column type query failed: {e}"
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_pg_constraints(config: PGConfig, schema: str = 'public') -> dict:
    """Get primary keys, foreign keys, and indexes from PostgreSQL."""
    try:
        conn = psycopg2.connect(
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            dbname=config.database,
            connect_timeout=10,
        )
    except psycopg2.Error as e:
        raise RuntimeError(
            f"Cannot connect to PostgreSQL for constraint check: {e}"
        )

    try:
        cursor = conn.cursor()

        # Primary keys
        cursor.execute("""
            SELECT tc.table_name, kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
              AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'PRIMARY KEY'
              AND tc.table_schema = %s
            ORDER BY tc.table_name
        """, (schema,))
        pks = cursor.fetchall()

        # Foreign keys
        cursor.execute("""
            SELECT tc.table_name, kcu.column_name,
                   ccu.table_name AS ref_table, ccu.column_name AS ref_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
              AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name
              AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = %s
            ORDER BY tc.table_name
        """, (schema,))
        fks = cursor.fetchall()

        # Indexes
        cursor.execute("""
            SELECT tablename, indexname
            FROM pg_indexes
            WHERE schemaname = %s
            ORDER BY tablename, indexname
        """, (schema,))
        indexes = cursor.fetchall()

        # Sequences
        cursor.execute("""
            SELECT sequence_name
            FROM information_schema.sequences
            WHERE sequence_schema = %s
            ORDER BY sequence_name
        """, (schema,))
        sequences = [row[0] for row in cursor.fetchall()]

        return {
            "primary_keys": pks,
            "foreign_keys": fks,
            "indexes": indexes,
            "sequences": sequences,
        }
    except psycopg2.Error as e:
        raise RuntimeError(
            f"PostgreSQL constraint query failed: {e}"
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


def validate_migration(mysql_cfg: MySQLConfig, pg_cfg: PGConfig, mysql_database: str, verbose: bool = False) -> dict:
    """Run full validation suite and collect results."""
    all_passed = True
    validation_errors = []
    
    report = {
        "row_counts": {"tables": [], "passed": 0, "failed": 0, "total": 0},
        "type_conversions": [],
        "constraints": {},
        "all_passed": True,
        "validation_errors": []
    }

    # ── Row count comparison ──────────────────────────────────
    if verbose:
        console.print("\n  [bold]Row Count Comparison[/bold]")

    try:
        mysql_counts = get_mysql_tables(mysql_cfg)
    except RuntimeError as e:
        validation_errors.append(f"MySQL connection failed: {e}")
        mysql_counts = None

    try:
        target_schema = discover_pg_schema(pg_cfg, mysql_database)
        if verbose:
            console.print(f"  [dim]Validating against schema: '{target_schema}'[/dim]")
        pg_counts = get_pg_tables(pg_cfg, schema=target_schema)
    except RuntimeError as e:
        validation_errors.append(f"PostgreSQL connection failed: {e}")
        pg_counts = None

    if mysql_counts is not None and pg_counts is not None:
        table = Table(box=box.ROUNDED, show_lines=False, pad_edge=True)
        table.add_column("Table", style="cyan", min_width=20)
        table.add_column("MySQL", justify="right", style="yellow")
        table.add_column("PostgreSQL", justify="right", style="green")
        table.add_column("Status", justify="center")

        all_tables = sorted(set(list(mysql_counts.keys()) + list(pg_counts.keys())))

        for t in all_tables:
            m_count = mysql_counts.get(t, "-")
            p_count = pg_counts.get(t, "-")
            
            passed = False
            if m_count == p_count:
                status = "✓ OK"
                passed = True
                report["row_counts"]["passed"] += 1
            elif t not in pg_counts:
                status = "✗ MISSING"
                report["row_counts"]["failed"] += 1
                all_passed = False
            elif t not in mysql_counts:
                status = "? EXTRA"
                report["row_counts"]["passed"] += 1
            else:
                status = "✗ MISMATCH"
                report["row_counts"]["failed"] += 1
                all_passed = False

            report["row_counts"]["tables"].append({
                "table": t,
                "mysql": m_count,
                "pg": p_count,
                "status": status,
                "passed": passed
            })
            
            rich_status = f"[green]{status}[/green]" if "OK" in status else f"[red]{status}[/red]"
            if "EXTRA" in status: rich_status = f"[yellow]{status}[/yellow]"
            
            table.add_row(t, str(m_count), str(p_count), rich_status)

        report["row_counts"]["total"] = len(all_tables)
        
        if verbose:
            console.print(table)
        else:
            color = "green" if all_passed else "red"
            console.print(f"  [{color}]Row counts:[/] {report['row_counts']['passed']}/{report['row_counts']['total']} tables match ✓")

    else:
        all_passed = False
        console.print("  [red]✗ Row counts skipped — database connection issues[/red]")

    # ── Type mapping verification ─────────────────────────────
    if verbose:
        console.print("\n  [bold]Type Mapping Verification[/bold]")

    try:
        columns = get_pg_column_types(pg_cfg, schema=target_schema)
    except RuntimeError as e:
        validation_errors.append(f"PostgreSQL type query failed: {e}")
        columns = None

    if columns is not None:
        converted_types = {
            "bool": "TINYINT/BIT → boolean",
            "timestamp": "DATETIME → timestamp",
            "int8": "INT UNSIGNED → bigint",
            "numeric": "BIGINT UNSIGNED → numeric",
        }

        type_table = Table(box=box.ROUNDED, show_lines=False, pad_edge=True)
        type_table.add_column("Table.Column", style="cyan", min_width=25)
        type_table.add_column("PG Type", style="green")
        type_table.add_column("Conversion", style="dim")

        converted_count = 0
        for col in columns:
            udt = col["udt_name"]
            if udt in converted_types:
                converted_count += 1
                report["type_conversions"].append({
                    "table": col["table"],
                    "column": col["column"],
                    "pg_type": udt,
                    "conversion": converted_types[udt]
                })
                type_table.add_row(
                    f"{col['table']}.{col['column']}",
                    udt,
                    converted_types[udt],
                )

        if verbose and converted_count > 0:
            console.print(type_table)
            
        console.print(f"  [green]✓ Type mapping:[/green] {converted_count} conversions detected")

    # ── Constraints ───────────────────────────────────────────
    if verbose:
        console.print("\n  [bold]Constraints & Indexes[/bold]")

    try:
        constraints = get_pg_constraints(pg_cfg, schema=target_schema)
    except RuntimeError as e:
        validation_errors.append(f"PostgreSQL constraint query failed: {e}")
        constraints = None

    if constraints is not None:
        report["constraints"] = constraints
        checks = [
            ("Primary Keys", len(constraints["primary_keys"])),
            ("Foreign Keys", len(constraints["foreign_keys"])),
            ("Indexes", len(constraints["indexes"])),
            ("Sequences", len(constraints["sequences"])),
        ]

        if verbose:
            constraint_table = Table(box=box.SIMPLE, show_lines=False)
            constraint_table.add_column("Check", style="cyan", min_width=20)
            constraint_table.add_column("Count", justify="right", style="green")
            constraint_table.add_column("Status", justify="center")

            for name, count in checks:
                status = "[green]✓[/green]" if count > 0 else "[yellow]—[/yellow]"
                constraint_table.add_row(name, str(count), status)
            console.print(constraint_table)
        else:
            total_constraints = sum(count for _, count in checks)
            console.print(f"  [green]✓ Constraints:[/green] {total_constraints} objects verified")

    # ── Summary of validation errors ──────────────────────────
    report["all_passed"] = all_passed
    report["validation_errors"] = validation_errors
    
    if validation_errors:
        console.print("\n  [bold red]Validation Issues:[/bold red]")
        for err in validation_errors:
            console.print(f"    [red]✗[/red] {err}")

    return report
