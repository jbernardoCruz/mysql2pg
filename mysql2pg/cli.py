"""
CLI: argument parsing, dry-run mode, and main migration pipeline.
"""

import sys
import argparse

import docker
from docker.errors import DockerException, NotFound
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import box

from mysql2pg import (
    console, CONFIG_FILE,
    PG_IMAGE, PGLOADER_IMAGE,
)
from mysql2pg.config import init_config, load_config, test_mysql_connection
from mysql2pg.docker_utils import get_docker_client, start_postgres, wait_for_postgres
from mysql2pg.pgloader import generate_pgloader_config, run_pgloader_with_progress
from mysql2pg.validation import validate_migration, get_mysql_tables
from mysql2pg.schema_diff import get_mysql_schema, schema_diff_report
from mysql2pg.reporting import generate_html_report


def parse_args():
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="MySQL → PostgreSQL Migration Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python migrate.py --init       Create config file template\n"
            "  python migrate.py --dry-run    Validate without migrating\n"
            "  python migrate.py              Run full migration\n"
        ),
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Create migration_config.json template and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config, test connections, preview schema — no migration",
    )
    parser.add_argument("--verbose", action="store_true", help="Show full detailed tables in console")
    return parser.parse_args()


def dry_run(mysql_cfg, pg_cfg):
    """Validate config and preview what would be migrated, without actually migrating."""
    console.print(
        Panel(
            "[bold white]MySQL → PostgreSQL Migration Tool[/bold white]\n"
            "[dim]🔍 DRY RUN — No data will be migrated[/dim]",
            border_style="bright_magenta",
            padding=(1, 4),
        )
    )

    all_ok = True

    # ── 1. Config ─────────────────────────────────────────────
    console.print("[bold yellow][1/4][/bold yellow] Configuration")
    console.print(
        Panel(
            f"[bold]Source:[/bold]  mysql://{mysql_cfg.user}:****@{mysql_cfg.host}:{mysql_cfg.port}/{mysql_cfg.database}\n"
            f"[bold]Target:[/bold]  postgresql://{pg_cfg.user}:****@localhost:{pg_cfg.port}/{pg_cfg.database}",
            border_style="dim",
        )
    )
    console.print("  [green]✓[/green] Config is valid\n")

    # ── 2. MySQL connection ───────────────────────────────────
    console.print("[bold yellow][2/4][/bold yellow] MySQL connection")
    connected = test_mysql_connection(mysql_cfg)
    if not connected:
        console.print("  [red]✗ MySQL is not reachable.[/red]")
        all_ok = False
    else:
        console.print("  [green]✓[/green] MySQL is reachable\n")

    # ── 3. Docker ─────────────────────────────────────────────
    console.print("[bold yellow][3/4][/bold yellow] Docker availability")
    try:
        client = docker.from_env()
        client.ping()
        console.print("  [green]✓[/green] Docker is running")

        # Check if PG image is available (only if we're running it)
        if pg_cfg.host in ("localhost", "127.0.0.1"):
            try:
                client.images.get(PG_IMAGE)
                console.print(f"  [green]✓[/green] Image [cyan]{PG_IMAGE}[/cyan] is available")
            except NotFound:
                console.print(f"  [yellow]⚠[/yellow] Image [cyan]{PG_IMAGE}[/cyan] will be pulled on first run")
        else:
            console.print(f"  [dim]Skipping PG image check (remote host: {pg_cfg.host})[/dim]")

        try:
            client.images.get(PGLOADER_IMAGE)
            console.print(f"  [green]✓[/green] Image [cyan]{PGLOADER_IMAGE}[/cyan] is available")
        except NotFound:
            console.print(f"  [yellow]⚠[/yellow] Image [cyan]{PGLOADER_IMAGE}[/cyan] will be pulled on first run")

        console.print("")
    except DockerException:
        console.print("  [red]✗ Docker is not running.[/red]")
        console.print("  [dim]Start it: sudo systemctl start docker[/dim]\n")
        all_ok = False

    # ── 4. Schema preview ─────────────────────────────────────
    console.print("[bold yellow][4/4][/bold yellow] Source database preview")
    if connected:
        try:
            mysql_counts = get_mysql_tables(mysql_cfg)
            mysql_schema = get_mysql_schema(mysql_cfg)

            if not mysql_counts:
                console.print("  [yellow]⚠ No tables found in the MySQL database.[/yellow]\n")
            else:
                # Tables and row counts
                preview_table = Table(box=box.ROUNDED, show_lines=False, pad_edge=True)
                preview_table.add_column("Table", style="cyan", min_width=20)
                preview_table.add_column("Rows", justify="right", style="yellow")
                preview_table.add_column("Columns", justify="right", style="green")

                # Count columns per table
                cols_per_table = {}
                for col in mysql_schema:
                    tbl = col["table"].lower()
                    cols_per_table[tbl] = cols_per_table.get(tbl, 0) + 1

                total_rows = 0
                total_cols = 0
                for tbl in sorted(mysql_counts.keys()):
                    row_count = mysql_counts[tbl]
                    col_count = cols_per_table.get(tbl, 0)
                    total_rows += row_count if row_count >= 0 else 0
                    total_cols += col_count
                    preview_table.add_row(tbl, f"{row_count:,}", str(col_count))

                # Footer
                preview_table.add_section()
                preview_table.add_row(
                    f"[bold]{len(mysql_counts)} tables[/bold]",
                    f"[bold]{total_rows:,}[/bold]",
                    f"[bold]{total_cols}[/bold]",
                )

                console.print(preview_table)
                console.print(
                    f"\n  [green]✓[/green] {len(mysql_counts)} tables with "
                    f"{total_rows:,} total rows ready to migrate\n"
                )

        except RuntimeError as e:
            console.print(f"  [red]✗ Could not preview schema:[/red] {e}")
            all_ok = False
    else:
        console.print("  [dim]Skipped — MySQL connection failed.[/dim]\n")

    # ── Summary ───────────────────────────────────────────────
    if all_ok:
        console.print(
            Panel(
                "[bold green]✓ Dry run passed — everything looks good![/bold green]\n\n"
                "[bold]Ready to migrate. Run:[/bold]\n"
                "  [cyan]python migrate.py[/cyan]",
                border_style="green",
                padding=(1, 2),
            )
        )
    else:
        console.print(
            Panel(
                "[bold red]✗ Dry run found issues.[/bold red]\n"
                "[yellow]Fix the errors above before running the actual migration.[/yellow]",
                border_style="red",
                padding=(1, 2),
            )
        )

    return 0 if all_ok else 1


