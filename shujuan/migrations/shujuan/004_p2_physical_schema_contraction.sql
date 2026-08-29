DO $$
DECLARE
  constraint_row RECORD;
BEGIN
  FOR constraint_row IN
    SELECT
      n.nspname AS schema_name,
      c.relname AS table_name,
      con.conname AS constraint_name
    FROM pg_constraint con
    JOIN pg_class c ON c.oid = con.conrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE con.contype = 'f'
      AND n.nspname = current_schema()
      AND con.confrelid = ANY(ARRAY[
        to_regclass('standard_events'),
        to_regclass('work_chains'),
        to_regclass('review_results'),
        to_regclass('endpoint_inherited_blockers'),
        to_regclass('source_promises'),
        to_regclass('hard_predicates'),
        to_regclass('forbidden_substitutes'),
        to_regclass('task_predicate_links'),
        to_regclass('evidence_predicate_coverage')
      ]::oid[])
  LOOP
    EXECUTE format(
      'ALTER TABLE %I.%I DROP CONSTRAINT IF EXISTS %I',
      constraint_row.schema_name,
      constraint_row.table_name,
      constraint_row.constraint_name
    );
  END LOOP;
END $$;

DROP TABLE IF EXISTS evidence_predicate_coverage;
DROP TABLE IF EXISTS task_predicate_links;
DROP TABLE IF EXISTS forbidden_substitutes;
DROP TABLE IF EXISTS review_results;
DROP TABLE IF EXISTS endpoint_inherited_blockers;
DROP TABLE IF EXISTS hard_predicates;
DROP TABLE IF EXISTS source_promises;
DROP TABLE IF EXISTS work_chains;
DROP TABLE IF EXISTS standard_events;
