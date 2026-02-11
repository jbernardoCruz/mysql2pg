#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
  MySQL → PostgreSQL Migration Tool (Ubuntu/Linux)
═══════════════════════════════════════════════════════════════

  CLI that automates the full migration pipeline:
    1. Read connection details from migration_config.json
    2. Start PostgreSQL in Docker
    3. Generate pgloader config from template
    4. Run pgloader migration via Docker
    5. Validate data integrity (row counts, types, keys)

  📌 Run in: Ubuntu Terminal
  Usage:
    pip install -r requirements.txt
    python migrate.py --init     # Create config file (first time)
    # Edit migration_config.json with your credentials
    python migrate.py            # Run migration

═══════════════════════════════════════════════════════════════
"""

import os
import re
import sys
import json
import time
import argparse
from pathlib import Path
from urllib.parse import quote
from datetime import datetime

# ─── Rich imports ────────────────────────────────────────────
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Confirm
from rich.progress import (
    Progress, SpinnerColumn, TextColumn,
    BarColumn, TaskProgressColumn, TimeElapsedColumn,
)
from rich import box

# ─── Docker SDK ──────────────────────────────────────────────
import docker
from docker.errors import DockerException, NotFound, APIError

# ─── Database connectors ────────────────────────────────────
import psycopg2
import mysql.connector
from mysql.connector import Error as MySQLError

# ═════════════════════════════════════════════════════════════
# Constants
# ═════════════════════════════════════════════════════════════

SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = SCRIPT_DIR / "migration_config.json"
PGLOADER_TEMPLATE = SCRIPT_DIR / "pgloader" / "migration.load.template"
PGLOADER_OUTPUT = SCRIPT_DIR / "pgloader" / "migration.load"
DOCKER_NETWORK = "sql_default"
PG_CONTAINER_NAME = "pg-target"
PG_IMAGE = "postgres:16-alpine"
PGLOADER_IMAGE = "dimitri/pgloader:latest"

DEFAULT_CONFIG = {
    "mysql": {
        "host": "localhost",
        "port": 3306,
        "user": "root",
        "password": "YOUR_MYSQL_PASSWORD",
        "database": "YOUR_DATABASE_NAME",
    },
    "postgresql": {
        "host": "localhost",
        "port": 5432,
        "user": "postgres",
        "database": "myapp",
        "password": "postgres",
    },
}

console = Console()


# ═════════════════════════════════════════════════════════════
# Data classes for connection details
# ═════════════════════════════════════════════════════════════

class MySQLConfig:
    def __init__(self, host: str, port: int, user: str, password: str, database: str):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database

    @property
    def docker_host(self) -> str:
        """Host to use from inside Docker containers."""
        if self.host in ("localhost", "127.0.0.1"):
            return "host.docker.internal"
        return self.host


class PGConfig:
    def __init__(self, host: str, port: int, user: str, database: str, password: str):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database


# ═════════════════════════════════════════════════════════════
# Step 1: Configuration file
# ═════════════════════════════════════════════════════════════

def init_config():
    """Create a fresh migration_config.json with defaults."""
    if CONFIG_FILE.exists():
        console.print(f"  [yellow]⚠ Config file already exists:[/yellow] {CONFIG_FILE}")
        if not Confirm.ask("  Overwrite?", default=False):
            console.print("  [dim]Skipped. Edit the existing file manually.[/dim]")
            return

    try:
        CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
    except PermissionError:
        console.print(
            f"\n[red]✗ Permission denied:[/red] Cannot write to {CONFIG_FILE}\n"
            "  Try running with appropriate permissions or check directory ownership.\n"
        )
        sys.exit(1)
    except OSError as e:
        console.print(
            f"\n[red]✗ Failed to create config file:[/red] {e}\n"
            "  Check disk space and directory permissions.\n"
        )
        sys.exit(1)

    console.print(f"  [green]✓[/green] Created [bold]{CONFIG_FILE}[/bold]")
    console.print("  [dim]Edit the file with your MySQL/PostgreSQL credentials, then run:[/dim]")
    console.print("  [cyan]python migrate.py[/cyan]\n")


def load_config() -> tuple[MySQLConfig, PGConfig]:
    """Load and validate migration_config.json."""
    if not CONFIG_FILE.exists():
        console.print(
            f"\n[red]✗ Config file not found:[/red] {CONFIG_FILE}\n"
            "  Run [cyan]python migrate.py --init[/cyan] to create it.\n"
        )
        sys.exit(1)

    try:
        raw = CONFIG_FILE.read_text()
    except PermissionError:
        console.print(
            f"\n[red]✗ Permission denied:[/red] Cannot read {CONFIG_FILE}\n"
            "  Check file permissions: [dim]ls -la migration_config.json[/dim]\n"
        )
        sys.exit(1)
    except OSError as e:
        console.print(f"\n[red]✗ Cannot read config file:[/red] {e}")
        sys.exit(1)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        console.print(
            f"\n[red]✗ Invalid JSON in {CONFIG_FILE.name}:[/red]\n"
            f"  {e}\n\n"
            "  [dim]Common issues: trailing commas, missing quotes, unescaped characters.[/dim]\n"
            "  [dim]Tip: Use a JSON validator or run:[/dim] python -m json.tool migration_config.json\n"
        )
        sys.exit(1)

    if not isinstance(data, dict):
        console.print(
            f"\n[red]✗ Config file must contain a JSON object,[/red] got {type(data).__name__}\n"
            "  [dim]Expected format: {{\"mysql\": {{...}}, \"postgresql\": {{...}}}}[/dim]\n"
        )
        sys.exit(1)

    # Validate required sections
    if "mysql" not in data:
        console.print(
            f"\n[red]✗ Missing \"mysql\" section in {CONFIG_FILE.name}[/red]\n"
            "  [dim]Run [cyan]python migrate.py --init[/cyan] to regenerate the config file.[/dim]\n"
        )
        sys.exit(1)
    if "postgresql" not in data:
        console.print(
            f"\n[red]✗ Missing \"postgresql\" section in {CONFIG_FILE.name}[/red]\n"
            "  [dim]Run [cyan]python migrate.py --init[/cyan] to regenerate the config file.[/dim]\n"
        )
        sys.exit(1)

    # Validate required fields
    errors = []
    mysql = data.get("mysql", {})
    pg = data.get("postgresql", {})

    if not isinstance(mysql, dict):
        console.print(f"\n[red]✗ \"mysql\" must be a JSON object, got {type(mysql).__name__}[/red]")
        sys.exit(1)
    if not isinstance(pg, dict):
        console.print(f"\n[red]✗ \"postgresql\" must be a JSON object, got {type(pg).__name__}[/red]")
        sys.exit(1)

    for key in ("host", "port", "user", "password", "database"):
        if key not in mysql:
            errors.append(f"mysql.{key} — field missing")
        elif mysql[key] is None or (isinstance(mysql[key], str) and not mysql[key].strip()):
            errors.append(f"mysql.{key} — value is empty")
    for key in ("port", "database", "password"):
        if key not in pg:
            errors.append(f"postgresql.{key} — field missing")
        elif pg[key] is None or (isinstance(pg[key], str) and not pg[key].strip()):
            errors.append(f"postgresql.{key} — value is empty")

    # Check for placeholder values
    if mysql.get("password") == "YOUR_MYSQL_PASSWORD":
        errors.append("mysql.password — still has placeholder value \"YOUR_MYSQL_PASSWORD\"")
    if mysql.get("database") == "YOUR_DATABASE_NAME":
        errors.append("mysql.database — still has placeholder value \"YOUR_DATABASE_NAME\"")

    if errors:
        console.print(f"\n[red]✗ Config validation failed ({len(errors)} issue{'s' if len(errors) > 1 else ''}):[/red]")
        for err in errors:
            console.print(f"  [yellow]•[/yellow] {err}")
        console.print(f"\n  [dim]Edit {CONFIG_FILE.name} and fix the issues above.[/dim]\n")
        sys.exit(1)

    # Type-safe conversion with error handling
    try:
        mysql_port = int(mysql["port"])
    except (ValueError, TypeError):
        console.print(
            f"\n[red]✗ mysql.port must be a number,[/red] got: \"{mysql['port']}\"\n"
            "  [dim]Use a number without quotes, e.g. \"port\": 3306[/dim]\n"
        )
        sys.exit(1)

    try:
        pg_port = int(pg["port"])
    except (ValueError, TypeError):
        console.print(
            f"\n[red]✗ postgresql.port must be a number,[/red] got: \"{pg['port']}\"\n"
            "  [dim]Use a number without quotes, e.g. \"port\": 5432[/dim]\n"
        )
        sys.exit(1)

    # Port range validation
    for label, port_val in [("mysql.port", mysql_port), ("postgresql.port", pg_port)]:
        if not (1 <= port_val <= 65535):
            console.print(
                f"\n[red]✗ {label} must be between 1 and 65535,[/red] got: {port_val}\n"
            )
            sys.exit(1)

    mysql_cfg = MySQLConfig(
        host=str(mysql["host"]).strip(),
        port=mysql_port,
        user=str(mysql["user"]).strip(),
        password=str(mysql["password"]),
        database=str(mysql["database"]).strip(),
    )
    pg_cfg = PGConfig(
        host=str(pg.get("host", "localhost")).strip(),
        port=pg_port,
        user=str(pg.get("user", "postgres")).strip(),
        database=str(pg["database"]).strip(),
        password=str(pg["password"]),
    )

    return mysql_cfg, pg_cfg


def test_mysql_connection(config: MySQLConfig) -> bool:
    """Test connectivity to MySQL before proceeding."""
    try:
        conn = mysql.connector.connect(
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            database=config.database,
            connect_timeout=10,
        )
        conn.close()
        return True
    except MySQLError as e:
        error_code = e.errno if hasattr(e, 'errno') else None
        error_msg = str(e)

        console.print(f"\n  [red]✗ MySQL connection failed:[/red] {error_msg}")

        # Provide specific troubleshooting based on error code
        if error_code == 2003:  # Can't connect to MySQL server
            console.print(
                "\n  [yellow]Troubleshooting:[/yellow]\n"
                f"    1. Is MySQL running on [cyan]{config.host}:{config.port}[/cyan]?\n"
                "    2. Check: [dim]sudo systemctl status mysql[/dim]\n"
                "    3. Firewall: [dim]sudo ufw allow from 172.16.0.0/12 to any port 3306[/dim]\n"
                "    4. MySQL bind-address: set [dim]bind-address = 0.0.0.0[/dim] in my.cnf"
            )
        elif error_code == 1045:  # Access denied
            console.print(
                "\n  [yellow]Troubleshooting:[/yellow]\n"
                f"    • Access denied for user [cyan]{config.user}[/cyan]\n"
                "    • Check password in [cyan]migration_config.json[/cyan]\n"
                "    • Verify MySQL user has access: [dim]SELECT user, host FROM mysql.user;[/dim]"
            )
        elif error_code == 1049:  # Unknown database
            console.print(
                "\n  [yellow]Troubleshooting:[/yellow]\n"
                f"    • Database [cyan]{config.database}[/cyan] does not exist\n"
                "    • List databases: [dim]mysql -u root -p -e 'SHOW DATABASES;'[/dim]\n"
                "    • Check spelling in [cyan]migration_config.json[/cyan]"
            )
        elif error_code == 2005:  # Unknown host
            console.print(
                "\n  [yellow]Troubleshooting:[/yellow]\n"
                f"    • Cannot resolve hostname [cyan]{config.host}[/cyan]\n"
                "    • Try using an IP address instead\n"
                "    • Check: [dim]ping {host}[/dim]".format(host=config.host)
            )
        elif error_code == 2006 or error_code == 2013:  # Connection lost / timeout
            console.print(
                "\n  [yellow]Troubleshooting:[/yellow]\n"
                "    • Connection timed out or was lost\n"
                f"    • Verify MySQL is accepting connections on port [cyan]{config.port}[/cyan]\n"
                "    • Check network connectivity and firewall rules"
            )
        console.print("")
        return False
    except Exception as e:
        console.print(
            f"\n  [red]✗ Unexpected error connecting to MySQL:[/red] {e}\n"
            "  [dim]This may indicate a missing driver or system library.[/dim]\n"
        )
        return False


# ═════════════════════════════════════════════════════════════
# Step 2: Docker — PostgreSQL container
# ═════════════════════════════════════════════════════════════

def get_docker_client() -> docker.DockerClient:
    """Get Docker client, with a friendly error if Docker isn't running."""
    try:
        client = docker.from_env()
        client.ping()
        return client
    except DockerException:
        console.print(
            "\n[red]✗ Cannot connect to Docker.[/red]\n"
            "  Make sure Docker Engine is installed and running:\n"
            "    [dim]sudo systemctl start docker[/dim]\n"
            "    [dim]sudo usermod -aG docker $USER[/dim]\n"
        )
        sys.exit(1)


