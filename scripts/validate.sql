-- ═══════════════════════════════════════════════════════════════
-- Data Validation: Row Counts
-- ═══════════════════════════════════════════════════════════════
-- Run this against PostgreSQL AFTER migration to verify
-- that all tables were migrated and row counts are correct.
--
-- Compare the output with MySQL's information_schema.tables:
--   SELECT table_name, table_rows
--   FROM information_schema.tables
--   WHERE table_schema = 'source_db';
--
-- NOTE: pg_stat_user_tables may show approximate counts.
-- For exact counts, use the query at the bottom of this file.
-- ═══════════════════════════════════════════════════════════════

-- Quick overview (approximate counts, fast)
SELECT
  schemaname,
  relname     AS table_name,
  n_live_tup  AS approx_row_count
FROM pg_stat_user_tables
WHERE schemaname = 'public'
ORDER BY relname;


-- ═══════════════════════════════════════════════════════════════
-- Exact row counts (slower, but precise)
-- ═══════════════════════════════════════════════════════════════
-- Uncomment and run the following to get exact counts.
-- This generates and executes a COUNT(*) for each table.
-- ═══════════════════════════════════════════════════════════════

-- DO $$
-- DECLARE
--   rec RECORD;
--   cnt BIGINT;
-- BEGIN
--   RAISE NOTICE '%-40s | %s', 'Table', 'Exact Count';
--   RAISE NOTICE '%-40s-+-%s', REPEAT('-', 40), REPEAT('-', 12);
--   FOR rec IN
--     SELECT table_name
--     FROM information_schema.tables
--     WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
--     ORDER BY table_name
--   LOOP
--     EXECUTE format('SELECT COUNT(*) FROM public.%I', rec.table_name) INTO cnt;
--     RAISE NOTICE '%-40s | %s', rec.table_name, cnt;
--   END LOOP;
-- END $$;
