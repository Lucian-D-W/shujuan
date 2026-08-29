CREATE TABLE IF NOT EXISTS interaction_events (
  id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL UNIQUE,
  endpoint_id TEXT,
  event_type TEXT NOT NULL,
  mode TEXT,
  session_id TEXT,
  actor TEXT,
  summary TEXT,
  source TEXT,
  content_hash TEXT,
  occurred_at TEXT,
  imported_at TEXT NOT NULL,
  reviewed_at TEXT,
  metadata TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (node_id) REFERENCES nodes(id),
  FOREIGN KEY (endpoint_id) REFERENCES endpoints(id),
  FOREIGN KEY (session_id) REFERENCES conversation_sessions(id)
);

CREATE TABLE IF NOT EXISTS discussion_segments (
  id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL UNIQUE,
  endpoint_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  session_id TEXT,
  title TEXT,
  status TEXT NOT NULL DEFAULT 'unreviewed',
  created_at TEXT NOT NULL,
  reviewed_at TEXT,
  metadata TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (node_id) REFERENCES nodes(id),
  FOREIGN KEY (endpoint_id) REFERENCES endpoints(id),
  FOREIGN KEY (event_id) REFERENCES interaction_events(id),
  FOREIGN KEY (session_id) REFERENCES conversation_sessions(id)
);

CREATE TABLE IF NOT EXISTS discussion_messages (
  id TEXT PRIMARY KEY,
  segment_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  node_id TEXT NOT NULL UNIQUE,
  session_id TEXT,
  agent_name TEXT,
  model_name TEXT,
  source_message_id TEXT,
  source_node_id TEXT,
  actor TEXT NOT NULL,
  content TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  turn_index INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (segment_id) REFERENCES discussion_segments(id),
  FOREIGN KEY (event_id) REFERENCES interaction_events(id),
  FOREIGN KEY (node_id) REFERENCES nodes(id),
  FOREIGN KEY (session_id) REFERENCES conversation_sessions(id),
  FOREIGN KEY (source_message_id) REFERENCES messages(id),
  FOREIGN KEY (source_node_id) REFERENCES nodes(id)
);

ALTER TABLE discussion_messages ADD COLUMN IF NOT EXISTS session_id TEXT;
ALTER TABLE discussion_messages ADD COLUMN IF NOT EXISTS agent_name TEXT;
ALTER TABLE discussion_messages ADD COLUMN IF NOT EXISTS model_name TEXT;
ALTER TABLE discussion_messages ADD COLUMN IF NOT EXISTS source_message_id TEXT;
ALTER TABLE discussion_messages ADD COLUMN IF NOT EXISTS source_node_id TEXT;

