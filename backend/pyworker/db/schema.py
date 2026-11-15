"""GENERATED from the legacy SQLAlchemy models (schema v16). Static DDL for sqlite3."""

SCHEMA_VERSION = 18

# Tables workflows, workflow_executions, node_executions, execution_events and opencode_conversations are retained
# for existing databases and no longer written (the code that used them was removed).
TABLES = {
    'agent_profiles': 'CREATE TABLE agent_profiles (\n\tid VARCHAR(64) NOT NULL, \n\tname VARCHAR(255) NOT NULL, \n\trole_type VARCHAR(64) NOT NULL, \n\tdescription TEXT, \n\tinstructions TEXT NOT NULL, \n\tinput_schema_json TEXT, \n\toutput_schema_json TEXT, \n\tdefault_model_id VARCHAR(128) NOT NULL, \n\tdefault_variant_id VARCHAR(64), \n\ttask_depth VARCHAR(32) NOT NULL, \n\tsettings_json TEXT, \n\tengine VARCHAR(32), \n\tcreated_at DATETIME NOT NULL, \n\tupdated_at DATETIME NOT NULL, \n\tPRIMARY KEY (id)\n)',
    'ceo_messages': 'CREATE TABLE ceo_messages (\n\tid VARCHAR(36) NOT NULL, \n\tseq INTEGER NOT NULL, \n\trole VARCHAR(16) NOT NULL, \n\tcontent TEXT NOT NULL, \n\tfast BOOLEAN NOT NULL, \n\tcreated_at DATETIME NOT NULL, \n\tPRIMARY KEY (id)\n)',
    'missions': 'CREATE TABLE missions (\n\tid VARCHAR(36) NOT NULL, \n\ttitle VARCHAR(255) NOT NULL, \n\tobjective TEXT NOT NULL, \n\tstatus VARCHAR(32) NOT NULL, \n\tmodel_id VARCHAR(128), \n\tvariant VARCHAR(64), \n\tsource_conversation_id VARCHAR(128), \n\tidempotency_key VARCHAR(128), \n\trequest_fingerprint VARCHAR(64), \n\tplan_json TEXT, \n\tfinal_summary TEXT, \n\terror_summary TEXT, \n\tcreated_at DATETIME NOT NULL, \n\tupdated_at DATETIME NOT NULL, \n\tstarted_at DATETIME, \n\tfinished_at DATETIME, \n\tPRIMARY KEY (id)\n)',
    'opencode_conversations': 'CREATE TABLE opencode_conversations (\n\tconversation_id VARCHAR(128) NOT NULL, \n\tsession_id VARCHAR(128) NOT NULL, \n\tmodel_id VARCHAR(128) NOT NULL, \n\tprovider_id VARCHAR(128) NOT NULL, \n\tagent_id VARCHAR(128) NOT NULL, \n\tcreated_at DATETIME NOT NULL, \n\tupdated_at DATETIME NOT NULL, \n\tPRIMARY KEY (conversation_id)\n)',
    'pending_actions': 'CREATE TABLE pending_actions (\n\tid VARCHAR(36) NOT NULL, \n\tkind VARCHAR(64) NOT NULL, \n\tpayload_json TEXT NOT NULL, \n\tsummary TEXT NOT NULL, \n\tstatus VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(128), \n\tcreated_at DATETIME NOT NULL, \n\tdecided_at DATETIME, \n\tdecision_note TEXT, \n\tPRIMARY KEY (id)\n)',
    'skill_sources': 'CREATE TABLE skill_sources (\n\tid VARCHAR(36) NOT NULL, \n\tname VARCHAR(255) NOT NULL, \n\tpath TEXT NOT NULL, \n\tcreated_at DATETIME NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (path)\n)',
    'usage_events': 'CREATE TABLE usage_events (\n\tid VARCHAR(36) NOT NULL, \n\trun_id VARCHAR(36), \n\tcreated_at DATETIME NOT NULL, \n\tkind VARCHAR(64) NOT NULL, \n\tagent_id VARCHAR(128) NOT NULL, \n\tmodel_id VARCHAR(128) NOT NULL, \n\tprovider_id VARCHAR(128) NOT NULL, \n\tvariant_id VARCHAR(64), \n\tsession_id VARCHAR(128), \n\tduration_ms INTEGER NOT NULL, \n\ttokens_in INTEGER, \n\ttokens_out INTEGER, \n\ttokens_cache_read INTEGER, \n\tsuccess BOOLEAN NOT NULL, \n\terror_kind VARCHAR(64), \n\terror_detail TEXT, \n\tPRIMARY KEY (id)\n)',
    'workflows': 'CREATE TABLE workflows (\n\tid VARCHAR(36) NOT NULL, \n\tname VARCHAR(255) NOT NULL, \n\tdescription TEXT, \n\tdefinition_json TEXT NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at DATETIME NOT NULL, \n\tupdated_at DATETIME NOT NULL, \n\tPRIMARY KEY (id)\n)',
    'workspaces': 'CREATE TABLE workspaces (\n\tid VARCHAR(36) NOT NULL, \n\tname VARCHAR(255) NOT NULL, \n\tpath TEXT NOT NULL, \n\tcreated_at DATETIME NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (path)\n)',
    'artifacts': 'CREATE TABLE artifacts (\n\tid VARCHAR(36) NOT NULL, \n\tworkspace_id VARCHAR(36) NOT NULL, \n\trun_id VARCHAR(36), \n\trelative_path TEXT NOT NULL, \n\tmime VARCHAR(128) NOT NULL, \n\tsize INTEGER NOT NULL, \n\tcreated_at DATETIME NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (workspace_id, relative_path), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE\n)',
    'conversations': 'CREATE TABLE conversations (\n\tid VARCHAR(36) NOT NULL, \n\tworkspace_id VARCHAR(36), \n\ttitle VARCHAR(255) NOT NULL, \n\tcreated_at DATETIME NOT NULL, \n\tupdated_at DATETIME NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE SET NULL\n)',
    'mission_tasks': 'CREATE TABLE mission_tasks (\n\tid VARCHAR(36) NOT NULL, \n\tmission_id VARCHAR(36) NOT NULL, \n\ttask_key VARCHAR(64) NOT NULL, \n\temployee_role VARCHAR(64) NOT NULL, \n\tagent_profile_id VARCHAR(64), \n\ttitle VARCHAR(255) NOT NULL, \n\tinstructions TEXT NOT NULL, \n\tinput_context TEXT, \n\texpected_output TEXT, \n\tdepends_on_json TEXT, \n\tstatus VARCHAR(32) NOT NULL, \n\tmodel_id VARCHAR(128), \n\tvariant VARCHAR(64), \n\tattempt_count INTEGER NOT NULL, \n\toutput_json TEXT, \n\terror_message TEXT, \n\tsession_id VARCHAR(128), \n\tduration_ms INTEGER, \n\tstarted_at DATETIME, \n\tfinished_at DATETIME, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(mission_id) REFERENCES missions (id) ON DELETE CASCADE\n)',
    'workflow_executions': 'CREATE TABLE workflow_executions (\n\tid VARCHAR(36) NOT NULL, \n\tworkflow_id VARCHAR(36) NOT NULL, \n\tstatus VARCHAR(32) NOT NULL, \n\tinput_json TEXT, \n\tfinal_output_json TEXT, \n\tworkflow_snapshot_json TEXT, \n\truntime_status VARCHAR(64), \n\tstarted_at DATETIME, \n\tfinished_at DATETIME, \n\terror_summary TEXT, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(workflow_id) REFERENCES workflows (id) ON DELETE CASCADE\n)',
    'conversation_messages': 'CREATE TABLE conversation_messages (\n\tseq INTEGER NOT NULL, \n\tconversation_id VARCHAR(36) NOT NULL, \n\tclient_message_id VARCHAR(128), \n\trole VARCHAR(16) NOT NULL, \n\tcontent TEXT NOT NULL, \n\tcreated_at DATETIME NOT NULL, \n\tPRIMARY KEY (seq), \n\tUNIQUE (conversation_id, client_message_id), \n\tFOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE\n)',
    'conversation_runs': "CREATE TABLE conversation_runs (\n\tid VARCHAR(36) NOT NULL, \n\tconversation_id VARCHAR(36), \n\tkind VARCHAR(32) DEFAULT 'conversation' NOT NULL, \n\tmission_id VARCHAR(36), \n\tmission_task_id VARCHAR(36), \n\tattempt INTEGER, \n\tresult_text TEXT, \n\tengine VARCHAR(32) NOT NULL, \n\tmodel_id VARCHAR(128) NOT NULL, \n\tvariant VARCHAR(64), \n\trequest_key VARCHAR(128), \n\tskills_json TEXT, \n\tinput_files_json TEXT, \n\ttimeout INTEGER NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\tuser_message_seq INTEGER, \n\tassistant_message_seq INTEGER, \n\terror TEXT, \n\tcreated_at DATETIME NOT NULL, \n\tstarted_at DATETIME, \n\tfinished_at DATETIME, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE\n)",
    'node_executions': 'CREATE TABLE node_executions (\n\tid VARCHAR(36) NOT NULL, \n\texecution_id VARCHAR(36) NOT NULL, \n\tnode_id VARCHAR(64) NOT NULL, \n\trole_type VARCHAR(64), \n\tmodel_id VARCHAR(128), \n\tnative_variant_id VARCHAR(64), \n\ttask_depth VARCHAR(32) NOT NULL, \n\tstatus VARCHAR(32) NOT NULL, \n\tattempt_count INTEGER NOT NULL, \n\tinput_json TEXT, \n\toutput_json TEXT, \n\terror_message TEXT, \n\tstarted_at DATETIME, \n\tfinished_at DATETIME, \n\tduration_ms INTEGER, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(execution_id) REFERENCES workflow_executions (id) ON DELETE CASCADE\n)',
    'approvals': 'CREATE TABLE approvals (\n\tid VARCHAR(36) NOT NULL, \n\trun_id VARCHAR(36) NOT NULL, \n\taction_summary TEXT NOT NULL, \n\tscope VARCHAR(255) NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\texpires_at DATETIME NOT NULL, \n\tcreated_at DATETIME NOT NULL, \n\tdecided_at DATETIME, \n\tdecision_note TEXT, \n\texpired_reason VARCHAR(32), \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(run_id) REFERENCES conversation_runs (id) ON DELETE CASCADE\n)',
    'conversation_run_events': 'CREATE TABLE conversation_run_events (\n\tid INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, \n\trun_id VARCHAR(36) NOT NULL, \n\ttype VARCHAR(16) NOT NULL, \n\tdata_json TEXT NOT NULL, \n\tcreated_at DATETIME NOT NULL, \n\tFOREIGN KEY(run_id) REFERENCES conversation_runs (id) ON DELETE CASCADE\n)',
    'deliverable_drafts': 'CREATE TABLE deliverable_drafts (\n\tid VARCHAR(36) NOT NULL, \n\trun_id VARCHAR(36) NOT NULL, \n\tfilename VARCHAR(128) NOT NULL, \n\tmime VARCHAR(64) NOT NULL, \n\tcontent TEXT NOT NULL, \n\tcontent_sha256 VARCHAR(64) NOT NULL, \n\tsize_bytes INTEGER NOT NULL, \n\tprovenance_json TEXT NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\tcreated_at DATETIME NOT NULL, \n\tdecided_at DATETIME, \n\tdecision_note TEXT, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_deliverable_run_content_name UNIQUE (run_id, content_sha256, filename), \n\tFOREIGN KEY(run_id) REFERENCES conversation_runs (id) ON DELETE CASCADE\n)',
    'execution_events': 'CREATE TABLE execution_events (\n\tid VARCHAR(36) NOT NULL, \n\texecution_id VARCHAR(36) NOT NULL, \n\tnode_execution_id VARCHAR(36), \n\tevent_type VARCHAR(64) NOT NULL, \n\tevent_json TEXT NOT NULL, \n\tcreated_at DATETIME NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(execution_id) REFERENCES workflow_executions (id) ON DELETE CASCADE, \n\tFOREIGN KEY(node_execution_id) REFERENCES node_executions (id) ON DELETE CASCADE\n)',
}

