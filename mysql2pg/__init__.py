"""
═══════════════════════════════════════════════════════════════
  MySQL → PostgreSQL Migration Tool (mysql2pg)
═══════════════════════════════════════════════════════════════
"""

from pathlib import Path
from rich.console import Console

# ═════════════════════════════════════════════════════════════
# Shared console instance
# ═════════════════════════════════════════════════════════════

console = Console()

# ═════════════════════════════════════════════════════════════
# Constants
# ═════════════════════════════════════════════════════════════

SCRIPT_DIR = Path(__file__).parent.parent.resolve()
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
