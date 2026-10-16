"""
Comprehensive Graph and DAG Validator Tests
Verifies terminal nodes, reachability, orphan/dead-end detection, cycle detection, and agent configs.
"""

from backend.app.engine.validator import validate_workflow_graph


def test_valid_standard_chain():
    """
    Start -> Planner -> Analyst -> Reviewer -> Output
    """
    definition = {
        "nodes": [
            {"id": "start-1", "type": "start"},
            {"id": "planner-1", "type": "agent", "data": {"role_type": "planner", "model_id": "muse-spark-1.3-contributor-free"}},
            {"id": "analyst-1", "type": "agent", "data": {"role_type": "analyst", "model_id": "muse-spark-1.3-contributor-free"}},
            {"id": "reviewer-1", "type": "agent", "data": {"role_type": "reviewer", "model_id": "muse-spark-1.3-contributor-free"}},
            {"id": "output-1", "type": "output"}
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "planner-1"},
            {"id": "e2", "source": "planner-1", "target": "analyst-1"},
            {"id": "e3", "source": "analyst-1", "target": "reviewer-1"},
            {"id": "e4", "source": "reviewer-1", "target": "output-1"}
        ]
    }
    res = validate_workflow_graph(definition)
    assert res.valid is True
    assert len(res.errors) == 0
    assert res.topological_order == ["start-1", "planner-1", "analyst-1", "reviewer-1", "output-1"]


def test_cycle_detection():
    """
    Rejects cycle: Reviewer -> Analyst -> Reviewer
    """
    definition = {
        "nodes": [
            {"id": "start-1", "type": "start"},
            {"id": "planner-1", "type": "agent", "data": {"role_type": "planner", "model_id": "muse-spark-1.3-contributor-free"}},
            {"id": "analyst-1", "type": "agent", "data": {"role_type": "analyst", "model_id": "muse-spark-1.3-contributor-free"}},
            {"id": "reviewer-1", "type": "agent", "data": {"role_type": "reviewer", "model_id": "muse-spark-1.3-contributor-free"}},
            {"id": "output-1", "type": "output"}
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "planner-1"},
            {"id": "e2", "source": "planner-1", "target": "analyst-1"},
            {"id": "e3", "source": "analyst-1", "target": "reviewer-1"},
            {"id": "e4", "source": "reviewer-1", "target": "output-1"},
            {"id": "e-cycle", "source": "reviewer-1", "target": "analyst-1"}  # Circular loop
        ]
    }
    res = validate_workflow_graph(definition)
    assert res.valid is False
    assert any("Circular dependency" in err or "cycle" in err.lower() for err in res.errors)


def test_self_loop_rejection():
    definition = {
        "nodes": [
            {"id": "start-1", "type": "start"},
            {"id": "planner-1", "type": "agent", "data": {"role_type": "planner", "model_id": "muse-spark-1.3-contributor-free"}},
            {"id": "output-1", "type": "output"}
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "planner-1"},
            {"id": "e-loop", "source": "planner-1", "target": "planner-1"},
            {"id": "e2", "source": "planner-1", "target": "output-1"}
        ]
    }
    res = validate_workflow_graph(definition)
    assert res.valid is False
    assert any("Self-loop" in err for err in res.errors)


def test_missing_start_node():
    definition = {
        "nodes": [
            {"id": "planner-1", "type": "agent", "data": {"role_type": "planner", "model_id": "muse-spark-1.3-contributor-free"}},
            {"id": "output-1", "type": "output"}
        ],
        "edges": [
            {"id": "e1", "source": "planner-1", "target": "output-1"}
        ]
    }
    res = validate_workflow_graph(definition)
    assert res.valid is False
    assert any("exactly one Start node" in err for err in res.errors)


def test_multiple_start_nodes():
    definition = {
        "nodes": [
            {"id": "start-1", "type": "start"},
            {"id": "start-2", "type": "start"},
            {"id": "output-1", "type": "output"}
        ],
        "edges": []
    }
    res = validate_workflow_graph(definition)
    assert res.valid is False
    assert any("exactly one Start node" in err for err in res.errors)


def test_missing_output_node():
    definition = {
        "nodes": [
            {"id": "start-1", "type": "start"},
            {"id": "planner-1", "type": "agent", "data": {"role_type": "planner", "model_id": "muse-spark-1.3-contributor-free"}}
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "planner-1"}
        ]
    }
    res = validate_workflow_graph(definition)
    assert res.valid is False
    assert any("at least one Output node" in err for err in res.errors)


def test_orphan_node_detection():
    """
    Node disconnected from Start
    """
    definition = {
        "nodes": [
            {"id": "start-1", "type": "start"},
            {"id": "planner-1", "type": "agent", "data": {"role_type": "planner", "model_id": "muse-spark-1.3-contributor-free"}},
            {"id": "isolated-node", "type": "agent", "data": {"role_type": "analyst", "model_id": "muse-spark-1.3-contributor-free"}},
            {"id": "output-1", "type": "output"}
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "planner-1"},
            {"id": "e2", "source": "planner-1", "target": "output-1"},
            {"id": "e3", "source": "isolated-node", "target": "output-1"}
        ]
    }
    res = validate_workflow_graph(definition)
    assert res.valid is False
    assert any("Orphan node" in err and "isolated-node" in err for err in res.errors)


def test_dead_end_node_detection():
    """
    Node that cannot reach any Output node
    """
    definition = {
        "nodes": [
            {"id": "start-1", "type": "start"},
            {"id": "planner-1", "type": "agent", "data": {"role_type": "planner", "model_id": "muse-spark-1.3-contributor-free"}},
            {"id": "dead-end", "type": "agent", "data": {"role_type": "analyst", "model_id": "muse-spark-1.3-contributor-free"}},
            {"id": "output-1", "type": "output"}
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "planner-1"},
            {"id": "e2", "source": "planner-1", "target": "output-1"},
            {"id": "e3", "source": "start-1", "target": "dead-end"}  # Reached from start, but doesn't lead to output
        ]
    }
    res = validate_workflow_graph(definition)
    assert res.valid is False
    assert any("Dead-end node" in err and "dead-end" in err for err in res.errors)


def test_agent_node_missing_config():
    definition = {
        "nodes": [
            {"id": "start-1", "type": "start"},
            {"id": "planner-1", "type": "agent", "data": {}},  # Missing role_type & model_id
            {"id": "output-1", "type": "output"}
        ],
        "edges": [
            {"id": "e1", "source": "start-1", "target": "planner-1"},
            {"id": "e2", "source": "planner-1", "target": "output-1"}
        ]
    }
    res = validate_workflow_graph(definition)
    assert res.valid is False
    assert any("missing required 'role_type'" in err for err in res.errors)
    assert any("missing required 'model_id'" in err for err in res.errors)


def test_exceeding_max_nodes():
    nodes = [{"id": f"node-{i}", "type": "agent", "data": {"role_type": "coder", "model_id": "m1"}} for i in range(25)]
    nodes[0] = {"id": "start-1", "type": "start"}
    nodes[-1] = {"id": "output-1", "type": "output"}
    res = validate_workflow_graph({"nodes": nodes, "edges": []}, max_nodes=20)
    assert res.valid is False
    assert any("exceeds maximum allowed nodes" in err for err in res.errors)
