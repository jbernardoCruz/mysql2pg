"""
pgloader configuration generation and migration execution.
"""

import re
import sys

import docker
from docker.errors import NotFound, APIError
from rich.progress import (
    Progress, SpinnerColumn, TextColumn,
    BarColumn, TaskProgressColumn, TimeElapsedColumn,
)

from mysql2pg import (
    console, SCRIPT_DIR, PGLOADER_TEMPLATE, PGLOADER_OUTPUT,
    DOCKER_NETWORK, PG_CONTAINER_NAME, PGLOADER_IMAGE,
)
from mysql2pg.config import MySQLConfig, PGConfig
from mysql2pg.docker_utils import ensure_network
from urllib.parse import quote


def generate_pgloader_config(mysql: MySQLConfig, pg: PGConfig) -> tuple[str, str]:
    """Generate pgloader config based on template, return (source_uri, target_uri)."""
    if not PGLOADER_TEMPLATE.exists():
        console.print(
            f"\n[red]✗ pgloader template not found:[/red] {PGLOADER_TEMPLATE}\n"
            "  [dim]This file should be in the pgloader/ directory.\n"
            "  Re-clone the repository to restore it.[/dim]\n"
        )
        sys.exit(1)

    try:
        template = PGLOADER_TEMPLATE.read_text()
    except OSError as e:
        console.print(f"\n[red]✗ Cannot read template file:[/red] {e}")
        sys.exit(1)

    # URL-encode all credential parts to handle special characters safely
    mysql_user_encoded = quote(mysql.user, safe="")
    mysql_pw_encoded = quote(mysql.password, safe="")
    mysql_db_encoded = quote(mysql.database, safe="")

    pg_user_encoded = quote(pg.user, safe="")
    pg_pw_encoded = quote(pg.password, safe="")
    pg_db_encoded = quote(pg.database, safe="")

    # Construct URIs for environment variables
    source_uri = f"mysql://{mysql_user_encoded}:{mysql_pw_encoded}@{mysql.docker_host}:{mysql.port}/{mysql_db_encoded}"
    target_host = PG_CONTAINER_NAME if pg.host in ("localhost", "127.0.0.1") else pg.host
    target_port = 5432 if pg.host in ("localhost", "127.0.0.1") else pg.port
    target_uri = f"postgresql://{pg_user_encoded}:{pg_pw_encoded}@{target_host}:{target_port}/{pg_db_encoded}"

    try:
        # Format non-URI parts (like the database name for ALTER SCHEMA)
        config = template.format(
            mysql_database=mysql.database,
        )
        
        # Replace URI markers with actual percent-encoded URIs
        config = config.replace("$SOURCE_URI", source_uri)
        config = config.replace("$TARGET_URI", target_uri)
        
        # Debug output for user verification
        safe_source = f"mysql://{mysql_user_encoded}:****@{mysql.docker_host}:{mysql.port}/{mysql_db_encoded}"
        safe_target = f"postgresql://{pg_user_encoded}:****@{target_host}:{target_port}/{pg_db_encoded}"
        console.print(f"  [dim]Generated URIs:\n    Source: {safe_source}\n    Target: {safe_target}[/dim]")

    except KeyError as e:
        console.print(
            f"\n[red]✗ Template has an unrecognized placeholder:[/red] {e}\n"
            f"  [dim]Check {PGLOADER_TEMPLATE} for invalid {{placeholders}}.[/dim]\n"
        )
        sys.exit(1)

    try:
        PGLOADER_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        PGLOADER_OUTPUT.write_text(config)
        console.print(f"  [green]✓[/green] Config written to [cyan]{PGLOADER_OUTPUT}[/cyan]")
    except OSError as e:
        console.print(
            f"\n[red]✗ Cannot write pgloader config:[/red] {e}\n"
            f"  [dim]Check permissions on {PGLOADER_OUTPUT.parent}[/dim]\n"
        )
        sys.exit(1)

    return source_uri, target_uri


