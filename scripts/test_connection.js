// ═══════════════════════════════════════════════════════════════
// Test Connection Script
// ═══════════════════════════════════════════════════════════════
// Verifies that Prisma can connect to the migrated PostgreSQL
// database and perform basic read operations.
//
// Usage:
//   npm run test:connection
//   # or
//   node scripts/test_connection.js
// ═══════════════════════════════════════════════════════════════

const { PrismaClient } = require("@prisma/client");

const prisma = new PrismaClient({
  log: ["query", "info", "warn", "error"],
});

async function main() {
  console.log("═══════════════════════════════════════════════════");
  console.log("  Prisma Connection Test");
  console.log("═══════════════════════════════════════════════════");
  console.log("");

  try {
    // Test 1: Basic connection
    console.log("[1/4] Testing database connection...");
    await prisma.$connect();
    console.log("  ✓ Connected to PostgreSQL\n");

    // Test 2: Raw query — list tables
    console.log("[2/4] Listing migrated tables...");
    const tables = await prisma.$queryRaw`
      SELECT table_name
      FROM information_schema.tables
      WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
      ORDER BY table_name;
    `;
    console.log("  Tables found:", tables.length);
    tables.forEach((t) => console.log(`    - ${t.table_name}`));
    console.log("");

    // Test 3: Row counts per table
    console.log("[3/4] Checking row counts...");
    for (const table of tables) {
      const count = await prisma.$queryRawUnsafe(
        `SELECT COUNT(*) as count FROM "${table.table_name}"`
      );
      console.log(`    ${table.table_name}: ${count[0].count} rows`);
    }
    console.log("");

    // Test 4: Check column types for date/boolean columns
    console.log("[4/4] Verifying critical column types...");
    const criticalTypes = await prisma.$queryRaw`
      SELECT table_name, column_name, data_type, udt_name
      FROM information_schema.columns
      WHERE table_schema = 'public'
        AND (udt_name = 'bool' OR data_type LIKE 'timestamp%' OR udt_name = 'int2')
      ORDER BY table_name, column_name;
    `;

    if (criticalTypes.length > 0) {
      console.log("  Converted columns:");
      criticalTypes.forEach((c) =>
        console.log(
          `    ${c.table_name}.${c.column_name} → ${c.data_type} (${c.udt_name})`
        )
      );
    } else {
      console.log("  No boolean/timestamp/smallint columns found.");
    }

    console.log("");
    console.log("═══════════════════════════════════════════════════");
    console.log("  ✓ All connection tests passed!");
    console.log("═══════════════════════════════════════════════════");
  } catch (error) {
    console.error("");
    console.error("═══════════════════════════════════════════════════");
    console.error("  ✗ Connection test FAILED");
    console.error("═══════════════════════════════════════════════════");
    console.error("  Error:", error.message);
    console.error("");
    console.error("  Troubleshooting:");
    console.error("    1. Is PostgreSQL running? docker ps | grep pg_target");
    console.error("    2. Check .env DATABASE_URL");
    console.error("    3. Run: npx prisma db pull && npx prisma generate");
    process.exit(1);
  } finally {
    await prisma.$disconnect();
  }
}

main();