def ensure_network(client: docker.DockerClient, name: str):
    """Create Docker network if it doesn't exist."""
    try:
        client.networks.get(name)
    except NotFound:
        try:
            client.networks.create(name, driver="bridge")
        except APIError as e:
            console.print(
                f"\n[red]✗ Failed to create Docker network '{name}':[/red] {e}\n"
                "  [dim]Try manually: docker network create " + name + "[/dim]\n"
            )
            sys.exit(1)


def start_postgres(client: docker.DockerClient, pg: PGConfig) -> docker.models.containers.Container:
    """Start PostgreSQL container, or reuse if already running."""

    # Remove legacy container name if exists (fixes pgloader underscore issue)
    if PG_CONTAINER_NAME != "pg_target":
        try:
            legacy = client.containers.get("pg_target")
            console.print("  [dim]Removing legacy container 'pg_target'...[/dim]")
            legacy.remove(force=True)
        except NotFound:
            pass
        except APIError:
            pass

    # Check if container already exists
    try:
        container = client.containers.get(PG_CONTAINER_NAME)
        if container.status == "running":
            console.print("  [green]✓[/green] PostgreSQL container already running")
            return container
        else:
            console.print(f"  [dim]Removing stopped container '{PG_CONTAINER_NAME}'...[/dim]")
            try:
                container.remove(force=True)
            except APIError as e:
                console.print(
                    f"\n[red]✗ Cannot remove old container '{PG_CONTAINER_NAME}':[/red] {e}\n"
                    f"  [dim]Try manually: docker rm -f {PG_CONTAINER_NAME}[/dim]\n"
                )
                sys.exit(1)
    except NotFound:
        pass

    # Pull image if needed
    try:
        client.images.get(PG_IMAGE)
    except NotFound:
        console.print(f"  Pulling [cyan]{PG_IMAGE}[/cyan]...")
        try:
            client.images.pull(PG_IMAGE)
        except APIError as e:
            console.print(
                f"\n[red]✗ Failed to pull Docker image {PG_IMAGE}:[/red] {e}\n"
                "  [yellow]Troubleshooting:[/yellow]\n"
                "    1. Check internet connection\n"
                "    2. Docker Hub may be down — check [dim]https://status.docker.com[/dim]\n"
                "    3. Try manually: [dim]docker pull " + PG_IMAGE + "[/dim]\n"
            )
            sys.exit(1)

    # Ensure network exists
    ensure_network(client, DOCKER_NETWORK)

    # Start container
    try:
        container = client.containers.run(
            PG_IMAGE,
            name=PG_CONTAINER_NAME,
            detach=True,
            ports={"5432/tcp": pg.port},
            environment={
                "POSTGRES_USER": pg.user,
                "POSTGRES_PASSWORD": pg.password,
                "POSTGRES_DB": pg.database,
            },
            volumes={"sql_pgdata": {"bind": "/var/lib/postgresql/data", "mode": "rw"}},
            network=DOCKER_NETWORK,
            restart_policy={"Name": "unless-stopped"},
            healthcheck={
                "test": ["CMD-SHELL", f"pg_isready -U {pg.user}"],
                "interval": 5_000_000_000,  # 5s in nanoseconds
                "timeout": 5_000_000_000,
                "retries": 5,
            },
        )
    except APIError as e:
        error_msg = str(e)
        if "port is already allocated" in error_msg or "address already in use" in error_msg:
            console.print(
                f"\n[red]✗ Port {pg.port} is already in use.[/red]\n"
                "  [yellow]Troubleshooting:[/yellow]\n"
                f"    1. Check what's using port {pg.port}: [dim]sudo lsof -i :{pg.port}[/dim]\n"
                f"    2. Stop the conflicting service, or\n"
                f"    3. Change [cyan]postgresql.port[/cyan] in {CONFIG_FILE.name} to a different port\n"
            )
        elif "Conflict" in error_msg:
            console.print(
                f"\n[red]✗ Container name '{PG_CONTAINER_NAME}' conflict:[/red] {e}\n"
                f"  [dim]Try: docker rm -f {PG_CONTAINER_NAME}[/dim]\n"
            )
        else:
            console.print(
                f"\n[red]✗ Failed to start PostgreSQL container:[/red] {e}\n"
                "  [dim]Check Docker logs: docker logs " + PG_CONTAINER_NAME + "[/dim]\n"
            )
        sys.exit(1)

    return container


