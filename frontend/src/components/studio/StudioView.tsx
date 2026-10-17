import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Loader2 } from 'lucide-react';
import { Header } from '../header/Header';
import { WorkflowCanvas } from '../canvas/WorkflowCanvas';
import { NodeInspector } from '../inspector/NodeInspector';
import { ExecutionDrawer } from '../execution/ExecutionDrawer';
import { ExecutionHistoryModal } from '../history/ExecutionHistoryModal';
import {
  cancelExecution,
  createWorkflow,
  getAgentProfiles,
  getExecution,
  getFreeModels,
  getRuntimeHealth,
  getTeamMessages,
  listExecutions,
  runWorkflow,
  updateWorkflow,
} from '../../api/client';
import type {
  AgentNodeData,
  AgentProfile,
  ModelCatalogEntry,
  RuntimeHealth,
  StartNodeData,
  TeamMessage,
  ValidationResult,
  WorkflowExecution,
} from '../../types';

/**
 * Legacy Studio restoration — the self-contained DAG workflow builder page.
 *
 * Owns the workspace state (nodes/edges/workflow id), performs real client-side
 * DAG validation (Kahn's algorithm + terminal/reachability checks mirroring the
 * backend validator), saves through the workflows API, runs pipelines in the
 * background and streams the team-chat turns into the Execution Drawer by
 * polling the real executions/messages endpoints.
 */

const DEMO_WORKFLOW_NAME = 'University Capstone Pipeline';
const FREE_MODEL_FALLBACK = 'muse-spark-1.3-contributor-free';
const POLL_INTERVAL_MS = 800;

/** Execution-only fields that never belong in a saved definition_json. */
const EXECUTION_DATA_KEYS = ['executionStatus', 'attemptCount', 'durationMs', 'output', 'errorMessage'];

function createDemoNodes(): Node[] {
  return [
    {
      id: 'start-1',
      type: 'start',
      position: { x: 0, y: 260 },
      data: {
        label: 'Start Trigger',
        initialInput:
          'Design and validate a university capstone enrollment platform with clear milestones and risk mitigations.',
      },
    },
    {
      id: 'agent-planner',
      type: 'agent',
      position: { x: 360, y: 240 },
      data: {
        display_name: 'Lead Planner',
        role_type: 'planner',
        model_id: FREE_MODEL_FALLBACK,
        variant: 'high',
        task_depth: 'Standard',
        timeout: 120,
        instructions:
          'Break the capstone goal into logical, sequential and measurable milestones with explicit dependencies and intermediate deliverables.',
      },
    },
    {
      id: 'agent-analyst',
      type: 'agent',
      position: { x: 720, y: 240 },
      data: {
        display_name: 'Risk Analyst',
        role_type: 'analyst',
        model_id: FREE_MODEL_FALLBACK,
        variant: 'high',
        task_depth: 'Deep',
        timeout: 150,
        instructions:
          'Evaluate feasibility, architecture trade-offs and security risks of the plan; flag operational constraints with actionable mitigations.',
      },
    },
    {
      id: 'agent-reviewer',
      type: 'agent',
      position: { x: 1080, y: 240 },
      data: {
        display_name: 'Quality Reviewer',
        role_type: 'reviewer',
        model_id: FREE_MODEL_FALLBACK,
        variant: 'max',
        task_depth: 'Deep',
        timeout: 180,
        instructions:
          'Audit the plan and the risk analysis for contradictions, missing edge cases and contract compliance before final approval.',
      },
    },
    {
      id: 'output-1',
      type: 'output',
      position: { x: 1440, y: 260 },
      data: { label: 'Capstone Deliverable' },
    },
  ];
}

function createDemoEdges(): Edge[] {
  return [
    { id: 'e-start-planner', source: 'start-1', target: 'agent-planner' },
    { id: 'e-planner-analyst', source: 'agent-planner', target: 'agent-analyst' },
    { id: 'e-analyst-reviewer', source: 'agent-analyst', target: 'agent-reviewer' },
    { id: 'e-reviewer-output', source: 'agent-reviewer', target: 'output-1' },
  ];
}