def main():
    args = parse_args()

    # ── Handle --init flag ────────────────────────────────────
    if args.init:
        console.print(
            Panel(
                "[bold white]MySQL → PostgreSQL Migration Tool[/bold white]\n"
                "[dim]Configuration Setup[/dim]",
                border_style="bright_cyan",
                padding=(1, 4),
            )
        )
        init_config()
        return 0

    # ── Load config (needed for both dry-run and full migration) ──
    mysql_cfg, pg_cfg = load_config()

    # ── Handle --dry-run flag ─────────────────────────────────
    if args.dry_run:
        return dry_run(mysql_cfg, pg_cfg)

    # ── Banner ────────────────────────────────────────────────
    console.print(
        Panel(
            "[bold white]MySQL → PostgreSQL Migration Tool[/bold white]\n"
            "[dim]Ubuntu/Linux • Powered by pgloader + Docker[/dim]",
            border_style="bright_cyan",
            padding=(1, 4),
        )
    )

    # ── Step 1: Load config file ─────────────────────────────
    console.print(f"  [green]✓[/green] Config loaded from [cyan]{CONFIG_FILE.name}[/cyan]\n")

    # ── Test MySQL connection ─────────────────────────────────
    console.print("")
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Testing MySQL connection...", total=1)
        connected = test_mysql_connection(mysql_cfg)
        progress.update(task, completed=1)

    if not connected:
        console.print(
            "\n[red]Cannot connect to MySQL. Please check your credentials and try again.[/red]\n"
            "[dim]If MySQL is on this machine, ensure it accepts connections on the specified port.\n"
            "If using ufw: sudo ufw allow from 172.16.0.0/12 to any port 3306[/dim]\n"
        )
        sys.exit(1)

    console.print("  [green]✓[/green] MySQL connection successful\n")

    # ── Confirmation ──────────────────────────────────────────
    console.print(
        Panel(
            f"[bold]Source:[/bold]  mysql://{mysql_cfg.user}:****@{mysql_cfg.host}:{mysql_cfg.port}/{mysql_cfg.database}\n"
            f"[bold]Target:[/bold]  postgresql://{pg_cfg.user}:****@localhost:{pg_cfg.port}/{pg_cfg.database}",
            title="Migration Summary",
            border_style="yellow",
        )
    )

    if not Confirm.ask("\n  Proceed with migration?", default=True):
        console.print("[dim]Migration cancelled.[/dim]")
        sys.exit(0)

    console.print("")

    # ── Step 2: Start PostgreSQL (if local) ───────────────────
    client = get_docker_client()

    if pg_cfg.host in ("localhost", "127.0.0.1"):
        console.print("[bold yellow][1/6][/bold yellow] Starting PostgreSQL container...")
        start_postgres(client, pg_cfg)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]Waiting for PostgreSQL to be healthy...", total=1)
            healthy = wait_for_postgres(client)
            progress.update(task, completed=1)

        if not healthy:
            console.print("[red]PostgreSQL failed to start. Check Docker logs.[/red]")
            sys.exit(1)

        console.print("  [green]✓[/green] PostgreSQL is ready\n")
    else:
        console.print(f"[bold yellow][1/6][/bold yellow] Using remote PostgreSQL at [cyan]{pg_cfg.host}[/cyan]")
        console.print("  [dim]Skipping container startup.[/dim]\n")

    # ── Step 3: Generate pgloader config ──────────────────────
    console.print("[bold yellow][2/6][/bold yellow] Generating pgloader configuration...")
    source_uri, target_uri = generate_pgloader_config(mysql_cfg, pg_cfg)
    console.print("")

    # ── Step 4: Run pgloader ──────────────────────────────────
    console.print("[bold yellow][3/6][/bold yellow] Running pgloader migration...")
    console.print("  [dim]This may take a while depending on database size.[/dim]\n")

    success = run_pgloader_with_progress(client, mysql_cfg, source_uri, target_uri)
    if not success:
        console.print("\n[red]Migration failed. Check the pgloader output above.[/red]")
        sys.exit(1)

    console.print("\n  [green]✓[/green] pgloader migration complete\n")

    # ── Step 5: Validate ──────────────────────────────────────
    console.print("[bold yellow][4/6][/bold yellow] Validating migration...")

    try:
        report = validate_migration(mysql_cfg, pg_cfg, mysql_cfg.database, verbose=args.verbose)
        all_passed = report["all_passed"]
    except Exception as e:
        console.print(f"\n  [red]✗ Validation error: {e}[/red]")
        console.print("  [dim]You can still validate manually with the SQL scripts in scripts/[/dim]")
        all_passed = False
        report = None

    # ── Step 6: Schema diff ───────────────────────────────────
    console.print("[bold yellow][5/6][/bold yellow] Schema diff report...")

    try:
        diff_report = schema_diff_report(mysql_cfg, pg_cfg, mysql_cfg.database, verbose=args.verbose)
    except Exception as e:
        console.print(f"\n  [red]✗ Schema diff error: {e}[/red]")
        diff_report = None

    # ── Step 7: HTML Report ───────────────────────────────────
    if report and diff_report:
        try:
            html_path = generate_html_report(report, diff_report, mysql_cfg.database, pg_cfg.database)
            console.print(f"  [green]✓[/green] Detailed report generated: [cyan]{html_path}[/cyan]")
        except Exception as e:
            console.print(f"  [red]✗ HTML report error: {e}[/red]")

    # ── Summary ───────────────────────────────────────────────
    console.print("")
    console.print("[bold yellow][6/6][/bold yellow] Migration summary")

    if all_passed:
        console.print(
            Panel(
                "[bold green]✓ Migration completed successfully![/bold green]\n"
                f"[green]All tables and types have been verified.[/green]\n\n"
                "Detailed results saved to: [bold cyan]migration_report.html[/bold cyan]",
                border_style="green",
                padding=(1, 2),
            )
        )
    else:
        console.print(
            Panel(
                "[bold red]✗ Migration finished with issues[/bold red]\n"
                "Some validations failed. Check the summary above or the HTML report.\n\n"
                "Detailed results saved to: [bold cyan]migration_report.html[/bold cyan]",
                border_style="red",
                padding=(1, 2),
            )
        )

    return 0 if all_passed else 1