def run_pgloader(client: docker.DockerClient, mysql: MySQLConfig, source_uri: str, target_uri: str):
    """Run pgloader container to perform the migration."""

    # Pull image if needed
    try:
        client.images.get(PGLOADER_IMAGE)
    except NotFound:
        console.print(f"  Pulling [cyan]{PGLOADER_IMAGE}[/cyan]...")
        try:
            client.images.pull(PGLOADER_IMAGE)
        except APIError as e:
            console.print(
                f"\n[red]✗ Failed to pull Docker image {PGLOADER_IMAGE}:[/red] {e}\n"
                "  [yellow]Troubleshooting:[/yellow]\n"
                "    1. Check internet connection\n"
                "    2. Try manually: [dim]docker pull " + PGLOADER_IMAGE + "[/dim]\n"
            )
            sys.exit(1)

    # Ensure network exists
    ensure_network(client, DOCKER_NETWORK)

    # Remove previous pgloader container if exists
    try:
        old = client.containers.get("pgloader_runner")
        old.remove(force=True)
    except NotFound:
        pass
    except APIError as e:
        console.print(
            f"\n[yellow]⚠ Could not remove old pgloader container:[/yellow] {e}\n"
            "  [dim]Trying to continue anyway...[/dim]\n"
        )

    # Build extra_hosts for Linux host networking
    extra_hosts = {}
    if mysql.docker_host == "host.docker.internal":
        extra_hosts["host.docker.internal"] = "host-gateway"

    # Run pgloader
    pgloader_dir = str(SCRIPT_DIR / "pgloader")
    try:
        container = client.containers.run(
            PGLOADER_IMAGE,
            command="pgloader /pgloader/migration.load",
            name="pgloader_runner",
            detach=True,
            volumes={pgloader_dir: {"bind": "/pgloader", "mode": "ro"}},
            network=DOCKER_NETWORK,
            extra_hosts=extra_hosts,
            remove=False,  # Keep so we can read logs
        )
    except APIError as e:
        error_msg = str(e)
        if "Conflict" in error_msg:
            console.print(
                "\n[red]✗ pgloader container name conflict.[/red]\n"
                "  [dim]Try: docker rm -f pgloader_runner[/dim]\n"
            )
        else:
            console.print(
                f"\n[red]✗ Failed to start pgloader container:[/red] {e}\n"
                "  [dim]Check Docker daemon status: docker info[/dim]\n"
            )
        return False

    # Stream logs
    console.print("")
    try:
        for line in container.logs(stream=True, follow=True):
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                console.print(f"  [dim]{text}[/dim]")
    except Exception as e:
        console.print(f"\n  [yellow]⚠ Log streaming interrupted:[/yellow] {e}")

    # Wait for container to finish
    try:
        result = container.wait(timeout=600)  # 10 min timeout
        exit_code = result.get("StatusCode", -1)
    except Exception as e:
        console.print(
            f"\n[red]✗ pgloader container timed out or failed:[/red] {e}\n"
            "  [dim]Check container status: docker ps -a | grep pgloader[/dim]\n"
            "  [dim]View logs: docker logs pgloader_runner[/dim]\n"
        )
        return False

    # Get full logs for error reporting
    if exit_code != 0:
        try:
            logs = container.logs().decode("utf-8", errors="replace")
        except Exception:
            logs = "(could not retrieve logs)"

        console.print(f"\n  [red]✗ pgloader exited with code {exit_code}[/red]")

        # Provide specific guidance based on common pgloader errors
        if "Access denied" in logs or "authentication" in logs.lower():
            console.print(
                "\n  [yellow]Likely cause:[/yellow] MySQL authentication failed inside Docker.\n"
                "    • Check credentials in [cyan]migration_config.json[/cyan]\n"
                "    • Ensure MySQL user can connect from Docker network\n"
                "    • Grant access: [dim]GRANT ALL ON db.* TO 'user'@'%';[/dim]\n"
            )
        elif "could not connect" in logs.lower() or "connection refused" in logs.lower():
            console.print(
                "\n  [yellow]Likely cause:[/yellow] pgloader cannot reach MySQL from inside Docker.\n"
                "    • Firewall: [dim]sudo ufw allow from 172.16.0.0/12 to any port 3306[/dim]\n"
                "    • MySQL bind-address: set [dim]bind-address = 0.0.0.0[/dim]\n"
                "    • Restart MySQL: [dim]sudo systemctl restart mysql[/dim]\n"
            )
        elif "No such file" in logs:
            console.print(
                "\n  [yellow]Likely cause:[/yellow] pgloader config file not mounted correctly.\n"
                f"    • Expected at: {PGLOADER_OUTPUT}\n"
                "    • Check that pgloader/ directory exists\n"
            )

        # Show last portion of logs
        log_tail = logs[-2000:] if len(logs) > 2000 else logs
        if log_tail.strip():
            console.print("\n  [bold]Last pgloader output:[/bold]")
            for line in log_tail.strip().split("\n")[-20:]:
                console.print(f"  [dim]{line}[/dim]")

        try:
            container.remove(force=True)
        except Exception:
            pass
        return False

    try:
        container.remove(force=True)
    except Exception:
        pass  # Non-critical — container will be cleaned up eventually
    return True