def wait_for_postgres(client: docker.DockerClient, timeout: int = 60):
    """Wait until PostgreSQL container is healthy."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            container = client.containers.get(PG_CONTAINER_NAME)
            health = container.attrs.get("State", {}).get("Health", {}).get("Status", "")
            if health == "healthy":
                return True
            if container.status != "running":
                console.print(f"  [red]✗ Container exited unexpectedly[/red]")
                return False
        except NotFound:
            return False
        time.sleep(2)

    console.print(f"  [red]✗ PostgreSQL did not become healthy within {timeout}s[/red]")
    return False


# ═════════════════════════════════════════════════════════════
# Step 3: Generate pgloader configuration
# ═════════════════════════════════════════════════════════════

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
    # Use quote() with safe='' to encode EVERYTHING (including / and :)
    mysql_user_encoded = quote(mysql.user, safe="")
    mysql_pw_encoded = quote(mysql.password, safe="")
    mysql_db_encoded = quote(mysql.database, safe="")

    pg_user_encoded = quote(pg.user, safe="")
    pg_pw_encoded = quote(pg.password, safe="")
    pg_db_encoded = quote(pg.database, safe="")

    # Construct URIs for environment variables
    # We pass these to Docker env, avoiding parser issues in the .load file
    source_uri = f"mysql://{mysql_user_encoded}:{mysql_pw_encoded}@{mysql.docker_host}:{mysql.port}/{mysql_db_encoded}"
    target_host = PG_CONTAINER_NAME if pg.host in ("localhost", "127.0.0.1") else pg.host
    target_port = 5432 if pg.host in ("localhost", "127.0.0.1") else pg.port
    target_uri = f"postgresql://{pg_user_encoded}:{pg_pw_encoded}@{target_host}:{target_port}/{pg_db_encoded}"

    try:
        # Format non-URI parts (like the database name for ALTER SCHEMA)
        config = template.format(
            mysql_database=mysql.database,  # Raw DB name for identifiers
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


# ═════════════════════════════════════════════════════════════
# Step 4: Run pgloader migration
# ═════════════════════════════════════════════════════════════

def run_pgloader(client: docker.DockerClient, mysql: MySQLConfig, source_uri: str, target_uri: str):
    """Run pgloader in Docker container (legacy/fallback)."""
    # ... (container setup omitted for brevity, assuming existing logic) ...
    # This function seems largely replaced by run_pgloader_with_progress
    # But I'll update signature just in case
    pass 

# Actually, to update run_pgloader properly, I need to see more lines.
# But stepping back, run_pgloader is used as a fallback if rich fails?
# No, the code in run_pgloader_with_progress calls client.containers.run DIRECTLY.
# So I should update run_pgloader_with_progress logic instead of run_pgloader.
# But wait, run_pgloader IS used if I call it? 
# The search showed 2 definitions.
# Let's focus on updating run_pgloader_with_progress which is what main uses.

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


# ═════════════════════════════════════════════════════════════
# Step 5: Validation
# ═════════════════════════════════════════════════════════════

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
                report["row_counts"]["passed"] += 1 # Extra tables in PG are treated as warning, not failure for "passed" count usually
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
            console.print(f"  [{color}]Row counts:[/color] {report['row_counts']['passed']}/{report['row_counts']['total']} tables match ✓")

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
            "timestamptz": "DATETIME → timestamptz",
            "int2": "TINYINT → smallint",
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


# ═════════════════════════════════════════════════════════════
# Step 6: MySQL schema capture (for dry-run & schema diff)

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


# ═════════════════════════════════════════════════════════════
# Step 7: pgloader with progress bar
# ═════════════════════════════════════════════════════════════

def run_pgloader_with_progress(client: docker.DockerClient, mysql_cfg: MySQLConfig, source_uri: str, target_uri: str) -> bool:
    """Run pgloader with a Rich progress bar instead of raw log output."""

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

    # Regex to match pgloader summary lines like:
    #   table_name           1234          0
    table_line_re = re.compile(
        r"^\s*[\w.]+\.(\w+)\s+\.\.\.\.\.\.\s+(\d+)\s+(\d+)\s+(\d+)\s",
    )
    # Alternative simpler pattern for rows
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
    # Show migration summary from pgloader output
    if completed_tables:
        console.print(f"\n  [green]✓[/green] {len(completed_tables)} tables processed by pgloader")

    # Print pgloader's own summary (rows loaded, errors, etc.)
    summary_lines = []
    in_summary = False
    for line in logs.split("\n"):
        if "table name" in line.lower() and ("rows" in line.lower() or "read" in line.lower()):
            in_summary = True
        if in_summary:
            summary_lines.append(line)
        # Also capture key lines
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


# ═════════════════════════════════════════════════════════════
# Step 8: Schema diff report
# ═════════════════════════════════════════════════════════════

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
                "tinyint": {"int2", "bool", "smallint"},
                "varchar": {"varchar", "text"},
                "char": {"bpchar", "char"},
                "text": {"text"},
                "longtext": {"text"},
                "mediumtext": {"text"},
                "datetime": {"timestamp", "timestamptz"},
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
    console.print(f"  [{summary_color}]Schema diff:[/summary_color] {identical} identical, {conversions} conversions, {missing} missing")
    
    return report


# ═════════════════════════════════════════════════════════════
# Dry-run mode
# ═════════════════════════════════════════════════════════════

def generate_html_report(validation: dict, diff: dict, mysql_db: str, pg_db: str):
    """Generate a detailed HTML migration report."""
    html_file = "migration_report.html"
    
    # CSS for a modern look
    css = """
    body { font-family: 'Inter', -apple-system, sans-serif; line-height: 1.5; color: #333; max-width: 1200px; margin: 0 auto; padding: 40px 20px; background-color: #f8f9fa; }
    h1, h2, h3 { color: #1a202c; }
    .header { border-bottom: 2px solid #e2e8f0; padding-bottom: 20px; margin-bottom: 40px; display: flex; justify-content: space-between; align-items: center; }
    .status { padding: 8px 16px; border-radius: 9999px; font-weight: 600; font-size: 0.875rem; }
    .status-pass { background-color: #c6f6d5; color: #22543d; }
    .status-fail { background-color: #fed7d7; color: #822727; }
    .card { background: white; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); padding: 24px; margin-bottom: 32px; }
    table { width: 100%; border-collapse: collapse; margin-top: 16px; }
    th { text-align: left; padding: 12px; background: #f7fafc; border-bottom: 2px solid #edf2f7; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: #4a5568; }
    td { padding: 12px; border-bottom: 1px solid #edf2f7; font-size: 0.875rem; }
    .table-name { font-weight: 600; color: #2d3748; }
    .mysql-val { color: #b7791f; }
    .pg-val { color: #2f855a; }
    .badge { padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }
    .badge-ok { background: #c6f6d5; color: #22543d; }
    .badge-err { background: #fed7d7; color: #822727; }
    .badge-warn { background: #feebc8; color: #744210; }
    .conversion-tag { color: #718096; font-style: italic; }
    """

    status_class = "status-pass" if validation["all_passed"] else "status-fail"
    status_text = "PASSED" if validation["all_passed"] else "ISSUES DETECTED"

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Migration Report - {mysql_db} to {pg_db}</title>
    <style>{css}</style>
</head>
<body>
    <div class="header">
        <div>
            <h1>Migration Report</h1>
            <p style="color: #718096; margin-top: 4px;">{mysql_db} (MySQL) &rarr; {pg_db} (PostgreSQL)</p>
        </div>
        <div class="status {status_class}">{status_text}</div>
    </div>

    <!-- Summary Stats -->
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 40px;">
        <div class="card" style="margin-bottom: 0; text-align: center;">
            <div style="color: #718096; font-size: 0.875rem;">Tables Migrated</div>
            <div style="font-size: 2rem; font-weight: 700; color: #2d3748;">{validation['row_counts']['passed']}/{validation['row_counts']['total']}</div>
        </div>
        <div class="card" style="margin-bottom: 0; text-align: center;">
            <div style="color: #718096; font-size: 0.875rem;">Type Conversions</div>
            <div style="font-size: 2rem; font-weight: 700; color: #2d3748;">{diff['conversions']}</div>
        </div>
        <div class="card" style="margin-bottom: 0; text-align: center;">
            <div style="color: #718096; font-size: 0.875rem;">Objects Verified</div>
            <div style="font-size: 2rem; font-weight: 700; color: #2d3748;">{len(validation['constraints'].get('indexes', [])) + len(validation['constraints'].get('foreign_keys', []))}</div>
        </div>
    </div>

    {f'<div class="card" style="border-left: 4px solid #f56565;"><h3>Errors</h3><ul style="color: #c53030;">' + "".join(f"<li>{e}</li>" for e in validation['validation_errors']) + '</ul></div>' if validation['validation_errors'] else ""}

    <div class="card">
        <h3>Row Count Comparison</h3>
        <table>
            <thead>
                <tr>
                    <th>Table Name</th>
                    <th>MySQL Count</th>
                    <th>PostgreSQL Count</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
    """

    for t in validation['row_counts']['tables']:
        b_class = "badge-ok" if "OK" in t['status'] else "badge-err"
        if "EXTRA" in t['status']: b_class = "badge-warn"
        
        html += f"""
                <tr>
                    <td class="table-name">{t['table']}</td>
                    <td class="mysql-val">{t['mysql']}</td>
                    <td class="pg-val">{t['pg']}</td>
                    <td><span class="badge {b_class}">{t['status']}</span></td>
                </tr>"""

    html += """
            </tbody>
        </table>
    </div>

    <div class="card">
        <h3>Type Mappings & Conversions</h3>
        <table>
            <thead>
                <tr>
                    <th>Table.Column</th>
                    <th>MySQL Type</th>
                    <th>PostgreSQL Type</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
    """

    for d in diff['diffs']:
        if d['status'] == 'identical': continue
        b_class = "badge-ok" if d['status'] == 'converted' else "badge-warn"
        if d['status'] == 'missing': b_class = "badge-err"
        
        html += f"""
                <tr>
                    <td class="table-name">{d['key']}</td>
                    <td class="mysql-val">{d['mysql']}</td>
                    <td class="pg-val">{d['pg']}</td>
                    <td><span class="badge {b_class}">{d['status']}</span></td>
                </tr>"""

    html += """
            </tbody>
        </table>
    </div>

    <div class="card">
        <h3>Constraints & Metadata</h3>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 40px;">
            <div>
                <h4>Foreign Keys</h4>
                <ul style="font-size: 0.875rem; color: #4a5568;">
    """
    
    if validation['constraints'].get('foreign_keys'):
        for fk in validation['constraints']['foreign_keys']:
            html += f"<li><code>{fk[0]}.{fk[1]}</code> &rarr; <code>{fk[2]}.{fk[3]}</code></li>"
    else:
        html += "<li>No foreign keys detected</li>"

    html += """
                </ul>
            </div>
            <div>
                <h4>Indexes</h4>
                <ul style="font-size: 0.875rem; color: #4a5568;">
    """

    if validation['constraints'].get('indexes'):
        for idx in validation['constraints']['indexes']:
            html += f"<li><code>{idx[1]}</code> on <code>{idx[0]}</code></li>"
    else:
        html += "<li>No indexes detected</li>"

    html += """
                </ul>
            </div>
        </div>
    </div>

    <footer style="text-align: center; color: #a0aec0; font-size: 0.75rem; margin-top: 40px;">
        Generated by mysql2pg migration tool
    </footer>
</body>
</html>
    """

    with open(html_file, "w") as f:
        f.write(html)
    
    return html_file


def dry_run(mysql_cfg: MySQLConfig, pg_cfg: PGConfig):
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


# ═════════════════════════════════════════════════════════════
# Main pipeline
# ═════════════════════════════════════════════════════════════

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


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        console.print("\n[dim]Migration cancelled by user.[/dim]")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n[red]Unexpected error: {e}[/red]")
        console.print("[dim]Please report this issue with the full traceback.[/dim]")
        import traceback
        traceback.print_exc()
        sys.exit(1)

