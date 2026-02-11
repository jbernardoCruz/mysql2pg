-- ═══════════════════════════════════════════════════════════════
-- Data Validation: Column Type Verification
-- ═══════════════════════════════════════════════════════════════
-- Run this against PostgreSQL AFTER migration to verify
-- that data types were correctly mapped from MySQL.
--
-- Key things to verify:
--   ✓ TINYINT(1)  → boolean
--   ✓ TINYINT     → smallint (non-boolean)
--   ✓ DATETIME    → timestamp with time zone (timestamptz)
--   ✓ DATE        → date
--   ✓ YEAR        → integer
--   ✓ ENUM        → text
--   ✓ SET         → text
--   ✓ INT UNSIGNED→ bigint
--   ✓ BIT(1)      → boolean
--   ✓ AUTO_INCREMENT → sequences created
-- ═══════════════════════════════════════════════════════════════


-- ─── 1. Full column type listing ─────────────────────────────
SELECT
  table_name,
  column_name,
  data_type,
  udt_name,
  character_maximum_length,
  numeric_precision,
  is_nullable,
  column_default
FROM information_schema.columns
WHERE table_schema = 'public'
ORDER BY table_name, ordinal_position;


-- ─── 2. Boolean columns check ────────────────────────────────
-- These should be 'boolean' type (converted from TINYINT(1))
SELECT
  table_name,
  column_name,
  data_type,
  udt_name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND udt_name = 'bool'
ORDER BY table_name, column_name;


-- ─── 3. Timestamp columns check ─────────────────────────────
-- DATETIME columns should be 'timestamp with time zone'
SELECT
  table_name,
  column_name,
  data_type,
  udt_name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND data_type LIKE 'timestamp%'
ORDER BY table_name, column_name;


-- ─── 4. Check for zero-date NULLs ───────────────────────────
-- Verify that zero-dates were converted to NULL.
-- Replace 'your_table' and 'your_date_column' with actual names.
-- 
-- SELECT COUNT(*) AS null_dates
-- FROM your_table
-- WHERE your_date_column IS NULL;


-- ─── 5. Sequence / Auto-increment check ─────────────────────
-- Verify that sequences were created for auto-increment columns
SELECT
  sequence_name,
  data_type,
  start_value,
  minimum_value,
  maximum_value,
  increment
FROM information_schema.sequences
WHERE sequence_schema = 'public'
ORDER BY sequence_name;


-- ─── 6. Primary key check ───────────────────────────────────
-- Verify all primary keys were migrated
SELECT
  tc.table_name,
  kcu.column_name,
  tc.constraint_type
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON tc.constraint_name = kcu.constraint_name
  AND tc.table_schema = kcu.table_schema
WHERE tc.constraint_type = 'PRIMARY KEY'
  AND tc.table_schema = 'public'
ORDER BY tc.table_name;


-- ─── 7. Index check ─────────────────────────────────────────
-- Verify indexes were recreated
SELECT
  tablename,
  indexname,
  indexdef
FROM pg_indexes
WHERE schemaname = 'public'
ORDER BY tablename, indexname;


-- ─── 8. Foreign key check ───────────────────────────────────
-- Verify foreign key constraints were migrated
SELECT
  tc.table_name,
  kcu.column_name,
  ccu.table_name  AS foreign_table,
  ccu.column_name AS foreign_column,
  tc.constraint_name
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON tc.constraint_name = kcu.constraint_name
  AND tc.table_schema = kcu.table_schema
JOIN information_schema.constraint_column_usage ccu
  ON ccu.constraint_name = tc.constraint_name
  AND ccu.table_schema = tc.table_schema
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND tc.table_schema = 'public'
ORDER BY tc.table_name;