def run_pgloader_with_progress(client: docker.DockerClient, mysql_cfg: MySQLConfig, source_uri: str, target_uri: str) -> bool:
    """Run pgloader with a Rich progress bar instead of raw log output."""
    from mysql2pg.validation import get_mysql_tables

    # Get expected tables from MySQL for progress tracking
    try:
        mysql_counts = get_mysql_tables(mysql_cfg)
        expected_tables = list(mysql_counts.keys())
        total_rows = sum(mysql_counts.values())
    except Exception:
        # Fall back to basic mode if we can't get table info
        console.print("  [dim]Could not pre-fetch table list — using basic progress.[/dim]")
        expected_tables = []
        total_rows = 0

    # Pull image if needed
    try:
        client.images.get(PGLOADER_IMAGE)
    except NotFound:
        console.print(f"  Pulling [cyan]{PGLOADER_IMAGE}[/cyan]...")
        try:
            client.images.pull(PGLOADER_IMAGE)
        except APIError as e:
            console.print(
                f"\n[red]✗ Failed to pull Docker image {PGLOADER_IMAGE}:[/red] {e}\n"
                "  [yellow]Troubleshooting:[/yellow]\n"
                "    1. Check internet connection\n"
                "    2. Try manually: [dim]docker pull " + PGLOADER_IMAGE + "[/dim]\n"
            )
            sys.exit(1)

    # Ensure network exists
    ensure_network(client, DOCKER_NETWORK)

    # Remove previous pgloader container if exists
    try:
        old = client.containers.get("pgloader_runner")
        old.remove(force=True)
    except NotFound:
        pass
    except APIError as e:
        console.print(
            f"\n[yellow]⚠ Could not remove old pgloader container:[/yellow] {e}\n"
            "  [dim]Trying to continue anyway...[/dim]\n"
        )

    # Build extra_hosts for Linux host networking
    extra_hosts = {}
    if mysql_cfg.docker_host == "host.docker.internal":
        extra_hosts["host.docker.internal"] = "host-gateway"

    # Run pgloader
    pgloader_dir = str(SCRIPT_DIR / "pgloader")
    try:
        container = client.containers.run(
            PGLOADER_IMAGE,
            command="pgloader /pgloader/migration.load",
            name="pgloader_runner",
            detach=True,
            volumes={pgloader_dir: {"bind": "/pgloader", "mode": "ro"}},
            network=DOCKER_NETWORK,
            extra_hosts=extra_hosts,
            remove=False,
        )
    except APIError as e:
        error_msg = str(e)
        if "Conflict" in error_msg:
            console.print(
                "\n[red]✗ pgloader container name conflict.[/red]\n"
                "  [dim]Try: docker rm -f pgloader_runner[/dim]\n"
            )
        else:
            console.print(
                f"\n[red]✗ Failed to start pgloader container:[/red] {e}\n"
                "  [dim]Check Docker daemon status: docker info[/dim]\n"
            )
        return False

    # Stream logs with progress bar
    console.print("")
    completed_tables = set()
    migrated_rows = 0
    all_log_lines = []

    # Regex to match pgloader summary lines
    table_line_re = re.compile(
        r"^\s*[\w.]+\.(\w+)\s+\.\.\.\.\.\.\s+(\d+)\s+(\d+)\s+(\d+)\s",
    )
    simple_row_re = re.compile(
        r"^\s+(\w+)\s+(\d+)\s+(\d+)\s*$"
    )

    try:
        if expected_tables:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(bar_width=30),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                main_task = progress.add_task(
                    f"[cyan]Migrating {len(expected_tables)} tables ({total_rows:,} rows)...",
                    total=len(expected_tables),
                )

                for line in container.logs(stream=True, follow=True):
                    text = line.decode("utf-8", errors="replace").rstrip()
                    if not text:
                        continue
                    all_log_lines.append(text)

                    # Try to detect completed table from pgloader output
                    text_lower = text.lower().strip()
                    for tbl in expected_tables:
                        if tbl not in completed_tables and tbl.lower() in text_lower:
                            # Check if it looks like a completion line (has numbers)
                            if re.search(r'\d+\s+\d+', text):
                                completed_tables.add(tbl)
                                progress.update(main_task, completed=len(completed_tables))
                                progress.update(
                                    main_task,
                                    description=f"[cyan]Migrated [green]{tbl}[/green] ({len(completed_tables)}/{len(expected_tables)})...",
                                )
                                break

                # Ensure bar completes
                progress.update(main_task, completed=len(expected_tables))
        else:
            # Fallback: basic spinner if no table info
            for line in container.logs(stream=True, follow=True):
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    all_log_lines.append(text)
                    console.print(f"  [dim]{text}[/dim]")

    except Exception as e:
        console.print(f"\n  [yellow]⚠ Log streaming interrupted:[/yellow] {e}")

    # Wait for container to finish
    try:
        result = container.wait(timeout=600)
        exit_code = result.get("StatusCode", -1)
    except Exception as e:
        console.print(
            f"\n[red]✗ pgloader container timed out or failed:[/red] {e}\n"
            "  [dim]Check container status: docker ps -a | grep pgloader[/dim]\n"
            "  [dim]View logs: docker logs pgloader_runner[/dim]\n"
        )
        return False

    # Get full logs (always, not just on error)
    try:
        logs = container.logs().decode("utf-8", errors="replace")
    except Exception:
        logs = "\n".join(all_log_lines) if all_log_lines else "(could not retrieve logs)"

    # Always save logs for debugging
    log_file = SCRIPT_DIR / "pgloader" / ("pgloader_error.log" if exit_code != 0 else "pgloader.log")
    try:
        log_file.write_text(logs)
        console.print(f"\n  [dim]Full pgloader log saved to: {log_file}[/dim]")
    except OSError:
        pass

    if exit_code != 0:
        console.print(f"\n  [red]✗ pgloader exited with code {exit_code}[/red]")

        if "Access denied" in logs or "authentication" in logs.lower():
            console.print(
                "\n  [yellow]Likely cause:[/yellow] MySQL authentication failed inside Docker.\n"
                "    • Check credentials in [cyan]migration_config.json[/cyan]\n"
                "    • Ensure MySQL user can connect from Docker network\n"
                "    • Grant access: [dim]GRANT ALL ON db.* TO 'user'@'%';[/dim]\n"
            )
        elif "could not connect" in logs.lower() or "connection refused" in logs.lower():
            console.print(
                "\n  [yellow]Likely cause:[/yellow] pgloader cannot reach MySQL from inside Docker.\n"
                "    • Firewall: [dim]sudo ufw allow from 172.16.0.0/12 to any port 3306[/dim]\n"
                "    • MySQL bind-address: set [dim]bind-address = 0.0.0.0[/dim]\n"
                "    • Restart MySQL: [dim]sudo systemctl restart mysql[/dim]\n"
            )
        elif "No such file" in logs:
            console.print(
                "\n  [yellow]Likely cause:[/yellow] pgloader config file not mounted correctly.\n"
                f"    • Expected at: {PGLOADER_OUTPUT}\n"
                "    • Check that pgloader/ directory exists\n"
            )

        log_tail = logs[-4000:] if len(logs) > 4000 else logs
        if log_tail.strip():
            console.print("\n  [bold]Last pgloader output:[/bold]")
            for log_line in log_tail.strip().split("\n")[-50:]:
                console.print(f"  [dim]{log_line}[/dim]")

        try:
            container.remove(force=True)
        except Exception:
            pass
        return False

    # ── Success path ──────────────────────────────────────────
    if completed_tables:
        console.print(f"\n  [green]✓[/green] {len(completed_tables)} tables processed by pgloader")

    # Print pgloader's own summary
    summary_lines = []
    in_summary = False
    for line in logs.split("\n"):
        if "table name" in line.lower() and ("rows" in line.lower() or "read" in line.lower()):
            in_summary = True
        if in_summary:
            summary_lines.append(line)
        if "error" in line.lower() and "0" not in line:
            summary_lines.append(f"  ⚠ {line.strip()}")

    if summary_lines:
        console.print("\n  [bold]pgloader summary:[/bold]")
        for sl in summary_lines[-30:]:
            console.print(f"  [dim]{sl}[/dim]")

    try:
        container.remove(force=True)
    except Exception:
        pass
    return True
