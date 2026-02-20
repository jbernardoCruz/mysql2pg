"""
Configuration loading, validation, and connection testing.
"""

import sys
import json

import mysql.connector
from mysql.connector import Error as MySQLError
from rich.prompt import Confirm

from mysql2pg import console, CONFIG_FILE, DEFAULT_CONFIG


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
# Configuration functions
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
