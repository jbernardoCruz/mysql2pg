# 🐘 mysql2pg

**Automated MySQL → PostgreSQL migration tool** with data validation and optional Prisma ORM integration.

Built with [pgloader](https://pgloader.io/) for data migration, [Docker](https://www.docker.com/) for the PostgreSQL runtime, and [Rich](https://rich.readthedocs.io/) for a beautiful terminal experience.

---

## ✨ Features

- **One-command migration** — `python migrate.py` handles everything
- **Dry-run mode** — `python migrate.py --dry-run` validates config, tests connections, previews tables — without migrating
- **Progress bar** — Live per-table progress during pgloader migration
- **Schema diff report** — Side-by-side MySQL vs PostgreSQL type comparison after migration
- **Automatic Docker management** — Spins up PostgreSQL and pgloader containers (if running locally)
- **Remote PostgreSQL support** — Can migrate to external servers (RDS, DigitalOcean, etc.)
- **Smart type casting** — Handles MySQL→PostgreSQL type differences (booleans, dates, enums, unsigned ints)
- **Zero-date handling** — Converts MySQL's `0000-00-00` to `NULL`
- **Built-in validation** — Row counts, column types, primary keys, foreign keys, indexes, sequences
- **Rich terminal UI** — Colored output, progress bars, formatted tables
- **File-based config** — No interactive prompts; fill in a JSON file and run
- **Prisma-ready** — Optional post-migration Prisma ORM setup
- **Safe & Read-Only** — Never modifies or deletes data from the source MySQL database

---

## 📸 Preview

```
╭──────────────────────────────────────────────────────╮
│   MySQL → PostgreSQL Migration Tool                  │
│   Ubuntu/Linux • Powered by pgloader + Docker        │
╰──────────────────────────────────────────────────────╯

  ✓ Config loaded from migration_config.json

[1/6] Starting PostgreSQL container...         ✓
[2/6] Generating pgloader configuration...     ✓
[3/6] Running pgloader migration...

  ⠋ Migrating 8 tables (104,783 rows)...  ████████████▌     50%  0:00:12
  ⠋ Migrated orders (5/8)...

[4/6] Validating migration...

  Row Count Comparison
  ╭──────────────┬─────────┬──────────┬────────╮
  │ Table        │ MySQL   │ Postgres │ Status │
  ├──────────────┼─────────┼──────────┼────────┤
  │ users        │ 12,450  │ 12,450   │ ✓ OK   │
  │ orders       │ 89,231  │ 89,231   │ ✓ OK   │
  │ products     │ 3,102   │ 3,102    │ ✓ OK   │
  ╰──────────────┴─────────┴──────────┴────────╯

[5/6] Schema diff report...
[6/6] Migration summary

╭──────────────────────────────────────────────────────╮
│  ✓ Migration completed successfully!                 │
│  All row counts match and types are correctly mapped │
╰──────────────────────────────────────────────────────╯
```

---

## 🚀 Quick Start

### 1. Clone the repo

```bash
git clone https://github.com/jbernardoCruz/mysql2pg.git
cd mysql2pg
```

### 2. Install dependencies

```bash
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
    "password": "postgres"
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

---

## 📁 Project Structure

```
mysql2pg/
├── migrate.py                      # Main CLI tool
├── migration_config.json           # Connection credentials (gitignored)
├── requirements.txt                # Python dependencies
├── README.md                       # ← You are here
├── LICENSE                         # MIT License
├── docs/
│   └── PRISMA_SETUP.md             # Optional Prisma ORM integration guide
├── pgloader/
│   └── migration.load.template     # pgloader config template
├── scripts/
│   ├── validate.sql                # Manual row count queries (psql)
│   ├── validate_types.sql          # Manual column type queries (psql)
│   └── test_connection.js          # Prisma connection test
├── package.json                    # Node.js deps for Prisma (optional)
└── prisma/
    └── schema.prisma               # Generated by prisma db pull (optional)
```

---

## 🔄 Type Mapping

The tool automatically handles these MySQL → PostgreSQL type conversions:

| MySQL Type | PostgreSQL Type | Notes |
|---|---|---|
| `TINYINT(1)` | `BOOLEAN` | MySQL boolean emulation |
| `TINYINT` | `BOOLEAN` | All tinyint mapped to boolean |
| `BIT(1)` | `BOOLEAN` | — |
| `INT UNSIGNED` | `BIGINT` | Unsigned → wider signed |
| `BIGINT UNSIGNED` | `NUMERIC` | No PG unsigned equivalent |
| `DATETIME` | `TIMESTAMPTZ` | Zero-dates → `NULL` |
| `DATE` | `DATE` | Zero-dates → `NULL` |
| `YEAR` | `INTEGER` | No PG year type |
| `ENUM(...)` | `TEXT` | Optionally add CHECK constraints |
| `SET(...)` | `TEXT` | No PG equivalent |
| `AUTO_INCREMENT` | `SERIAL` / `BIGSERIAL` | Sequences auto-created |

> **Note:** pgloader 3.6.7 does not support conditional CAST predicates (`when (= precision N)`),
> so all `TINYINT` columns are cast to `BOOLEAN`. If your schema uses `TINYINT` for non-boolean
> values, consider a post-migration `ALTER TABLE` to fix affected columns.

---

## ✅ Validation

The tool automatically validates after migration:

| Check | Details |
|---|---|
| **Row counts** | Exact comparison MySQL vs PostgreSQL per table |
| **Schema discovery** | Auto-detects target schema (handles `public` or database-named schemas) |
| **Boolean types** | Verifies `TINYINT` → `boolean` |
| **Timestamp types** | Verifies `DATETIME` → `timestamptz` |
| **Unsigned ints** | Verifies `INT UNSIGNED` → `bigint` |
| **Primary keys** | Confirms PKs exist on all tables |
| **Foreign keys** | Confirms FK constraints migrated |
| **Indexes** | Confirms indexes recreated |
| **Sequences** | Confirms auto-increment sequences created |

For manual inspection via psql:

```bash
docker exec -it pg-target psql -U postgres -d myapp

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

> ⚠️ **Security:** `migration_config.json` is in `.gitignore` — your credentials are never committed.
>
> **Remote PostgreSQL:** If you set `postgresql.host` to anything other than `localhost` or `127.0.0.1`, the tool will **skip starting the PostgreSQL container** and attempt to connect to your remote server directly.

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

## 📦 Prisma Integration (Optional)

After migration, you can optionally set up [Prisma ORM](https://www.prisma.io/) to work with the migrated PostgreSQL database.

See **[docs/PRISMA_SETUP.md](docs/PRISMA_SETUP.md)** for the full guide.

Quick version:

```bash
npm install
npx prisma db pull
npx prisma generate
npm run test:connection
```

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
