# Prisma ORM Setup (Post-Migration)

Set up [Prisma](https://www.prisma.io/) to work with your migrated PostgreSQL database.

> ⚠️ **Run these steps AFTER `python migrate.py` completes successfully.**

---

## Prerequisites

- [Node.js](https://nodejs.org/) 18+ and npm
- PostgreSQL container running (started by the migration tool)

### Install Node.js (Ubuntu)

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
```

---

## Step 1 — Install Dependencies

```bash
npm install
```

This installs `prisma` and `@prisma/client` from `package.json`.

---

## Step 2 — Introspect the Database

```bash
npx prisma db pull
```

This connects to PostgreSQL using `DATABASE_URL` from `.env` and generates `prisma/schema.prisma` with models matching your migrated tables.

---

## Step 3 — Review and Refine the Schema

Open `prisma/schema.prisma` and apply refinements:

```prisma
// BEFORE (auto-generated — lowercase, underscores):
model users {
  id         Int       @id @default(autoincrement())
  email      String    @unique
  is_active  Boolean?
  created_at DateTime? @db.Timestamptz
}

// AFTER (refined — PascalCase models, camelCase fields):
model User {
  id        Int       @id @default(autoincrement())
  email     String    @unique
  isActive  Boolean?  @map("is_active")
  createdAt DateTime? @map("created_at") @db.Timestamptz

  @@map("users")
}
```

### Key refinements:

| What | Before | After |
|---|---|---|
| Model names | `users` (table name) | `User` (PascalCase) + `@@map("users")` |
| Field names | `is_active` (column name) | `isActive` (camelCase) + `@map("is_active")` |
| Relations | May need manual setup | Add `@relation` fields |

---

## Step 4 — Generate Prisma Client

```bash
npx prisma generate
```

This generates the type-safe client in `node_modules/@prisma/client`.

---

## Step 5 — Baseline the Migration

Mark the current database state as the initial migration (without re-running DDL):

```bash
mkdir -p prisma/migrations/0_init

npx prisma migrate diff \
  --from-empty \
  --to-schema-datamodel prisma/schema.prisma \
  --script > prisma/migrations/0_init/migration.sql

npx prisma migrate resolve --applied 0_init
```

---

## Step 6 — Test the Connection

```bash
npm run test:connection
```

This runs `scripts/test_connection.js`, which:
1. Connects via Prisma Client
2. Lists all migrated tables
3. Shows row counts per table
4. Verifies critical column types (boolean, timestamptz, smallint)

---

## Ongoing Development

After the initial setup, use standard Prisma workflow:

```bash
# Create new migrations
npx prisma migrate dev --name add_new_feature

# Open Prisma Studio (visual database editor)
npx prisma studio

# Regenerate client after schema changes
npx prisma generate
```

---

## npm Scripts Reference

| Script | Command | Description |
|---|---|---|
| `npm run prisma:pull` | `npx prisma db pull` | Introspect DB → schema |
| `npm run prisma:generate` | `npx prisma generate` | Generate Prisma Client |
| `npm run prisma:studio` | `npx prisma studio` | Visual DB editor |
| `npm run prisma:baseline` | `npx prisma migrate resolve --applied 0_init` | Mark migration as applied |
| `npm run test:connection` | `node scripts/test_connection.js` | Test Prisma connection |

---

## Troubleshooting

### `prisma db pull` fails with P1001

```
ERROR: P1001 Can't reach database server
```

**Fix:**

```bash
# 1. Is PostgreSQL running? (If using local Docker container)
docker ps | grep pg_target

# 2. Check DATABASE_URL in .env
cat .env | grep DATABASE_URL

# 3. Test connectivity
docker exec pg_target pg_isready -U postgres
```

### Encoding issues

```sql
-- In psql, check PostgreSQL encoding:
SHOW server_encoding;  -- Should be UTF8
```
