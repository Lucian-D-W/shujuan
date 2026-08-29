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
  FOREIGN KEY (work_chain_id) REFERENCES work_chains(id),
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

CREATE INDEX IF NOT EXISTS idx_delegation_lanes_endpoint ON delegation_lanes(endpoint_id, lifecycle);
CREATE INDEX IF NOT EXISTS idx_delegation_lanes_task ON delegation_lanes(task_id, check_id);
CREATE INDEX IF NOT EXISTS idx_delegation_packets_lane ON delegation_packets(lane_id, packet_kind);
CREATE INDEX IF NOT EXISTS idx_worker_ownership_snapshots_lane ON worker_ownership_snapshots(lane_id, snapshot_kind);
