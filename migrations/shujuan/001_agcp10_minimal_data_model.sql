CREATE TABLE IF NOT EXISTS source_promises (
  id TEXT PRIMARY KEY,
  endpoint_id TEXT NOT NULL,
  source_node_id TEXT NOT NULL,
  source_locator TEXT,
  kind TEXT NOT NULL,
  text TEXT NOT NULL,
  hardness TEXT NOT NULL DEFAULT 'hard' CHECK (hardness IN ('hard', 'soft', 'optional')),
  downgrade_policy TEXT NOT NULL DEFAULT 'requires_user_scope_change',
  created_at TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (endpoint_id) REFERENCES endpoints(id),
  FOREIGN KEY (source_node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS hard_predicates (
  id TEXT PRIMARY KEY,
  source_promise_id TEXT NOT NULL,
  claim TEXT NOT NULL,
  proof_required TEXT NOT NULL DEFAULT '[]',
  lifecycle TEXT NOT NULL DEFAULT 'active' CHECK (lifecycle IN ('active', 'resolved', 'deferred', 'product_backlog', 'invalidated', 'superseded')),
  created_at TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (source_promise_id) REFERENCES source_promises(id)
);

CREATE TABLE IF NOT EXISTS forbidden_substitutes (
  id TEXT PRIMARY KEY,
  predicate_id TEXT NOT NULL,
  substitute_text TEXT NOT NULL,
  reason TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (predicate_id) REFERENCES hard_predicates(id)
);

CREATE TABLE IF NOT EXISTS work_chains (
  id TEXT PRIMARY KEY,
  endpoint_id TEXT NOT NULL,
  name TEXT NOT NULL,
  parent_chain_id TEXT,
  mode TEXT,
  lifecycle TEXT NOT NULL DEFAULT 'active' CHECK (lifecycle IN ('active', 'resolved', 'deferred', 'product_backlog', 'invalidated', 'superseded')),
  created_at TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (endpoint_id) REFERENCES endpoints(id),
  FOREIGN KEY (parent_chain_id) REFERENCES work_chains(id)
);

CREATE TABLE IF NOT EXISTS task_predicate_links (
  task_id TEXT NOT NULL,
  check_id TEXT,
  predicate_id TEXT NOT NULL,
  relationship TEXT NOT NULL CHECK (relationship IN ('implements', 'proves', 'guards', 'negative_test')),
  PRIMARY KEY (task_id, check_id, predicate_id, relationship),
  FOREIGN KEY (task_id) REFERENCES tasks(id),
  FOREIGN KEY (check_id) REFERENCES acceptance_checks(id),
  FOREIGN KEY (predicate_id) REFERENCES hard_predicates(id)
);

CREATE TABLE IF NOT EXISTS evidence_predicate_coverage (
  id TEXT PRIMARY KEY,
  evidence_node_id TEXT NOT NULL,
  check_id TEXT NOT NULL,
  predicate_id TEXT NOT NULL,
  assertion TEXT NOT NULL,
  result TEXT NOT NULL CHECK (result IN ('pass', 'fail', 'partial', 'not_covered')),
  reviewer_state TEXT NOT NULL DEFAULT 'unreviewed',
  created_at TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (evidence_node_id) REFERENCES nodes(id),
  FOREIGN KEY (check_id) REFERENCES acceptance_checks(id),
  FOREIGN KEY (predicate_id) REFERENCES hard_predicates(id)
);

CREATE TABLE IF NOT EXISTS review_results (
  id TEXT PRIMARY KEY,
  endpoint_id TEXT NOT NULL,
  work_chain_id TEXT,
  reviewer_agent TEXT,
  reviewer_model TEXT,
  result TEXT NOT NULL CHECK (result IN ('accept', 'reject', 'partial', 'needs_user_decision')),
  summary TEXT NOT NULL,
  artifact_node_id TEXT,
  created_at TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (endpoint_id) REFERENCES endpoints(id),
  FOREIGN KEY (work_chain_id) REFERENCES work_chains(id),
  FOREIGN KEY (artifact_node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS endpoint_inherited_blockers (
  id TEXT PRIMARY KEY,
  child_endpoint_id TEXT NOT NULL,
  source_endpoint_id TEXT NOT NULL,
  finding_node_id TEXT NOT NULL,
  target_kind TEXT NOT NULL,
  target_id TEXT NOT NULL,
  lifecycle TEXT NOT NULL DEFAULT 'active' CHECK (lifecycle IN ('active', 'resolved', 'deferred', 'product_backlog', 'invalidated', 'superseded')),
  created_at TEXT NOT NULL,
  resolved_at TEXT,
  metadata TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (child_endpoint_id) REFERENCES endpoints(id),
  FOREIGN KEY (source_endpoint_id) REFERENCES endpoints(id),
  FOREIGN KEY (finding_node_id) REFERENCES nodes(id)
);

CREATE INDEX IF NOT EXISTS idx_source_promises_endpoint ON source_promises(endpoint_id, hardness);
CREATE INDEX IF NOT EXISTS idx_hard_predicates_promise ON hard_predicates(source_promise_id, lifecycle);
CREATE INDEX IF NOT EXISTS idx_forbidden_substitutes_predicate ON forbidden_substitutes(predicate_id);
CREATE INDEX IF NOT EXISTS idx_work_chains_endpoint ON work_chains(endpoint_id, lifecycle);
CREATE INDEX IF NOT EXISTS idx_task_predicate_links_predicate ON task_predicate_links(predicate_id, relationship);
CREATE INDEX IF NOT EXISTS idx_evidence_predicate_coverage_evidence ON evidence_predicate_coverage(evidence_node_id, check_id);
CREATE INDEX IF NOT EXISTS idx_evidence_predicate_coverage_predicate ON evidence_predicate_coverage(predicate_id, reviewer_state);
CREATE INDEX IF NOT EXISTS idx_review_results_endpoint ON review_results(endpoint_id, result);
CREATE INDEX IF NOT EXISTS idx_endpoint_inherited_blockers_child ON endpoint_inherited_blockers(child_endpoint_id, lifecycle);
CREATE INDEX IF NOT EXISTS idx_endpoint_inherited_blockers_finding ON endpoint_inherited_blockers(finding_node_id, lifecycle);
