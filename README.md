# 🐘 mysql2pg

**Automated MySQL → PostgreSQL migration tool** with data validation and detailed reporting.

Built with [pgloader](https://pgloader.io/) for data migration, [Docker](https://www.docker.com/) for the PostgreSQL runtime, and [Rich](https://rich.readthedocs.io/) for a beautiful terminal experience.

---

## ✨ Features

- **One-command migration** — `python migrate.py` handles everything
- **Prisma compatibility** — `python migrate.py --prisma-compat` converts naming to snake_case and creates native PostgreSQL ENUMs
- **Dry-run mode** — `python migrate.py --dry-run` validates config, tests connections, previews tables — without migrating
- **Progress bar** — Live per-table progress during pgloader migration
- **Schema diff report** — Side-by-side MySQL vs PostgreSQL type comparison after migration
- **HTML migration report** — Detailed `migration_report.html` with row counts, type mappings, and constraints
- **Automatic Docker management** — Spins up PostgreSQL and pgloader containers (if running locally)
- **Remote PostgreSQL support** — Can migrate to external servers (RDS, DigitalOcean, etc.)
- **Smart type casting** — Handles MySQL→PostgreSQL type differences (booleans, dates, enums, unsigned ints)
- **Zero-date handling** — Converts MySQL's `0000-00-00` to `NULL`
- **Built-in validation** — Row counts, column types, primary keys, foreign keys, indexes, sequences
- **Rich terminal UI** — Colored output, progress bars, formatted tables
- **File-based config** — No interactive prompts; fill in a JSON file and run
- **Safe & Read-Only** — Never modifies or deletes data from the source MySQL database

---

## 📸 Preview

```
╭──────────────────────────────────────────────────────╮
│   MySQL → PostgreSQL Migration Tool                  │
│   Ubuntu/Linux • Powered by pgloader + Docker        │
╰──────────────────────────────────────────────────────╯

  ✓ Config loaded from migration_config.json

[1/7] Starting PostgreSQL container...         ✓
[2/7] Generating pgloader configuration...     ✓
[3/7] Running pgloader migration...

  ⠋ Migrating 8 tables (104,783 rows)...  ████████████▌     50%  0:00:12
  ⠋ Migrated orders (5/8)...

[4/7] Prisma compatibility transforms...
  ✓ Renamed 3 tables to snake_case
  ✓ Renamed 12 columns to snake_case
  ✓ Created 2 native ENUM types

[5/7] Validating migration...

  Row Count Comparison
  ╭──────────────────┬─────────┬──────────┬────────╮
  │ Table            │ MySQL   │ Postgres │ Status │
  ├──────────────────┼─────────┼──────────┼────────┤
  │ user_accounts    │ 12,450  │ 12,450   │ ✓ OK   │
  │ orders           │ 89,231  │ 89,231   │ ✓ OK   │
  │ products         │ 3,102   │ 3,102    │ ✓ OK   │
  ╰──────────────────┴─────────┴──────────┴────────╯

[6/7] Schema diff report...
[7/7] Migration summary

╭──────────────────────────────────────────────────────╮
│  ✓ Migration completed successfully!                 │
│  All row counts match and types are correctly mapped │
╰──────────────────────────────────────────────────────╯
```

> **Note:** The preview above shows the output with `--prisma-compat`. Without the flag, the pipeline has 6 steps and skips Prisma transforms.

---

## 🚀 Quick Start

### 1. Clone the repo

```bash
git clone https://github.com/jbernardoCruz/mysql2pg.git
cd mysql2pg
```

### 2. Create a virtual environment and install dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

> **Prerequisites:** Python 3.8+, Docker Engine ≥ 20.10

### 3. Create config file

```bash
python migrate.py --init
```

### 4. Edit `migration_config.json`

```json
{
  "mysql": {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "your_password",
    "database": "your_database"
  },
  "postgresql": {
    "host": "localhost",
    "port": 5432,
    "user": "postgres",
    "database": "myapp",
    "password": "postgres",
    "schema": "legacy",
    "container_name": "pg-target"
  }
}
```

### 5. Preview with dry-run (optional)

```bash
python migrate.py --dry-run
```

This validates your config, tests the MySQL connection, checks Docker, and shows what will be migrated — **without touching any data**.

### 6. Run the migration

```bash
python migrate.py
```

That's it. The tool will start PostgreSQL in Docker, run pgloader with a progress bar, migrate your data, validate the results, and show a schema diff report.

### 7. Prisma compatibility (optional)

If you're using [Prisma ORM](https://www.prisma.io/) with the migrated database, add the `--prisma-compat` flag:

```bash
python migrate.py --prisma-compat
```

This applies post-migration transforms to ensure full Prisma compatibility:
- **snake_case naming** — Renames tables and columns from `camelCase`/`PascalCase` to `snake_case` (e.g. `UserAccounts` → `user_accounts`, `firstName` → `first_name`)
- **Native ENUM types** — Converts pgloader's `ENUM→TEXT` to native PostgreSQL ENUM types, which Prisma introspects as `enum` blocks

After migration with `--prisma-compat`, running `prisma db pull` will generate a clean schema with:
- Models in PascalCase (Prisma infers from snake_case tables)
- Fields in camelCase (Prisma infers from snake_case columns)
- Proper `@@map()` and `@map()` attributes
- Native enum definitions

---

## 📁 Project Structure

```
mysql2pg/
├── migrate.py                      # CLI entrypoint
├── mysql2pg/                       # Core package
│   ├── __init__.py                 # Shared console, constants
│   ├── config.py                   # Config classes, loading, MySQL test
│   ├── docker_utils.py             # Docker client, PostgreSQL container
│   ├── pgloader.py                 # Config generation, migration execution
│   ├── validation.py               # Row counts, types, constraints
│   ├── schema_diff.py              # Schema capture, diff report
│   ├── reporting.py                # HTML report generation
│   ├── cli.py                      # CLI args, dry-run, main pipeline
│   ├── naming.py                   # camelCase → snake_case conversion (Prisma)
│   ├── enums.py                    # MySQL ENUM → native PG ENUM (Prisma)
│   └── post_migration.py           # Post-migration transforms (Prisma)
├── migration_config.json           # Connection credentials (gitignored)
├── requirements.txt                # Python dependencies
├── README.md                       # ← You are here
├── LICENSE                         # MIT License
├── pgloader/
│   └── migration.load.template     # pgloader config template
├── tests/
│   └── test_naming.py              # Unit tests for naming conventions
└── scripts/
    ├── validate.sql                # Manual row count queries (psql)
    └── validate_types.sql          # Manual column type queries (psql)
```

---

## 🔄 Type Mapping

The tool automatically handles these MySQL → PostgreSQL type conversions:

| MySQL Type | PostgreSQL Type | Notes |
|---|---|---|
| `TINYINT(1)` | `BOOLEAN` | `0` → `false`, non-zero → `true` |
| `TINYINT` | `BOOLEAN` | `0` → `false`, non-zero → `true` |
| `SMALLINT UNSIGNED` | `INTEGER` | Unsigned → wider signed |
| `BIT(1)` | `BOOLEAN` | — |
| `INT UNSIGNED` | `BIGINT` | Unsigned → wider signed |
| `BIGINT UNSIGNED` | `NUMERIC` | No PG unsigned equivalent |
| `DATETIME` | `TIMESTAMP` | Zero-dates → `NULL`, no timezone conversion |
| `DATE` | `DATE` | Zero-dates → `NULL` |
| `YEAR` | `INTEGER` | No PG year type |
| `ENUM(...)` | `TEXT` | Default: mapped to TEXT |
| `ENUM(...)` | Native `ENUM` | With `--prisma-compat`: creates native PG ENUM type |
| `SET(...)` | `TEXT` | No PG equivalent |
| `AUTO_INCREMENT` | `SERIAL` / `BIGSERIAL` | Sequences auto-created |

> **Note:** pgloader 3.6.7 does not support conditional CAST predicates (`when (= precision N)`).
> The `drop typemod` flag is used to prevent `boolean(1)` syntax errors in PostgreSQL.
>
> **Prisma ENUMs:** With `--prisma-compat`, native ENUM type names follow the `{table}_{column}` convention (e.g. `users_status`). This ensures Prisma introspects them correctly as `enum` blocks.

---

## ✅ Validation

The tool automatically validates after migration:

| Check | Details |
|---|---|
| **Row counts** | Exact comparison MySQL vs PostgreSQL per table |
| **Schema discovery** | Auto-detects target schema (handles `legacy`, `public`, or database-named schemas) |
| **Boolean types** | Verifies `TINYINT` → `boolean` |
| **Timestamp types** | Verifies `DATETIME` → `timestamp` |
| **Unsigned ints** | Verifies `INT UNSIGNED` → `bigint` |
| **Primary keys** | Confirms PKs exist on all tables |
| **Foreign keys** | Confirms FK constraints migrated |
| **Indexes** | Confirms indexes recreated |
| **Sequences** | Confirms auto-increment sequences created |

For manual inspection via psql:

```bash
# Replace <container_name> with your configured container name (default: pg-target)
docker exec -it <container_name> psql -U postgres -d myapp

# Inside psql:
\i scripts/validate.sql
\i scripts/validate_types.sql
```

---

## 🔧 Configuration

### `migration_config.json`

| Field | Default | Description |
|---|---|---|
| `mysql.host` | `localhost` | MySQL server hostname or IP |
| `mysql.port` | `3306` | MySQL port |
| `mysql.user` | `root` | MySQL username |
| `mysql.password` | — | MySQL password (required) |
| `mysql.database` | — | MySQL database name (required) |
| `postgresql.host` | `localhost` | PostgreSQL server hostname or IP |
| `postgresql.port` | `5432` | Host port for PostgreSQL container |
| `postgresql.user` | `postgres` | PostgreSQL username |
| `postgresql.database` | `myapp` | PostgreSQL database name |
| `postgresql.password` | `postgres` | PostgreSQL password |
| `postgresql.schema` | `legacy` | Target schema name in PostgreSQL |
| `postgresql.container_name` | `pg-target` | Name of the Docker container for PostgreSQL |

> ⚠️ **Security:** `migration_config.json` is in `.gitignore` — your credentials are never committed.
>
> **Remote PostgreSQL:** If you set `postgresql.host` to anything other than `localhost` or `127.0.0.1`, the tool will **skip starting the PostgreSQL container** and attempt to connect to your remote server directly.
>
> **Schema naming:** By default, tables are placed in the `legacy` schema (not `public`). This separates migrated data from new PostgreSQL-native tables. Prisma supports this via the `schemas` property in `datasource`.

---

## 🐧 Ubuntu/Linux Notes

On Linux, Docker containers can't resolve `host.docker.internal` by default. The tool automatically adds `extra_hosts: host-gateway` to the pgloader container, which maps `host.docker.internal` → your host IP (requires Docker Engine ≥ 20.10).

If MySQL is behind `ufw` firewall:

```bash
sudo ufw allow from 172.16.0.0/12 to any port 3306
```

If MySQL only listens on `127.0.0.1`:

```bash
# Edit /etc/mysql/mysql.conf.d/mysqld.cnf
bind-address = 0.0.0.0

# Restart
sudo systemctl restart mysql
```

---

## 🐳 Docker Container Management

After migration, the PostgreSQL container (default: `pg-target`, configurable via `postgresql.container_name`) keeps running so your data stays available.

### Common Commands

```bash
# Replace <container_name> with your configured name (default: pg-target)

# Check status
docker ps | grep <container_name>

# Stop (data is preserved)
docker stop <container_name>

# Start again later
docker start <container_name>

# Connect to PostgreSQL
docker exec -it <container_name> psql -U postgres -d your_database

# View logs
docker logs <container_name>

# ⚠️ Remove container AND data (destructive)
docker rm -f <container_name>
docker volume rm sql_pgdata
```

### Data Persistence

| Action | Data Survives? |
|---|---|
| `docker stop <container>` | ✅ Yes |
| `docker start <container>` | ✅ Yes |
| System reboot | ✅ Yes — just `docker start <container>` |
| `docker rm <container>` | ✅ Yes — volume still exists |
| `docker volume rm sql_pgdata` | ❌ **No** — data is gone |

### Typical Workflow

1. **Migrate:** `python migrate.py` — starts container + migrates
2. **Use:** Connect your app to `localhost:5432`
3. **Pause:** `docker stop <container_name>` (saves resources)
4. **Resume:** `docker start <container_name>` — everything is still there
5. **Re-migrate:** The tool drops and recreates tables automatically

---

## 🛠 Troubleshooting

<details>
<summary><strong>pgloader can't connect to MySQL</strong></summary>

1. Check firewall: `sudo ufw allow from 172.16.0.0/12 to any port 3306`
2. Check MySQL bind-address: set `bind-address = 0.0.0.0` in MySQL config
3. Verify Docker networking: `docker exec pgloader_runner ping -c 1 host.docker.internal`

</details>

<details>
<summary><strong>Duplicate key errors after migration</strong></summary>

Sequences may not be synced. Fix in psql:

```sql
SELECT setval(
  pg_get_serial_sequence('your_table', 'id'),
  (SELECT MAX(id) FROM your_table)
);
```

The tool's `reset sequences` option usually handles this automatically.

</details>

<details>
<summary><strong>Docker not found / permission denied</strong></summary>

```bash
# Install Docker Engine
sudo apt-get install -y docker-ce docker-ce-cli containerd.io

# Add user to docker group
sudo usermod -aG docker $USER
newgrp docker
```

</details>

<details>
<summary><strong>Config file validation errors</strong></summary>

The tool detects placeholder values. Make sure you've replaced:
- `YOUR_MYSQL_PASSWORD` → your actual MySQL password
- `YOUR_DATABASE_NAME` → your actual database name

</details>

<details>
<summary><strong>Remote PostgreSQL connection failed</strong></summary>

If targeting a remote server (e.g., RDS):
1. Ensure the remote server accepts connections from your IP.
2. If running locally, you might need to allow your IP in the remote firewall (Security Groups).
3. `pgloader` runs inside Docker, so it needs internet access.

</details>

<details>
<summary><strong>pgloader CAST / parsing errors</strong></summary>

pgloader `3.6.7~devel` has limited CAST syntax support:
- `using <function>` is a **terminal clause** — no modifiers (`drop typemod`, `when`) can follow it.
- `when (= precision N)` S-expression predicates are **not supported**.

The template has been designed for 3.6.7 compatibility. If you upgrade pgloader, you may
re-enable conditional CAST rules in `pgloader/migration.load.template`.

pgloader logs are always saved to `pgloader/pgloader.log` (success) or
`pgloader/pgloader_error.log` (failure) for debugging.

</details>

---

## 📝 License

This project is licensed under the [MIT License](LICENSE).

---

## 🤝 Contributing

Contributions are welcome! Here's how:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Ideas for contributions:

- [ ] Support for custom type casting rules via config
- [x] ~~Dry-run mode (validate config without migrating)~~ — ✅ Implemented
- [ ] Support for table filtering (include/exclude specific tables)
- [x] ~~Progress bar during pgloader migration (parse log output)~~ — ✅ Implemented
- [ ] macOS / Windows support
- [x] ~~Schema diff report (before/after comparison)~~ — ✅ Implemented
- [x] ~~Prisma ORM compatibility (snake_case naming + native ENUMs)~~ — ✅ Implemented (`--prisma-compat`)
