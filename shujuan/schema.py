SCHEMA_VERSION = "0.4.0"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS project_meta (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  repo_root TEXT NOT NULL,
  default_branch TEXT,
  schema_version TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  label TEXT,
  summary TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  valid_from TEXT,
  valid_to TEXT,
  superseded_by_node_id TEXT,
  embedding TEXT,
  search_tsv TEXT,
  props TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (superseded_by_node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS center_bodies (
  id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL UNIQUE,
  body TEXT NOT NULL,
  version INTEGER NOT NULL,
  is_current INTEGER NOT NULL DEFAULT 1,
  created_from_node_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (node_id) REFERENCES nodes(id),
  FOREIGN KEY (created_from_node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS endpoints (
  id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL UNIQUE,
  description TEXT,
  root_node_id TEXT,
  current_body_id TEXT,
  created_at TEXT NOT NULL,
  archived_at TEXT,
  FOREIGN KEY (node_id) REFERENCES nodes(id),
  FOREIGN KEY (root_node_id) REFERENCES nodes(id),
  FOREIGN KEY (current_body_id) REFERENCES endpoint_bodies(id)
);

CREATE TABLE IF NOT EXISTS endpoint_bodies (
  id TEXT PRIMARY KEY,
  endpoint_id TEXT NOT NULL,
  node_id TEXT NOT NULL UNIQUE,
  body TEXT NOT NULL,
  created_from_node_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (endpoint_id) REFERENCES endpoints(id),
  FOREIGN KEY (node_id) REFERENCES nodes(id),
  FOREIGN KEY (created_from_node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS conversation_sessions (
  id TEXT PRIMARY KEY,
  agent_name TEXT,
  model_name TEXT,
  source TEXT,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  node_id TEXT NOT NULL UNIQUE,
  actor TEXT NOT NULL,
  content TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  turn_index INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (session_id) REFERENCES conversation_sessions(id),
  FOREIGN KEY (node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS source_documents (
  id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  source_type TEXT NOT NULL,
  origin TEXT,
  body TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  imported_at TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS document_sections (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  node_id TEXT NOT NULL UNIQUE,
  section_index INTEGER NOT NULL,
  heading TEXT,
  body TEXT NOT NULL,
  start_offset INTEGER NOT NULL,
  end_offset INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  FOREIGN KEY (document_id) REFERENCES source_documents(id),
  FOREIGN KEY (node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS edges (
  id TEXT PRIMARY KEY,
  from_node_id TEXT NOT NULL,
  type TEXT NOT NULL,
  to_node_id TEXT NOT NULL,
  reason TEXT,
  confidence REAL,
  evidence_node_id TEXT,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  props TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (from_node_id) REFERENCES nodes(id),
  FOREIGN KEY (to_node_id) REFERENCES nodes(id),
  FOREIGN KEY (evidence_node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS agent_runs (
  id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL UNIQUE,
  session_id TEXT,
  agent_name TEXT,
  model_name TEXT,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  base_commit TEXT,
  end_head_commit TEXT,
  final_report TEXT,
  metadata TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (node_id) REFERENCES nodes(id),
  FOREIGN KEY (session_id) REFERENCES conversation_sessions(id)
);

CREATE TABLE IF NOT EXISTS run_snapshots (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  phase TEXT NOT NULL CHECK (phase IN ('before', 'after')),
  head_commit TEXT,
  worktree_patch_hash TEXT NOT NULL,
  staged_patch_hash TEXT NOT NULL,
  patch_ref TEXT,
  captured_at TEXT NOT NULL,
  FOREIGN KEY (run_id) REFERENCES agent_runs(id)
);

CREATE TABLE IF NOT EXISTS change_sets (
  id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL UNIQUE,
  run_id TEXT NOT NULL,
  base_snapshot_id TEXT,
  after_snapshot_id TEXT,
  patch_hash TEXT NOT NULL,
  summary TEXT,
  created_at TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (node_id) REFERENCES nodes(id),
  FOREIGN KEY (run_id) REFERENCES agent_runs(id),
  FOREIGN KEY (base_snapshot_id) REFERENCES run_snapshots(id),
  FOREIGN KEY (after_snapshot_id) REFERENCES run_snapshots(id)
);

CREATE TABLE IF NOT EXISTS diff_files (
  id TEXT PRIMARY KEY,
  change_set_id TEXT NOT NULL,
  path_old TEXT,
  path_new TEXT,
  change_type TEXT NOT NULL,
  additions INTEGER NOT NULL DEFAULT 0,
  deletions INTEGER NOT NULL DEFAULT 0,
  file_hash_before TEXT,
  file_hash_after TEXT,
  FOREIGN KEY (change_set_id) REFERENCES change_sets(id)
);

CREATE TABLE IF NOT EXISTS diff_hunks (
  id TEXT PRIMARY KEY,
  diff_file_id TEXT NOT NULL,
  node_id TEXT NOT NULL UNIQUE,
  old_start INTEGER,
  old_lines INTEGER,
  new_start INTEGER,
  new_lines INTEGER,
  hunk_header TEXT NOT NULL,
  old_text TEXT NOT NULL,
  new_text TEXT NOT NULL,
  context_text TEXT NOT NULL,
  hunk_hash TEXT NOT NULL,
  summary TEXT,
  FOREIGN KEY (diff_file_id) REFERENCES diff_files(id),
  FOREIGN KEY (node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS code_objects (
  id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL UNIQUE,
  type TEXT NOT NULL,
  path TEXT NOT NULL,
  symbol_name TEXT,
  qualified_name TEXT,
  language TEXT,
  start_line INTEGER,
  end_line INTEGER,
  content_hash TEXT,
  last_seen_commit TEXT,
  archived_at TEXT,
  props TEXT NOT NULL DEFAULT '{}',
  UNIQUE (type, path, qualified_name),
  FOREIGN KEY (node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS change_code_links (
  id TEXT PRIMARY KEY,
  change_set_id TEXT NOT NULL,
  code_object_id TEXT NOT NULL,
  relation_type TEXT NOT NULL,
  confidence REAL,
  evidence_hunk_id TEXT,
  FOREIGN KEY (change_set_id) REFERENCES change_sets(id),
  FOREIGN KEY (code_object_id) REFERENCES code_objects(id),
  FOREIGN KEY (evidence_hunk_id) REFERENCES diff_hunks(id)
);

CREATE TABLE IF NOT EXISTS terms (
  id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL UNIQUE,
  canonical_term TEXT NOT NULL,
  definition TEXT NOT NULL,
  avoid_aliases TEXT NOT NULL DEFAULT '[]',
  ambiguity_notes TEXT,
  scope_node_id TEXT,
  created_from_node_id TEXT,
  valid_from TEXT NOT NULL,
  valid_to TEXT,
  FOREIGN KEY (node_id) REFERENCES nodes(id),
  FOREIGN KEY (scope_node_id) REFERENCES nodes(id),
  FOREIGN KEY (created_from_node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS scope_contracts (
  id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL UNIQUE,
  source_node_id TEXT,
  body TEXT NOT NULL,
  non_downgrade_rules TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (node_id) REFERENCES nodes(id),
  FOREIGN KEY (source_node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL UNIQUE,
  contract_id TEXT,
  parent_task_id TEXT,
  task_body TEXT NOT NULL,
  is_mandatory INTEGER NOT NULL DEFAULT 1,
  created_from_node_id TEXT,
  closed_by_node_id TEXT,
  closed_at TEXT,
  FOREIGN KEY (node_id) REFERENCES nodes(id),
  FOREIGN KEY (contract_id) REFERENCES scope_contracts(id),
  FOREIGN KEY (parent_task_id) REFERENCES tasks(id),
  FOREIGN KEY (created_from_node_id) REFERENCES nodes(id),
  FOREIGN KEY (closed_by_node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS acceptance_checks (
  id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL UNIQUE,
  task_id TEXT NOT NULL,
  check_body TEXT NOT NULL,
  expected_evidence_type TEXT,
  closed_by_node_id TEXT,
  closed_at TEXT,
  FOREIGN KEY (node_id) REFERENCES nodes(id),
  FOREIGN KEY (task_id) REFERENCES tasks(id),
  FOREIGN KEY (closed_by_node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS activation_logs (
  id TEXT PRIMARY KEY,
  task_text TEXT NOT NULL,
  loaded_center_body_id TEXT,
  loaded_endpoint_body_ids TEXT NOT NULL DEFAULT '[]',
  loaded_term_node_ids TEXT NOT NULL DEFAULT '[]',
  loaded_node_ids TEXT NOT NULL DEFAULT '[]',
  reason TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (loaded_center_body_id) REFERENCES center_bodies(id)
);

CREATE TABLE IF NOT EXISTS applied_migrations (
  id TEXT PRIMARY KEY,
  filename TEXT NOT NULL UNIQUE,
  checksum TEXT NOT NULL,
  applied_at TEXT NOT NULL
);

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

CREATE TABLE IF NOT EXISTS delegation_lanes (
  id TEXT PRIMARY KEY,
  endpoint_id TEXT NOT NULL,
  work_chain_id TEXT,
  task_id TEXT,
  check_id TEXT,
  lane_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('controller', 'worker', 'reviewer', 'researcher', 'writer', 'provider')),
  lifecycle TEXT NOT NULL DEFAULT 'planned' CHECK (lifecycle IN ('planned', 'active', 'returned', 'verified', 'cancelled')),
  controller_agent TEXT,
  delegated_agent TEXT,
  created_from_node_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (endpoint_id) REFERENCES endpoints(id),
  FOREIGN KEY (task_id) REFERENCES tasks(id),
  FOREIGN KEY (check_id) REFERENCES acceptance_checks(id),
  FOREIGN KEY (created_from_node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS delegation_packets (
  id TEXT PRIMARY KEY,
  lane_id TEXT NOT NULL,
  endpoint_id TEXT NOT NULL,
  packet_kind TEXT NOT NULL CHECK (packet_kind IN ('delegation', 'return', 'review', 'research', 'writer')),
  role TEXT NOT NULL CHECK (role IN ('controller', 'worker', 'reviewer', 'researcher', 'writer', 'provider')),
  task_id TEXT,
  check_id TEXT,
  body TEXT NOT NULL,
  authority_boundary TEXT NOT NULL,
  forbidden_actions TEXT NOT NULL DEFAULT '[]',
  expected_return_fields TEXT NOT NULL DEFAULT '[]',
  artifact_node_id TEXT,
  created_by_agent TEXT,
  created_at TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (lane_id) REFERENCES delegation_lanes(id),
  FOREIGN KEY (endpoint_id) REFERENCES endpoints(id),
  FOREIGN KEY (task_id) REFERENCES tasks(id),
  FOREIGN KEY (check_id) REFERENCES acceptance_checks(id),
  FOREIGN KEY (artifact_node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS worker_ownership_snapshots (
  id TEXT PRIMARY KEY,
  lane_id TEXT NOT NULL,
  packet_id TEXT,
  worker_agent TEXT,
  snapshot_kind TEXT NOT NULL CHECK (snapshot_kind IN ('baseline', 'return', 'verification')),
  base_commit TEXT,
  head_commit TEXT,
  pre_existing_dirty_paths TEXT NOT NULL DEFAULT '[]',
  worker_touched_paths TEXT NOT NULL DEFAULT '[]',
  artifact_node_id TEXT,
  created_at TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (lane_id) REFERENCES delegation_lanes(id),
  FOREIGN KEY (packet_id) REFERENCES delegation_packets(id),
  FOREIGN KEY (artifact_node_id) REFERENCES nodes(id)
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

CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_node_id, type);
CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_node_id, type);
CREATE INDEX IF NOT EXISTS idx_document_sections_document ON document_sections(document_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_started ON agent_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_diff_files_change_set ON diff_files(change_set_id);
CREATE INDEX IF NOT EXISTS idx_code_objects_path ON code_objects(path);
CREATE INDEX IF NOT EXISTS idx_terms_term ON terms(canonical_term);
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
CREATE INDEX IF NOT EXISTS idx_delegation_lanes_endpoint ON delegation_lanes(endpoint_id, lifecycle);
CREATE INDEX IF NOT EXISTS idx_delegation_lanes_task ON delegation_lanes(task_id, check_id);
CREATE INDEX IF NOT EXISTS idx_delegation_packets_lane ON delegation_packets(lane_id, packet_kind);
CREATE INDEX IF NOT EXISTS idx_worker_ownership_snapshots_lane ON worker_ownership_snapshots(lane_id, snapshot_kind);
CREATE INDEX IF NOT EXISTS idx_semantic_items_state ON semantic_items(current_state, item_type);
CREATE INDEX IF NOT EXISTS idx_semantic_lifecycle_item ON semantic_lifecycle_events(semantic_item_id, created_at);
"""