/** Strip React Flow / execution-only bookkeeping so definition_json stays portable. */
function serializeDefinition(nodes: Node[], edges: Edge[]) {
  return {
    nodes: nodes.map((node) => {
      const data: Record<string, unknown> = { ...(node.data as Record<string, unknown>) };
      EXECUTION_DATA_KEYS.forEach((key) => delete data[key]);
      return {
        id: node.id,
        type: node.type,
        position: { x: node.position.x, y: node.position.y },
        data,
      };
    }),
    edges: edges.map((edge) => ({ id: edge.id, source: edge.source, target: edge.target })),
  };
}

/**
 * Real client-side DAG validation (mirrors backend/app/engine/validator.py):
 * exactly one Start, at least one Output, unique ids, valid endpoints, no
 * self-loops, full reachability both ways and Kahn's cycle detection.
 */
function validateGraph(nodes: Node[], edges: Edge[]): ValidationResult {
  const errors: string[] = [];

  if (nodes.length === 0) {
    return { valid: false, errors: ['Workflow contains no nodes'], topological_order: [] };
  }

  const nodeIds = new Set<string>();
  const startIds: string[] = [];
  const outputIds: string[] = [];

  for (const node of nodes) {
    if (!node.id || nodeIds.has(node.id)) {
      errors.push(`Duplicate or missing node ID detected: '${node.id}'`);
    }
    nodeIds.add(node.id);

    if (node.type === 'start') {
      startIds.push(node.id);
    } else if (node.type === 'output') {
      outputIds.push(node.id);
    } else if (node.type === 'agent') {
      const data = (node.data || {}) as AgentNodeData;
      if (!data.role_type) errors.push(`Agent node '${node.id}' is missing required 'role_type'`);
      if (!data.model_id) errors.push(`Agent node '${node.id}' is missing required 'model_id'`);
    } else {
      errors.push(`Node '${node.id}' has unsupported type '${node.type ?? ''}'. Supported types: Start, Agent, Output`);
    }
  }

  if (startIds.length === 0) {
    errors.push('Workflow must contain exactly one Start node (found 0)');
  } else if (startIds.length > 1) {
    errors.push(`Workflow must contain exactly one Start node (found ${startIds.length}: ${startIds.join(', ')})`);
  }
  if (outputIds.length === 0) {
    errors.push('Workflow must contain at least one Output node (found 0)');
  }

  const adjacency = new Map<string, string[]>();
  const reverseAdjacency = new Map<string, string[]>();
  const inDegree = new Map<string, number>();
  nodeIds.forEach((id) => {
    adjacency.set(id, []);
    reverseAdjacency.set(id, []);
    inDegree.set(id, 0);
  });

  const edgeIds = new Set<string>();
  for (const edge of edges) {
    if (edgeIds.has(edge.id)) errors.push(`Duplicate edge ID detected: '${edge.id}'`);
    edgeIds.add(edge.id);

    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) {
      errors.push(`Edge '${edge.id}' references non-existent source or target node`);
      continue;
    }
    if (edge.source === edge.target) {
      errors.push(`Self-loop detected on node '${edge.source}' via edge '${edge.id}'`);
      continue;
    }
    if (startIds.includes(edge.target)) {
      errors.push(`Start node '${edge.target}' cannot have incoming edges (from '${edge.source}')`);
    }
    if (outputIds.includes(edge.source)) {
      errors.push(`Output node '${edge.source}' cannot have outgoing edges (to '${edge.target}')`);
    }

    adjacency.get(edge.source)?.push(edge.target);
    reverseAdjacency.get(edge.target)?.push(edge.source);
    inDegree.set(edge.target, (inDegree.get(edge.target) ?? 0) + 1);
  }

  if (errors.length > 0) {
    return { valid: false, errors, topological_order: [] };
  }

  // Forward reachability from the Start node (no orphans).
  const reachableFromStart = new Set<string>();
  const forwardQueue = [startIds[0]];
  while (forwardQueue.length > 0) {
    const current = forwardQueue.shift() as string;
    if (reachableFromStart.has(current)) continue;
    reachableFromStart.add(current);
    for (const neighbor of adjacency.get(current) ?? []) {
      if (!reachableFromStart.has(neighbor)) forwardQueue.push(neighbor);
    }
  }
  const orphanNodes = [...nodeIds].filter((id) => !reachableFromStart.has(id));
  if (orphanNodes.length > 0) {
    errors.push(`Orphan node(s) detected (unreachable from Start): ${orphanNodes.join(', ')}`);
  }

  // Backward reachability from Output nodes (no dead ends).
  const reachesOutput = new Set<string>();
  const backwardQueue = [...outputIds];
  while (backwardQueue.length > 0) {
    const current = backwardQueue.shift() as string;
    if (reachesOutput.has(current)) continue;
    reachesOutput.add(current);
    for (const predecessor of reverseAdjacency.get(current) ?? []) {
      if (!reachesOutput.has(predecessor)) backwardQueue.push(predecessor);
    }
  }
  const deadEndNodes = [...nodeIds].filter((id) => !reachesOutput.has(id));
  if (deadEndNodes.length > 0) {
    errors.push(`Dead-end node(s) detected (no directed path to Output): ${deadEndNodes.join(', ')}`);
  }

  // Kahn's algorithm — strict DAG assertion + topological order.
  const remainingInDegree = new Map(inDegree);
  const zeroInDegree = [...nodeIds].filter((id) => (remainingInDegree.get(id) ?? 0) === 0);
  const order: string[] = [];
  while (zeroInDegree.length > 0) {
    const current = zeroInDegree.shift() as string;
    order.push(current);
    for (const neighbor of adjacency.get(current) ?? []) {
      const next = (remainingInDegree.get(neighbor) ?? 0) - 1;
      remainingInDegree.set(neighbor, next);
      if (next === 0) zeroInDegree.push(neighbor);
    }
  }
  if (order.length !== nodeIds.size) {
    const unresolved = [...nodeIds].filter((id) => (remainingInDegree.get(id) ?? 0) > 0);
    errors.push(
      `Circular dependency (cycle) detected among nodes: ${unresolved.join(', ')}. Workflows must be strict DAGs.`
    );
    return { valid: false, errors, topological_order: [] };
  }

  return { valid: true, errors: [], topological_order: order };
}