TABLES["workspace_writes"] = """CREATE TABLE workspace_writes (
    id TEXT PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL REFERENCES conversation_runs(id),
    root TEXT NOT NULL,
    path TEXT NOT NULL,
    content TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    approval_id TEXT,
    temporary_path TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
)"""

# Shared by fresh DDL and the recognised additive migration path.
DRIVER_COLUMNS = {
    "conversation_runs": {
        "runtime_key": "TEXT NOT NULL DEFAULT 'default'",
        "driver_protocol": "TEXT",
        "native_session_id": "TEXT",
        "native_turn_id": "TEXT",
        "session_generation": "INTEGER",
    },
    "conversation_run_events": {"driver_event_id": "TEXT"},
    "approvals": {
        "origin": "TEXT NOT NULL DEFAULT 'workspace'",
        "runtime_request_id": "TEXT",
        "request_type": "TEXT",
        "request_json": "TEXT",
        "response_json": "TEXT",
        "delivery_status": "TEXT",
    },
}
for _table, _columns in DRIVER_COLUMNS.items():
    # Insert before existing constraints (the event PK is declared inline).
    _marker = "\n\tFOREIGN KEY" if _table == "conversation_run_events" else "\n\tPRIMARY KEY"
    TABLES[_table] = TABLES[_table].replace(
        _marker, "".join(f"\n\t{name} {decl}, " for name, decl in _columns.items()) + _marker, 1)