CREATE TABLE IF NOT EXISTS discussion_lifecycle_events (
  id TEXT PRIMARY KEY,
  segment_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT NOT NULL,
  source_node_id TEXT,
  actor TEXT,
  reason TEXT,
  created_at TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (segment_id) REFERENCES discussion_segments(id),
  FOREIGN KEY (source_node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS projection_snapshots (
  id TEXT PRIMARY KEY,
  projection_type TEXT NOT NULL,
  endpoint_id TEXT,
  generated_from_event_id TEXT,
  generated_at TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  payload_ref TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (endpoint_id) REFERENCES endpoints(id)
);

CREATE TABLE IF NOT EXISTS evidence_records (
  id TEXT PRIMARY KEY,
  evidence_node_id TEXT NOT NULL,
  record_type TEXT NOT NULL,
  ref TEXT,
  sha256 TEXT,
  created_at TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (evidence_node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS provider_runs (
  id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL UNIQUE,
  provider TEXT NOT NULL,
  contract_version TEXT NOT NULL,
  status TEXT NOT NULL,
  command TEXT,
  started_at TEXT,
  ended_at TEXT,
  metadata TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS provider_artifacts (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  node_id TEXT NOT NULL UNIQUE,
  path TEXT,
  capture_ref TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  content_type TEXT,
  created_at TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (run_id) REFERENCES provider_runs(id),
  FOREIGN KEY (node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS provider_entity_map (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  external_id TEXT NOT NULL,
  node_id TEXT NOT NULL,
  confidence REAL,
  created_at TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  UNIQUE (provider, external_id),
  FOREIGN KEY (node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS provider_facts (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  artifact_id TEXT NOT NULL,
  node_id TEXT NOT NULL UNIQUE,
  external_id TEXT,
  fact_type TEXT NOT NULL,
  summary TEXT NOT NULL,
  confidence REAL,
  provenance TEXT NOT NULL DEFAULT '{}',
  classification TEXT NOT NULL DEFAULT 'provider_hypothesis',
  mapped_node_id TEXT,
  created_at TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (run_id) REFERENCES provider_runs(id),
  FOREIGN KEY (artifact_id) REFERENCES provider_artifacts(id),
  FOREIGN KEY (node_id) REFERENCES nodes(id),
  FOREIGN KEY (mapped_node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS semantic_items (
  id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL UNIQUE,
  item_type TEXT NOT NULL,
  current_state TEXT NOT NULL,
  scope_node_id TEXT,
  source_node_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  props TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (node_id) REFERENCES nodes(id),
  FOREIGN KEY (scope_node_id) REFERENCES nodes(id),
  FOREIGN KEY (source_node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS semantic_lifecycle_events (
  id TEXT PRIMARY KEY,
  semantic_item_id TEXT NOT NULL,
  node_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  from_state TEXT,
  to_state TEXT NOT NULL,
  source_node_id TEXT,
  reason TEXT,
  created_at TEXT NOT NULL,
  props TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (semantic_item_id) REFERENCES semantic_items(id),
  FOREIGN KEY (node_id) REFERENCES nodes(id),
  FOREIGN KEY (source_node_id) REFERENCES nodes(id)
);

CREATE INDEX IF NOT EXISTS idx_interaction_events_endpoint ON interaction_events(endpoint_id, event_type, imported_at);
CREATE INDEX IF NOT EXISTS idx_discussion_segments_endpoint ON discussion_segments(endpoint_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_discussion_messages_segment ON discussion_messages(segment_id, turn_index);
CREATE INDEX IF NOT EXISTS idx_discussion_messages_session ON discussion_messages(session_id, turn_index);
CREATE INDEX IF NOT EXISTS idx_discussion_lifecycle_segment ON discussion_lifecycle_events(segment_id, created_at);
CREATE INDEX IF NOT EXISTS idx_projection_snapshots_endpoint ON projection_snapshots(endpoint_id, projection_type, generated_at);
CREATE INDEX IF NOT EXISTS idx_evidence_records_node ON evidence_records(evidence_node_id, record_type);
CREATE INDEX IF NOT EXISTS idx_provider_facts_run ON provider_facts(run_id);
CREATE INDEX IF NOT EXISTS idx_provider_facts_external ON provider_facts(external_id);
CREATE INDEX IF NOT EXISTS idx_provider_entity_map_external ON provider_entity_map(provider, external_id);
CREATE INDEX IF NOT EXISTS idx_semantic_items_state ON semantic_items(current_state, item_type);
CREATE INDEX IF NOT EXISTS idx_semantic_lifecycle_item ON semantic_lifecycle_events(semantic_item_id, created_at);

CREATE OR REPLACE FUNCTION shujuan_validate_semantic_item()
RETURNS trigger AS $$
BEGIN
  IF NEW.current_state NOT IN ('resolved', 'deferred', 'product_backlog', 'backlog', 'invalidated', 'superseded') THEN
    IF NEW.source_node_id IS NULL THEN
      RAISE EXCEPTION 'active semantic item % requires source_node_id', NEW.node_id;
    END IF;
    IF NEW.item_type IN ('change_set', 'test_result', 'artifact', 'user_confirmation') THEN
      RETURN NEW;
    END IF;
    IF NEW.scope_node_id IS NULL
       AND NOT EXISTS (
         SELECT 1 FROM edges e
         WHERE (
           e.from_node_id = NEW.node_id
           AND e.type IN ('APPLIES_TO', 'IMPLEMENTS')
         ) OR (
           e.to_node_id = NEW.node_id
           AND e.type = 'VALIDATED_BY'
         )
       ) THEN
      RAISE EXCEPTION 'active semantic item % requires scope_node_id or active linkage edge', NEW.node_id;
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS shujuan_semantic_item_guard ON semantic_items;

CREATE CONSTRAINT TRIGGER shujuan_semantic_item_guard
AFTER INSERT OR UPDATE OF current_state, source_node_id, scope_node_id ON semantic_items
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION shujuan_validate_semantic_item();

CREATE OR REPLACE FUNCTION shujuan_validate_check_closure()
RETURNS trigger AS $$
DECLARE
  evidence_type TEXT;
  expected TEXT;
  allowed TEXT[];
BEGIN
  IF NEW.closed_by_node_id IS NULL THEN
    RETURN NEW;
  END IF;
  SELECT type INTO evidence_type FROM nodes WHERE id = NEW.closed_by_node_id;
  IF evidence_type IS NULL OR evidence_type <> ALL(ARRAY['change_set','test_result','artifact','user_confirmation']) THEN
    RAISE EXCEPTION 'acceptance check % requires evidence node closure', NEW.id;
  END IF;
  expected := lower(replace(coalesce(NEW.expected_evidence_type, ''), '-', '_'));
  IF expected = '' THEN
    RETURN NEW;
  ELSIF expected IN ('diff', 'change_set') THEN
    allowed := ARRAY['change_set'];
  ELSIF expected IN ('test', 'test_result') THEN
    allowed := ARRAY['test_result'];
  ELSIF expected IN ('artifact', 'file') THEN
    allowed := ARRAY['artifact'];
  ELSIF expected = 'doc_update' THEN
    allowed := ARRAY['artifact','change_set'];
  ELSIF expected IN ('user_confirmation', 'confirmation') THEN
    allowed := ARRAY['user_confirmation'];
  ELSE
    allowed := ARRAY[expected];
  END IF;
  IF NOT evidence_type = ANY(allowed) AND NOT EXISTS (
    SELECT 1
    FROM nodes override_node
    WHERE override_node.type = 'audit_finding'
      AND override_node.props::jsonb @> jsonb_build_object(
        'kind', 'evidence_type_override',
        'check_id', NEW.id,
        'evidence_node_id', NEW.closed_by_node_id
      )
  ) THEN
    RAISE EXCEPTION 'acceptance check % expected %, got %', NEW.id, NEW.expected_evidence_type, evidence_type;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS shujuan_check_closure_guard ON acceptance_checks;

CREATE CONSTRAINT TRIGGER shujuan_check_closure_guard
AFTER INSERT OR UPDATE OF closed_by_node_id, expected_evidence_type ON acceptance_checks
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION shujuan_validate_check_closure();

CREATE OR REPLACE FUNCTION shujuan_validate_task_closure()
RETURNS trigger AS $$
BEGIN
  IF NEW.closed_by_node_id IS NOT NULL
     AND EXISTS (
       SELECT 1 FROM acceptance_checks ac
       WHERE ac.task_id = NEW.id
         AND ac.closed_by_node_id IS NULL
     ) THEN
    RAISE EXCEPTION 'task % cannot close while acceptance checks remain open', NEW.id;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS shujuan_task_closure_guard ON tasks;

CREATE CONSTRAINT TRIGGER shujuan_task_closure_guard
AFTER INSERT OR UPDATE OF closed_by_node_id ON tasks
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION shujuan_validate_task_closure();