export const StudioView: React.FC = () => {
  // Workspace state (the DAG being edited)
  const [workflowId, setWorkflowId] = useState<string | null>(null);
  const [workflowName, setWorkflowName] = useState(DEMO_WORKFLOW_NAME);
  const [nodes, setNodes] = useState<Node[]>(createDemoNodes);
  const [edges, setEdges] = useState<Edge[]>(createDemoEdges);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null);

  // Catalog + runtime bootstrap (gates the workspace so the inspector always
  // has the real model catalog and role library available).
  const [models, setModels] = useState<ModelCatalogEntry[] | null>(null);
  const [profiles, setProfiles] = useState<AgentProfile[] | null>(null);
  const [runtimeHealth, setRuntimeHealth] = useState<RuntimeHealth | null>(null);

  // Execution state
  const [isValidating, setIsValidating] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [activeExecution, setActiveExecution] = useState<WorkflowExecution | null>(null);
  const [teamMessages, setTeamMessages] = useState<TeamMessage[]>([]);
  const [pollingExecutionId, setPollingExecutionId] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyRuns, setHistoryRuns] = useState<WorkflowExecution[]>([]);

  // Transient feedback banner
  const [notice, setNotice] = useState<string | null>(null);
  const noticeTimerRef = useRef<number | undefined>(undefined);

  const showNotice = useCallback((text: string) => {
    setNotice(text);
    if (noticeTimerRef.current) window.clearTimeout(noticeTimerRef.current);
    noticeTimerRef.current = window.setTimeout(() => setNotice(null), 4200);
  }, []);

  useEffect(
    () => () => {
      if (noticeTimerRef.current) window.clearTimeout(noticeTimerRef.current);
    },
    []
  );

  useEffect(() => {
    let ignore = false;
    Promise.allSettled([getFreeModels(), getAgentProfiles(), getRuntimeHealth()]).then(
      ([modelsResult, profilesResult, healthResult]) => {
        if (ignore) return;
        setModels(modelsResult.status === 'fulfilled' ? modelsResult.value : []);
        setProfiles(profilesResult.status === 'fulfilled' ? profilesResult.value : []);
        setRuntimeHealth(healthResult.status === 'fulfilled' ? healthResult.value : null);
      }
    );
    return () => {
      ignore = true;
    };
  }, []);

  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId) ?? null,
    [nodes, selectedNodeId]
  );

  const validationErrors = useMemo(
    () => (validationResult && !validationResult.valid ? validationResult.errors : []),
    [validationResult]
  );

  // --- Canvas wiring -------------------------------------------------------

  const handleNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((current) => applyNodeChanges(changes, current));
  }, []);

  const handleEdgesChange = useCallback((changes: EdgeChange[]) => {
    setEdges((current) => applyEdgeChanges(changes, current));
  }, []);

  const handleConnect = useCallback((connection: Connection) => {
    setEdges((current) => addEdge(connection, current));
  }, []);

  const handleSelectNode = useCallback((node: Node | null) => {
    setSelectedNodeId(node ? node.id : null);
  }, []);

  const handleUpdateNodeData = useCallback((nodeId: string, updatedData: any) => {
    setNodes((current) => current.map((node) => (node.id === nodeId ? { ...node, data: updatedData } : node)));
  }, []);

  const handleAddNode = useCallback(
    (type: 'start' | 'agent' | 'output') => {
      const id = `${type}-${Date.now().toString(36)}`;
      const index = nodes.length;
      const position = { x: 40 + ((index % 4) * 340), y: 620 };
      let data: Record<string, unknown>;
      if (type === 'agent') {
        data = {
          display_name: 'New Agent',
          role_type: 'custom',
          model_id: models?.[0]?.id || FREE_MODEL_FALLBACK,
          task_depth: 'Standard',
          timeout: 120,
          instructions: '',
        };
      } else if (type === 'start') {
        data = { label: 'Workflow Start', initialInput: '' };
      } else {
        data = { label: 'Workflow Output' };
      }
      setNodes((current) => [...current, { id, type, position, data }]);
      setSelectedNodeId(id);
    },
    [models, nodes.length]
  );

  // --- Workspace actions ---------------------------------------------------

  const handleLoadDemo = useCallback(() => {
    setNodes(createDemoNodes());
    setEdges(createDemoEdges());
    setSelectedNodeId(null);
    setWorkflowId(null);
    setWorkflowName(DEMO_WORKFLOW_NAME);
    setValidationResult(null);
    showNotice('Capstone demo chain loaded — Start → Planner → Analyst → Reviewer → Deliverable.');
  }, [showNotice]);

  const handleNewWorkflow = useCallback(() => {
    setNodes([]);
    setEdges([]);
    setSelectedNodeId(null);
    setWorkflowId(null);
    setWorkflowName('Untitled Workflow');
    setValidationResult(null);
    showNotice('New blank workflow created. Add nodes from the palette.');
  }, [showNotice]);

  const handleValidate = useCallback((): Promise<void> => {
    setIsValidating(true);
    const result = validateGraph(nodes, edges);
    setValidationResult(result);
    if (result.valid) {
      showNotice(`Valid DAG ✓ — topological order: ${result.topological_order.join(' → ')}`);
    } else {
      showNotice(`Validation failed — ${result.errors.length} issue(s) found.`);
    }
    setIsValidating(false);
    return Promise.resolve();
  }, [nodes, edges, showNotice]);

  const saveWorkflow = useCallback(async (): Promise<string | null> => {
    if (nodes.length === 0) {
      showNotice('Nothing to save — add at least one node first.');
      return null;
    }
    const definition_json = JSON.stringify(serializeDefinition(nodes, edges));
    try {
      if (workflowId) {
        await updateWorkflow(workflowId, { name: workflowName, definition_json });
        return workflowId;
      }
      const created = await createWorkflow({
        name: workflowName || 'Untitled Workflow',
        definition_json,
      });
      setWorkflowId(created.id);
      return created.id;
    } catch (error) {
      showNotice(`Save failed: ${error instanceof Error ? error.message : String(error)}`);
      return null;
    }
  }, [nodes, edges, workflowId, workflowName, showNotice]);

  const handleSave = useCallback(async () => {
    const id = await saveWorkflow();
    if (id) showNotice(`Workflow saved (${id.slice(0, 8)}).`);
  }, [saveWorkflow, showNotice]);

  // Map real node execution records back onto the canvas for live status halos.
  const syncCanvasFromExecution = useCallback((execution: WorkflowExecution) => {
    if (!execution.node_executions || execution.node_executions.length === 0) return;
    setNodes((current) =>
      current.map((node) => {
        const nodeExecution = execution.node_executions.find((entry) => entry.node_id === node.id);
        if (!nodeExecution) return node;
        let output: unknown;
        if (nodeExecution.output_json) {
          try {
            output = JSON.parse(nodeExecution.output_json);
          } catch {
            output = nodeExecution.output_json;
          }
        }
        return {
          ...node,
          data: {
            ...node.data,
            executionStatus: nodeExecution.status,
            attemptCount: nodeExecution.attempt_count,
            durationMs: nodeExecution.duration_ms,
            output,
            errorMessage: nodeExecution.error_message,
          },
        };
      })
    );
  }, []);

  const refreshHistory = useCallback(async () => {
    try {
      setHistoryRuns(await listExecutions());
    } catch {
      /* keep the previous list on transient failures */
    }
  }, []);

  const handleRun = useCallback(async () => {
    if (isRunning) return;
    setIsRunning(true);
    try {
      const validation = validateGraph(nodes, edges);
      setValidationResult(validation);
      if (!validation.valid) {
        showNotice(`Cannot run — ${validation.errors.length} validation issue(s) block the pipeline.`);
        return;
      }
      const id = await saveWorkflow();
      if (!id) return;

      const startNode = nodes.find((node) => node.type === 'start');
      const input =
        ((startNode?.data as unknown as StartNodeData)?.initialInput || '').trim() ||
        'Start workflow execution';
      const run = await runWorkflow(id, input, false);

      setTeamMessages([]);
      // Provisional record so the drawer opens immediately; polling replaces it.
      setActiveExecution({
        id: run.execution_id,
        workflow_id: id,
        status: run.status || 'pending',
        node_executions: [],
        events: [],
      });
      setDrawerOpen(true);
      setPollingExecutionId(run.execution_id);
    } catch (error) {
      showNotice(`Run failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setIsRunning(false);
    }
  }, [isRunning, nodes, edges, saveWorkflow, showNotice]);

  // Poll the real execution + team-message endpoints while the run is active.
  useEffect(() => {
    if (!pollingExecutionId) return;
    let disposed = false;
    let timer = 0;

    const poll = async () => {
      if (disposed) return;
      try {
        const [execution, messageList] = await Promise.all([
          getExecution(pollingExecutionId),
          getTeamMessages(pollingExecutionId),
        ]);
        if (disposed) return;
        setActiveExecution(execution);
        setTeamMessages(messageList.messages || []);
        syncCanvasFromExecution(execution);
        if (execution.status !== 'running' && execution.status !== 'pending') {
          window.clearInterval(timer);
          setPollingExecutionId(null);
          refreshHistory();
        }
      } catch {
        /* transient failure — the next tick retries */
      }
    };

    timer = window.setInterval(() => {
      void poll();
    }, POLL_INTERVAL_MS);
    void poll();

    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [pollingExecutionId, refreshHistory, syncCanvasFromExecution]);

  const handleCancelExecution = useCallback(
    async (executionId: string) => {
      try {
        await cancelExecution(executionId);
        setPollingExecutionId(executionId);
      } catch (error) {
        showNotice(`Cancel failed: ${error instanceof Error ? error.message : String(error)}`);
      }
    },
    [showNotice]
  );

  const handleOpenHistory = useCallback(() => {
    setHistoryOpen(true);
    void refreshHistory();
  }, [refreshHistory]);

  const handleSelectExecution = useCallback(async (execution: WorkflowExecution) => {
    setActiveExecution(execution);
    setTeamMessages([]);
    setDrawerOpen(true);
    setPollingExecutionId(execution.id);
    try {
      const messageList = await getTeamMessages(execution.id);
      setTeamMessages(messageList.messages || []);
    } catch {
      /* the poller will retry */
    }
  }, []);

  // --- Render --------------------------------------------------------------

  if (models === null || profiles === null) {
    return (
      <div
        data-testid="studio-boot"
        className="flex h-full min-h-0 flex-col items-center justify-center gap-3 text-sm text-gray-400"
      >
        <Loader2 className="h-5 w-5 animate-spin text-indigo-400" />
        <span>Loading Studio runtime…</span>
      </div>
    );
  }

  return (
    <div
      data-testid="studio-view"
      className="relative flex h-full min-h-0 flex-col overflow-hidden rounded-2xl border border-gray-800 bg-gray-950"
    >
      <Header
        workflowName={workflowName}
        onUpdateWorkflowName={setWorkflowName}
        runtimeHealth={runtimeHealth}
        onValidate={handleValidate}
        onRun={handleRun}
        onSave={handleSave}
        onLoadDemo={handleLoadDemo}
        onNewWorkflow={handleNewWorkflow}
        onOpenHistory={handleOpenHistory}
        isValidating={isValidating}
        isRunning={isRunning}
        validationResult={validationResult}
      />

      <div className="flex flex-1 min-h-0">
        <WorkflowCanvas
          nodes={nodes}
          edges={edges}
          onNodesChange={handleNodesChange}
          onEdgesChange={handleEdgesChange}
          onConnect={handleConnect}
          onSelectNode={handleSelectNode}
          onAddNode={handleAddNode}
          validationErrors={validationErrors}
          isRunning={isRunning}
        />

        {selectedNode && (
          <NodeInspector
            selectedNode={selectedNode}
            models={models}
            profiles={profiles}
            onClose={() => setSelectedNodeId(null)}
            onUpdateNodeData={handleUpdateNodeData}
          />
        )}
      </div>

      {/* Transient Studio feedback banner */}
      {notice && (
        <div
          role="status"
          className="absolute bottom-4 left-4 z-40 max-w-[420px] rounded-xl border border-gray-700 bg-gray-900/95 px-3.5 py-2 text-xs font-medium text-gray-200 shadow-2xl backdrop-blur-md"
        >
          {notice}
        </div>
      )}

      <ExecutionHistoryModal
        isOpen={historyOpen}
        onClose={() => setHistoryOpen(false)}
        executions={historyRuns}
        onSelectExecution={handleSelectExecution}
      />

      <ExecutionDrawer
        execution={activeExecution}
        teamMessages={teamMessages}
        isOpen={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onCancelExecution={handleCancelExecution}
        selectedNodeId={selectedNodeId}
      />
    </div>
  );
};

export default StudioView;