TABLES["runtime_sessions"] = """CREATE TABLE runtime_sessions (
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('conversation','ceo','mission_attempt','workflow')),
    scope_id TEXT NOT NULL,
    engine TEXT NOT NULL,
    runtime_key TEXT NOT NULL,
    protocol TEXT NOT NULL,
    namespace TEXT NOT NULL,
    native_session_id TEXT NOT NULL,
    generation INTEGER NOT NULL DEFAULT 1,
    state TEXT NOT NULL DEFAULT 'unverified' CHECK (state IN ('unverified','ready','invalid')),
    model_id TEXT NOT NULL,
    provider_id TEXT,
    variant TEXT,
    agent_id TEXT,
    cwd TEXT,
    config_fingerprint TEXT,
    last_turn_id TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (scope_kind, scope_id, engine, runtime_key)
)"""

# Indexes per table (created with the table, like SQLAlchemy create_all).
INDEXES = {
    'agent_profiles': [
        'CREATE INDEX ix_agent_profiles_role_type ON agent_profiles (role_type)',
    ],
    'ceo_messages': [
        'CREATE INDEX ix_ceo_messages_created_at ON ceo_messages (created_at)',
        'CREATE INDEX ix_ceo_messages_seq ON ceo_messages (seq)',
    ],
    'missions': [
        'CREATE UNIQUE INDEX ix_missions_idempotency_key ON missions (idempotency_key)',
        'CREATE INDEX ix_missions_source_conversation_id ON missions (source_conversation_id)',
        'CREATE INDEX ix_missions_status ON missions (status)',
    ],
    'opencode_conversations': [
        'CREATE UNIQUE INDEX ix_opencode_conversations_session_id ON opencode_conversations (session_id)',
    ],
    'pending_actions': [
        'CREATE INDEX ix_pending_actions_kind ON pending_actions (kind)',
        'CREATE INDEX ix_pending_actions_status ON pending_actions (status)',
    ],
    'usage_events': [
        'CREATE INDEX ix_usage_events_created_at ON usage_events (created_at)',
        'CREATE INDEX ix_usage_events_kind ON usage_events (kind)',
        'CREATE INDEX ix_usage_events_model_id ON usage_events (model_id)',
        'CREATE INDEX ix_usage_events_run_id ON usage_events (run_id)',
    ],
    'workflows': [
        'CREATE INDEX ix_workflows_name ON workflows (name)',
    ],
    'artifacts': [
        'CREATE INDEX ix_artifacts_run_id ON artifacts (run_id)',
        'CREATE INDEX ix_artifacts_workspace_id ON artifacts (workspace_id)',
    ],
    'conversations': [
        'CREATE INDEX ix_conversations_workspace_id ON conversations (workspace_id)',
    ],
    'mission_tasks': [
        'CREATE INDEX ix_mission_tasks_employee_role ON mission_tasks (employee_role)',
        'CREATE INDEX ix_mission_tasks_mission_id ON mission_tasks (mission_id)',
        'CREATE INDEX ix_mission_tasks_status ON mission_tasks (status)',
        'CREATE INDEX ix_mission_tasks_task_key ON mission_tasks (task_key)',
    ],
    'workflow_executions': [
        'CREATE INDEX ix_workflow_executions_status ON workflow_executions (status)',
        'CREATE INDEX ix_workflow_executions_workflow_id ON workflow_executions (workflow_id)',
    ],
    'conversation_messages': [
        'CREATE INDEX ix_conversation_messages_conversation_id ON conversation_messages (conversation_id)',
    ],
    'conversation_runs': [
        'CREATE INDEX ix_conversation_runs_conversation_id ON conversation_runs (conversation_id)',
        'CREATE INDEX ix_conversation_runs_mission_id ON conversation_runs (mission_id)',
        'CREATE INDEX ix_conversation_runs_mission_task_id ON conversation_runs (mission_task_id)',
        'CREATE INDEX ix_conversation_runs_status ON conversation_runs (status)',
        "CREATE UNIQUE INDEX uq_conversation_runs_one_active ON conversation_runs (conversation_id) WHERE status IN ('queued', 'running')",
        'CREATE UNIQUE INDEX uq_runs_request_key ON conversation_runs (conversation_id, request_key)',
    ],
    'node_executions': [
        'CREATE INDEX ix_node_executions_execution_id ON node_executions (execution_id)',
        'CREATE INDEX ix_node_executions_node_id ON node_executions (node_id)',
        'CREATE INDEX ix_node_executions_status ON node_executions (status)',
    ],
    'approvals': [
        'CREATE INDEX ix_approvals_run_id_status ON approvals (run_id, status)',
    ],
    'conversation_run_events': [
        'CREATE INDEX ix_conversation_run_events_run_id_id ON conversation_run_events (run_id, id)',
    ],
    'deliverable_drafts': [
        'CREATE INDEX ix_deliverable_drafts_run_id_status ON deliverable_drafts (run_id, status)',
    ],
    'execution_events': [
        'CREATE INDEX ix_execution_events_event_type ON execution_events (event_type)',
        'CREATE INDEX ix_execution_events_execution_id ON execution_events (execution_id)',
        'CREATE INDEX ix_execution_events_node_execution_id ON execution_events (node_execution_id)',
    ],
}

INDEXES["runtime_sessions"] = [
    "CREATE UNIQUE INDEX uq_runtime_sessions_native ON runtime_sessions (engine, runtime_key, namespace, protocol, native_session_id)",
]
INDEXES["conversation_run_events"].append(
    "CREATE UNIQUE INDEX uq_run_driver_event ON conversation_run_events (run_id, driver_event_id) WHERE driver_event_id IS NOT NULL")
INDEXES["approvals"].append(
    "CREATE UNIQUE INDEX uq_approval_runtime_request ON approvals (run_id, runtime_request_id) WHERE runtime_request_id IS NOT NULL")
