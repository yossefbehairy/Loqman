"""
Strict Graph and DAG Validator for AgentOS Workflows
Validates terminal nodes, integrity, reachability, configuration, and enforces DAG order.
"""

from typing import Dict, List, Any, Optional, Set
from collections import deque
from pydantic import BaseModel, Field


class ValidationResult(BaseModel):
    valid: bool
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    topological_order: List[str] = Field(default_factory=list)


def normalize_node_type(raw_type: Optional[str]) -> str:
    """Normalizes various casing and naming variations of node types."""
    if not raw_type:
        return ""
    t = raw_type.lower().replace("-", "_").replace("node", "").strip()
    if t in ("start",):
        return "start"
    if t in ("agent", "ai_agent", "aiagent", "ai"):
        return "agent"
    if t in ("output",):
        return "output"
    return t


def validate_workflow_graph(definition: Dict[str, Any], max_nodes: int = 20) -> ValidationResult:
    """
    Rigorously validates a workflow definition against all AgentOS engineering rules:
    1. Node bounds (<= max_nodes).
    2. Exactly 1 Start node.
    3. At least 1 Output node.
    4. Unique node and edge IDs.
    5. Valid edges (no dangling endpoints, no self-loops).
    6. Full forward reachability from Start (no orphan nodes).
    7. Full backward reachability to Output (no dead-ends).
    8. Strict DAG cycle detection via Kahn's algorithm, producing topological order.
    9. AI Agent node configuration checks (role_type, model_id, timeouts, retries).
    """
    errors: List[str] = []
    warnings: List[str] = []
    topological_order: List[str] = []

    nodes: List[Dict[str, Any]] = definition.get("nodes", [])
    edges: List[Dict[str, Any]] = definition.get("edges", [])

    if not isinstance(nodes, list):
        return ValidationResult(valid=False, errors=["'nodes' must be a list"])
    if not isinstance(edges, list):
        return ValidationResult(valid=False, errors=["'edges' must be a list"])

    # 1. Node count safety cap
    if len(nodes) == 0:
        return ValidationResult(valid=False, errors=["Workflow contains no nodes"])
    if len(nodes) > max_nodes:
        errors.append(f"Workflow exceeds maximum allowed nodes ({len(nodes)} > {max_nodes})")

    # 2. Node IDs uniqueness & Terminal node classification
    node_ids: Set[str] = set()
    start_node_ids: List[str] = []
    output_node_ids: List[str] = []
    node_map: Dict[str, Dict[str, Any]] = {}

    for idx, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(f"Node at index {idx} is not a valid object")
            continue

        nid = str(node.get("id", "")).strip()
        if not nid:
            errors.append(f"Node at index {idx} is missing required 'id'")
            continue

        if nid in node_ids:
            errors.append(f"Duplicate node ID detected: '{nid}'")
        node_ids.add(nid)
        node_map[nid] = node

        ntype = normalize_node_type(node.get("type"))
        if ntype == "start":
            start_node_ids.append(nid)
        elif ntype == "output":
            output_node_ids.append(nid)
        elif ntype == "agent":
            # 9. Configuration Check for AI Agent Nodes
            data = node.get("data", {})
            role = data.get("role_type") or data.get("role")
            model = data.get("model_id") or data.get("model")
            timeout = data.get("timeout")
            max_retries = data.get("max_retries")

            if not role:
                errors.append(f"Agent node '{nid}' is missing required 'role_type'")
            if not model:
                errors.append(f"Agent node '{nid}' is missing required 'model_id'")
            if timeout is not None:
                try:
                    t_val = int(timeout)
                    if t_val <= 0 or t_val > 600:
                        errors.append(f"Agent node '{nid}' has invalid timeout ({t_val}s; must be 1-600s)")
                except (ValueError, TypeError):
                    errors.append(f"Agent node '{nid}' has non-integer timeout")
            if max_retries is not None:
                try:
                    r_val = int(max_retries)
                    if r_val < 0 or r_val > 5:
                        errors.append(f"Agent node '{nid}' has invalid max_retries ({r_val}; must be 0-5)")
                except (ValueError, TypeError):
                    errors.append(f"Agent node '{nid}' has non-integer max_retries")
        else:
            errors.append(f"Node '{nid}' has unsupported type '{node.get('type')}'. Supported types: Start, Agent, Output")

    # Terminal node checks
    if len(start_node_ids) == 0:
        errors.append("Workflow must contain exactly one Start node (found 0)")
    elif len(start_node_ids) > 1:
        errors.append(f"Workflow must contain exactly one Start node (found {len(start_node_ids)}: {start_node_ids})")

    if len(output_node_ids) == 0:
        errors.append("Workflow must contain at least one Output node (found 0)")

    # 4. Edge IDs and endpoints validation
    edge_ids: Set[str] = set()
    adj_list: Dict[str, List[str]] = {nid: [] for nid in node_ids}
    rev_adj_list: Dict[str, List[str]] = {nid: [] for nid in node_ids}
    in_degree: Dict[str, int] = {nid: 0 for nid in node_ids}

    for idx, edge in enumerate(edges):
        if not isinstance(edge, dict):
            errors.append(f"Edge at index {idx} is not a valid object")
            continue

        eid = str(edge.get("id", f"edge-{idx}")).strip()
        if eid in edge_ids:
            errors.append(f"Duplicate edge ID detected: '{eid}'")
        edge_ids.add(eid)

        source = str(edge.get("source", "")).strip()
        target = str(edge.get("target", "")).strip()

        if not source or not target:
            errors.append(f"Edge '{eid}' is missing 'source' or 'target'")
            continue

        if source not in node_ids:
            errors.append(f"Edge '{eid}' references non-existent source node '{source}'")
            continue
        if target not in node_ids:
            errors.append(f"Edge '{eid}' references non-existent target node '{target}'")
            continue

        # Self-loops check
        if source == target:
            errors.append(f"Self-loop detected on node '{source}' via edge '{eid}'")
            continue

        # Terminal flow constraints: Start has no inputs, Output has no outputs
        if target in start_node_ids:
            errors.append(f"Start node '{target}' cannot have incoming edges (from '{source}')")
        if source in output_node_ids:
            errors.append(f"Output node '{source}' cannot have outgoing edges (to '{target}')")

        adj_list[source].append(target)
        rev_adj_list[target].append(source)
        in_degree[target] += 1

    # Stop early if structural endpoint errors exist before graph traversals
    if errors:
        return ValidationResult(valid=False, errors=errors, warnings=warnings)

    start_node_id = start_node_ids[0]

    # 6. Reachability from Start (Forward BFS)
    reachable_from_start: Set[str] = set()
    q = deque([start_node_id])
    while q:
        curr = q.popleft()
        if curr not in reachable_from_start:
            reachable_from_start.add(curr)
            for neighbor in adj_list.get(curr, []):
                if neighbor not in reachable_from_start:
                    q.append(neighbor)

    unreachable_nodes = node_ids - reachable_from_start
    if unreachable_nodes:
        errors.append(f"Orphan node(s) detected (unreachable from Start): {sorted(list(unreachable_nodes))}")

    # 7. Reachability to Output (Backward BFS)
    reaches_output: Set[str] = set()
    rev_q = deque(output_node_ids)
    while rev_q:
        curr = rev_q.popleft()
        if curr not in reaches_output:
            reaches_output.add(curr)
            for pred in rev_adj_list.get(curr, []):
                if pred not in reaches_output:
                    rev_q.append(pred)

    dead_end_nodes = node_ids - reaches_output
    if dead_end_nodes:
        errors.append(f"Dead-end node(s) detected (no directed path to Output): {sorted(list(dead_end_nodes))}")

    # 8. Strict DAG Cycle Detection & Topological Sort (Kahn's Algorithm)
    cur_in_degree = in_degree.copy()
    zero_in_degree_q = deque([nid for nid in node_ids if cur_in_degree[nid] == 0])
    topo_sorted = []

    while zero_in_degree_q:
        curr = zero_in_degree_q.popleft()
        topo_sorted.append(curr)

        for neighbor in adj_list.get(curr, []):
            cur_in_degree[neighbor] -= 1
            if cur_in_degree[neighbor] == 0:
                zero_in_degree_q.append(neighbor)

    if len(topo_sorted) != len(node_ids):
        # Cycle detected!
        unresolved = [nid for nid in node_ids if cur_in_degree[nid] > 0]
        errors.append(f"Circular dependency (cycle) detected among nodes: {sorted(unresolved)}. Workflows must be strict DAGs.")
    else:
        topological_order = topo_sorted

    is_valid = len(errors) == 0
    return ValidationResult(
        valid=is_valid,
        errors=errors,
        warnings=warnings,
        topological_order=topological_order
    )
